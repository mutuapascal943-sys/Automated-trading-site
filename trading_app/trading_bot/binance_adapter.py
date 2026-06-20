from __future__ import annotations

import hashlib
import hmac
import json
import logging
import threading
import time
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Callable

import requests

from .errors import ConnectionLost, InsufficientFunds, InvalidSymbol, OrderRejected, RateLimited
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
    180: "3m",
    300: "5m",
    900: "15m",
    1800: "30m",
    3600: "1h",
    7200: "2h",
    14400: "4h",
    28800: "8h",
    86400: "1d",
}

BINANCE_ERROR_MAP: dict[str, type] = {
    "-2010": InsufficientFunds,
    "-2011": OrderRejected,
    "-1121": InvalidSymbol,
    "-1003": RateLimited,
    "-1001": ConnectionLost,
}


class BinanceAdapter(BrokerAdapter):
    REST_URL = "https://api.binance.com"
    WS_URL = "wss://stream.binance.com:9443/ws"

    def __init__(self) -> None:
        self._api_key: str | None = None
        self._secret: str | None = None
        self._connected = False

        self._ws: Any = None
        self._tick_subscribers: dict[str, list[TickCallback]] = {}
        self._running = False
        self._thread: threading.Thread | None = None

    def connect(self, credentials: dict[str, str]) -> None:
        self._api_key = credentials.get("api_key")
        self._secret = credentials.get("secret")
        self._connected = True
        self._running = True
        if websockets:
            self._thread = threading.Thread(target=self._run_ws, daemon=True)
            self._thread.start()

    def disconnect(self) -> None:
        self._running = False
        self._connected = False

    def get_candles(self, symbol: str, granularity: int, count: int) -> list[Candle]:
        interval = GRANULARITY_MAP.get(granularity)
        if interval is None:
            raise ValueError(f"Unsupported granularity: {granularity}s")

        resp = requests.get(
            f"{self.REST_URL}/api/v3/klines",
            params={"symbol": symbol, "interval": interval, "limit": count},
            timeout=10,
        )
        self._check_error(resp)
        data = resp.json()

        return [
            Candle(
                symbol=symbol,
                open=Decimal(entry[1]),
                high=Decimal(entry[2]),
                low=Decimal(entry[3]),
                close=Decimal(entry[4]),
                volume=Decimal(entry[5]),
                timestamp=datetime.fromtimestamp(entry[0] / 1000, tz=timezone.utc),
                granularity=granularity,
            )
            for entry in data
        ]

    def subscribe_ticks(self, symbol: str, on_tick: TickCallback) -> None:
        with threading.Lock():
            self._tick_subscribers.setdefault(symbol, []).append(on_tick)

    def unsubscribe_ticks(self, symbol: str) -> None:
        with threading.Lock():
            self._tick_subscribers.pop(symbol, None)

    def get_balance(self) -> Balance:
        resp = self._signed_request("GET", "/api/v3/account", {})
        self._check_error(resp)
        data = resp.json()

        total = Decimal("0")
        for bal in data.get("balances", []):
            free = Decimal(bal.get("free", "0"))
            locked = Decimal(bal.get("locked", "0"))
            total += free + locked

        return Balance(
            total=total,
            currency="USDT",
            available=total,
            equity=total,
        )

    def place_order(self, request: OrderRequest) -> Order:
        params: dict[str, Any] = {
            "symbol": request.symbol,
            "side": "BUY" if request.side.lower() == "buy" else "SELL",
            "type": request.order_type.upper(),
            "quantity": float(request.volume),
        }
        if request.price is not None:
            params["price"] = float(request.price)
            params["timeInForce"] = "GTC"

        resp = self._signed_request("POST", "/api/v3/order", params)
        self._check_error(resp)
        data = resp.json()

        return Order(
            id=str(uuid.uuid4()),
            symbol=request.symbol,
            side=request.side,
            order_type=request.order_type,
            volume=Decimal(str(data.get("executedQty", request.volume))),
            price=Decimal(str(data.get("price", "0"))) if data.get("price") else None,
            stop_loss=request.stop_loss,
            take_profit=request.take_profit,
            status=data.get("status", "pending").lower(),
            created_at=datetime.now(timezone.utc),
            filled_at=datetime.now(timezone.utc) if data.get("status") == "FILLED" else None,
            filled_price=Decimal(str(data.get("cummulativeQuoteQty", "0"))) if data.get("status") == "FILLED" else None,
            broker_order_id=str(data.get("orderId", "")),
        )

    def cancel_order(self, order_id: str) -> None:
        resp = self._signed_request("DELETE", "/api/v3/order", {"orderId": order_id})
        self._check_error(resp)

    def get_open_positions(self) -> list[Position]:
        resp = self._signed_request("GET", "/api/v3/account", {})
        self._check_error(resp)
        data = resp.json()

        positions_list: list[Position] = []
        for pos in data.get("positions", []):
            amt = Decimal(pos.get("positionAmt", "0"))
            if amt == 0:
                continue
            entry = Decimal(pos.get("entryPrice", "0"))
            cur = Decimal(pos.get("markPrice", entry))
            pos_id = str(uuid.uuid4())
            positions_list.append(Position(
                id=pos_id,
                symbol=pos.get("symbol", ""),
                side="buy" if amt > 0 else "sell",
                volume=abs(amt),
                entry_price=entry,
                current_price=cur,
                unrealized_pnl=Decimal(pos.get("unrealizedProfit", "0")),
                opened_at=datetime.now(timezone.utc),
            ))
        return positions_list

    def get_trade_history(self, date_range: tuple[datetime, datetime]) -> list[Trade]:
        resp = self._signed_request("GET", "/api/v3/myTrades", {
            "startTime": int(date_range[0].timestamp() * 1000),
            "endTime": int(date_range[1].timestamp() * 1000),
        })
        self._check_error(resp)
        data = resp.json()

        return [
            Trade(
                id=str(uuid.uuid4()),
                symbol=t.get("symbol", ""),
                side="buy" if t.get("isBuyer") else "sell",
                volume=Decimal(str(t.get("qty", "0"))),
                entry_price=Decimal(str(t.get("price", "0"))),
                exit_price=Decimal(str(t.get("price", "0"))),
                pnl=Decimal(str(t.get("realizedPnl", "0"))),
                entry_time=datetime.fromtimestamp(t.get("time", 0) / 1000, tz=timezone.utc),
                exit_time=datetime.fromtimestamp(t.get("time", 0) / 1000, tz=timezone.utc),
                broker_trade_id=str(t.get("id", "")),
            )
            for t in data
        ]

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _signed_request(self, method: str, path: str, params: dict[str, Any]) -> requests.Response:
        if not self._api_key or not self._secret:
            raise ConnectionLost("API key not configured")

        params["timestamp"] = int(time.time() * 1000)
        query = "&".join(f"{k}={v}" for k, v in sorted(params.items()))
        signature = hmac.new(
            self._secret.encode("utf-8"),
            query.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        query += f"&signature={signature}"

        url = f"{self.REST_URL}{path}?{query}"
        headers = {"X-MBX-APIKEY": self._api_key}

        if method == "GET":
            return requests.get(url, headers=headers, timeout=10)
        elif method == "POST":
            return requests.post(url, headers=headers, timeout=10)
        elif method == "DELETE":
            return requests.delete(url, headers=headers, timeout=10)
        else:
            raise ValueError(f"Unsupported method: {method}")

    def _check_error(self, resp: requests.Response) -> None:
        if resp.status_code == 429:
            raise RateLimited("Binance rate limit hit")
        if resp.status_code >= 400:
            try:
                body = resp.json()
                code = str(body.get("code", ""))
                msg = body.get("msg", resp.text)
                exc = BINANCE_ERROR_MAP.get(code, OrderRejected)
                raise exc(f"[{code}] {msg}")
            except (json.JSONDecodeError, KeyError):
                raise OrderRejected(f"HTTP {resp.status_code}: {resp.text}")

    def _run_ws(self) -> None:
        if not websockets:
            return
        import asyncio

        asyncio.set_event_loop(asyncio.new_event_loop())
        loop = asyncio.get_event_loop()
        loop.run_until_complete(self._ws_loop())

    async def _ws_loop(self) -> None:
        import asyncio

        while self._running:
            try:
                streams = "/".join(
                    f"{s.lower()}@ticker"
                    for s in self._tick_subscribers
                )
                if not streams:
                    await asyncio.sleep(1)
                    continue

                url = f"{self.WS_URL}/{streams}"
                async with websockets.connect(url) as ws:
                    async for raw in ws:
                        data = json.loads(raw)
                        if data.get("e") != "24hrTicker":
                            continue
                        symbol = data.get("s", "")
                        tick = Tick(
                            symbol=symbol,
                            bid=Decimal(data.get("b", "0")),
                            ask=Decimal(data.get("a", "0")),
                            timestamp=datetime.fromtimestamp(data.get("E", 0) / 1000, tz=timezone.utc),
                            volume=Decimal(data.get("v", "0")),
                        )
                        with threading.Lock():
                            cbs = list(self._tick_subscribers.get(symbol, []))
                        for cb in cbs:
                            try:
                                cb(tick)
                            except Exception:
                                logger.exception("Tick callback error")
            except Exception:
                if self._running:
                    await asyncio.sleep(5)
