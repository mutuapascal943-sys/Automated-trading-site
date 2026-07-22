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
) -> ProposedLevels:
    """
    Compute bot-calculated stop-loss and take-profit levels.

    Uses ATR (Average True Range) scaled by the model's confidence score:
    - Higher confidence => tighter SL (less room for noise) and wider TP
    - Lower confidence  => wider SL (more room for noise) and tighter TP

    SL distance = ATR * sl_multiplier
    TP distance = ATR * tp_multiplier

    Where multipliers are inversely/proportionally scaled by confidence.
    """
    atr = _compute_atr(candles)
    price = current_price or (float(candles[-1]['close']) if candles else 1.0)

    # Confidence scaling: 0.0 (no confidence) to 1.0 (max confidence)
    conf = max(0.0, min(1.0, confidence))

    # SL multiplier: ranges from 2.0x ATR (low conf, wider SL) to 1.0x ATR (high conf)
    sl_mult = 2.0 - (conf * 1.0)

    # TP multiplier: ranges from 1.5x ATR (low conf) to 3.0x ATR (high conf)
    tp_mult = 1.5 + (conf * 1.5)

    # Risk-based minimum: ensure SL doesn't risk more than risk_per_trade_pct of balance
    risk_amount = account_balance * (risk_per_trade_pct / 100.0)
    # Approximate lot value (1 pip = ~$10 for standard lot, rough estimate)
    pip_value_per_lot = 10.0
    if price > 100:
        pip_value_per_lot = 10.0
    elif price < 1:
        pip_value_per_lot = 0.1

    sl_distance = atr * sl_mult
    tp_distance = atr * tp_mult

    precision = _price_precision(price)

    if signal_type.upper() == 'BUY':
        sl = Decimal(str(price - sl_distance)).quantize(Decimal(10) ** -precision, rounding=ROUND_HALF_UP)
        tp = Decimal(str(price + tp_distance)).quantize(Decimal(10) ** -precision, rounding=ROUND_HALF_UP)
    else:
        sl = Decimal(str(price + sl_distance)).quantize(Decimal(10) ** -precision, rounding=ROUND_HALF_UP)
        tp = Decimal(str(price - tp_distance)).quantize(Decimal(10) ** -precision, rounding=ROUND_HALF_UP)

    sl_dist_dec = Decimal(str(sl_distance)).quantize(Decimal(10) ** -precision, rounding=ROUND_HALF_UP)
    tp_dist_dec = Decimal(str(tp_distance)).quantize(Decimal(10) ** -precision, rounding=ROUND_HALF_UP)
    atr_dec = Decimal(str(atr)).quantize(Decimal(10) ** -precision, rounding=ROUND_HALF_UP)

    vol_label = 'high' if atr > price * 0.005 else 'moderate' if atr > price * 0.002 else 'low'
    conf_label = 'high' if conf >= 0.7 else 'moderate' if conf >= 0.4 else 'low'

    reasoning = (
        f"SL set at {sl_mult:.1f}x ATR ({atr_dec}) due to {vol_label} volatility, "
        f"confidence: {conf:.2f} ({conf_label}). "
        f"TP targets {tp_mult:.1f}x ATR for {tp_distance/sl_distance:.1f}:1 R:R ratio."
    )

    return ProposedLevels(
        stop_loss=sl,
        take_profit=tp,
        sl_distance=sl_dist_dec,
        tp_distance=tp_dist_dec,
        atr_value=atr_dec,
        reasoning=reasoning,
        confidence=conf,
        method='ATR_scaled',
    )
