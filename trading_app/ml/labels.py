"""
Generate target labels for supervised learning.

Labels are generated forward-looking from the close price at time T.
Each label at time T only uses future price data that would be known
at T + horizon (no lookahead at training time).

Target: binary classification
  1 = price moves UP by at least `threshold_pct` within `horizon` bars
  0 = otherwise (price stays flat or moves down)

Also supports regression target: actual return over horizon.
"""

from __future__ import annotations

import math
from typing import Literal


def generate_labels(
    candles: list[dict],
    horizon: int = 4,
    threshold_pct: float = 0.1,
    target_type: Literal["binary", "regression"] = "binary",
) -> list[dict]:
    """
    Add target labels to feature candles.

    For each candle at index i:
    - Look at closes[i+1] through[i+horizon]
    - If max price in that window exceeds close[i] by >= threshold_pct: label=1
    - If min price drops below close[i] by >= threshold_pct: label=-1 (for 3-class)
    - Otherwise: label=0

    For binary classification, only up/down:
    - 1 if max future close >= close[i] * (1 + threshold/100)
    - 0 otherwise

    Returns candles with 'target' and 'target_return' keys added.
    """
    n = len(candles)
    result = []

    for i in range(n):
        c = candles[i].copy()
        entry_price = c["close"]

        if i + horizon >= n:
            # Last bars — no future data available, mark as unknown
            c["target"] = -1  # unknown
            c["target_return"] = 0.0
            c["target_max_up"] = 0.0
            c["target_max_down"] = 0.0
            c["target_horizon"] = horizon
            c["target_threshold"] = threshold_pct
            result.append(c)
            continue

        # Track max upward and downward move within horizon
        future_closes = [candles[j]["close"] for j in range(i + 1, min(i + 1 + horizon, n))]

        max_price = max(future_closes)
        min_price = min(future_closes)

        max_up_pct = (max_price - entry_price) / max(entry_price, 1e-8) * 100
        max_down_pct = (entry_price - min_price) / max(entry_price, 1e-8) * 100

        # Actual return over horizon (for regression)
        horizon_return = (future_closes[-1] - entry_price) / max(entry_price, 1e-8) * 100

        if target_type == "binary":
            c["target"] = 1 if max_up_pct >= threshold_pct else 0
        else:
            c["target"] = 1 if max_up_pct >= threshold_pct else (-1 if max_down_pct >= threshold_pct else 0)

        c["target_return"] = round(horizon_return, 6)
        c["target_max_up"] = round(max_up_pct, 6)
        c["target_max_down"] = round(max_down_pct, 6)
        c["target_horizon"] = horizon
        c["target_threshold"] = threshold_pct

        result.append(c)

    return result


def label_distribution(candles: list[dict]) -> dict:
    """Return class distribution statistics for labeled data."""
    labeled = [c for c in candles if c.get("target", -1) >= 0]
    if not labeled:
        return {"total": 0}

    targets = [c["target"] for c in labeled]
    n = len(targets)
    pos = sum(1 for t in targets if t == 1)
    neg = sum(1 for t in targets if t == 0)
    returns = [c.get("target_return", 0) for c in labeled]

    return {
        "total": n,
        "positive": pos,
        "negative": neg,
        "pos_ratio": round(pos / n, 4) if n > 0 else 0,
        "avg_return": round(sum(returns) / n, 6) if n > 0 else 0,
        "max_return": round(max(returns), 6) if returns else 0,
        "min_return": round(min(returns), 6) if returns else 0,
    }
