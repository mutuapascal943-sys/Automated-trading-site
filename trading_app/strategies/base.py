from __future__ import annotations

import time
from dataclasses import dataclass, field

from trading_app.ml.features import compute_features


@dataclass
class StrategyResult:
    name: str
    bias: str
    confidence: float
    reasons: tuple[str, ...]
    conditions: tuple[str, ...]
    levels: dict[str, float]
    processing_time_ms: float
    error: str | None = None

    def as_dict(self) -> dict:
        return {
            'name': self.name,
            'bias': self.bias,
            'confidence': self.confidence,
            'reasons': list(self.reasons),
            'conditions': list(self.conditions),
            'levels': self.levels,
            'processing_time_ms': self.processing_time_ms,
            'error': self.error,
        }


@dataclass
class CandleContext:
    candles: list[dict]
    featured: list[dict]
    current: dict
    previous: dict
    support: float | None
    resistance: float | None
    support_touches: int
    resistance_touches: int
    zone_tolerance: float
    preprocessing_ms: float
    multi_timeframe: dict[str, list[dict]] = field(default_factory=dict)
    news_status: dict | None = None


def prepare_context(candles: list[dict], lookback: int = 50) -> CandleContext:
    started = time.perf_counter()
    featured = compute_features(candles)
    current = featured[-1] if featured else {}
    previous = featured[-2] if len(featured) > 1 else current

    valid = [
        candle for candle in candles[-lookback:]
        if _valid_candle(candle)
    ]
    closes = [float(candle['close']) for candle in valid]
    true_ranges = []
    for index, candle in enumerate(valid):
        prior_close = closes[index - 1] if index else float(candle['close'])
        high, low = float(candle['high']), float(candle['low'])
        true_ranges.append(max(high - low, abs(high - prior_close), abs(low - prior_close)))
    atr = sum(true_ranges[-14:]) / max(1, min(14, len(true_ranges))) if true_ranges else 0.0
    price = float(current.get('close', 0) or 0)
    tolerance = max(atr * 0.25, abs(price) * 0.0002)

    pivot_lows: list[float] = []
    pivot_highs: list[float] = []
    for index in range(2, len(valid) - 2):
        lows = [float(c['low']) for c in valid[index - 2:index + 3]]
        highs = [float(c['high']) for c in valid[index - 2:index + 3]]
        if lows[2] == min(lows):
            pivot_lows.append(lows[2])
        if highs[2] == max(highs):
            pivot_highs.append(highs[2])

    support = _strongest_nearby_zone(pivot_lows, price, tolerance, below=True)
    resistance = _strongest_nearby_zone(pivot_highs, price, tolerance, below=False)
    return CandleContext(
        candles=candles,
        featured=featured,
        current=current,
        previous=previous,
        support=support[0] if support else None,
        resistance=resistance[0] if resistance else None,
        support_touches=support[1] if support else 0,
        resistance_touches=resistance[1] if resistance else 0,
        zone_tolerance=tolerance,
        preprocessing_ms=round((time.perf_counter() - started) * 1000, 3),
        multi_timeframe={},
        news_status=None,
    )


def _strongest_nearby_zone(
    pivots: list[float], price: float, tolerance: float, *, below: bool,
) -> tuple[float, int] | None:
    candidates = (
        [value for value in pivots if value <= price]
        if below else [value for value in pivots if value >= price]
    )
    if not candidates:
        return None

    clusters: list[list[float]] = []
    for value in sorted(candidates):
        cluster = next((group for group in clusters if abs(sum(group) / len(group) - value) <= tolerance), None)
        if cluster is None:
            clusters.append([value])
        else:
            cluster.append(value)
    level_group = max(
        clusters,
        key=lambda group: (len(group), -abs(price - sum(group) / len(group))),
    )
    return sum(level_group) / len(level_group), len(level_group)


def _valid_candle(candle: dict) -> bool:
    try:
        return (
            float(candle['low']) <= float(candle['high'])
            and float(candle['low']) <= float(candle['close']) <= float(candle['high'])
            and float(candle['volume']) >= 0
        )
    except (KeyError, TypeError, ValueError):
        return False