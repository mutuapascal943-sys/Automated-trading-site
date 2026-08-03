"""Single shared source of simulated market data for paper mode.

Every paper-mode consumer (ticker bridge, candle fallbacks, chart endpoint,
analysis views) reads from the SAME deterministic per-symbol series, so
entries, stop-losses and take-profits always line up with the candles drawn
on the chart instead of appearing as random lines far from the price.

The series is a deterministic random walk seeded by symbol. Each symbol has
one canonical ``MASTER_LEN``-tick path anchored to wall-clock time; candles
are built by aggregating slices of that path, and live ticks continue the
same path from its current end.
"""

from __future__ import annotations

import random
import threading
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from .interface import Candle

BASE_PRICES: dict[str, float] = {
    "EUR/USD": 1.08432,
    "GBP/USD": 1.27380,
    "USD/JPY": 149.820,
    "AUD/USD": 0.65120,
    "USD/CAD": 1.35840,
    "NZD/USD": 0.60330,
    "XAU/USD": 2318.50,
    "BTC/USD": 62450.0,
    "Boom 1000 Index": 1423.80,
    "Crash 1000 Index": 987.40,
    "Volatility 75 Index": 8742.10,
    "Volatility 100 Index": 5320.60,
}

# Simulated ticks are 1-second resolution. Long enough to cover the largest
# fallback request (50 * 3600 = 180k ticks) plus chart history.
MASTER_LEN = 200000
TICK_INTERVAL = 1

# Per-symbol per-tick volatility (standard deviation of simple returns).
# FX is tight, crypto and synthetic indices are wider.
VOLATILITIES: dict[str, float] = {
    "EUR/USD": 0.00012,
    "GBP/USD": 0.00012,
    "USD/JPY": 0.00012,
    "AUD/USD": 0.00014,
    "USD/CAD": 0.00012,
    "NZD/USD": 0.00014,
    "XAU/USD": 0.00040,
    "BTC/USD": 0.00120,
    "Boom 1000 Index": 0.00060,
    "Crash 1000 Index": 0.00060,
    "Volatility 75 Index": 0.00250,
    "Volatility 100 Index": 0.00350,
}

# Mean-reversion strength per tick. Keeps the walk anchored to BASE_PRICES so
# prices oscillate realistically instead of compounding off to absurd levels.
KAPPA = 0.0002


class SimulatedMarket:
    """Deterministic, shared simulated price series per symbol."""

    _lock = threading.RLock()
    _series: dict[str, dict] = {}
    _master_start: float = 0.0

    # ------------------------------------------------------------------
    # Series construction
    # ------------------------------------------------------------------

    @classmethod
    def _series_for(cls, symbol: str) -> dict:
        with cls._lock:
            s = cls._series.get(symbol)
            if s is not None:
                return s

            if cls._master_start == 0.0:
                cls._master_start = datetime.now(timezone.utc).timestamp() - MASTER_LEN

            rng = random.Random(f"simulated_market:{symbol}")
            base = BASE_PRICES.get(symbol, 1.0)
            vol = VOLATILITIES.get(symbol, 0.0002)

            prices: list[float] = []
            price = base
            for _ in range(MASTER_LEN):
                price = cls._step(price, base, vol, KAPPA, rng)
                prices.append(price)

            s = {
                "prices": prices,
                "rng": rng,
                "vol": vol,
                "base": base,
                "next_price": price,
            }
            cls._series[symbol] = s
            return s

    @staticmethod
    def _step(price: float, base: float, vol: float, kappa: float, rng: random.Random) -> float:
        """One mean-reverting random-walk step around the base price."""
        ret = kappa * (base - price) / base + rng.gauss(0, vol)
        return max(0.001, price * (1 + ret))

    # ------------------------------------------------------------------
    # Candle history — always a slice of the canonical path ending at the
    # current price, so every caller sees the same series.
    # ------------------------------------------------------------------

    @classmethod
    def get_candles(cls, symbol: str, granularity: int = 60, count: int = 200) -> list[Candle]:
        s = cls._series_for(symbol)
        with cls._lock:
            prices = s["prices"]
            step = max(1, int(granularity / TICK_INTERVAL))
            total = min(count, len(prices) // step)
            if total < 1:
                total = 1
            start_idx = len(prices) - total * step

            candles: list[Candle] = []
            for i in range(total):
                chunk = prices[start_idx + i * step: start_idx + (i + 1) * step]
                o = chunk[0]
                c = chunk[-1]
                h = max(chunk)
                l = min(chunk)
                ts = datetime.fromtimestamp(
                    cls._master_start + (start_idx + i * step) * TICK_INTERVAL,
                    tz=timezone.utc,
                )
                candles.append(Candle(
                    symbol=symbol,
                    open=Decimal(str(round(o, 5))),
                    high=Decimal(str(round(h, 5))),
                    low=Decimal(str(round(l, 5))),
                    close=Decimal(str(round(c, 5))),
                    volume=Decimal(str(round(step * 25.0, 2))),
                    timestamp=ts,
                    granularity=granularity,
                ))
            return candles

    # ------------------------------------------------------------------
    # Live ticks — continue the canonical path from its current end so the
    # first tick equals the last candle close.
    # ------------------------------------------------------------------

    @classmethod
    def current_price(cls, symbol: str) -> Decimal:
        s = cls._series_for(symbol)
        return Decimal(str(s["next_price"]))

    @classmethod
    def next_tick(cls, symbol: str) -> Candle | None:
        """Return the next simulated price as a 1-second pseudo-candle so the
        ticker bridge can publish it; continues the shared walk."""
        s = cls._series_for(symbol)
        with cls._lock:
            price = cls._step(s["next_price"], s["base"], s["vol"], KAPPA, s["rng"])
            s["next_price"] = price
            ts = datetime.now(timezone.utc)
        return Candle(
            symbol=symbol,
            open=Decimal(str(round(price, 5))),
            high=Decimal(str(round(price, 5))),
            low=Decimal(str(round(price, 5))),
            close=Decimal(str(round(price, 5))),
            volume=Decimal("0"),
            timestamp=ts,
            granularity=TICK_INTERVAL,
        )
