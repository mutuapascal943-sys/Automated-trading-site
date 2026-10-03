from __future__ import annotations

import time

from .base import CandleContext, StrategyResult


def evaluate(context: CandleContext) -> StrategyResult:
    started = time.perf_counter()
    current = context.current
    previous = context.previous
    close = float(current.get('close', 0) or 0)
    high = float(current.get('high', close) or close)
    low = float(current.get('low', close) or close)
    prior_close = float(previous.get('close', close) or close)
    current_volume = float(current.get('volume', 0) or 0)
    recent_volumes = [float(c.get('volume', 0) or 0) for c in context.candles[-21:-1]]
    average_volume = sum(recent_volumes) / len(recent_volumes) if recent_volumes else 0.0
    volume_confirmed = average_volume > 0 and current_volume > average_volume
    tolerance = context.zone_tolerance
    support = context.support
    resistance = context.resistance

    reasons: list[str] = []
    conditions: list[str] = []
    levels: dict[str, float] = {}
    bullish = False
    bearish = False

    if support is not None:
        levels['support_zone'] = support
        near_support = low <= support + tolerance and close >= support
        spring = low < support - tolerance and close > support
        if near_support and context.support_touches >= 1:
            bullish = True
            reasons.append('Price approached a support zone')
            conditions.append('support_zone_approach')
            if context.support_touches >= 2:
                conditions.append('repeated_support_tests')
            if close > prior_close:
                conditions.append('support_bounce_confirmation')
        elif spring and close > prior_close:
            bullish = True
            reasons.append('Wyckoff Spring: price moved below support and returned above the zone')
            conditions.extend(('support_sweep', 'close_back_above_support'))

    if resistance is not None:
        levels['resistance_zone'] = resistance
        near_resistance = high >= resistance - tolerance and close <= resistance
        upthrust = high > resistance + tolerance and close < resistance
        rsi = float(current.get('rsi_14', 50) or 50)
        sma20 = float(current.get('sma_20', close) or close)
        bearish_confirmation = rsi < 50 or close < sma20
        if near_resistance and bearish_confirmation and context.resistance_touches >= 1:
            bearish = True
            reasons.append('Price reached a resistance zone with RSI or moving-average confirmation')
            conditions.extend(('resistance_zone', 'bearish_rsi_or_ma_confirmation'))
            if context.resistance_touches >= 2:
                conditions.append('repeated_resistance_tests')
            if close < prior_close:
                conditions.append('resistance_rejection_confirmation')
        elif upthrust and close < prior_close:
            bearish = True
            reasons.append('Wyckoff Upthrust: price moved above resistance and returned below the zone')
            conditions.extend(('resistance_sweep', 'close_back_below_resistance'))

    if support is not None and close < support - tolerance:
        rapid_breakdown = close < float(previous.get('low', prior_close))
        if rapid_breakdown and volume_confirmed:
            bearish = True
            reasons.append('Rapid support-zone breakout with increased momentum and above-average volume')
            conditions.extend(('support_breakout', 'rapid_move', 'increased_momentum', 'volume_confirmed'))
            levels['breakout_zone'] = support
    if resistance is not None and close > resistance + tolerance:
        rapid_breakout = close > float(previous.get('high', prior_close))
        if rapid_breakout and volume_confirmed:
            bullish = True
            reasons.append('Rapid resistance-zone breakout with increased momentum and above-average volume')
            conditions.extend(('resistance_breakout', 'rapid_move', 'increased_momentum', 'volume_confirmed'))
            levels['breakout_zone'] = resistance

    bias = 'neutral'
    confidence = 0.0
    if bullish and not bearish:
        bias, confidence = 'bullish', 0.76
    elif bearish and not bullish:
        bias, confidence = 'bearish', 0.76
    elif bullish and bearish:
        reasons.append('Support and resistance conditions conflict')
        conditions.append('conflicting_zone_conditions')

    return StrategyResult(
        'Support/Resistance', bias, confidence, tuple(reasons), tuple(conditions), levels,
        round((time.perf_counter() - started) * 1000, 3),
    )