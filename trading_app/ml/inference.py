from __future__ import annotations

import glob
import json
import logging
import os
import pickle
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

OUTPUT_DIR = Path(__file__).resolve().parent.parent.parent / "ml_output"


@dataclass
class MLSignal:
    bias: str  # bullish | bearish | neutral
    confidence: float  # 0.0 - 1.0
    probability: float
    feature_importance: dict[str, float] | None = None
    model_info: str = ""


def load_latest_model(output_dir: str | None = None) -> tuple[Any, list[str], dict] | None:
    out = Path(output_dir) if output_dir else OUTPUT_DIR
    if not out.exists():
        logger.warning("ML output directory %s does not exist", out)
        return None

    model_files = sorted(out.glob("*_model.pkl"))
    if not model_files:
        logger.warning("No trained model found in %s", out)
        return None

    latest_model = model_files[-1]
    meta_files = sorted(out.glob("*_metadata.json"))
    metadata = {}
    if meta_files:
        meta_path = meta_files[-1]
        try:
            with open(meta_path) as f:
                metadata = json.load(f)
        except Exception as e:
            logger.warning("Failed to load metadata %s: %s", meta_path, e)

    try:
        with open(latest_model, "rb") as f:
            model = pickle.load(f)
        feature_columns = metadata.get("feature_columns", [])
        logger.info("Loaded model %s with %d features", latest_model.name, len(feature_columns))
        return model, feature_columns, metadata
    except Exception as e:
        logger.error("Failed to load model %s: %s", latest_model, e)
        return None


_model_cache: tuple[Any, list[str], dict] | None = None
_model_cache_key: str | None = None


def _get_model(output_dir: str | None = None):
    global _model_cache, _model_cache_key
    out = str(Path(output_dir) if output_dir else OUTPUT_DIR)
    if _model_cache is not None and _model_cache_key == out:
        return _model_cache
    result = load_latest_model(output_dir)
    _model_cache = result
    _model_cache_key = out
    return result


def predict_signal(
    candles: list[dict],
    output_dir: str | None = None,
    granularity: int = 900,
) -> MLSignal | None:
    model_data = _get_model(output_dir)
    if model_data is None:
        return None

    model, feature_columns, metadata = model_data
    if len(candles) < 50:
        return MLSignal(
            bias="neutral", confidence=0.0, probability=0.5,
            model_info="Insufficient data (< 50 candles)",
        )

    from .features import compute_features

    featured = compute_features(candles)
    if not featured:
        return None

    last = featured[-1]
    missing = [col for col in feature_columns if col not in last]
    if missing:
        logger.warning("Missing feature columns: %s", missing)
        return MLSignal(
            bias="neutral", confidence=0.0, probability=0.5,
            model_info=f"Missing features: {missing}",
        )

    X = [[last[col] for col in feature_columns]]
    try:
        proba = model.predict_proba(X)[0]
        pred = model.predict(X)[0]

        probability = float(proba[1]) if model.classes_[1] == 1 else float(proba[0])
        confidence = abs(probability - 0.5) * 2

        if pred == 1 and probability > 0.55:
            bias = "bullish"
        elif pred == 0 and probability < 0.45:
            bias = "bearish"
        else:
            bias = "neutral"

        importances = {}
        if hasattr(model, "feature_importances_"):
            top_idx = model.feature_importances_.argsort()[-5:][::-1]
            for idx in top_idx:
                importances[feature_columns[idx]] = round(float(model.feature_importances_[idx]), 4)

        horizon = metadata.get("dataset_info", {}).get("horizon", 4)
        gran_label = metadata.get("granularity", granularity)

        return MLSignal(
            bias=bias,
            confidence=round(confidence, 3),
            probability=round(probability, 4),
            feature_importance=importances,
            model_info=f"RF horizon={horizon} gran={gran_label}s",
        )
    except Exception as e:
        logger.error("ML prediction failed: %s", e)
        return None
