"""
ML Training Pipeline — end-to-end orchestration.

Pulls data → cleans → computes features → generates labels →
assembles dataset → trains model → exports ONNX.

Usage:
    from trading_app.ml.pipeline import run_pipeline
    result = run_pipeline(symbol="EURUSD", granularity=900, years=2.0)
"""

from __future__ import annotations

import json
import logging
import math
import os
import pickle
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

OUTPUT_DIR = Path(__file__).resolve().parent.parent.parent / "ml_output"


def run_pipeline(
    symbol: str = "EURUSD",
    granularity: int = 900,
    years: float = 2.0,
    horizon: int = 4,
    threshold_pct: float = 0.1,
    n_windows: int = 5,
    use_adapter: Any | None = None,
    output_dir: str | None = None,
    feedback_rows: list[dict] | None = None,
    data_symbol: str | None = None,
) -> dict[str, Any]:
    """
    Run the full ML pipeline end-to-end.

    Steps:
    1. Collect OHLCV data
    2. Clean data
    3. Compute features
    4. Generate labels
    5. Assemble dataset (chronological split + walk-forward)
    6. Train model (Random Forest + optional LightGBM)
    7. Evaluate on test set
    8. Export model (ONNX + metadata)

    Returns dict with results, metrics, and file paths.
    """
    out = Path(output_dir) if output_dir else OUTPUT_DIR
    out.mkdir(parents=True, exist_ok=True)

    results = {"symbol": symbol, "granularity": granularity, "steps": []}

    # ── 1. COLLECT ──
    logger.info("Step 1: Collecting OHLCV data for %s", symbol)
    from .data_collector import collect_candles, collect_from_adapter

    collect_symbol = data_symbol or symbol

    if use_adapter is not None:
        candles_raw = collect_from_adapter(use_adapter, collect_symbol, granularity, years)
    else:
        candles_raw = collect_candles(collect_symbol, granularity, years)

    results["steps"].append({"step": "collect", "raw_count": len(candles_raw)})
    logger.info("Collected %d raw candles", len(candles_raw))

    if len(candles_raw) < 500:
        raise ValueError(f"Insufficient data: only {len(candles_raw)} candles collected")

    # ── 2. CLEAN ──
    logger.info("Step 2: Cleaning data")
    from .data_cleaner import clean_ohlcv, validate_ohlcv

    candles_clean = clean_ohlcv(candles_raw, granularity)
    quality = validate_ohlcv(candles_clean)
    results["steps"].append({"step": "clean", **quality})
    logger.info("Cleaned: %d candles, span: %.0f days", len(candles_clean), quality.get("span_days", 0))

    # ── 3. FEATURES ──
    logger.info("Step 3: Computing features")
    from .features import compute_features

    candles_featured = compute_features(candles_clean)
    results["steps"].append({"step": "features", "count": len(candles_featured), "feature_count": 25})
    logger.info("Computed 25 features for %d candles", len(candles_featured))

    # ── 4. LABELS ──
    logger.info("Step 4: Generating labels (horizon=%d, threshold=%.2f%%)", horizon, threshold_pct)
    from .labels import generate_labels, label_distribution

    candles_labeled = generate_labels(candles_featured, horizon=horizon, threshold_pct=threshold_pct)
    dist = label_distribution(candles_labeled)
    results["steps"].append({"step": "labels", **dist})
    logger.info("Labels: %d total, %d positive, ratio=%.2f", dist["total"], dist["positive"], dist["pos_ratio"])

    # ── 5. DATASET ──
    logger.info("Step 5: Assembling dataset")
    from .dataset import assemble_dataset, walk_forward_split

    dataset = assemble_dataset(candles_labeled)
    results["steps"].append({"step": "dataset", **dataset["metadata"]})

    walk_forward = walk_forward_split(candles_labeled, n_windows=n_windows)
    results["steps"].append({
        "step": "walk_forward",
        "windows": len(walk_forward),
        "window_sizes": [{"train": w["metadata"]["train_size"], "test": w["metadata"]["test_size"]} for w in walk_forward],
    })
    logger.info("Dataset: train=%d, val=%d, test=%d, walk-forward=%d windows",
                dataset["metadata"]["train_size"], dataset["metadata"]["val_size"],
                dataset["metadata"]["test_size"], len(walk_forward))

    # ── 5b. FEEDBACK ──
    # Merge resolved live predictions into the training set so the model
    # learns from real trading outcomes.
    if feedback_rows:
        n_before = dataset["metadata"]["train_size"]
        _merge_feedback_rows(dataset, feedback_rows)
        n_after = dataset["metadata"]["train_size"]
        results["steps"].append({
            "step": "feedback",
            "rows_added": n_after - n_before,
            "feedback_rows": len(feedback_rows),
        })
        logger.info("Feedback: added %d rows (train %d -> %d)",
                    len(feedback_rows), n_before, n_after)

    # ── 6. TRAIN ──
    logger.info("Step 6: Training model")
    model, train_metrics = _train_model(dataset)
    results["steps"].append({"step": "train", **train_metrics})

    # ── 7. EVALUATE ──
    logger.info("Step 7: Evaluating on test set")
    test_metrics = _evaluate(model, dataset["X_test"], dataset["y_test"], dataset["feature_columns"])
    results["steps"].append({"step": "test", **test_metrics})

    # Walk-forward evaluation
    wf_metrics = _walk_forward_evaluate(model, walk_forward, dataset["feature_columns"])
    results["steps"].append({"step": "walk_forward_eval", **wf_metrics})

    # ── 8. EXPORT ──
    logger.info("Step 8: Exporting model")
    export_path = _export_model(model, dataset, train_metrics, test_metrics, wf_metrics, out, symbol, granularity)

    results["model_path"] = str(export_path["model"])
    results["metadata_path"] = str(export_path["metadata"])
    results["metrics"] = {**train_metrics, **test_metrics, **wf_metrics}
    results["output_dir"] = str(out)

    # Save full pipeline results
    results_path = out / "pipeline_results.json"
    with open(results_path, "w") as f:
        json.dump(results, f, indent=2, default=str)

    logger.info("Pipeline complete. Results saved to %s", results_path)
    return results


def _merge_feedback_rows(dataset: dict, feedback_rows: list[dict]) -> None:
    """Append resolved prediction rows to the training set (train split only)."""
    n_cols = len(dataset["feature_columns"])
    valid = [
        r for r in feedback_rows
        if r.get("target") in (0, 1)
        and isinstance(r.get("features"), (list, tuple))
        and len(r["features"]) == n_cols
    ]
    if not valid:
        logger.info("No valid feedback rows to merge")
        return
    dataset["X_train"] = dataset["X_train"] + [list(r["features"]) for r in valid]
    dataset["y_train"] = dataset["y_train"] + [int(r["target"]) for r in valid]
    dataset["metadata"]["train_size"] = len(dataset["X_train"])
    dataset["metadata"]["feedback_rows"] = len(valid)


def _train_model(dataset: dict) -> tuple[Any, dict]:
    """Train a Random Forest classifier on the dataset."""
    from sklearn.ensemble import RandomForestClassifier
    from sklearn.metrics import accuracy_score, precision_score, recall_score, f1_score, roc_auc_score

    X_train = dataset["X_train"]
    y_train = dataset["y_train"]
    X_val = dataset["X_val"]
    y_val = dataset["y_val"]

    clf = RandomForestClassifier(
        n_estimators=200,
        max_depth=10,
        min_samples_split=50,
        min_samples_leaf=20,
        max_features="sqrt",
        class_weight="balanced",
        random_state=42,
        n_jobs=-1,
    )

    clf.fit(X_train, y_train)

    train_pred = clf.predict(X_train)
    val_pred = clf.predict(X_val)
    val_proba = clf.predict_proba(X_val)[:, 1] if hasattr(clf, "predict_proba") else val_pred

    metrics = {
        "train_accuracy": round(accuracy_score(y_train, train_pred), 4),
        "val_accuracy": round(accuracy_score(y_val, val_pred), 4),
        "val_precision": round(precision_score(y_val, val_pred, zero_division=0), 4),
        "val_recall": round(recall_score(y_val, val_pred, zero_division=0), 4),
        "val_f1": round(f1_score(y_val, val_pred, zero_division=0), 4),
    }

    try:
        metrics["val_auc"] = round(roc_auc_score(y_val, val_proba), 4)
    except ValueError:
        metrics["val_auc"] = 0.5

    return clf, metrics


def _evaluate(model: Any, X_test: list, y_test: list, feature_columns: list) -> dict:
    """Evaluate model on test set."""
    from sklearn.metrics import (
        accuracy_score, precision_score, recall_score, f1_score,
        roc_auc_score, confusion_matrix,
    )

    y_pred = model.predict(X_test)
    y_proba = model.predict_proba(X_test)[:, 1] if hasattr(model, "predict_proba") else y_pred

    metrics = {
        "test_accuracy": round(accuracy_score(y_test, y_pred), 4),
        "test_precision": round(precision_score(y_test, y_pred, zero_division=0), 4),
        "test_recall": round(recall_score(y_test, y_pred, zero_division=0), 4),
        "test_f1": round(f1_score(y_test, y_pred, zero_division=0), 4),
    }

    try:
        metrics["test_auc"] = round(roc_auc_score(y_test, y_proba), 4)
    except ValueError:
        metrics["test_auc"] = 0.5

    cm = confusion_matrix(y_test, y_pred).tolist()
    metrics["confusion_matrix"] = cm

    # Feature importance
    importances = model.feature_importances_
    top_features = sorted(
        zip(feature_columns, importances),
        key=lambda x: x[1],
        reverse=True,
    )[:10]
    metrics["top_features"] = [{"name": name, "importance": round(float(imp), 4)} for name, imp in top_features]

    return metrics


def _walk_forward_evaluate(
    model: Any,
    windows: list[dict],
    feature_columns: list,
) -> dict:
    """Evaluate using walk-forward windows."""
    from sklearn.metrics import accuracy_score, f1_score

    window_results = []
    for w in windows:
        y_pred = model.predict(w["X_test"])
        acc = accuracy_score(w["y_test"], y_pred)
        f1 = f1_score(w["y_test"], y_pred, zero_division=0)
        window_results.append({
            "window": w["window"],
            "accuracy": round(acc, 4),
            "f1": round(f1, 4),
            "train_size": w["metadata"]["train_size"],
            "test_size": w["metadata"]["test_size"],
        })

    avg_acc = sum(w["accuracy"] for w in window_results) / len(window_results) if window_results else 0
    avg_f1 = sum(w["f1"] for w in window_results) / len(window_results) if window_results else 0

    return {
        "wf_avg_accuracy": round(avg_acc, 4),
        "wf_avg_f1": round(avg_f1, 4),
        "wf_windows": window_results,
    }


def _export_model(
    model: Any,
    dataset: dict,
    train_metrics: dict,
    test_metrics: dict,
    wf_metrics: dict,
    output_dir: Path,
    symbol: str,
    granularity: int,
) -> dict:
    """Export trained model and metadata."""
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    prefix = f"{symbol}_{granularity}_{timestamp}"

    # Save sklearn model
    model_path = output_dir / f"{prefix}_model.pkl"
    with open(model_path, "wb") as f:
        pickle.dump(model, f)

    # Try ONNX export
    onnx_path = output_dir / f"{prefix}_model.onnx"
    try:
        from skl2onnx import convert_sklearn
        from skl2onnx.common.data_types import FloatTensorType

        initial_type = [("float_input", FloatTensorType([None, len(dataset["feature_columns"])]))]
        onnx_model = convert_sklearn(model, initial_types=initial_type)
        with open(onnx_path, "wb") as f:
            f.write(onnx_model.SerializeToString())
        logger.info("ONNX model exported to %s", onnx_path)
    except ImportError:
        onnx_path = None
        logger.warning("skl2onnx not installed, skipping ONNX export")

    # Save metadata
    metadata = {
        "symbol": symbol,
        "granularity": granularity,
        "timestamp": timestamp,
        "feature_columns": dataset["feature_columns"],
        "model_type": "RandomForestClassifier",
        "model_params": model.get_params() if hasattr(model, "get_params") else {},
        "train_metrics": train_metrics,
        "test_metrics": test_metrics,
        "walk_forward_metrics": wf_metrics,
        "dataset_info": dataset["metadata"],
        "model_path": str(model_path),
        "onnx_path": str(onnx_path) if onnx_path else None,
    }

    meta_path = output_dir / f"{prefix}_metadata.json"
    with open(meta_path, "w") as f:
        json.dump(metadata, f, indent=2, default=str)

    return {"model": model_path, "onnx": onnx_path, "metadata": meta_path}
