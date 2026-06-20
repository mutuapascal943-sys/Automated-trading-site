from __future__ import annotations

import logging
import threading
import time
from decimal import Decimal
from typing import Any

from channels.layers import get_channel_layer
from asgiref.sync import async_to_sync

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
    def ensure_subscription(cls, symbol: str) -> None:
        """Start forwarding ticks for *symbol* if not already active.

        When no real broker adapter is available (e.g. in development or
        demo mode) this starts a simple background thread that simulates
        price ticks so the front-end always has data to display.
        """
        with cls._lock:
            if symbol in cls._active_subscriptions:
                cls._active_subscriptions[symbol]["refcount"] += 1
                return

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
