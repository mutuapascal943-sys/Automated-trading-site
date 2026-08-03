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
import time
from collections import deque
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
# Cap on live ticks retained so long-running processes don't grow unbounded.
TICK_HISTORY_LEN = 500000

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
                "ticks": [],
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
    # Candle history — a slice of the canonical path ending at the most
    # recent live tick (or the master path end if no ticks yet), so every
    # caller sees the same series and history keeps extending over time.
    # ------------------------------------------------------------------

    @classmethod
    def get_candles(cls, symbol: str, granularity: int = 60, count: int = 200) -> list[Candle]:
        s = cls._series_for(symbol)
        with cls._lock:
            step = max(1, int(granularity / TICK_INTERVAL))
            master_start = cls._master_start
            total_points = len(s["prices"]) + len(s["ticks"])
            total = min(count, total_points // step)
            if total < 1:
                total = 1

            window = deque(maxlen=total * step)
            for i, p in enumerate(s["prices"]):
                window.append((master_start + i * TICK_INTERVAL, p))
            for epoch, p in s["ticks"]:
                window.append((epoch, p))
            points = list(window)

            candles: list[Candle] = []
            for i in range(total):
                chunk = points[i * step:(i + 1) * step]
                o = chunk[0][1]
                c = chunk[-1][1]
                h = max(p[1] for p in chunk)
                l = min(p[1] for p in chunk)
                ts = datetime.fromtimestamp(chunk[0][0], tz=timezone.utc)
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
        with cls._lock:
            if s["ticks"]:
                return Decimal(str(s["ticks"][-1][1]))
            return Decimal(str(s["next_price"]))

    @classmethod
    def next_tick(cls, symbol: str) -> Candle | None:
        """Return the next simulated price as a 1-second pseudo-candle so the
        ticker bridge can publish it; continues the shared walk and records
        the tick so candle history keeps extending."""
        s = cls._series_for(symbol)
        with cls._lock:
            price = cls._step(s["next_price"], s["base"], s["vol"], KAPPA, s["rng"])
            s["next_price"] = price
            s["ticks"].append((int(time.time()), price))
            if len(s["ticks"]) > TICK_HISTORY_LEN:
                del s["ticks"][:len(s["ticks"]) - TICK_HISTORY_LEN]
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

    @classmethod
    def advance(cls, symbol: str, ticks: int = 60) -> None:
        """Advance the shared walk by *ticks* simulated ticks so paper-mode
        predictions keep resolving even when no browser is streaming ticks.
        The bot cycle calls this each run; 60 ticks/minute mirrors live
        tick speed so resolutions happen in real time, not instantly."""
        for _ in range(int(ticks)):
            cls.next_tick(symbol)
