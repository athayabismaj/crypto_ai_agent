"""
feature_importance.py — Analisis Kontribusi Fitur
Menghitung importance setiap fitur via gain-based (LightGBM/XGBoost)
atau permutation importance (sklearn). Auto-flag fitur tidak berguna.
"""
from __future__ import annotations

import logging
from typing import Any

import numpy as np
import pandas as pd

log = logging.getLogger(__name__)


def get_gain_importance(model: Any, feature_names: list[str]) -> pd.DataFrame:
    """
    Ambil feature importance dari tree-based model (LightGBM/XGBoost).
    Return DataFrame sorted desc by importance.
    """
    try:
        # LightGBM & XGBoost punya property feature_importances_
        importances = model.feature_importances_
    except AttributeError:
        log.warning("Model tidak punya feature_importances_. Skip gain importance.")
        return pd.DataFrame(columns=["feature", "importance", "importance_pct"])

    total = sum(importances)
    if total == 0:
        total = 1  # avoid div zero

    df = pd.DataFrame({
        "feature": feature_names[:len(importances)],
        "importance": importances,
        "importance_pct": importances / total * 100,
    })
    df = df.sort_values("importance", ascending=False).reset_index(drop=True)
    return df


def get_permutation_importance(
    model: Any,
    X: np.ndarray | pd.DataFrame,
    y: np.ndarray,
    feature_names: list[str],
    n_repeats: int = 5,
) -> pd.DataFrame:
    """
    Permutation importance — metode model-agnostic.
    Lebih lambat tapi lebih reliable untuk deteksi fitur redundan.
    """
    from sklearn.inspection import permutation_importance

    result = permutation_importance(
        model, X, y, n_repeats=n_repeats, random_state=42, n_jobs=-1
    )

    df = pd.DataFrame({
        "feature": feature_names,
        "importance_mean": result.importances_mean,
        "importance_std": result.importances_std,
    })
    df = df.sort_values("importance_mean", ascending=False).reset_index(drop=True)
    return df


def flag_low_importance(
    importance_df: pd.DataFrame,
    threshold_pct: float = 1.0,
) -> list[str]:
    """
    Flag fitur yang kontribusinya < threshold_pct%.
    Fitur ini adalah kandidat untuk dihapus di iterasi berikutnya.
    """
    if "importance_pct" not in importance_df.columns:
        return []

    low = importance_df[importance_df["importance_pct"] < threshold_pct]
    flagged = low["feature"].tolist()

    if flagged:
        log.info(
            f"🔍 {len(flagged)} fitur berkontribusi < {threshold_pct}%: "
            f"{flagged[:10]}{'...' if len(flagged) > 10 else ''}"
        )

    return flagged


def analyze_feature_importance(
    model: Any,
    feature_names: list[str],
    X_val: np.ndarray | pd.DataFrame | None = None,
    y_val: np.ndarray | None = None,
) -> dict[str, Any]:
    """
    Analisis lengkap feature importance.
    Return dict ringkasan.
    """
    result: dict[str, Any] = {}

    # 1. Gain-based
    gain_df = get_gain_importance(model, feature_names)
    result["gain_importance"] = gain_df
    result["top_10_features"] = gain_df.head(10)["feature"].tolist()

    # 2. Flag low importance
    low_feats = flag_low_importance(gain_df)
    result["low_importance_features"] = low_feats

    # 3. Permutation (opsional, jika X_val tersedia)
    if X_val is not None and y_val is not None:
        perm_df = get_permutation_importance(model, X_val, y_val, feature_names)
        result["permutation_importance"] = perm_df

        # Cross-check: fitur yang pentingan di gain tapi tidak di permutation
        gain_top = set(gain_df.head(10)["feature"])
        perm_top = set(perm_df.head(10)["feature"])
        disagreement = gain_top.symmetric_difference(perm_top)
        if disagreement:
            log.info(f"Gain vs Permutation disagreement: {disagreement}")
        result["importance_disagreement"] = list(disagreement)

    log.info(f"Feature importance analysis selesai. Top 5: {result['top_10_features'][:5]}")
    return result
