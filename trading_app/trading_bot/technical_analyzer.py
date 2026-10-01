from __future__ import annotations

import logging
import math
from dataclasses import dataclass

from .candlestick_strategies import detect_price_action_signals
from .smc_strategy import detect_smc_signal

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TechnicalResult:
    bias: str  # bullish | bearish | neutral
    confidence: float  # 0.0 - 1.0
    rationale: str
    rsi: float
    sma_short: float
    sma_long: float
    atr: float
    momentum: float
    macd: float
    macd_signal: float
    bb_upper: float
    bb_middle: float
    bb_lower: float
    stop_loss: float | None = None
    take_profit: float | None = None


def _rsi(closes: list[float], period: int = 14) -> float:
    if len(closes) < period + 1:
        return 50.0
    deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    gains = [d if d > 0 else 0 for d in deltas]
    losses = [-d if d < 0 else 0 for d in deltas]
    avg_gain = sum(gains[-period:]) / period
    avg_loss = sum(losses[-period:]) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def _ema(values: list[float], period: int) -> float:
    if not values:
        return 0.0
    effective_period = min(period, len(values))
    smoothing = 2.0 / (effective_period + 1)
    result = sum(values[-effective_period:]) / effective_period
    for value in values[-effective_period:]:
        result = value * smoothing + result * (1 - smoothing)
    return result


def _sma(values: list[float], period: int) -> float:
    if not values:
        return 0.0
    effective_period = min(period, len(values))
    return sum(values[-effective_period:]) / effective_period


def _stddev(values: list[float], mean: float) -> float:
    if len(values) < 2:
        return 0.0
    variance = sum((value - mean) ** 2 for value in values) / len(values)
    return math.sqrt(variance)


def _macd(closes: list[float]) -> tuple[float, float]:
    if len(closes) < 26:
        return 0.0, 0.0
    macd_line = _ema(closes, 12) - _ema(closes, 26)
    return macd_line, macd_line


def _bollinger_bands(closes: list[float], period: int = 20) -> tuple[float, float, float]:
    effective_period = min(period, len(closes))
    middle = _sma(closes, effective_period)
    deviation = _stddev(closes[-effective_period:], middle)
    return middle + 2 * deviation, middle, middle - 2 * deviation


def _atr(candles: list[dict], period: int = 14) -> float:
    true_ranges = []
    for index in range(1, len(candles)):
        high = float(candles[index].get('high', candles[index].get('close', 0)))
        low = float(candles[index].get('low', candles[index].get('close', 0)))
        previous_close = float(candles[index - 1].get('close', candles[index].get('close', 0)))
        true_ranges.append(max(high - low, abs(high - previous_close), abs(low - previous_close)))
    if not true_ranges:
        return 1e-8
    effective_period = min(period, len(true_ranges))
    return sum(true_ranges[-effective_period:]) / effective_period


def analyze_technical(
    symbol: str,
    candles: list[dict],
    trade_history: list[dict] | None = None,
) -> TechnicalResult:
    """Analyze PDF-based candlestick setups with trend and key-level context."""
    if not candles or len(candles) < 5:
        return TechnicalResult(
            bias='neutral', confidence=0.0,
            rationale=f'Insufficient data for {symbol} ({len(candles)} candles)',
            rsi=50.0, sma_short=0.0, sma_long=0.0, atr=0.0, momentum=0.0,
            macd=0.0, macd_signal=0.0, bb_upper=0.0, bb_middle=0.0, bb_lower=0.0,
        )

    closes = [float(candle.get('close', 0)) for candle in candles]
    rsi_value = _rsi(closes)
    sma_short = _sma(closes, 5)
    sma_long = _sma(closes, 20)
    atr_value = _atr(candles)
    price = closes[-1]
    macd_line, macd_signal = _macd(closes)
    bb_upper, bb_middle, bb_lower = _bollinger_bands(closes)
    momentum = (price - closes[-10]) / closes[-10] * 100 if len(closes) >= 10 and closes[-10] else 0.0

    strategy_signals = detect_price_action_signals(candles)
    smc_signal = detect_smc_signal(candles)
    if smc_signal is not None:
        strategy_signals.append(smc_signal)
    bullish_signals = [signal for signal in strategy_signals if signal.bias == 'bullish']
    bearish_signals = [signal for signal in strategy_signals if signal.bias == 'bearish']
    reasons = [signal.rationale for signal in strategy_signals]

    history_adjustment = 1.0
    if trade_history:
        recent = trade_history[-10:]
        wins = sum(1 for trade in recent if trade.get('pnl', 0) > 0)
        win_rate = wins / len(recent) if recent else 0.5
        if win_rate >= 0.6:
            history_adjustment = 1.1
            reasons.append(f'Recent win rate {win_rate:.0%} (boosting)')
        elif win_rate <= 0.3:
            history_adjustment = 0.8
            reasons.append(f'Recent win rate {win_rate:.0%} (reducing)')

    if bullish_signals and not bearish_signals:
        bias = 'bullish'
        selected_signal = max(bullish_signals, key=lambda signal: signal.confidence)
        raw_confidence = selected_signal.confidence
    elif bearish_signals and not bullish_signals:
        bias = 'bearish'
        selected_signal = max(bearish_signals, key=lambda signal: signal.confidence)
        raw_confidence = selected_signal.confidence
    else:
        bias = 'neutral'
        raw_confidence = 0.0
        selected_signal = None
        if bullish_signals and bearish_signals:
            reasons.append('Conflicting candlestick strategies; no signal')

    confidence = max(0.0, min(1.0, raw_confidence * history_adjustment))
    atr_percent = atr_value / price * 100 if price else 0.0
    volatility = 'high' if atr_percent > 0.5 else 'moderate' if atr_percent > 0.2 else 'low'
    rationale = (
        f"{bias.upper()} | {' + '.join(reasons) if reasons else 'No confirmed candlestick setup'}. "
        f"Volatility: {volatility} (ATR {atr_percent:.3f}%)."
    )

    if trade_history and len(trade_history) >= 3:
        recent = trade_history[-5:]
        wins = sum(1 for trade in recent if trade.get('pnl', 0) > 0)
        rationale += f" Last {len(recent)} trades: {wins}W-{len(recent) - wins}L."

    return TechnicalResult(
        bias=bias,
        confidence=round(confidence, 3),
        rationale=rationale,
        rsi=round(rsi_value, 2),
        sma_short=round(sma_short, 6),
        sma_long=round(sma_long, 6),
        atr=round(atr_value, 6),
        momentum=round(momentum, 4),
        macd=round(macd_line, 6),
        macd_signal=round(macd_signal, 6),
        bb_upper=round(bb_upper, 6),
        bb_middle=round(bb_middle, 6),
        bb_lower=round(bb_lower, 6),
        stop_loss=selected_signal.stop_loss if selected_signal else None,
        take_profit=selected_signal.take_profit if selected_signal else None,
    )
