from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from .base import StrategyResult


@dataclass(frozen=True)
class ConsensusResult:
    bias: str
    confidence: float
    buy_votes: int
    sell_votes: int
    total_directional_votes: int
    agreement: float
    reason: str
    contributing_strategies: tuple[str, ...]
    neutral_votes: int = 0
    group_count: int = 0
    contradiction: bool = False
    missing_groups: tuple[str, ...] = ()


def combine(
    results: Iterable[StrategyResult],
    *,
    threshold: float,
    minimum_votes: int,
    expected_groups: int | None = None,
    strong_contradiction_confidence: float = 0.75,
) -> ConsensusResult:
    results = list(results)
    missing_groups = tuple(
        result.name for result in results if result.error is not None
    )
    if expected_groups is not None and len(results) != expected_groups:
        return ConsensusResult(
            'neutral', 0.0, 0, 0, 0, 0.0, 'Required strategy evidence is missing', (),
            neutral_votes=max(0, expected_groups - len(results)),
            group_count=len(results),
            missing_groups=missing_groups or ('strategy_group_count',),
        )
    if missing_groups:
        return ConsensusResult(
            'neutral', 0.0, 0, 0, 0, 0.0, 'Required strategy evaluation failed', (),
            neutral_votes=sum(result.bias not in ('bullish', 'bearish') for result in results),
            group_count=len(results),
            missing_groups=missing_groups,
        )
    invalid = [result for result in results if result.bias not in (
        'bullish', 'bearish', 'neutral', 'insufficient_data',
    )]
    if invalid:
        return ConsensusResult(
            'neutral', 0.0, 0, 0, 0, 0.0, 'Invalid strategy evidence', (),
            neutral_votes=sum(result.bias not in ('bullish', 'bearish') for result in results),
            group_count=len(results),
            missing_groups=tuple(result.name for result in invalid),
        )

    votes = [result for result in results if result.bias in ('bullish', 'bearish')]
    buy = [result for result in votes if result.bias == 'bullish']
    sell = [result for result in votes if result.bias == 'bearish']
    total = len(votes)
    neutral_count = len(results) - total
    group_count = expected_groups if expected_groups is not None else len(results)
    if total == 0:
        return ConsensusResult(
            'neutral', 0.0, 0, 0, 0, 0.0, 'No directional evidence', (),
            neutral_votes=neutral_count, group_count=group_count,
        )

    strong_conflicts = [
        result for result in results
        if result.confidence >= strong_contradiction_confidence
    ]
    strong_buy = any(result.bias == 'bullish' for result in strong_conflicts)
    strong_sell = any(result.bias == 'bearish' for result in strong_conflicts)
    strong_neutral_contradiction = any(
        result.bias == 'neutral'
        and result.confidence >= strong_contradiction_confidence
        and any('conflict' in condition for condition in result.conditions)
        for result in results
    )
    if (strong_buy and strong_sell) or strong_neutral_contradiction:
        return ConsensusResult(
            'neutral', 0.0, len(buy), len(sell), total,
            max(len(buy), len(sell)) / max(1, group_count),
            'Strong contradictory strategy evidence', (),
            neutral_votes=neutral_count, group_count=group_count, contradiction=True,
        )

    winner, loser = (buy, sell) if len(buy) >= len(sell) else (sell, buy)
    agreement = len(winner) / max(1, group_count)
    required_agreement = max(threshold, minimum_votes / group_count) if group_count else 1.0
    if (
        len(winner) < minimum_votes
        or agreement < required_agreement
        or len(buy) == len(sell)
    ):
        return ConsensusResult(
            'neutral', 0.0, len(buy), len(sell), total, agreement,
            'No consensus', tuple(result.name for result in votes),
            neutral_votes=neutral_count, group_count=group_count,
        )

    bias = 'bullish' if winner is buy else 'bearish'
    return ConsensusResult(
        bias=bias,
        confidence=sum(result.confidence for result in winner) / len(winner),
        buy_votes=len(buy),
        sell_votes=len(sell),
        total_directional_votes=total,
        agreement=agreement,
        reason=f'{len(winner)}/{group_count} core strategy groups agree',
        contributing_strategies=tuple(result.name for result in winner),
        neutral_votes=neutral_count, group_count=group_count,
    )