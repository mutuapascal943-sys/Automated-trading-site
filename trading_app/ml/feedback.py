"""
Feed resolved live predictions back into model training.

Resolved PredictionRecords (correct/incorrect directional calls against
realized price) become extra labeled rows appended to the training data,
so the model can improve over time based on real trading outcomes.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def load_feedback_rows(
    symbol: str,
    feature_columns: list[str],
    limit: int = 50000,
    granularity: int | None = None,
) -> list[dict]:
    """
    Return resolved predictions as labeled rows aligned to feature_columns.

    Each returned row has this structure:

        {
            "features": [...],
            "target": 0 | 1
        }

    Records are skipped when:

    - They belong to a different symbol.
    - They are not resolved.
    - They do not have a realized target.
    - They have empty features.
    - They do not contain every feature required by the current model.
    - A feature cannot be converted to float.

    When ``granularity`` is provided, only predictions made on that
    candle size are included. This prevents feedback from an older
    model/timeframe from being mixed into a retraining job for another
    timeframe.
    """

    # Imports are intentionally inside the function to avoid unnecessary
    # model-loading/import-order problems during Django startup.
    from trading_app.ml.inference import _normalize_symbol
    from trading_app.models import PredictionRecord

    normalized_symbol = _normalize_symbol(symbol)

    # IMPORTANT:
    # Apply every database filter BEFORE taking the QuerySet slice.
    #
    # Django does not allow:
    #
    #     queryset[:100].filter(...)
    #
    # because slicing creates a limited QuerySet that cannot then be
    # filtered.
    queryset = (
        PredictionRecord.objects
        .filter(
            resolved=True,
            realized_target__isnull=False,
        )
        .exclude(features={})
    )

    # Apply granularity BEFORE slicing.
    if granularity is not None:
        queryset = queryset.filter(
            granularity=granularity
        )

    # Only slice after ALL database filters have been applied.
    #
    # We retrieve more than ``limit`` records because some records may
    # later be skipped due to:
    #
    # - symbol mismatch
    # - missing features
    # - invalid feature values
    queryset = (
        queryset
        .order_by("-predicted_at")[: limit * 4]
    )

    rows: list[dict] = []

    for record in queryset:

        # Normalize the stored symbol and compare it with the requested
        # symbol. This handles variations such as XAU/USD vs XAUUSD
        # when _normalize_symbol() supports them.
        if _normalize_symbol(record.symbol) != normalized_symbol:
            continue

        features = record.features or {}

        # The current model must receive exactly the feature columns
        # it expects. Skip incomplete historical records.
        if not all(
            column in features
            for column in feature_columns
        ):
            continue

        try:
            feature_values = [
                float(features[column])
                for column in feature_columns
            ]

        except (TypeError, ValueError):
            # A malformed feature should not stop the entire
            # feedback-retraining process.
            logger.warning(
                "Skipping PredictionRecord %s because one or more "
                "feature values are not numeric.",
                record.pk,
            )
            continue

        try:
            target = int(record.realized_target)
        except (TypeError, ValueError):
            logger.warning(
                "Skipping PredictionRecord %s because realized_target "
                "is invalid: %r",
                record.pk,
                record.realized_target,
            )
            continue

        # Keep the target restricted to the expected binary labels.
        if target not in (0, 1):
            logger.warning(
                "Skipping PredictionRecord %s because realized_target "
                "must be 0 or 1, got %r.",
                record.pk,
                record.realized_target,
            )
            continue

        rows.append(
            {
                "features": feature_values,
                "target": target,
            }
        )

        # Stop once the requested number of valid rows has been collected.
        if len(rows) >= limit:
            break

    logger.info(
        "Loaded %d feedback rows for %s "
        "(granularity=%s, requested_limit=%d)",
        len(rows),
        symbol,
        granularity,
        limit,
    )

    return rows