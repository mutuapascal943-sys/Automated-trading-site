from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class StrategyParameters:
    min_candles: int = 25
    trend_lookback: int = 21
    level_lookback: int = 20
    pin_max_body_ratio: float = 0.30
    pin_min_wick_ratio: float = 0.60
    pin_wick_body_ratio: float = 2.0
    level_tolerance_atr: float = 0.5


@dataclass(frozen=True)
class StrategySignal:
    name: str
    bias: str
    confidence: float
    rationale: str
    stop_loss: float
    take_profit: float


DEFAULT_PARAMETERS = StrategyParameters()


def detect_price_action_signals(
    candles: list[dict],
    parameters: StrategyParameters = DEFAULT_PARAMETERS,
) -> list[StrategySignal]:
    if len(candles) < parameters.min_candles:
        return []

    signals: list[StrategySignal] = []
    last = candles[-1]
    current = _ohlc(last)
    if current is None:
        return signals

    trend_history = candles[:-1]
    trend = _trend(trend_history, parameters.trend_lookback)
    level_history = candles[-parameters.level_lookback - 1:-1]
    support, resistance, atr = _levels(level_history)
    at_support = _near_level(current[2], support, atr, parameters)
    at_resistance = _near_level(current[1], resistance, atr, parameters)

    open_price, high, low, close = current
    candle_range = high - low
    body = abs(close - open_price)
    lower_wick = min(open_price, close) - low
    upper_wick = high - max(open_price, close)

    if candle_range > 0 and body / candle_range <= parameters.pin_max_body_ratio:
        if (
            lower_wick / candle_range >= parameters.pin_min_wick_ratio
            and lower_wick >= max(body, candle_range * 0.05) * parameters.pin_wick_body_ratio
            and close >= low + candle_range * 0.65
            and (trend == "bullish" or at_support)
        ):
            signals.append(_signal(
                "Pin Bar", "bullish", trend, at_support, at_resistance,
                0.62, close, low - atr * 0.1,
            ))
        if (
            upper_wick / candle_range >= parameters.pin_min_wick_ratio
            and upper_wick >= max(body, candle_range * 0.05) * parameters.pin_wick_body_ratio
            and close <= low + candle_range * 0.35
            and (trend == "bearish" or at_resistance)
        ):
            signals.append(_signal(
                "Pin Bar", "bearish", trend, at_support, at_resistance,
                0.62, close, high + atr * 0.1,
            ))

    previous = _ohlc(candles[-2])
    if previous is not None:
        previous_open, previous_high, previous_low, previous_close = previous
        bullish_engulf = (
            previous_close < previous_open
            and close > open_price
            and open_price <= previous_close
            and close >= previous_open
        )
        bearish_engulf = (
            previous_close > previous_open
            and close < open_price
            and open_price >= previous_close
            and close <= previous_open
        )
        if bullish_engulf and (trend == "bullish" or at_support):
            signals.append(_signal(
                "Engulfing Bar", "bullish", trend, at_support, at_resistance,
                0.66, close, low - atr * 0.1,
            ))
        if bearish_engulf and (trend == "bearish" or at_resistance):
            signals.append(_signal(
                "Engulfing Bar", "bearish", trend, at_support, at_resistance,
                0.66, close, high + atr * 0.1,
            ))

    mother = _ohlc(candles[-3])
    inside = _ohlc(candles[-2])
    if mother is not None and inside is not None:
        _, mother_high, mother_low, _ = mother
        _, inside_high, inside_low, _ = inside
        is_inside = inside_high <= mother_high and inside_low >= mother_low

        if is_inside:
            mother_support = _near_level(mother_low, support, atr, parameters)
            mother_resistance = _near_level(mother_high, resistance, atr, parameters)
            if close > mother_high and trend == "bullish" and (mother_support or mother_resistance):
                signals.append(_signal(
                    "Inside Bar Breakout", "bullish", trend, at_support, at_resistance,
                    0.64, close, mother_low - atr * 0.1,
                ))
            elif close < mother_low and trend == "bearish" and (mother_support or mother_resistance):
                signals.append(_signal(
                    "Inside Bar Breakout", "bearish", trend, at_support, at_resistance,
                    0.64, close, mother_high + atr * 0.1,
                ))
            if low < mother_low and close > mother_low and close < mother_high and mother_support:
                signals.append(_signal(
                    "Inside Bar False Breakout", "bullish", trend, at_support, at_resistance,
                    0.70, close, low - atr * 0.1,
                ))
            elif high > mother_high and close < mother_high and close > mother_low and mother_resistance:
                signals.append(_signal(
                    "Inside Bar False Breakout", "bearish", trend, at_support, at_resistance,
                    0.70, close, high + atr * 0.1,
                ))

    return signals


def _ohlc(candle: dict) -> tuple[float, float, float, float] | None:
    try:
        close = float(candle["close"])
        open_price = float(candle.get("open", close))
        high = float(candle.get("high", close))
        low = float(candle.get("low", close))
    except (KeyError, TypeError, ValueError):
        return None
    if low > min(open_price, close) or high < max(open_price, close) or high <= low:
        return None
    return open_price, high, low, close


def _trend(candles: list[dict], lookback: int) -> str:
    values = [_ohlc(candle) for candle in candles[-lookback:]]
    valid = [value for value in values if value is not None]
    if len(valid) < lookback:
        return "neutral"
    closes = [value[3] for value in valid]
    fast_average = sum(closes[-5:]) / 5
    slow_average = sum(closes) / len(closes)
    if fast_average > slow_average and closes[-1] > closes[0]:
        return "bullish"
    if fast_average < slow_average and closes[-1] < closes[0]:
        return "bearish"
    return "neutral"


def _levels(candles: list[dict]) -> tuple[float, float, float]:
    values = [_ohlc(candle) for candle in candles]
    valid = [value for value in values if value is not None]
    if not valid:
        return 0.0, 0.0, 0.0
    support = min(value[2] for value in valid)
    resistance = max(value[1] for value in valid)
    atr = sum(value[1] - value[2] for value in valid[-14:]) / min(len(valid), 14)
    return support, resistance, atr


def _near_level(price: float, level: float, atr: float, parameters: StrategyParameters) -> bool:
    tolerance = max(atr * parameters.level_tolerance_atr, abs(level) * 0.0002)
    return abs(price - level) <= tolerance


def _signal(
    name: str,
    bias: str,
    trend: str,
    at_support: bool,
    at_resistance: bool,
    base_confidence: float,
    entry_price: float,
    stop_loss: float,
) -> StrategySignal:
    aligned_trend = trend == bias
    at_key_level = at_support if bias == "bullish" else at_resistance
    confidence = min(0.95, base_confidence + 0.08 * aligned_trend + 0.08 * at_key_level)
    context = []
    if aligned_trend:
        context.append("trend aligned")
    if at_key_level:
        context.append("key level confluence")
    detail = ", ".join(context) if context else "pattern confirmation"
    risk = entry_price - stop_loss if bias == "bullish" else stop_loss - entry_price
    take_profit = entry_price + 2 * risk if bias == "bullish" else entry_price - 2 * risk
    return StrategySignal(
        name, bias, confidence,
        f"{name}: {detail}; structural stop and 1:2 reward-to-risk target",
        stop_loss, take_profit,
    )