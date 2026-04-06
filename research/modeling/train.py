"""
train.py — Training Model ML
Mendukung LightGBM (default), XGBoost, dan sklearn estimator.
Output: TrainedModel dataclass yang siap di-serialize ke .pkl.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd
from sklearn.model_selection import train_test_split

log = logging.getLogger(__name__)


@dataclass
class TrainConfig:
    model_type: str = "lightgbm"         # lightgbm | xgboost | random_forest
    target_col: str = "target_return_4h"
    task: str = "regression"             # regression | classification
    test_size: float = 0.2
    n_splits: int = 5                    # untuk walk_forward
    early_stopping: int = 50
    verbose: int = 100
    output_dir: str = "research/models"

    lgbm_params: dict = field(default_factory=lambda: {
        "n_estimators": 1000,
        "learning_rate": 0.05,
        "num_leaves": 31,
        "subsample": 0.8,
        "colsample_bytree": 0.8,
        "reg_alpha": 0.1,
        "reg_lambda": 0.1,
    })


@dataclass
class TrainedModel:
    model: Any                              # estimator object
    feature_names: list[str]                # WAJIB — runtime butuh ini
    target_col: str
    model_type: str
    train_date_range: tuple[str, str]
    metrics: dict                           # val_score, ic, dll
    config: TrainConfig
    version: str = "1.0.0"


def _build_estimator(config: TrainConfig) -> Any:
    """Inisialisasi estimator berdasarkan config."""
    if config.model_type == "lightgbm":
        try:
            import lightgbm as lgb
        except ImportError:
            raise ImportError("Install lightgbm: pip install lightgbm")

        params = {**config.lgbm_params}
        if config.task == "regression":
            return lgb.LGBMRegressor(**params, verbose=-1)
        else:
            return lgb.LGBMClassifier(**params, verbose=-1)

    elif config.model_type == "xgboost":
        try:
            import xgboost as xgb
        except ImportError:
            raise ImportError("Install xgboost: pip install xgboost")

        if config.task == "regression":
            return xgb.XGBRegressor(
                n_estimators=config.lgbm_params.get("n_estimators", 1000),
                learning_rate=config.lgbm_params.get("learning_rate", 0.05),
                verbosity=0,
            )
        else:
            return xgb.XGBClassifier(
                n_estimators=config.lgbm_params.get("n_estimators", 1000),
                learning_rate=config.lgbm_params.get("learning_rate", 0.05),
                verbosity=0,
            )

    elif config.model_type == "random_forest":
        from sklearn.ensemble import RandomForestRegressor, RandomForestClassifier

        if config.task == "regression":
            return RandomForestRegressor(n_estimators=500, n_jobs=-1, random_state=42)
        else:
            return RandomForestClassifier(n_estimators=500, n_jobs=-1, random_state=42)

    else:
        raise ValueError(f"Model type tidak dikenali: {config.model_type}")


def train_model(
    df: pd.DataFrame,
    config: TrainConfig | None = None,
    feature_cols: list[str] | None = None,
) -> TrainedModel:
    """
    Latih model dari DataFrame berfitur lengkap.
    Return TrainedModel yang bisa di-serialize.
    """
    cfg = config or TrainConfig()

    if cfg.target_col not in df.columns:
        raise ValueError(f"Target column '{cfg.target_col}' tidak ada di DataFrame.")

    # Tentukan feature columns
    if feature_cols is None:
        exclude = {"timestamp", cfg.target_col}
        exclude.update(c for c in df.columns if c.startswith("target_"))
        feature_cols = [c for c in df.columns if c not in exclude
                        and df[c].dtype in (np.float64, np.float32, np.int64, float, int)]

    X = df[feature_cols].values
    y = df[cfg.target_col].values

    # Train-test split (temporal: TIDAK shuffle!)
    split_idx = int(len(X) * (1 - cfg.test_size))
    X_train, X_val = X[:split_idx], X[split_idx:]
    y_train, y_val = y[:split_idx], y[split_idx:]

    log.info(f"Training {cfg.model_type}: train={len(X_train)}, val={len(X_val)}, features={len(feature_cols)}")

    estimator = _build_estimator(cfg)

    # Training with early stopping (jika supported)
    fit_params: dict[str, Any] = {}
    if cfg.model_type in ("lightgbm", "xgboost"):
        fit_params["eval_set"] = [(X_val, y_val)]
        if cfg.model_type == "lightgbm":
            fit_params["callbacks"] = [
                __import__("lightgbm").early_stopping(cfg.early_stopping, verbose=False),
                __import__("lightgbm").log_evaluation(cfg.verbose),
            ]

    estimator.fit(X_train, y_train, **fit_params)

    # Score
    from scipy.stats import spearmanr

    y_pred_val = estimator.predict(X_val)
    ic, _ = spearmanr(y_val, y_pred_val)
    mse = float(np.mean((y_val - y_pred_val) ** 2))
    dir_acc = float(np.mean(np.sign(y_val) == np.sign(y_pred_val))) * 100

    metrics = {
        "ic_mean": float(ic),
        "mse": mse,
        "dir_accuracy": dir_acc,
        "train_size": len(X_train),
        "val_size": len(X_val),
    }

    # Date range
    ts_col = "timestamp" if "timestamp" in df.columns else None
    if ts_col:
        date_range = (str(df[ts_col].iloc[0]), str(df[ts_col].iloc[-1]))
    else:
        date_range = ("unknown", "unknown")

    trained = TrainedModel(
        model=estimator,
        feature_names=feature_cols,
        target_col=cfg.target_col,
        model_type=cfg.model_type,
        train_date_range=date_range,
        metrics=metrics,
        config=cfg,
    )

    log.info(f"Training selesai. IC={ic:.4f}, DirAcc={dir_acc:.1f}%, MSE={mse:.6f}")
    return trained


def save_model(trained: TrainedModel, path: str | Path | None = None) -> Path:
    """Serialize TrainedModel ke .pkl via joblib."""
    if path is None:
        os.makedirs(trained.config.output_dir, exist_ok=True)
        filename = f"{trained.model_type}_{trained.target_col}_v{trained.version}.pkl"
        path = Path(trained.config.output_dir) / filename

    path = Path(path)
    joblib.dump(trained, path)
    log.info(f"Model saved: {path}")
    return path


def load_model(path: str | Path) -> TrainedModel:
    """Deserialize TrainedModel dari .pkl."""
    trained = joblib.load(path)
    if not isinstance(trained, TrainedModel):
        raise TypeError(f"File {path} bukan TrainedModel, tapi {type(trained)}")
    return trained


def predict(model: TrainedModel, X: pd.DataFrame) -> np.ndarray:
    """Prediksi menggunakan model terlatih."""
    # Pastikan kolom sesuai kontrak
    missing = set(model.feature_names) - set(X.columns)
    if missing:
        raise ValueError(f"Feature missing dari input: {missing}")

    X_ordered = X[model.feature_names].values
    return model.model.predict(X_ordered)


def get_feature_list(model: TrainedModel) -> list[str]:
    """Return ordered feature list dari model."""
    return model.feature_names
