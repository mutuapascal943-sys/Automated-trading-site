from __future__ import annotations

import time
from dataclasses import dataclass
from .base import CandleContext, StrategyResult
from . import momentum, support_resistance, fibonacci, ict, choch

@dataclass(frozen=True)
class CombinedAnalysisResult:
    bias: str # bullish | bearish | neutral
    confidence: float # 0.0 - 1.0
    rationale: str
    components: dict[str, Any]
    processing_time_ms: float

def evaluate_common_combined(context: CandleContext) -> CombinedAnalysisResult:
    """Execute one efficient, common combined strategy that aggregates momentum,

    support/resistance, Fibonacci, ICT, and CHoCH into a single consolidated
    signal decision without multi-pipeline voting bottlenecks.
    """
    started = time.perf_counter()

    # Evaluate component indicators efficiently using shared context
    mom = momentum.evaluate(context)
    sr = support_resistance.evaluate(context)
    fib = fibonacci.evaluate(context)
    ic = ict.evaluate(context)
    ch = choch.evaluate(context)

    components = {
        'momentum': {'bias': mom.bias, 'confidence': mom.confidence},
        'support_resistance': {'bias': sr.bias, 'confidence': sr.confidence},
        'fibonacci': {'bias': fib.bias, 'confidence': fib.confidence},
        'ict': {'bias': ic.bias, 'confidence': ic.confidence},
        'choch': {'bias': ch.bias, 'confidence': ch.confidence},
    }

    # Calculate weighted combined score
    bullish_score = 0.0
    bearish_score = 0.0
    total_weight = 0.0

    weights = {'momentum': 0.25, 'support_resistance': 0.25, 'fibonacci': 0.15, 'ict': 0.20, 'choch': 0.15}
    for name, w in weights.items():
        comp = components[name]
        total_weight += w
        if comp['bias'] == 'bullish':
            bullish_score += w * comp['confidence']
        elif comp['bias'] == 'bearish':
            bearish_score += w * comp['confidence']

    net_bullish = bullish_score / total_weight if total_weight > 0 else 0.0
    net_bearish = bearish_score / total_weight if total_weight > 0 else 0.0

    threshold = 0.35 # Confidence threshold for combined strategy
    if net_bullish > net_bearish and net_bullish >= threshold:
        bias = 'bullish'
        confidence = net_bullish
        rationale = f"Unified strategy bullish consensus (score: {net_bullish:.2f})."
    elif net_bearish > net_bullish and net_bearish >= threshold:
        bias = 'bearish'
        confidence = net_bearish
        rationale = f"Unified strategy bearish consensus (score: {net_bearish:.2f})."
    else:
        bias = 'neutral'
        confidence = max(net_bullish, net_bearish)
        rationale = "Unified strategy neutral or insufficient directional evidence."

    elapsed = round((time.perf_counter() - started) * 1000, 3)
    return CombinedAnalysisResult(
        bias=bias,
        confidence=round(confidence, 3),
        rationale=rationale,
        components=components,
        processing_time_ms=elapsed,
    )
