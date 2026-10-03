from __future__ import annotations

import time

from .base import CandleContext, StrategyResult


def evaluate(context: CandleContext) -> StrategyResult:
    started = time.perf_counter()
    candles = context.candles[-80:]
    if len(candles) < 20:
        return StrategyResult(
            'Fibonacci', 'neutral', 0.0, (), ('insufficient_swing_data',), {},
            round((time.perf_counter() - started) * 1000, 3),
        )

    swing_high_index = max(range(len(candles)), key=lambda i: float(candles[i]['high']))
    swing_low_index = min(range(len(candles)), key=lambda i: float(candles[i]['low']))
    if swing_high_index == swing_low_index:
        return StrategyResult('Fibonacci', 'neutral', 0.0, (), ('ambiguous_swings',), {},
                              round((time.perf_counter() - started) * 1000, 3))

    high = float(candles[swing_high_index]['high'])
    low = float(candles[swing_low_index]['low'])
    if high <= low:
        return StrategyResult('Fibonacci', 'neutral', 0.0, (), ('invalid_swing_range',), {},
                              round((time.perf_counter() - started) * 1000, 3))
    current = context.current
    price = float(current.get('close', 0) or 0)
    prior = context.previous
    tolerance = max(context.zone_tolerance, (high - low) * 0.008)
    levels: dict[str, float] = {}
    reasons: list[str] = []
    conditions: list[str] = []

    low_before_high = swing_low_index < swing_high_index
    if low_before_high:
        retracements = {ratio: high - (high - low) * ratio for ratio in (0.382, 0.50, 0.618, 0.786)}
        support_levels = retracements
        extension = high + (high - low) * 0.272
        trend = 'bullish'
    else:
        retracements = {ratio: low + (high - low) * ratio for ratio in (0.382, 0.50, 0.618, 0.786)}
        support_levels = retracements
        extension = low - (high - low) * 0.272
        trend = 'bearish'
    levels.update({f'retracement_{ratio:.3f}': value for ratio, value in retracements.items()})
    levels['extension_1_272'] = extension

    clustered = []
    for ratio, level in retracements.items():
        touches = sum(
            1 for candle in candles
            if float(candle['low']) - tolerance <= level <= float(candle['high']) + tolerance
        )
        if touches >= 2:
            clustered.append((ratio, level))

    selected = None
    if clustered:
        selected = min(clustered, key=lambda item: abs(price - item[1]))
        conditions.append('retracement_zone_retested')
    if len(clustered) >= 3:
        conditions.append('three_relationship_cluster')

    bias = 'neutral'
    confidence = 0.0
    if selected:
        ratio, level = selected
        if trend == 'bullish' and price >= level and float(current.get('close', 0)) > float(prior.get('close', 0)):
            bias = 'bullish'
        elif trend == 'bearish' and price <= level and float(current.get('close', 0)) < float(prior.get('close', 0)):
            bias = 'bearish'
        if bias != 'neutral':
            reasons.append(f'{ratio:.3f} retracement area holds with a confirming directional close')
            confidence = 0.74 if len(clustered) >= 3 else 0.66
            conditions.append('recent_swing_with_trend')
            levels['active_retracement'] = level
            levels['initial_extension_target'] = extension

    return StrategyResult(
        'Fibonacci', bias, confidence, tuple(reasons), tuple(conditions), levels,
        round((time.perf_counter() - started) * 1000, 3),
    )