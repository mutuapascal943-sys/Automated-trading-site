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


def combine(
    results: Iterable[StrategyResult],
    *,
    threshold: float,
    minimum_votes: int,
) -> ConsensusResult:
    results = list(results)
    votes = [result for result in results if result.bias in ('bullish', 'bearish')]
    buy = [result for result in votes if result.bias == 'bullish']
    sell = [result for result in votes if result.bias == 'bearish']
    total = len(votes)
    if total == 0:
        return ConsensusResult('neutral', 0.0, 0, 0, 0, 0.0, 'No consensus', ())

    winner, loser = (buy, sell) if len(buy) >= len(sell) else (sell, buy)
    agreement = len(winner) / total
    if (
        len(winner) < minimum_votes
        or len(votes) != len(results)
        or agreement < threshold
        or len(buy) == len(sell)
        or bool(loser)
        or any(result.bias not in ('bullish', 'bearish', 'neutral', 'insufficient_data') for result in results)
    ):
        return ConsensusResult(
            'neutral', 0.0, len(buy), len(sell), total, agreement,
            'No consensus', tuple(result.name for result in votes),
        )

    bias = 'bullish' if winner is buy else 'bearish'
    return ConsensusResult(
        bias=bias,
        confidence=sum(result.confidence for result in winner) / len(winner),
        buy_votes=len(buy),
        sell_votes=len(sell),
        total_directional_votes=total,
        agreement=agreement,
        reason=f'{len(winner)}/{total} directional strategy votes agree',
        contributing_strategies=tuple(result.name for result in winner),
    )