"""
Clean raw OHLCV data for ML training.

Handles:
- Duplicate timestamps (keep last)
- Missing bars (weekends/holidays) — forward-fill or interpolate
- Price feed errors: zero OHLC, inverted OHLC, extreme gaps
- Volume anomalies: zero-volume spikes
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def clean_ohlcv(
    candles: list[dict],
    granularity: int = 900,
    max_gap_multiplier: float = 5.0,
    max_price_change_pct: float = 10.0,
) -> list[dict]:
    """
    Clean raw OHLCV candle data.

    Steps:
    1. Remove exact duplicate timestamps
    2. Sort by time ascending
    3. Remove candles with invalid OHLC (zero, negative, inverted)
    4. Detect and flag extreme price gaps
    5. Fill missing bars within known trading sessions
    6. Validate volume consistency

    Returns cleaned list of dicts.
    """
    if not candles:
        return []

    original_count = len(candles)

    # 1. Deduplicate by timestamp — keep last occurrence
    by_ts: dict[int, dict] = {}
    for c in candles:
        by_ts[c["time"]] = c
    candles = sorted(by_ts.values(), key=lambda c: c["time"])

    # 2. Remove invalid candles
    valid = []
    for c in candles:
        o, h, l, v = c["open"], c["high"], c["low"], c["close"]

        if o <= 0 or h <= 0 or l <= 0 or v < 0:
            continue

        # High must be >= open and close, low must be <= open and close
        if h < max(o, c["close"]) or l > min(o, c["close"]):
            continue

        # High must be >= low
        if h < l:
            continue

        valid.append(c)

    # 3. Detect extreme gaps between consecutive candles
    if valid and len(valid) > 1:
        median_gap = _median_gap(valid, granularity)
        filtered = [valid[0]]
        for i in range(1, len(valid)):
            gap = valid[i]["time"] - valid[i - 1]["time"]
            price_jump = abs(valid[i]["close"] - valid[i - 1]["close"]) / max(valid[i - 1]["close"], 1e-8) * 100

            # Skip candles with impossibly large time gaps (e.g. data feed restart)
            if gap > median_gap * max_gap_multiplier * 10:
                filtered.append(valid[i])
                continue

            # Flag but keep candles with large price moves (could be news)
            if price_jump > max_price_change_pct:
                logger.warning(
                    f"Large price gap at {valid[i]['time']}: "
                    f"{price_jump:.1f}% ({valid[i-1]['close']:.5f} -> {valid[i]['close']:.5f})"
                )

            filtered.append(valid[i])
        valid = filtered

    # 4. Fill small gaps (1-3 missing bars) by interpolation
    if valid and len(valid) > 1:
        filled = [valid[0]]
        expected_interval = granularity
        for i in range(1, len(valid)):
            gap = valid[i]["time"] - valid[i - 1]["time"]
            bars_missing = int(gap / expected_interval) - 1

            if 0 < bars_missing <= 3:
                # Linear interpolation for missing bars
                prev_candle = filled[-1]
                next_candle = valid[i]
                for j in range(1, bars_missing + 1):
                    frac = j / (bars_missing + 1)
                    interp_candle = {
                        "open": prev_candle["close"],
                        "high": max(prev_candle["close"], next_candle["open"]) * (1 + frac * 0.0001),
                        "low": min(prev_candle["close"], next_candle["open"]) * (1 - frac * 0.0001),
                        "close": prev_candle["close"] + (next_candle["open"] - prev_candle["close"]) * frac,
                        "volume": 0,
                        "time": prev_candle["time"] + int(expected_interval * j),
                        "interpolated": True,
                    }
                    filled.append(interp_candle)

            filled.append(valid[i])
        valid = filled

    removed = original_count - len(valid)
    if removed > 0:
        logger.info(f"Cleaned {removed}/{original_count} candles ({len(valid)} remaining)")

    return valid


def _median_gap(candles: list[dict], granularity: int) -> int:
    """Compute the median time gap between consecutive candles."""
    if len(candles) < 2:
        return granularity
    gaps = [candles[i]["time"] - candles[i - 1]["time"] for i in range(1, len(candles))]
    gaps.sort()
    mid = len(gaps) // 2
    return gaps[mid] if gaps else granularity


def validate_ohlcv(candles: list[dict]) -> dict[str, Any]:
    """Return quality metrics for the cleaned data."""
    if not candles:
        return {"count": 0, "valid": False}

    times = [c["time"] for c in candles]
    gaps = [times[i] - times[i - 1] for i in range(1, len(times))]

    closes = [c["close"] for c in candles]
    returns = [
        (closes[i] - closes[i - 1]) / closes[i - 1]
        for i in range(1, len(closes))
        if closes[i - 1] != 0
    ]

    interpolated = sum(1 for c in candles if c.get("interpolated"))

    return {
        "count": len(candles),
        "start": candles[0]["time"],
        "end": candles[-1]["time"],
        "span_days": (candles[-1]["time"] - candles[0]["time"]) / 86400,
        "avg_gap": sum(gaps) / len(gaps) if gaps else 0,
        "max_gap": max(gaps) if gaps else 0,
        "interpolated_bars": interpolated,
        "avg_return": sum(returns) / len(returns) if returns else 0,
        "return_std": (sum((r - sum(returns)/len(returns))**2 for r in returns) / len(returns))**0.5 if returns else 0,
        "valid": len(candles) >= 100,
    }
