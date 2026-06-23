from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Callable

from .errors import (
    ConnectionLost,
    InsufficientFunds,
    InvalidSymbol,
    OrderRejected,
    RateLimited,
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
    import websockets
except ImportError:
    websockets = None  # type: ignore[assignment]


GRANULARITY_MAP: dict[int, str] = {
    60: "1m",
    120: "2m",
    180: "3m",
    300: "5m",
    600: "10m",
    900: "15m",
    1800: "30m",
    3600: "1h",
    7200: "2h",
    14400: "4h",
    28800: "8h",
    86400: "1d",
}

ERROR_MAP: dict[str, type] = {
    "InsufficientBalance": InsufficientFunds,
    "InvalidSymbol": InvalidSymbol,
    "RateLimit": RateLimited,
    "OrderExecution": OrderRejected,
    "Disconnect": ConnectionLost,
}


class DerivAdapter(BrokerAdapter):
    WS_URL = "wss://ws.deriv.com/websockets/v3"

    def __init__(self, app_id: str = "1089") -> None:
        if websockets is None:
            raise ImportError("websockets package is required for DerivAdapter")

        self._app_id = app_id
        self._token: str | None = None
        self._ws_url = f"{self.WS_URL}?app_id={app_id}"

        self._ws: Any = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._connected = False
        self._running = False
        self._req_id = 0
        self._pending: dict[int, asyncio.Future] = {}
        self._tick_subscribers: dict[str, list[TickCallback]] = {}
        self._subscription_ids: dict[str, int] = {}
        self._last_pong: float = 0.0
        self._lock = threading.Lock()

    def connect(self, credentials: dict[str, str]) -> None:
        self._token = credentials.get("token")
        self._running = True
        self._thread = threading.Thread(target=self._run_event_loop, daemon=True)
        self._thread.start()
        time.sleep(0.5)

    def disconnect(self) -> None:
        self._running = False
        if self._loop and self._ws:
            asyncio.run_coroutine_threadsafe(self._disconnect_ws(), self._loop)

    def get_candles(self, symbol: str, granularity: int, count: int) -> list[Candle]:
        deriv_gran = GRANULARITY_MAP.get(granularity)
        if deriv_gran is None:
            raise ValueError(f"Unsupported granularity: {granularity}s")

        future = self._call_api("ticks_history", {
            "ticks_history": symbol,
            "adjust_start_time": 1,
            "start": 1,
            "end": "latest",
            "style": "candles",
            "granularity": granularity,
            "count": count,
        })
        result = self._await_future(future)

        candles = []
        for entry in result.get("candles", []):
            candles.append(Candle(
                symbol=symbol,
                open=Decimal(str(entry["open"])),
                high=Decimal(str(entry["high"])),
                low=Decimal(str(entry["low"])),
                close=Decimal(str(entry["close"])),
                volume=Decimal(str(entry.get("volume", 0))),
                timestamp=datetime.fromtimestamp(entry["epoch"], tz=timezone.utc),
                granularity=granularity,
            ))
        return candles

    def subscribe_ticks(self, symbol: str, on_tick: TickCallback) -> None:
        with self._lock:
            self._tick_subscribers.setdefault(symbol, []).append(on_tick)

        future = self._call_api("ticks", {"ticks": symbol, "subscribe": 1})
        result = self._await_future(future)
        sub_id = result.get("subscription", {}).get("id")
        if sub_id:
            with self._lock:
                self._subscription_ids[symbol] = sub_id

    def unsubscribe_ticks(self, symbol: str) -> None:
        with self._lock:
            self._tick_subscribers.pop(symbol, None)
            sub_id = self._subscription_ids.pop(symbol, None)
        if sub_id:
            future = self._call_api("forget", {"forget": sub_id})
            self._await_future(future)

    def get_balance(self) -> Balance:
        future = self._call_api("balance", {})
        result = self._await_future(future)
        bal = result.get("balance", {})
        return Balance(
            total=Decimal(str(bal.get("balance", 0))),
            currency=bal.get("currency", "USD"),
            available=Decimal(str(bal.get("balance", 0))),
            equity=Decimal(str(bal.get("balance", 0))),
        )

    def place_order(self, request: OrderRequest) -> Order:
        if request.order_type != "market":
            raise OrderRejected("DerivAdapter only supports market orders")

        proposal = self._await_future(self._call_api("proposal", {
            "proposal": 1,
            "amount": str(request.volume),
            "basis": "stake",
            "contract_type": "CALL" if request.side.lower() == "buy" else "PUT",
            "currency": "USD",
            "symbol": request.symbol,
            "duration": 1,
            "duration_unit": "m",
        }))

        proposal_id = proposal.get("proposal", {}).get("id")
        if not proposal_id:
            raise OrderRejected("Failed to get proposal")

        buy = self._await_future(self._call_api("buy", {
            "buy": proposal_id,
            "price": str(proposal.get("proposal", {}).get("ask_price", 0)),
        }))

        buy_result = buy.get("buy", {})
        order_id = str(uuid.uuid4())

        return Order(
            id=order_id,
            symbol=request.symbol,
            side=request.side,
            order_type=request.order_type,
            volume=request.volume,
            price=None,
            stop_loss=request.stop_loss,
            take_profit=request.take_profit,
            status="filled",
            created_at=datetime.now(timezone.utc),
            filled_at=datetime.now(timezone.utc),
            filled_price=Decimal(str(buy_result.get("buy_price", 0))),
            broker_order_id=str(buy_result.get("contract_id", "")),
        )

    def cancel_order(self, order_id: str) -> None:
        pass

    def get_open_positions(self) -> list[Position]:
        future = self._call_api("portfolio", {})
        result = self._await_future(future)
        positions: list[Position] = []
        for entry in result.get("portfolio", {}).get("contracts", []):
            pos_id = str(uuid.uuid4())
            positions.append(Position(
                id=pos_id,
                symbol=entry.get("symbol", ""),
                side="buy" if entry.get("contract_type") != "PUT" else "sell",
                volume=Decimal(str(entry.get("buy_price", 0))),
                entry_price=Decimal(str(entry.get("entry_tick", 0))),
                current_price=Decimal(str(entry.get("current_tick", 0))),
                unrealized_pnl=Decimal(str(entry.get("profit", 0))),
                opened_at=datetime.fromtimestamp(entry.get("date_start", 0), tz=timezone.utc),
                broker_position_id=str(entry.get("contract_id", "")),
            ))
        return positions

    def get_trade_history(self, date_range: tuple[datetime, datetime]) -> list[Trade]:
        future = self._call_api("profit_table", {
            "profit_table": 1,
            "date_from": int(date_range[0].timestamp()),
            "date_to": int(date_range[1].timestamp()),
        })
        result = self._await_future(future)
        trades: list[Trade] = []
        for entry in result.get("profit_table", {}).get("transactions", []):
            trades.append(Trade(
                id=str(uuid.uuid4()),
                symbol=entry.get("symbol", ""),
                side="buy" if entry.get("action") == "buy" else "sell",
                volume=Decimal(str(entry.get("amount", 0))),
                entry_price=Decimal(str(entry.get("entry_price", 0))),
                exit_price=Decimal(str(entry.get("exit_price", 0))),
                pnl=Decimal(str(entry.get("profit", 0))),
                entry_time=datetime.fromtimestamp(entry.get("entry_time", 0), tz=timezone.utc),
                exit_time=datetime.fromtimestamp(entry.get("exit_time", 0), tz=timezone.utc),
                broker_trade_id=str(entry.get("transaction_id", "")),
            ))
        return trades

    # ------------------------------------------------------------------
    # Internal WebSocket event loop
    # ------------------------------------------------------------------

    def _run_event_loop(self) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._loop.run_until_complete(self._ws_loop())

    async def _ws_loop(self) -> None:
        while self._running:
            try:
                async with websockets.connect(self._ws_url, ping_interval=30, ping_timeout=10) as ws:
                    self._ws = ws
                    await self._authorize()
                    self._connected = True
                    async for raw in ws:
                        try:
                            await self._handle_message(raw)
                        except Exception:
                            logger.exception("Error handling message")
            except Exception as e:
                self._connected = False
                logger.warning("WebSocket disconnected: %s", e)
                if self._running:
                    await asyncio.sleep(self._backoff())

    async def _authorize(self) -> None:
        if self._token:
            future = self._make_future()
            await self._ws.send(json.dumps({
                "authorize": self._token,
                "req_id": self._req_id,
            }))
            self._pending[self._req_id] = future
            self._req_id += 1
            await future

    async def _disconnect_ws(self) -> None:
        if self._ws:
            await self._ws.close()
        self._connected = False

    def _call_api(self, method: str, params: dict[str, Any]) -> asyncio.Future:
        if not self._loop:
            raise ConnectionLost("Not connected")
        future = asyncio.run_coroutine_threadsafe(
            self._async_call(method, params), self._loop
        )
        return future

    async def _async_call(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        if not self._ws:
            raise ConnectionLost("WebSocket not connected")

        req_id = self._req_id
        self._req_id += 1
        payload: dict[str, Any] = {**params, "req_id": req_id}

        future = self._make_future()
        self._pending[req_id] = future

        await self._ws.send(json.dumps(payload))
        return await future

    def _make_future(self) -> asyncio.Future:
        return asyncio.get_event_loop().create_future()

    def _await_future(self, future: asyncio.Future, timeout: float = 30) -> dict[str, Any]:
        try:
            return future.result(timeout=timeout)
        except Exception as e:
            raise ConnectionLost(f"Request failed: {e}") from e

    async def _handle_message(self, raw: str) -> None:
        data = json.loads(raw)
        req_id = data.get("req_id")
        msg_type = data.get("msg_type")

        error = data.get("error")
        if error:
            exc_type = ERROR_MAP.get(error.get("code", ""), OrderRejected)
            exc = exc_type(error.get("message", "Unknown error"))
            if req_id and req_id in self._pending:
                self._pending.pop(req_id).set_exception(exc)
            return

        if msg_type == "tick":
            tick_data = data.get("tick", {})
            tick = Tick(
                symbol=tick_data.get("symbol", ""),
                bid=Decimal(str(tick_data.get("bid", 0))),
                ask=Decimal(str(tick_data.get("ask", 0))),
                timestamp=datetime.fromtimestamp(tick_data.get("epoch", 0), tz=timezone.utc),
            )
            with self._lock:
                callbacks = self._tick_subscribers.get(tick.symbol, [])
            for cb in callbacks:
                try:
                    cb(tick)
                except Exception:
                    logger.exception("Tick callback error")
            return

        if msg_type == "pong":
            self._last_pong = time.time()

        if req_id and req_id in self._pending:
            self._pending.pop(req_id).set_result(data)

    @staticmethod
    def _backoff(attempt: int = 0) -> float:
        return min(1 * (2 ** attempt), 60)
