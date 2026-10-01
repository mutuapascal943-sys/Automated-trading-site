from __future__ import annotations

from .candlestick_strategies import StrategySignal


def detect_smc_signal(candles: list[dict]) -> StrategySignal | None:
    """Detect an SMC setup using only the supplied OHLC candles.

    The guide's higher-timeframe context is built by aggregating the input
    candles to 15-minute bars. Entry requires an external-liquidity sweep,
    OTE/FVG overlap, and a confirmed close through a recent internal swing.
    """
    if len(candles) < 45:
        return None

    bars = [_ohlc(candle) for candle in candles]
    if any(bar is None for bar in bars):
        return None
    valid_bars = [bar for bar in bars if bar is not None]

    higher_bars = _aggregate_higher_timeframe(candles, valid_bars)
    if len(higher_bars) < 6:
        return None

    structure = _structure_bias(higher_bars)
    if structure == 'neutral':
        return None

    current = valid_bars[-1]
    prior = valid_bars[:-1]
    swing_window = prior[-24:]
    if len(swing_window) < 12:
        return None

    current_open, current_high, current_low, current_close = current
    external_window = swing_window[-12:-2]
    external_high = max(bar[1] for bar in external_window)
    external_low = min(bar[2] for bar in external_window)
    sweep_window = valid_bars[-6:-1]
    liquidity_sweep = any(
        bar[2] < external_low and bar[3] > external_low
        for bar in sweep_window
    ) if structure == 'bullish' else any(
        bar[1] > external_high and bar[3] < external_high
        for bar in sweep_window
    )
    if not liquidity_sweep:
        return None

    range_high = max(bar[1] for bar in higher_bars[-12:])
    range_low = min(bar[2] for bar in higher_bars[-12:])
    dealing_range = range_high - range_low
    if dealing_range <= 0:
        return None

    ote_low, ote_high = _ote_zone(structure, range_low, range_high)
    fvg = _recent_fvg(valid_bars[:-1], structure)
    if fvg is None:
        return None
    overlap_low = max(ote_low, fvg[0])
    overlap_high = min(ote_high, fvg[1])
    if overlap_low >= overlap_high:
        return None
    if not any(bar[2] <= overlap_high and bar[1] >= overlap_low for bar in valid_bars[-10:-1]):
        return None

    internal = prior[-8:]
    if structure == 'bullish':
        internal_break = current_close > max(bar[1] for bar in internal[-5:])
        stop_loss = min(bar[2] for bar in swing_window[-6:])
    else:
        internal_break = current_close < min(bar[2] for bar in internal[-5:])
        stop_loss = max(bar[1] for bar in swing_window[-6:])
    if not internal_break:
        return None

    risk = current_close - stop_loss if structure == 'bullish' else stop_loss - current_close
    if risk <= 0:
        return None
    target = current_close + 3 * risk if structure == 'bullish' else current_close - 3 * risk

    return StrategySignal(
        name='SMC',
        bias=structure,
        confidence=0.82,
        rationale=(
            'SMC: higher-timeframe structure, external liquidity sweep, '
            'OTE/FVG overlap and lower-timeframe CHoCH confirmed; target >= 3R'
        ),
        stop_loss=stop_loss,
        take_profit=target,
    )


def _ohlc(candle: dict) -> tuple[float, float, float, float] | None:
    try:
        close = float(candle['close'])
        open_price = float(candle.get('open', close))
        high = float(candle.get('high', close))
        low = float(candle.get('low', close))
    except (KeyError, TypeError, ValueError):
        return None
    if low > min(open_price, close) or high < max(open_price, close) or high <= low:
        return None
    return open_price, high, low, close


def _aggregate_higher_timeframe(
    candles: list[dict],
    bars: list[tuple[float, float, float, float]],
) -> list[tuple[float, float, float, float]]:
    intervals = [
        int(candles[index].get('time', 0)) - int(candles[index - 1].get('time', 0))
        for index in range(1, len(candles))
        if int(candles[index].get('time', 0)) > int(candles[index - 1].get('time', 0))
    ]
    if not intervals:
        return []
    base_seconds = sorted(intervals)[len(intervals) // 2]
    group_size = max(3, min(60, round(900 / base_seconds)))

    aggregated = []
    for start in range(0, len(bars) - group_size + 1, group_size):
        group = bars[start:start + group_size]
        aggregated.append((group[0][0], max(bar[1] for bar in group),
                           min(bar[2] for bar in group), group[-1][3]))
    return aggregated


def _structure_bias(bars: list[tuple[float, float, float, float]]) -> str:
    structure_bars = bars[:-4] if len(bars) >= 10 else bars
    midpoint = len(structure_bars) // 2
    earlier = structure_bars[:midpoint]
    later = structure_bars[midpoint:]
    if len(earlier) < 3 or len(later) < 3:
        return 'neutral'
    earlier_high, later_high = max(bar[1] for bar in earlier), max(bar[1] for bar in later)
    earlier_low, later_low = min(bar[2] for bar in earlier), min(bar[2] for bar in later)
    higher_highs = later_high > earlier_high
    higher_lows = later_low > earlier_low
    lower_highs = later_high < earlier_high
    lower_lows = later_low < earlier_low
    if higher_highs and higher_lows:
        return 'bullish'
    if lower_highs and lower_lows:
        return 'bearish'
    return 'neutral'


def _ote_zone(bias: str, range_low: float, range_high: float) -> tuple[float, float]:
    length = range_high - range_low
    if bias == 'bullish':
        return range_high - length * 0.786, range_high - length * 0.618
    return range_low + length * 0.618, range_low + length * 0.786


def _recent_fvg(
    bars: list[tuple[float, float, float, float]], bias: str,
) -> tuple[float, float] | None:
    for index in range(len(bars) - 1, 1, -1):
        first = bars[index - 2]
        third = bars[index]
        if bias == 'bullish' and third[2] > first[1]:
            return first[1], third[2]
        if bias == 'bearish' and third[1] < first[2]:
            return third[1], first[2]
    return None