from __future__ import annotations

import asyncio
import json
import logging
import re
import threading
import time
from datetime import datetime, timezone
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
    GROUP_PREFIX = "market_"

    @staticmethod
    def _sanitize_group_name(symbol: str) -> str:
        return re.sub(r'[^a-zA-Z0-9_.-]', '_', symbol)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @classmethod
    def ensure_subscription(cls, symbol: str, user=None) -> None:
        """Start forwarding ticks for *symbol* if not already active.

        Connects to Deriv for live ticks and reports unavailable status rather
        than substituting simulated prices.
        """
        with cls._lock:
            if symbol in cls._active_subscriptions:
                cls._active_subscriptions[symbol]["refcount"] += 1
                return

        with cls._lock:
            cls._active_subscriptions[symbol] = {
                "refcount": 1,
                "mode": "live",
            }
        try:
            cls._start_live_ticker(symbol, {})
            logger.info("TickerBridge: connecting to Deriv for %s", symbol)
        except Exception as e:
            logger.warning("TickerBridge: live ticker failed for %s: %s", symbol, e)
            cls._mark_unavailable(symbol)

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

        logger.info("TickerBridge: unsubscribed from %s", symbol)

    @classmethod
    def _send_unavailable(cls, symbol: str) -> None:
        channel_layer = get_channel_layer()
        async_to_sync(channel_layer.group_send)(
            f"{cls.GROUP_PREFIX}{cls._sanitize_group_name(symbol)}",
            {"type": "market_status", "data": {
                "type": "status", "status": "unavailable", "symbol": symbol,
                "message": "Live market data unavailable",
            }},
        )

    @classmethod
    def _mark_unavailable(cls, symbol: str) -> None:
        with cls._lock:
            entry = cls._active_subscriptions.get(symbol)
            if entry:
                entry["mode"] = "unavailable"
        try:
            cls._send_unavailable(symbol)
        except Exception:
            logger.exception("Failed to report unavailable live data for %s", symbol)

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
                logger.warning("Live ticker failed for %s", symbol)
            finally:
                cls._mark_unavailable(symbol)

        thread = threading.Thread(target=_run, daemon=True)
        thread.start()
        time.sleep(1.5)

    @classmethod
    async def _live_tick_loop(
        cls, symbol: str, credentials: dict[str, str], app_id: str
    ) -> None:
        import websockets
        from trading_app.api_views import deriv_symbol_for_market

        url = f"wss://ws.derivws.com/websockets/v3?app_id={app_id}"
        async with websockets.connect(url, ping_interval=30, ping_timeout=10) as ws:
            await ws.send(json.dumps({
                "ticks": deriv_symbol_for_market(symbol),
                "subscribe": 1,
                "req_id": 2,
            }))
            sub_resp = await asyncio.wait_for(ws.recv(), timeout=10)
            sub_data = json.loads(sub_resp)
            if sub_data.get("error"):
                raise RuntimeError(sub_data["error"].get("message", "Deriv tick subscription failed"))
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
                    raise RuntimeError(error.get("message", "Deriv tick stream failed"))

                if msg_type != "tick":
                    continue

                tick = data.get("tick", {})
                epoch = tick.get("epoch", 0)
                bid = tick.get("bid", tick.get("quote", 0))
                ask = tick.get("ask", tick.get("quote", 0))
                price = tick.get("quote", bid)

                group_name = f"{cls.GROUP_PREFIX}{cls._sanitize_group_name(symbol)}"
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

