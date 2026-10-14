from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional


@dataclass(frozen=True)
class ProposedLevels:
    stop_loss: Decimal
    take_profit: Decimal
    sl_distance: Decimal
    tp_distance: Decimal
    atr_value: Decimal
    reasoning: str
    confidence: float
    method: str


def _compute_atr(candles: list[dict], period: int = 14) -> float:
    """Compute Average True Range from a list of candle dicts with high/low keys."""
    if len(candles) < 2:
        if candles:
            c = candles[-1]
            h = float(c.get('high', c.get('close', 0)))
            l = float(c.get('low', c.get('close', 0)))
            return max(h - l, 0.00001)
        return 0.0001

    trs = []
    for i in range(1, len(candles)):
        high = float(candles[i].get('high', candles[i].get('close', 0)))
        low = float(candles[i].get('low', candles[i].get('close', 0)))
        prev_close = float(candles[i - 1].get('close', candles[i].get('close', 0)))
        tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        trs.append(tr)

    if not trs:
        return 0.0001

    effective_period = min(period, len(trs))
    atr = sum(trs[-effective_period:]) / effective_period
    return max(atr, 1e-8)


def _price_precision(price: float) -> int:
    """Determine decimal places based on price magnitude."""
    if price > 100:
        return 2
    if price > 10:
        return 3
    if price > 1:
        return 5
    return 6


def compute_sl_tp(
    candles: list[dict],
    signal_type: str,
    confidence: float,
    current_price: Optional[float] = None,
    risk_per_trade_pct: float = 2.0,
    account_balance: float = 10000.0,
    min_reward_risk: float = 2.0,
    spread_buffer_pips: float = 0.5,
) -> ProposedLevels:
    """
    Compute bot-calculated stop-loss and take-profit levels with min R:R enforcement and spread buffer.
    """
    atr = _compute_atr(candles)
    price = current_price or (float(candles[-1]['close']) if candles else 1.0)
    precision = _price_precision(price)

    conf = max(0.0, min(1.0, confidence))
    sl_mult = 2.0 - (conf * 1.0)
    tp_mult = 1.5 + (conf * 1.5)

    sl_distance = atr * sl_mult
    tp_distance = atr * tp_mult

    current_rr = tp_distance / sl_distance if sl_distance > 0 else 0.0
    if current_rr < min_reward_risk:
        tp_distance = sl_distance * min_reward_risk

    spread_buffer = spread_buffer_pips * (10 ** -precision)
    sl_distance_with_spread = sl_distance + spread_buffer

    if signal_type.upper() == 'BUY':
        sl = Decimal(str(price - sl_distance_with_spread)).quantize(Decimal(10) ** -precision, rounding=ROUND_HALF_UP)
        tp = Decimal(str(price + tp_distance)).quantize(Decimal(10) ** -precision, rounding=ROUND_HALF_UP)
    else:
        sl = Decimal(str(price + sl_distance_with_spread)).quantize(Decimal(10) ** -precision, rounding=ROUND_HALF_UP)
        tp = Decimal(str(price - tp_distance)).quantize(Decimal(10) ** -precision, rounding=ROUND_HALF_UP)

    sl_dist_dec = Decimal(str(sl_distance_with_spread)).quantize(Decimal(10) ** -precision, rounding=ROUND_HALF_UP)
    tp_dist_dec = Decimal(str(tp_distance)).quantize(Decimal(10) ** -precision, rounding=ROUND_HALF_UP)
    atr_dec = Decimal(str(atr)).quantize(Decimal(10) ** -precision, rounding=ROUND_HALF_UP)

    vol_label = 'high' if atr > price * 0.005 else 'moderate' if atr > price * 0.002 else 'low'
    conf_label = 'high' if conf >= 0.7 else 'moderate' if conf >= 0.4 else 'low'

    rr_ratio = float(tp_distance / sl_distance_with_spread) if sl_distance_with_spread > 0 else min_reward_risk
    reasoning = (
        f"SL set at {sl_mult:.1f}x ATR ({atr_dec}) + spread buffer ({spread_buffer:.5f}) due to {vol_label} volatility, "
        f"confidence: {conf:.2f} ({conf_label}). "
        f"TP targets R:R ratio {rr_ratio:.1f}:1."
    )

    return ProposedLevels(
        stop_loss=sl,
        take_profit=tp,
        sl_distance=sl_dist_dec,
        tp_distance=tp_dist_dec,
        atr_value=atr_dec,
        reasoning=reasoning,
        confidence=conf,
        method='ATR_scaled_with_buffer',
    )
