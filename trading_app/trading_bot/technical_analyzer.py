from __future__ import annotations

import logging
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


def _sma(values: list[float], period: int) -> float:
    if not values:
        return 0.0
    p = min(period, len(values))
    return sum(values[-p:]) / p


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
    Rule-based technical analysis replacing the LLM pipeline.
    Uses RSI, SMA crossover, ATR, and price momentum to produce
    a bias, confidence, and human-readable rationale.
    """
    if not candles or len(candles) < 5:
        return TechnicalResult(
            bias='neutral', confidence=0.0,
            rationale=f'Insufficient data for {symbol} ({len(candles)} candles)',
            rsi=50.0, sma_short=0.0, sma_long=0.0, atr=0.0, momentum=0.0,
        )

    closes = [float(c.get('close', 0)) for c in candles]
    highs = [float(c.get('high', c.get('close', 0))) for c in candles]
    lows = [float(c.get('low', c.get('close', 0))) for c in candles]

    rsi_val = _rsi(closes)
    sma_5 = _sma(closes, 5)
    sma_20 = _sma(closes, 20)
    atr_val = _atr(candles)
    price = closes[-1]

    momentum = 0.0
    if len(closes) >= 10:
        momentum = (closes[-1] - closes[-10]) / closes[-10] * 100

    scores = []
    reasons = []

    # RSI signal (weight: 30%)
    if rsi_val < 30:
        scores.append(('bullish', 0.30))
        reasons.append(f'RSI({rsi_val:.0f}) oversold')
    elif rsi_val > 70:
        scores.append(('bearish', 0.30))
        reasons.append(f'RSI({rsi_val:.0f}) overbought')
    elif rsi_val < 40:
        scores.append(('bullish', 0.15))
        reasons.append(f'RSI({rsi_val:.0f}) approaching oversold')
    elif rsi_val > 60:
        scores.append(('bearish', 0.15))
        reasons.append(f'RSI({rsi_val:.0f}) approaching overbought')
    else:
        scores.append(('neutral', 0.10))
        reasons.append(f'RSI({rsi_val:.0f}) neutral')

    # SMA crossover signal (weight: 35%)
    if sma_5 > sma_20:
        spread_pct = (sma_5 - sma_20) / sma_20 * 100 if sma_20 else 0
        strength = min(0.35, 0.15 + spread_pct * 5)
        scores.append(('bullish', strength))
        reasons.append(f'SMA(5) above SMA(20) by {spread_pct:.3f}%')
    elif sma_5 < sma_20:
        spread_pct = (sma_20 - sma_5) / sma_20 * 100 if sma_20 else 0
        strength = min(0.35, 0.15 + spread_pct * 5)
        scores.append(('bearish', strength))
        reasons.append(f'SMA(5) below SMA(20) by {spread_pct:.3f}%')
    else:
        scores.append(('neutral', 0.05))
        reasons.append('SMA(5) and SMA(20) flat')

    # Price momentum signal (weight: 35%)
    if momentum > 0.3:
        strength = min(0.35, 0.10 + abs(momentum) * 0.1)
        scores.append(('bullish', strength))
        reasons.append(f'Momentum +{momentum:.2f}%')
    elif momentum < -0.3:
        strength = min(0.35, 0.10 + abs(momentum) * 0.1)
        scores.append(('bearish', strength))
        reasons.append(f'Momentum {momentum:.2f}%')
    else:
        scores.append(('neutral', 0.05))
        reasons.append(f'Momentum flat ({momentum:+.2f}%)')

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
    )
