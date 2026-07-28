"""
MetaTrader 5 adapter — bridges the local MT5 terminal via the official
MetaTrader5 Python package.

Requirements:
    pip install MetaTrader5          (Windows only)
    MT5 terminal must be running on the same machine

Environment variables (in .env):
    MT5_LOGIN      – account login ID (int)
    MT5_PASSWORD   – account password
    MT5_SERVER     – broker server name (e.g. DerivSVG-Server)
    MT5_PATH       – optional path to terminal64.exe
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from typing import Any, Callable

from .errors import (
    BrokerError,
    ConnectionLost,
    InsufficientFunds,
    InvalidSymbol,
    OrderRejected,
)
from .interface import (
    Balance,
    BrokerAdapter,
    Candle,
    Order,
    OrderRequest,
    Position,
    Tick,
    TickCallback,
    Trade,
)

logger = logging.getLogger(__name__)

try:
    import MetaTrader5 as mt5
except ImportError:
    mt5 = None  # type: ignore[assignment]

# MT5 timeframe mapping (seconds → MT5 constant)
_TF_MAP: dict[int, int] = {
    60: 1,      # M1
    300: 5,     # M5
    900: 15,    # M15
    1800: 30,   # M30
    3600: 60,   # H1
    14400: 240, # H4
    86400: 16388,  # D1
}


class MT5Adapter(BrokerAdapter):
    """
    Adapter that talks to a locally running MetaTrader 5 terminal.

    Lifecycle:
        connect()  → mt5.initialize() + mt5.login()
        ...        → mt5.copy_rates_from_pos / mt5.order_send etc.
        disconnect() → mt5.shutdown()
    """

    def __init__(self) -> None:
        if mt5 is None:
            raise ImportError(
                "MetaTrader5 package is required for MT5Adapter. "
                "Install with: pip install MetaTrader5"
            )
        self._connected = False
        self._tick_subscribers: dict[str, list[TickCallback]] = {}

    # ------------------------------------------------------------------
    # Connection
    # ------------------------------------------------------------------

    def connect(self, credentials: dict[str, str]) -> None:
        login_raw = credentials.get("login", credentials.get("account_id", ""))
        password = credentials.get("password", credentials.get("api_secret", ""))
        server = credentials.get("server", "")
        path = credentials.get("path", "")

        login = int(login_raw) if login_raw else 0

        kwargs: dict[str, Any] = {}
        if path:
            kwargs["path"] = path
        if server:
            kwargs["server"] = server

        if not mt5.initialize(**kwargs):
            err = mt5.last_error()
            raise ConnectionLost(f"MT5 initialize failed: {err}")

        if login:
            if not mt5.login(login, password=password, server=server):
                err = mt5.last_error()
                mt5.shutdown()
                raise ConnectionLost(f"MT5 login failed: {err}")

        self._connected = True
        info = mt5.account_info()
        if info:
            logger.info(
                "MT5 connected — %s %s, balance %.2f %s",
                info.server, info.login, info.balance, info.currency,
            )

    def disconnect(self) -> None:
        if self._connected:
            mt5.shutdown()
            self._connected = False
            logger.info("MT5 disconnected")

    # ------------------------------------------------------------------
    # Market Data
    # ------------------------------------------------------------------

    def get_candles(self, symbol: str, granularity: int, count: int) -> list[Candle]:
        tf = _TF_MAP.get(granularity)
        if tf is None:
            raise ValueError(f"Unsupported granularity: {granularity}s")

        rates = mt5.copy_rates_from_pos(symbol, tf, 0, count)
        if rates is None or len(rates) == 0:
            err = mt5.last_error()
            raise BrokerError(f"Failed to get candles for {symbol}: {err}")

        candles: list[Candle] = []
        for r in rates:
            candles.append(Candle(
                symbol=symbol,
                open=Decimal(str(r["open"])),
                high=Decimal(str(r["high"])),
                low=Decimal(str(r["low"])),
                close=Decimal(str(r["close"])),
                volume=Decimal(str(r.get("tick_volume", 0))),
                timestamp=datetime.fromtimestamp(r["time"], tz=timezone.utc),
                granularity=granularity,
            ))
        return candles

    def subscribe_ticks(self, symbol: str, on_tick: TickCallback) -> None:
        self._tick_subscribers.setdefault(symbol, []).append(on_tick)
        logger.info("MT5 tick subscription registered for %s", symbol)

    def unsubscribe_ticks(self, symbol: str) -> None:
        self._tick_subscribers.pop(symbol, None)

    # ------------------------------------------------------------------
    # Account
    # ------------------------------------------------------------------

    def get_balance(self) -> Balance:
        info = mt5.account_info()
        if info is None:
            raise BrokerError("Failed to get account info")
        return Balance(
            total=Decimal(str(info.balance)),
            currency=info.currency,
            available=Decimal(str(info.margin_free)),
            equity=Decimal(str(info.equity)),
        )

    # ------------------------------------------------------------------
    # Orders
    # ------------------------------------------------------------------

    def place_order(self, request: OrderRequest) -> Order:
        # Determine MT5 trade action
        if request.order_type == "market":
            trade_action = 1  # TRADE_ACTION_DEAL
            price = mt5.symbol_info_tick(request.symbol).ask if request.side.lower() == "buy" else mt5.symbol_info_tick(request.symbol).bid
        elif request.order_type == "limit":
            trade_action = 2  # TRADE_ACTION_PENDING
            price = float(request.price) if request.price else 0.0
        elif request.order_type == "stop":
            trade_action = 2
            price = float(request.price) if request.price else 0.0
        else:
            raise OrderRejected(f"Unsupported order type: {request.order_type}")

        order_type = 0 if request.side.lower() == "buy" else 1  # BUY=0, SELL=1

        req = {
            "action": trade_action,
            "symbol": request.symbol,
            "volume": float(request.volume),
            "type": order_type,
            "price": price,
            "deviation": 20,
            "magic": 0,
            "comment": "ATSApp",
            "type_time": 0,   # ORDER_TIME_GTC
            "type_filling": 0,  # ORDER_FILLING_IOC
        }

        if request.stop_loss is not None:
            req["sl"] = float(request.stop_loss)
        if request.take_profit is not None:
            req["tp"] = float(request.take_profit)

        result = mt5.order_send(req)
        if result is None:
            raise BrokerError(f"MT5 order_send returned None: {mt5.last_error()}")

        if result.retcode != 10009:  # TRADE_RETCODE_DONE
            raise OrderRejected(f"MT5 order rejected: {result.comment} (code {result.retcode})")

        order_id = str(uuid.uuid4())
        return Order(
            id=order_id,
            symbol=request.symbol,
            side=request.side,
            order_type=request.order_type,
            volume=request.volume,
            price=Decimal(str(price)),
            stop_loss=request.stop_loss,
            take_profit=request.take_profit,
            status="filled",
            created_at=datetime.now(timezone.utc),
            filled_at=datetime.now(timezone.utc),
            filled_price=Decimal(str(result.price)),
            broker_order_id=str(result.order),
        )

    def cancel_order(self, order_id: str) -> None:
        try:
            mt5.order_send({
                "action": 3,  # TRADE_ACTION_REMOVE
                "order": int(order_id),
            })
        except Exception as e:
            logger.warning("Failed to cancel MT5 order %s: %s", order_id, e)

    def get_open_positions(self) -> list[Position]:
        positions = mt5.positions_get()
        if positions is None:
            return []

        result: list[Position] = []
        for p in positions:
            result.append(Position(
                id=str(p.ticket),
                symbol=p.symbol,
                side="buy" if p.type == 0 else "sell",
                volume=Decimal(str(p.volume)),
                entry_price=Decimal(str(p.price_open)),
                current_price=Decimal(str(p.price_current)),
                unrealized_pnl=Decimal(str(p.profit)),
                opened_at=datetime.fromtimestamp(p.time, tz=timezone.utc),
                stop_loss=Decimal(str(p.sl)) if p.sl else None,
                take_profit=Decimal(str(p.tp)) if p.tp else None,
                broker_position_id=str(p.ticket),
            ))
        return result

    def get_trade_history(
        self, date_range: tuple[datetime, datetime]
    ) -> list[Trade]:
        from_ts = int(date_range[0].timestamp())
        to_ts = int(date_range[1].timestamp())
        deals = mt5.history_deals_get(from_ts, to_ts)
        if deals is None:
            return []

        trades: list[Trade] = []
        for d in deals:
            if d.entry == 0:  # skip entry deals, only want exits
                continue
            trades.append(Trade(
                id=str(d.ticket),
                symbol=d.symbol,
                side="buy" if d.type == 0 else "sell",
                volume=Decimal(str(d.volume)),
                entry_price=Decimal(str(d.price)),
                exit_price=Decimal(str(d.price)),
                pnl=Decimal(str(d.profit)),
                entry_time=datetime.fromtimestamp(d.time, tz=timezone.utc),
                exit_time=datetime.fromtimestamp(d.time, tz=timezone.utc),
                broker_trade_id=str(d.ticket),
            ))
        return trades
