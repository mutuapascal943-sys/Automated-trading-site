from __future__ import annotations

import time

from .base import CandleContext, StrategyResult


def evaluate(context: CandleContext) -> StrategyResult:
    started = time.perf_counter()
    candles = context.candles
    if len(candles) < 12:
        return StrategyResult('ICT', 'neutral', 0.0, (), ('insufficient_structure',), {},
                              round((time.perf_counter() - started) * 1000, 3))

    current = candles[-1]
    sweep_window = candles[-7:-2]
    prior_lows = [float(candle['low']) for candle in candles[-14:-1]]
    prior_highs = [float(candle['high']) for candle in candles[-14:-1]]
    if not prior_lows or not prior_highs:
        return StrategyResult('ICT', 'neutral', 0.0, (), ('missing_liquidity_reference',), {},
                              round((time.perf_counter() - started) * 1000, 3))

    sell_side = min(prior_lows)
    buy_side = max(prior_highs)
    bullish_sweep = any(float(c['low']) < sell_side and float(c['close']) > sell_side for c in sweep_window)
    bearish_sweep = any(float(c['high']) > buy_side and float(c['close']) < buy_side for c in sweep_window)
    conditions: list[str] = []
    reasons: list[str] = []
    levels: dict[str, float] = {'sell_side_liquidity': sell_side, 'buy_side_liquidity': buy_side}
    bullish = False
    bearish = False

    if bullish_sweep:
        swept = next(c for c in reversed(sweep_window) if float(c['low']) < sell_side and float(c['close']) > sell_side)
        next_index = candles.index(swept) + 1
        following_valid = next_index < len(candles) and (
            float(candles[next_index]['high']) <= float(swept['high'])
            and float(candles[next_index]['close']) >= float(swept['low'])
        )
        displacement = float(current['close']) > float(current['open']) and (float(current['high']) - float(current['low'])) >= 1.5 * context.zone_tolerance
        internal_break = float(current['close']) > max(float(c['high']) for c in candles[-6:-1])
        if following_valid and displacement and internal_break:
            bullish = True
            reasons.append('Sell-side liquidity sweep followed by displacement and LTF CHoCH confirmation')
            conditions.extend(('single_candle_htf_sweep', 'sweep_following_candle_valid', 'bullish_displacement', 'ltf_choch_confirmed'))

    if bearish_sweep:
        swept = next(c for c in reversed(sweep_window) if float(c['high']) > buy_side and float(c['close']) < buy_side)
        next_index = candles.index(swept) + 1
        following_valid = next_index < len(candles) and (
            float(candles[next_index]['low']) >= float(swept['low'])
            and float(candles[next_index]['close']) <= float(swept['high'])
        )
        displacement = float(current['close']) < float(current['open']) and (float(current['high']) - float(current['low'])) >= 1.5 * context.zone_tolerance
        internal_break = float(current['close']) < min(float(c['low']) for c in candles[-6:-1])
        if following_valid and displacement and internal_break:
            bearish = True
            reasons.append('Buy-side liquidity sweep followed by displacement and LTF CHoCH confirmation')
            conditions.extend(('single_candle_htf_sweep', 'sweep_following_candle_valid', 'bearish_displacement', 'ltf_choch_confirmed'))

    if bullish and not bearish:
        bias, confidence = 'bullish', 0.78
    elif bearish and not bullish:
        bias, confidence = 'bearish', 0.78
    elif bullish and bearish:
        reasons.append('Conflicting ICT liquidity sweep directions')
        conditions.append('conflicting_sweeps')
        bias, confidence = 'neutral', 0.0

    return StrategyResult(
        'ICT', bias, confidence, tuple(reasons), tuple(conditions), levels,
        round((time.perf_counter() - started) * 1000, 3),
    )