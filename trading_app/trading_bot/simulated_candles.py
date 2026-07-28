from __future__ import annotations

import math
import random
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from .interface import Candle


BASE_PRICES: dict[str, list[float]] = {
    "EUR/USD": [1.08432],
    "GBP/USD": [1.27380],
    "USD/JPY": [149.820],
    "XAU/USD": [2318.50],
    "BTC/USD": [62450.0],
    "AUD/USD": [0.65120],
    "USD/CAD": [1.35840],
    "NZD/USD": [0.60330],
}


def generate_simulated_candles(
    symbol: str,
    count: int = 50,
    granularity: int = 3600,
    trend: str | None = None,
    volatility: float = 0.001,
) -> list[Candle]:
    """Generate realistic-looking simulated OHLC candles with a random walk
    that includes a subtle trend bias and proper OHLC structure."""
    base = BASE_PRICES.get(symbol, [1.0])[0]

    if trend is None:
        trend = random.choice(["up", "down", "sideways"])

    now = datetime.now(timezone.utc)
    candles: list[Candle] = []
    price = base

    for i in range(count):
        ts = now - timedelta(seconds=granularity * (count - i))

        trend_bias = {"up": 0.0003, "down": -0.0003, "sideways": 0.0}.get(trend, 0.0)
        noise = random.gauss(0, volatility)

        change = price * (trend_bias + noise)
        close = price + change

        high = max(price, close) * (1 + random.uniform(0, volatility * 2))
        low = min(price, close) * (1 - random.uniform(0, volatility * 2))
        open_price = price

        if symbol == "BTC/USD":
            volume = random.uniform(100, 5000)
        elif "XAU" in symbol:
            volume = random.uniform(500, 10000)
        elif "JPY" in symbol:
            volume = random.uniform(100000, 5000000)
        else:
            volume = random.uniform(1000, 50000)

        candles.append(Candle(
            symbol=symbol,
            open=Decimal(str(round(open_price, 5))),
            high=Decimal(str(round(high, 5))),
            low=Decimal(str(round(low, 5))),
            close=Decimal(str(round(close, 5))),
            volume=Decimal(str(round(volume, 2))),
            timestamp=ts,
            granularity=granularity,
        ))
        price = close

    return candles


def generate_simulated_candle_dicts(
    symbol: str,
    count: int = 50,
    granularity: int = 3600,
    trend: str | None = None,
) -> list[dict]:
    """Like generate_simulated_candles but returns plain dicts for code paths
    that expect the dict format (api_views, etc.)."""
    candles = generate_simulated_candles(symbol, count, granularity, trend)
    now_ts = datetime.now(timezone.utc).timestamp()
    return [
        {
            "open": float(c.open),
            "high": float(c.high),
            "low": float(c.low),
            "close": float(c.close),
            "volume": float(c.volume),
            "time": int(now_ts - (granularity * (count - i))),
        }
        for i, c in enumerate(candles)
    ]
