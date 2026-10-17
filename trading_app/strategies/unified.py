from __future__ import annotations

import time
from .base import CandleContext, StrategyResult
from . import momentum, support_resistance, fibonacci, ict, choch

def evaluate_unified(context: CandleContext) -> tuple[list[StrategyResult], float]:
    """Evaluate all key analytical strategy components in a single unified pass

    to minimize processing overhead and maximize signal-generation speed.
    """
    started = time.perf_counter()

    # Run lightweight evaluations using shared context indicators
    results = []
    try:
        results.append(momentum.evaluate(context))
    except Exception as e:
        results.append(StrategyResult('Momentum', 'neutral', 0.0, (), (), {}, 0.0, str(e)))

    try:
        results.append(support_resistance.evaluate(context))
    except Exception as e:
        results.append(StrategyResult('Support/Resistance', 'neutral', 0.0, (), (), {}, 0.0, str(e)))

    try:
        results.append(fibonacci.evaluate(context))
    except Exception as e:
        results.append(StrategyResult('Fibonacci', 'neutral', 0.0, (), (), {}, 0.0, str(e)))

    try:
        results.append(ict.evaluate(context))
    except Exception as e:
        results.append(StrategyResult('ICT', 'neutral', 0.0, (), (), {}, 0.0, str(e)))

    try:
        results.append(choch.evaluate(context))
    except Exception as e:
        results.append(StrategyResult('CHoCH', 'neutral', 0.0, (), (), {}, 0.0, str(e)))

    elapsed = round((time.perf_counter() - started) * 1000, 3)
    return results, elapsed
