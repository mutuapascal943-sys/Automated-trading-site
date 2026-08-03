from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from .interface import Candle
from .simulated_market import SimulatedMarket, BASE_PRICES


def generate_simulated_candles(
    symbol: str,
    count: int = 50,
    granularity: int = 3600,
    trend: str | None = None,
    volatility: float = 0.001,
) -> list[Candle]:
    """Return OHLC candles from the shared simulated market series.

    The series is deterministic and shared with the chart endpoint and the
    paper ticker, so analysis prices, chart candles and live ticks are all
    consistent. ``trend`` and ``volatility`` are accepted for backward
    compatibility but the canonical series takes precedence.
    """
    return SimulatedMarket.get_candles(symbol, granularity, count)


def generate_simulated_candle_dicts(
    symbol: str,
    count: int = 50,
    granularity: int = 3600,
    trend: str | None = None,
) -> list[dict]:
    """Like generate_simulated_candles but returns plain dicts for code paths
    that expect the dict format (api_views, etc.)."""
    candles = generate_simulated_candles(symbol, count, granularity, trend)
    return [
        {
            "open": float(c.open),
            "high": float(c.high),
            "low": float(c.low),
            "close": float(c.close),
            "volume": float(c.volume),
            "time": int(c.timestamp.timestamp()),
        }
        for c in candles
    ]


def candles_to_dicts(candles: list[Candle]) -> list[dict]:
    """Convert Candle objects to the {open,high,low,close,volume,time} dicts
    expected by the technical analyzer."""
    return [
        {
            "open": float(c.open),
            "high": float(c.high),
            "low": float(c.low),
            "close": float(c.close),
            "volume": float(c.volume),
            "time": int(c.timestamp.timestamp()),
        }
        for c in candles
    ]
