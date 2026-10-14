from __future__ import annotations

from dataclasses import dataclass
from .base import CandleContext


@dataclass(frozen=True)
class ConfluenceResult:
    score: int
    items_fired: tuple[str, ...]
    passed: bool
    reason: str


def evaluate_confluence(context: CandleContext, min_confluence: int = 3) -> ConfluenceResult:
    items_fired: list[str] = []

    current_close = float(context.current.get('close', 0) or 0)
    sup = getattr(context, 'support', None)
    res = getattr(context, 'resistance', None)
    tol = getattr(context, 'zone_tolerance', 0.001)

    # Item 1: Inside unmitigated S/D zone or order block
    if (sup is not None and abs(current_close - sup) <= tol * 5) or (res is not None and abs(current_close - res) <= tol * 5):
        items_fired.append('unmitigated_sd_zone_or_ob')

    # Item 2: Overlaps S/R zone
    if (sup is not None and res is not None) or 'support_zone' in getattr(context, 'levels', {}) or 'resistance_zone' in getattr(context, 'levels', {}):
        items_fired.append('sr_zone_overlap')

    # Item 3: Inside the 61.8% - 78.6% Fib band
    levels = getattr(context, 'levels', {}) or {}
    fib_618 = levels.get('retracement_0.618')
    fib_786 = levels.get('retracement_0.786')
    if fib_618 is not None and fib_786 is not None:
        low_band = min(fib_618, fib_786)
        high_band = max(fib_618, fib_786)
        if low_band <= current_close <= high_band:
            items_fired.append('fib_entry_band_618_786')

    # Item 4: Liquidity sweep just occurred
    conditions = getattr(context, 'conditions', ()) or ()
    if any('sweep' in str(c).lower() for c in conditions) or any('upthrust' in str(c).lower() or 'spring' in str(c).lower() for c in conditions):
        items_fired.append('liquidity_sweep')

    # Item 5: FVG in or adjacent to the zone
    if any('fvg' in str(c).lower() or 'gap' in str(c).lower() for c in conditions) or 'fvg' in levels:
        items_fired.append('fair_value_gap')

    score = len(items_fired)
    passed = score >= min_confluence
    reason = f'Confluence score {score}/5 ({", ".join(items_fired) if items_fired else "none"})'
    return ConfluenceResult(score=score, items_fired=tuple(items_fired), passed=passed, reason=reason)
