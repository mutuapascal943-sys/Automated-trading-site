from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from channels.layers import get_channel_layer
from asgiref.sync import async_to_sync
from decouple import config

logger = logging.getLogger(__name__)


class TickerBridge:
    """Bridges broker tick subscriptions to Django Channels WebSocket groups.

    Maintains one broker adapter per subscribed symbol and forwards every
    received tick / built candle into the corresponding Channels group so
    that all connected MarketConsumers receive the data.
    """

    _lock = threading.Lock()
    _active_subscriptions: dict[str, dict[str, Any]] = {}
    _paper_prices: dict[str, Decimal] = {}
    _paper_thread: threading.Thread | None = None
    _paper_running = False

    GROUP_PREFIX = "market_"

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @classmethod
    def ensure_subscription(cls, symbol: str, user=None) -> None:
        """Start forwarding ticks for *symbol* if not already active.

        Tries to connect to Deriv for live ticks when broker credentials
        are available; falls back to a simulated paper ticker otherwise.
        """
        with cls._lock:
            if symbol in cls._active_subscriptions:
                cls._active_subscriptions[symbol]["refcount"] += 1
                return

        credentials = cls._resolve_credentials(user)

        if credentials and credentials.get("token"):
            try:
                cls._start_live_ticker(symbol, credentials)
                with cls._lock:
                    cls._active_subscriptions[symbol] = {
                        "refcount": 1,
                        "mode": "live",
                    }
                logger.info("TickerBridge: subscribed to %s (live)", symbol)
                return
            except Exception as e:
                logger.warning("TickerBridge: live ticker failed for %s: %s", symbol, e)

        cls._start_paper_ticker(symbol)

        with cls._lock:
            cls._active_subscriptions[symbol] = {
                "refcount": 1,
                "mode": "paper",
            }
            cls._paper_prices.setdefault(symbol, Decimal("1.08000"))

        logger.info("TickerBridge: subscribed to %s (paper)", symbol)

    @classmethod
    def remove_subscription(cls, symbol: str, channel_name: str | None = None) -> None:
        """Decrement the reference count for *symbol* and stop if zero."""
        with cls._lock:
            entry = cls._active_subscriptions.get(symbol)
            if entry is None:
                return
            entry["refcount"] -= 1
            if entry["refcount"] > 0:
                return
            cls._active_subscriptions.pop(symbol, None)
            cls._paper_prices.pop(symbol, None)

        logger.info("TickerBridge: unsubscribed from %s", symbol)

    # ------------------------------------------------------------------
    # Credential resolution
    # ------------------------------------------------------------------

    @staticmethod
    def _resolve_credentials(user) -> dict[str, str] | None:
        """Try to extract broker credentials from the user or env."""
        token = ""

        if user is not None and hasattr(user, "broker_api_key") and user.broker_api_key:
            from .credential_encrypt import decrypt

            raw = decrypt(user.broker_api_key)
            if raw:
                token = raw

        if not token:
            token = config("TRADING_API_KEY", default="")

        if not token or token.startswith("sk-your-") or token.startswith("your-"):
            return None

        account_id = config("TRADING_ACCOUNT_ID", default="")
        return {"token": token, "account_id": account_id}

    # ------------------------------------------------------------------
    # Live ticker via Deriv WebSocket
    # ------------------------------------------------------------------

    @classmethod
    def _start_live_ticker(cls, symbol: str, credentials: dict[str, str]) -> None:
        """Connect to Deriv and subscribe to live ticks in a background thread."""
        app_id = config("DERIV_APP_ID", default="1089")

        def _run():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                loop.run_until_complete(cls._live_tick_loop(symbol, credentials, app_id))
            except Exception:
                logger.exception("Live ticker loop ended for %s", symbol)

        thread = threading.Thread(target=_run, daemon=True)
        thread.start()
        time.sleep(1.5)

    @classmethod
    async def _live_tick_loop(
        cls, symbol: str, credentials: dict[str, str], app_id: str
    ) -> None:
        import websockets

        url = f"wss://ws.deriv.com/websockets/v3?app_id={app_id}"
        async with websockets.connect(url, ping_interval=30, ping_timeout=10) as ws:
            token = credentials.get("token", "")
            if token:
                await ws.send(json.dumps({"authorize": token, "req_id": 1}))
                auth_resp = await asyncio.wait_for(ws.recv(), timeout=10)
                auth_data = json.loads(auth_resp)
                if auth_data.get("error"):
                    logger.error("Deriv auth failed: %s", auth_data["error"]["message"])
                    return
                logger.info("TickerBridge: authorized with Deriv")

            await ws.send(json.dumps({
                "ticks": symbol,
                "subscribe": 1,
                "req_id": 2,
            }))
            sub_resp = await asyncio.wait_for(ws.recv(), timeout=10)
            logger.info("TickerBridge: subscribed to %s ticks", symbol)

            async for raw in ws:
                try:
                    data = json.loads(raw)
                except json.JSONDecodeError:
                    continue

                msg_type = data.get("msg_type")
                error = data.get("error")
                if error:
                    logger.warning("Deriv tick error: %s", error)
                    continue

                if msg_type != "tick":
                    continue

                tick = data.get("tick", {})
                epoch = tick.get("epoch", 0)
                bid = tick.get("bid", tick.get("quote", 0))
                ask = tick.get("ask", tick.get("quote", 0))
                price = tick.get("quote", bid)

                group_name = f"{cls.GROUP_PREFIX}{symbol.replace('/', '-')}"
                try:
                    channel_layer = get_channel_layer()
                    await channel_layer.group_send(
                        group_name,
                        {
                            "type": "tick",
                            "data": {
                                "type": "tick",
                                "symbol": symbol,
                                "bid": round(float(bid), 5),
                                "ask": round(float(ask), 5),
                                "price": round(float(price), 5),
                                "timestamp": (
                                    datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat()
                                    if epoch
                                    else datetime.now(timezone.utc).isoformat()
                                ),
                            },
                        },
                    )
                except Exception:
                    logger.exception("Failed to send live tick for %s", symbol)

    # ------------------------------------------------------------------
    # Paper-tick simulation
    # ------------------------------------------------------------------

    @classmethod
    def _start_paper_ticker(cls, symbol: str) -> None:
        """Ensure the paper-tick background thread is alive."""
        if cls._paper_thread and cls._paper_thread.is_alive():
            return

        cls._paper_running = True

        def _loop():
            base_price_map: dict[str, Decimal] = {
                "EUR/USD": Decimal("1.08432"),
                "GBP/USD": Decimal("1.27380"),
                "USD/JPY": Decimal("149.820"),
                "AUD/USD": Decimal("0.65120"),
                "USD/CAD": Decimal("1.35840"),
                "NZD/USD": Decimal("0.60330"),
                "XAU/USD": Decimal("2318.50"),
                "BTC/USD": Decimal("62450.0"),
                "Boom 1000 Index": Decimal("1423.80"),
                "Crash 1000 Index": Decimal("987.40"),
                "Volatility 75 Index": Decimal("8742.10"),
                "Volatility 100 Index": Decimal("5320.60"),
            }

            while cls._paper_running:
                with cls._lock:
                    active = list(cls._active_subscriptions.keys())

                for sym in active:
                    with cls._lock:
                        entry = cls._active_subscriptions.get(sym)
                    if entry is None or entry.get("mode") != "paper":
                        continue

                    with cls._lock:
                        price = cls._paper_prices.get(sym)
                    if price is None:
                        base = base_price_map.get(sym, Decimal("1.00"))
                        price = base
                    delta = float(price) * 0.0003 * (threading.get_ident() % 3 - 1) * 0.5
                    delta += float(price) * 0.00015 * ((time.time() * 1000) % 7 - 3) / 3
                    price = Decimal(str(max(0.001, float(price) + delta)))
                    with cls._lock:
                        cls._paper_prices[sym] = price

                    group_name = f"{cls.GROUP_PREFIX}{sym.replace('/', '-')}"

                    try:
                        channel_layer = get_channel_layer()
                        async_to_sync(channel_layer.group_send)(
                            group_name,
                            {
                                "type": "tick",
                                "data": {
                                    "type": "tick",
                                    "symbol": sym,
                                    "bid": round(float(price) * 0.9998, 5),
                                    "ask": round(float(price) * 1.0002, 5),
                                    "price": round(float(price), 5),
                                    "timestamp": time.strftime(
                                        "%Y-%m-%dT%H:%M:%SZ", time.gmtime()
                                    ),
                                },
                            },
                        )
                    except Exception:
                        logger.exception("Failed to send paper tick for %s", sym)

                time.sleep(1.0)

        cls._paper_thread = threading.Thread(target=_loop, daemon=True)
        cls._paper_thread.start()
        logger.info("TickerBridge: paper ticker thread started")
