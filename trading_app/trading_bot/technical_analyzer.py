from __future__ import annotations

import logging
import math
from dataclasses import dataclass

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
    p = min(period, len(values))
    if p == 0:
        return 0.0
    k = 2.0 / (p + 1)
    result = sum(values[-p:]) / p
    for v in values[-p:]:
        result = v * k + result * (1 - k)
    return result


def _sma(values: list[float], period: int) -> float:
    if not values:
        return 0.0
    p = min(period, len(values))
    return sum(values[-p:]) / p


def _stddev(values: list[float], mean: float) -> float:
    if len(values) < 2:
        return 0.0
    variance = sum((v - mean) ** 2 for v in values) / len(values)
    return math.sqrt(variance)


def _macd(closes: list[float]) -> tuple[float, float]:
    if len(closes) < 26:
        return 0.0, 0.0
    ema12 = _ema(closes, 12)
    ema26 = _ema(closes, 26)
    macd_line = ema12 - ema26
    signal = _ema([macd_line] * 9, 9) if len(closes) >= 26 else macd_line
    return macd_line, signal


def _bollinger_bands(closes: list[float], period: int = 20) -> tuple[float, float, float]:
    if len(closes) < period:
        p = len(closes)
    else:
        p = period
    middle = _sma(closes, p)
    std = _stddev(closes[-p:], middle)
    upper = middle + 2 * std
    lower = middle - 2 * std
    return upper, middle, lower


def _atr(candles: list[dict], period: int = 14) -> float:
    if len(candles) < 2:
        if candles:
            h = float(candles[-1].get('high', candles[-1].get('close', 0)))
            l = float(candles[-1].get('low', candles[-1].get('close', 0)))
            return max(h - l, 1e-8)
        return 1e-8
    trs = []
    for i in range(1, len(candles)):
        high = float(candles[i].get('high', candles[i].get('close', 0)))
        low = float(candles[i].get('low', candles[i].get('close', 0)))
        prev_close = float(candles[i - 1].get('close', candles[i].get('close', 0)))
        tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        trs.append(tr)
    if not trs:
        return 1e-8
    eff = min(period, len(trs))
    return sum(trs[-eff:]) / eff


def analyze_technical(
    symbol: str,
    candles: list[dict],
    trade_history: list[dict] | None = None,
) -> TechnicalResult:
    """
    Rule-based technical analysis using RSI, SMA crossover, MACD,
    Bollinger Bands, ATR, and price momentum to produce a bias,
    confidence, and human-readable rationale.
    """
    if not candles or len(candles) < 5:
        return TechnicalResult(
            bias='neutral', confidence=0.0,
            rationale=f'Insufficient data for {symbol} ({len(candles)} candles)',
            rsi=50.0, sma_short=0.0, sma_long=0.0, atr=0.0, momentum=0.0,
            macd=0.0, macd_signal=0.0, bb_upper=0.0, bb_middle=0.0, bb_lower=0.0,
        )

    closes = [float(c.get('close', 0)) for c in candles]
    highs = [float(c.get('high', c.get('close', 0))) for c in candles]
    lows = [float(c.get('low', c.get('close', 0))) for c in candles]

    rsi_val = _rsi(closes)
    sma_5 = _sma(closes, 5)
    sma_20 = _sma(closes, 20)
    atr_val = _atr(candles)
    price = closes[-1]
    macd_line, macd_signal = _macd(closes)
    bb_upper, bb_middle, bb_lower = _bollinger_bands(closes)

    momentum = 0.0
    if len(closes) >= 10:
        momentum = (closes[-1] - closes[-10]) / closes[-10] * 100

    scores: list[tuple[str, float]] = []
    reasons: list[str] = []

    # RSI signal (weight: 20%)
    if rsi_val < 30:
        scores.append(('bullish', 0.20))
        reasons.append(f'RSI({rsi_val:.0f}) oversold')
    elif rsi_val > 70:
        scores.append(('bearish', 0.20))
        reasons.append(f'RSI({rsi_val:.0f}) overbought')
    elif rsi_val < 40:
        scores.append(('bullish', 0.10))
        reasons.append(f'RSI({rsi_val:.0f}) approaching oversold')
    elif rsi_val > 60:
        scores.append(('bearish', 0.10))
        reasons.append(f'RSI({rsi_val:.0f}) approaching overbought')
    else:
        scores.append(('neutral', 0.05))
        reasons.append(f'RSI({rsi_val:.0f}) neutral')

    # SMA crossover signal (weight: 20%)
    if sma_5 > sma_20:
        spread_pct = (sma_5 - sma_20) / sma_20 * 100 if sma_20 else 0
        strength = min(0.20, 0.10 + spread_pct * 5)
        scores.append(('bullish', strength))
        reasons.append(f'SMA(5) above SMA(20) by {spread_pct:.3f}%')
    elif sma_5 < sma_20:
        spread_pct = (sma_20 - sma_5) / sma_20 * 100 if sma_20 else 0
        strength = min(0.20, 0.10 + spread_pct * 5)
        scores.append(('bearish', strength))
        reasons.append(f'SMA(5) below SMA(20) by {spread_pct:.3f}%')
    else:
        scores.append(('neutral', 0.03))
        reasons.append('SMA(5) and SMA(20) flat')

    # Price momentum signal (weight: 20%)
    if momentum > 0.3:
        strength = min(0.20, 0.10 + abs(momentum) * 0.1)
        scores.append(('bullish', strength))
        reasons.append(f'Momentum +{momentum:.2f}%')
    elif momentum < -0.3:
        strength = min(0.20, 0.10 + abs(momentum) * 0.1)
        scores.append(('bearish', strength))
        reasons.append(f'Momentum {momentum:.2f}%')
    else:
        scores.append(('neutral', 0.03))
        reasons.append(f'Momentum flat ({momentum:+.2f}%)')

    # MACD signal (weight: 20%)
    if len(closes) >= 26:
        if macd_line > macd_signal:
            spread = (macd_line - macd_signal) / abs(price) * 100 if price else 0
            strength = min(0.20, 0.10 + abs(spread) * 10)
            scores.append(('bullish', strength))
            reasons.append(f'MACD above signal ({spread:.4f}%)')
        elif macd_line < macd_signal:
            spread = (macd_signal - macd_line) / abs(price) * 100 if price else 0
            strength = min(0.20, 0.10 + abs(spread) * 10)
            scores.append(('bearish', strength))
            reasons.append(f'MACD below signal ({spread:.4f}%)')
        else:
            scores.append(('neutral', 0.03))
            reasons.append('MACD flat')
    else:
        scores.append(('neutral', 0.03))
        reasons.append('MACD insufficient data')

    # Bollinger Bands signal (weight: 20%)
    if len(closes) >= 20:
        if price <= bb_lower:
            scores.append(('bullish', 0.20))
            reasons.append(f'Price at BB lower band (oversold)')
        elif price >= bb_upper:
            scores.append(('bearish', 0.20))
            reasons.append(f'Price at BB upper band (overbought)')
        elif price < bb_middle:
            bw = (bb_upper - bb_lower) / bb_middle * 100 if bb_middle else 0
            strength = min(0.10, 0.05 + (bb_middle - price) / (bb_middle - bb_lower + 1e-8) * 0.05)
            scores.append(('bullish', strength))
            reasons.append(f'Price below BB midline (bandwidth {bw:.2f}%)')
        elif price > bb_middle:
            bw = (bb_upper - bb_lower) / bb_middle * 100 if bb_middle else 0
            strength = min(0.10, 0.05 + (price - bb_middle) / (bb_upper - bb_middle + 1e-8) * 0.05)
            scores.append(('bearish', strength))
            reasons.append(f'Price above BB midline (bandwidth {bw:.2f}%)')
        else:
            scores.append(('neutral', 0.03))
            reasons.append('Price at BB midline')
    else:
        scores.append(('neutral', 0.03))
        reasons.append('BB insufficient data')

    # Count votes
    bull_weight = sum(w for b, w in scores if b == 'bullish')
    bear_weight = sum(w for b, w in scores if b == 'bearish')

    # Trade history feedback: adjust confidence based on recent outcomes
    history_adj = 1.0
    if trade_history:
        recent = trade_history[-10:]
        wins = sum(1 for t in recent if t.get('pnl', 0) > 0)
        win_rate = wins / len(recent) if recent else 0.5
        if win_rate >= 0.6:
            history_adj = 1.1
            reasons.append(f'Recent win rate {win_rate:.0%} (boosting)')
        elif win_rate <= 0.3:
            history_adj = 0.8
            reasons.append(f'Recent win rate {win_rate:.0%} (reducing)')

    # Determine bias and confidence
    if bull_weight > bear_weight and bull_weight > 0.15:
        bias = 'bullish'
        raw_conf = min(1.0, bull_weight / 0.80)
    elif bear_weight > bull_weight and bear_weight > 0.15:
        bias = 'bearish'
        raw_conf = min(1.0, bear_weight / 0.80)
    else:
        bias = 'neutral'
        raw_conf = max(0.05, 1.0 - abs(bull_weight - bear_weight) * 2)

    confidence = max(0.05, min(1.0, raw_conf * history_adj))

    # ATR context for rationale
    atr_pct = (atr_val / price * 100) if price else 0
    vol_label = 'high' if atr_pct > 0.5 else 'moderate' if atr_pct > 0.2 else 'low'

    rationale = f"{bias.upper()} | {' + '.join(reasons)}. Volatility: {vol_label} (ATR {atr_pct:.3f}%)."

    if trade_history and len(trade_history) >= 3:
        recent = trade_history[-5:]
        wins = sum(1 for t in recent if t.get('pnl', 0) > 0)
        rationale += f" Last {len(recent)} trades: {wins}W-{len(recent)-wins}L."

    return TechnicalResult(
        bias=bias,
        confidence=round(confidence, 3),
        rationale=rationale,
        rsi=round(rsi_val, 2),
        sma_short=round(sma_5, 6),
        sma_long=round(sma_20, 6),
        atr=round(atr_val, 6),
        momentum=round(momentum, 4),
        macd=round(macd_line, 6),
        macd_signal=round(macd_signal, 6),
        bb_upper=round(bb_upper, 6),
        bb_middle=round(bb_middle, 6),
        bb_lower=round(bb_lower, 6),
    )
