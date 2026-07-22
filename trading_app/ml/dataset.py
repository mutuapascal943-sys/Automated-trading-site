"""
Assemble ML dataset from labeled candles with chronological splitting.

- Chronological split: train / val / test in time order (no shuffling)
- Walk-forward: rolling windows for robust out-of-sample evaluation
- Feature matrix extraction with missing-value handling
"""

from __future__ import annotations

import math
from typing import Any

from .features import FEATURE_COLUMNS


def assemble_dataset(
    candles: list[dict],
    feature_columns: list[str] | None = None,
    train_ratio: float = 0.7,
    val_ratio: float = 0.15,
) -> dict[str, Any]:
    """
    Split labeled candles into train/val/test sets chronologically.

    Returns dict with:
    - X_train, y_train, times_train
    - X_val, y_val, times_val
    - X_test, y_test, times_test
    - feature_columns: list of feature names used
    - metadata: split sizes, date ranges
    """
    cols = feature_columns or FEATURE_COLUMNS

    # Filter to labeled rows only (target >= 0 means we have future data)
    labeled = [c for c in candles if c.get("target", -1) >= 0]

    if not labeled:
        raise ValueError("No labeled data available")

    n = len(labeled)
    train_end = int(n * train_ratio)
    val_end = int(n * (train_ratio + val_ratio))

    train_data = labeled[:train_end]
    val_data = labeled[train_end:val_end]
    test_data = labeled[val_end:]

    X_train, y_train, t_train = _extract(train_data, cols)
    X_val, y_val, t_val = _extract(val_data, cols)
    X_test, y_test, t_test = _extract(test_data, cols)

    return {
        "X_train": X_train, "y_train": y_train, "times_train": t_train,
        "X_val": X_val, "y_val": y_val, "times_val": t_val,
        "X_test": X_test, "y_test": y_test, "times_test": t_test,
        "feature_columns": cols,
        "metadata": {
            "total_labeled": n,
            "train_size": len(X_train),
            "val_size": len(X_val),
            "test_size": len(X_test),
            "train_start": t_train[0] if t_train else 0,
            "train_end": t_train[-1] if t_train else 0,
            "val_start": t_val[0] if t_val else 0,
            "val_end": t_val[-1] if t_val else 0,
            "test_start": t_test[0] if t_test else 0,
            "test_end": t_test[-1] if t_test else 0,
        },
    }


def walk_forward_split(
    candles: list[dict],
    feature_columns: list[str] | None = None,
    n_windows: int = 5,
    train_pct: float = 0.6,
    test_pct: float = 0.2,
    min_train_size: int = 5000,
) -> list[dict[str, Any]]:
    """
    Generate walk-forward validation splits.

    Each window uses a training block followed by a test block,
    sliding forward in time. The training set always precedes the test set.

    Returns list of dataset dicts (one per window), each with
    X_train, y_train, X_test, y_test, and window metadata.
    """
    cols = feature_columns or FEATURE_COLUMNS
    labeled = [c for c in candles if c.get("target", -1) >= 0]

    if len(labeled) < min_train_size:
        raise ValueError(
            f"Need at least {min_train_size} labeled candles for walk-forward, "
            f"got {len(labeled)}"
        )

    n = len(labeled)
    window_size = n // n_windows
    train_size = int(window_size * (train_pct / (train_pct + test_pct)))
    test_size = window_size - train_size

    windows = []
    for w in range(n_windows):
        start = w * window_size
        train_end = start + train_size
        test_end = min(train_end + test_size, n)

        if train_end >= n or test_end <= train_end:
            break

        train_data = labeled[start:train_end]
        test_data = labeled[train_end:test_end]

        X_train, y_train, t_train = _extract(train_data, cols)
        X_test, y_test, t_test = _extract(test_data, cols)

        if len(X_train) < 100 or len(X_test) < 50:
            continue

        windows.append({
            "window": w + 1,
            "X_train": X_train, "y_train": y_train, "times_train": t_train,
            "X_test": X_test, "y_test": y_test, "times_test": t_test,
            "feature_columns": cols,
            "metadata": {
                "window": w + 1,
                "total_windows": n_windows,
                "train_size": len(X_train),
                "test_size": len(X_test),
                "train_start": t_train[0] if t_train else 0,
                "train_end": t_train[-1] if t_train else 0,
                "test_start": t_test[0] if t_test else 0,
                "test_end": t_test[-1] if t_test else 0,
            },
        })

    return windows


def _extract(
    data: list[dict],
    feature_columns: list[str],
) -> tuple[list[list[float]], list[int], list[int]]:
    """Extract feature matrix, labels, and timestamps from candle dicts."""
    X = []
    y = []
    times = []

    for c in data:
        row = []
        valid = True
        for col in feature_columns:
            val = c.get(col, 0.0)
            if val is None or (isinstance(val, float) and (math.isnan(val) or math.isinf(val))):
                row.append(0.0)
            else:
                row.append(float(val))

        X.append(row)
        y.append(int(c["target"]))
        times.append(c.get("time", 0))

    return X, y, times
