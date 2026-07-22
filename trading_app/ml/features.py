"""
Compute technical indicators as ML features.

All indicators are computed on the raw OHLCV series.
Each feature at time T uses ONLY data available up to time T — no lookahead.

Features:
- RSI (14)
- MACD (12, 26, 9) — MACD line, signal line, histogram
- ATR (14)
- SMA (5, 10, 20, 50)
- EMA (12, 26)
- Bollinger Bands (20, 2) — upper, lower, width, %B
- Price change (1, 3, 5 bars)
- Volume ratio vs SMA(20)
- Candle body ratio (body / range)
- High-low range as % of close
"""

from __future__ import annotations

import math
from typing import Any


def compute_features(candles: list[dict]) -> list[dict]:
    """
    Add technical indicator features to each candle.

    Input: list of {open, high, low, close, volume, time}
    Output: same list with additional keys for each feature.
    """
    n = len(candles)
    if n < 50:
        return candles

    closes = [c["close"] for c in candles]
    highs = [c["high"] for c in candles]
    lows = [c["low"] for c in candles]
    volumes = [c["volume"] for c in candles]

    rsi = _rsi(closes, 14)
    macd_line, signal_line, histogram = _macd(closes)
    atr = _atr(highs, lows, closes, 14)
    sma_5 = _sma(closes, 5)
    sma_10 = _sma(closes, 10)
    sma_20 = _sma(closes, 20)
    sma_50 = _sma(closes, 50)
    ema_12 = _ema(closes, 12)
    ema_26 = _ema(closes, 26)
    bb_upper, bb_lower, bb_width, bb_pctb = _bollinger(closes, 20, 2)
    vol_sma20 = _sma(volumes, 20)
    price_chg_1 = _pct_change(closes, 1)
    price_chg_3 = _pct_change(closes, 3)
    price_chg_5 = _pct_change(closes, 5)

    result = []
    for i in range(n):
        c = candles[i].copy()

        c["rsi_14"] = rsi[i]
        c["macd"] = macd_line[i]
        c["macd_signal"] = signal_line[i]
        c["macd_hist"] = histogram[i]
        c["atr_14"] = atr[i]
        c["sma_5"] = sma_5[i]
        c["sma_10"] = sma_10[i]
        c["sma_20"] = sma_20[i]
        c["sma_50"] = sma_50[i]
        c["ema_12"] = ema_12[i]
        c["ema_26"] = ema_26[i]
        c["bb_upper"] = bb_upper[i]
        c["bb_lower"] = bb_lower[i]
        c["bb_width"] = bb_width[i]
        c["bb_pctb"] = bb_pctb[i]
        c["vol_sma20"] = vol_sma20[i]
        c["vol_ratio"] = volumes[i] / max(vol_sma20[i], 1e-8)
        c["price_chg_1"] = price_chg_1[i]
        c["price_chg_3"] = price_chg_3[i]
        c["price_chg_5"] = price_chg_5[i]

        # Derived
        body = abs(c["close"] - c["open"])
        rng = c["high"] - c["low"]
        c["body_ratio"] = body / max(rng, 1e-8)
        c["hl_range_pct"] = rng / max(c["close"], 1e-8) * 100

        # Price position relative to SMAs
        c["price_vs_sma5"] = (c["close"] - sma_5[i]) / max(sma_5[i], 1e-8) * 100
        c["price_vs_sma20"] = (c["close"] - sma_20[i]) / max(sma_20[i], 1e-8) * 100
        c["sma5_vs_sma20"] = (sma_5[i] - sma_20[i]) / max(sma_20[i], 1e-8) * 100

        result.append(c)

    return result


# ---------------------------------------------------------------------------
# Indicator implementations — pure Python, no numpy dependency
# ---------------------------------------------------------------------------

def _rsi(closes: list[float], period: int = 14) -> list[float]:
    """Wilder's RSI."""
    n = len(closes)
    result = [50.0] * n
    if n < period + 1:
        return result

    deltas = [closes[i] - closes[i - 1] for i in range(1, n)]
    gains = [max(d, 0) for d in deltas]
    losses = [max(-d, 0) for d in deltas]

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    for i in range(period, n):
        if avg_loss == 0:
            result[i] = 100.0
        else:
            rs = avg_gain / avg_loss
            result[i] = 100.0 - (100.0 / (1.0 + rs))

        if i < n - 1:
            avg_gain = (avg_gain * (period - 1) + gains[i]) / period
            avg_loss = (avg_loss * (period - 1) + losses[i]) / period

    return result


def _ema(values: list[float], period: int) -> list[float]:
    """Exponential moving average."""
    n = len(values)
    result = [0.0] * n
    if n == 0:
        return result
    if n < period:
        avg = sum(values) / n
        return [avg] * n

    k = 2.0 / (period + 1)
    result[period - 1] = sum(values[:period]) / period
    for i in range(period, n):
        result[i] = values[i] * k + result[i - 1] * (1 - k)

    return result


def _sma(values: list[float], period: int) -> list[float]:
    """Simple moving average."""
    n = len(values)
    result = [0.0] * n
    for i in range(n):
        p = min(period, i + 1)
        result[i] = sum(values[i - p + 1:i + 1]) / p
    return result


def _macd(
    closes: list[float],
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> tuple[list[float], list[float], list[float]]:
    """MACD line, signal line, histogram."""
    ema_fast = _ema(closes, fast)
    ema_slow = _ema(closes, slow)

    macd_line = [ema_fast[i] - ema_slow[i] for i in range(len(closes))]
    signal_line = _ema(macd_line, signal)
    histogram = [macd_line[i] - signal_line[i] for i in range(len(closes))]

    return macd_line, signal_line, histogram


def _atr(
    highs: list[float],
    lows: list[float],
    closes: list[float],
    period: int = 14,
) -> list[float]:
    """Average True Range."""
    n = len(closes)
    result = [0.0] * n
    if n < 2:
        return result

    trs = [0.0] * n
    trs[0] = highs[0] - lows[0]
    for i in range(1, n):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        )
        trs[i] = tr

    if n >= period:
        result[period - 1] = sum(trs[:period]) / period
        for i in range(period, n):
            result[i] = (result[i - 1] * (period - 1) + trs[i]) / period

    return result


def _bollinger(
    closes: list[float],
    period: int = 20,
    num_std: float = 2.0,
) -> tuple[list[float], list[float], list[float], list[float]]:
    """Bollinger Bands: upper, lower, width, %B."""
    n = len(closes)
    upper = [0.0] * n
    lower = [0.0] * n
    width = [0.0] * n
    pctb = [0.5] * n

    sma_vals = _sma(closes, period)

    for i in range(n):
        p = min(period, i + 1)
        window = closes[i - p + 1:i + 1]
        mean = sum(window) / p
        variance = sum((x - mean) ** 2 for x in window) / p
        std = math.sqrt(variance) if variance > 0 else 0

        upper[i] = sma_vals[i] + num_std * std
        lower[i] = sma_vals[i] - num_std * std
        band_range = upper[i] - lower[i]
        width[i] = band_range / max(sma_vals[i], 1e-8) * 100

        if band_range > 0:
            pctb[i] = (closes[i] - lower[i]) / band_range
        else:
            pctb[i] = 0.5

    return upper, lower, width, pctb


def _pct_change(values: list[float], period: int) -> list[float]:
    """Percentage change over `period` bars."""
    n = len(values)
    result = [0.0] * n
    for i in range(period, n):
        if values[i - period] != 0:
            result[i] = (values[i] - values[i - period]) / values[i - period] * 100
    return result


FEATURE_COLUMNS = [
    "rsi_14", "macd", "macd_signal", "macd_hist",
    "atr_14", "sma_5", "sma_10", "sma_20", "sma_50",
    "ema_12", "ema_26",
    "bb_upper", "bb_lower", "bb_width", "bb_pctb",
    "vol_sma20", "vol_ratio",
    "price_chg_1", "price_chg_3", "price_chg_5",
    "body_ratio", "hl_range_pct",
    "price_vs_sma5", "price_vs_sma20", "sma5_vs_sma20",
]
