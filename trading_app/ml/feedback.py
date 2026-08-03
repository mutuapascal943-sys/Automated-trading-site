"""
Feed resolved live predictions back into model training.

Resolved PredictionRecords (correct/incorrect directional calls against
realized price) become extra labeled rows appended to the training data,
so the model improves over time based on real trading outcomes.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def load_feedback_rows(symbol: str, feature_columns: list[str], limit: int = 50000) -> list[dict]:
    """Return resolved predictions as labeled rows aligned to feature_columns.

    Each row is {'features': [...], 'target': 0|1}. Records that do not have
    the exact feature set of the target model are skipped.
    """
    from trading_app.ml.inference import _normalize_symbol
    from trading_app.models import PredictionRecord

    norm = _normalize_symbol(symbol)
    records = (
        PredictionRecord.objects
        .filter(resolved=True, realized_target__isnull=False)
        .exclude(features={})
        .order_by('-predicted_at')[:limit * 4]
    )

    rows = []
    for rec in records:
        if _normalize_symbol(rec.symbol) != norm:
            continue
        feats = rec.features or {}
        if not all(col in feats for col in feature_columns):
            continue
        rows.append({
            'features': [float(feats[col]) for col in feature_columns],
            'target': int(rec.realized_target),
        })
        if len(rows) >= limit:
            break

    if rows:
        logger.info('Loaded %d feedback rows for %s', len(rows), symbol)
    return rows
