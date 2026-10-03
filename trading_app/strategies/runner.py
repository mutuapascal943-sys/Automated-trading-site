from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable

from .base import CandleContext, StrategyResult


def run_parallel(
    context: CandleContext,
    evaluators: dict[str, Callable[[CandleContext], StrategyResult]],
) -> tuple[list[StrategyResult], dict[str, float]]:
    results: list[StrategyResult] = []
    timings: dict[str, float] = {}

    def run_one(name: str, evaluator: Callable[[CandleContext], StrategyResult]):
        started = time.perf_counter()
        try:
            result = evaluator(context)
            elapsed = round((time.perf_counter() - started) * 1000, 3)
            if result.processing_time_ms != elapsed:
                result = StrategyResult(
                    result.name, result.bias, result.confidence, result.reasons,
                    result.conditions, result.levels, elapsed, result.error,
                )
            return result, elapsed
        except Exception as exc:
            elapsed = round((time.perf_counter() - started) * 1000, 3)
            return StrategyResult(
                name, 'neutral', 0.0, (), (), {}, elapsed,
                error=f'{type(exc).__name__}: {exc}',
            ), elapsed

    with ThreadPoolExecutor(max_workers=max(1, len(evaluators))) as executor:
        futures = {
            executor.submit(run_one, name, evaluator): name
            for name, evaluator in evaluators.items()
        }
        for future in as_completed(futures):
            name = futures[future]
            result, elapsed = future.result()
            results.append(result)
            timings[name] = elapsed
    return results, timings