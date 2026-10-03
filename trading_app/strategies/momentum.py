from __future__ import annotations

import time

from .base import CandleContext, StrategyResult


def evaluate(context: CandleContext) -> StrategyResult:
    started = time.perf_counter()
    candles = context.featured
    reasons: list[str] = []
    conditions: list[str] = []
    levels: dict[str, float] = {}
    bias = 'neutral'
    confidence = 0.0

    if len(candles) < 51:
        return StrategyResult(
            'Momentum', bias, confidence, (), ('insufficient_candles',), {},
            round((time.perf_counter() - started) * 1000, 3),
        )

    current, previous = candles[-1], candles[-2]
    close = float(current['close'])
    rsi = float(current.get('rsi_14', 50))
    prior_rsi = float(previous.get('rsi_14', 50))
    sma20 = float(current.get('sma_20', close))
    sma50 = float(current.get('sma_50', close))
    prior_sma20 = float(previous.get('sma_20', sma20))
    prior_sma50 = float(previous.get('sma_50', sma50))
    volumes = [float(c.get('volume', 0)) for c in candles[-51:-1]]
    average_volume = sum(volumes) / len(volumes) if volumes else 0.0
    current_volume = float(current.get('volume', 0))
    volume_ratio = current_volume / average_volume if average_volume > 0 else 0.0
    lookback_high = max(float(c['high']) for c in candles[-21:-1])
    lookback_low = min(float(c['low']) for c in candles[-21:-1])

    bullish: list[tuple[str, float]] = []
    bearish: list[tuple[str, float]] = []
    if rsi > 50:
        bullish.append(('RSI above 50 confirms bullish momentum', 0.70))
        conditions.append('rsi_above_50')
    if prior_rsi <= 60 < rsi:
        bullish.append(('RSI crossed above 60 as an early momentum alert', 0.78))
        conditions.append('rsi_crossed_above_60')

    if prior_sma20 <= prior_sma50 and sma20 > sma50 and close > sma20 and close > sma50:
        if volume_ratio > 1:
            bullish.append(('20-period MA crossed above 50-period MA with price and volume confirmation', 0.80))
            conditions.append('bullish_ma_crossover_volume_confirmed')
    if prior_sma20 >= prior_sma50 and sma20 < sma50 and close < sma20 and close < sma50:
        if volume_ratio > 1:
            bearish.append(('20-period MA crossed back below 50-period MA with price and volume confirmation', 0.75))
            conditions.append('bearish_ma_crossover_volume_confirmed')

    if close > lookback_high and volume_ratio >= 1.5:
        bullish.append(('Breakout above recent resistance with at least 1.5x average volume', 0.82))
        conditions.append('resistance_breakout_volume_1_5x')
        levels['breakout_level'] = lookback_high
    if close < lookback_low and volume_ratio >= 1.5:
        bearish.append(('Downside breakout below recent support with at least 1.5x average volume', 0.82))
        conditions.append('support_breakdown_volume_1_5x')
        levels['breakout_level'] = lookback_low

    if close > sma50 and sma50 > float(candles[-6]['sma_50']):
        touched_rising_average = (
            (float(current['low']) <= sma20 and close > sma20)
            or (float(current['low']) <= sma50 and close > sma50)
        )
        if touched_rising_average:
            bullish.append(('Pullback to a rising 20/50-period MA followed by a bounce', 0.76))
            conditions.append('uptrend_ma_pullback_bounce')
            levels['pullback_level'] = sma20 if float(current['low']) <= sma20 else sma50

    if bullish and not bearish:
        bias = 'bullish'
        reasons = [reason for reason, _ in bullish]
        confidence = max(value for _, value in bullish)
    elif bearish and not bullish:
        bias = 'bearish'
        reasons = [reason for reason, _ in bearish]
        confidence = max(value for _, value in bearish)
    elif bullish and bearish:
        reasons = ['Conflicting source-defined momentum conditions']
        conditions.append('conflicting_directional_conditions')

    return StrategyResult(
        'Momentum', bias, confidence, tuple(reasons), tuple(conditions), levels,
        round((time.perf_counter() - started) * 1000, 3),
    )