"""
walk_forward.py — Walk-Forward Validation
Metode validasi paling realistis untuk time series:
- Expanding window (default): training window bertambah di setiap fold
- Rolling window: training window bergeser (ukuran tetap)
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd
from scipy.stats import spearmanr

log = logging.getLogger(__name__)


@dataclass
class WalkForwardConfig:
    n_splits: int = 5
    gap_periods: int = 4        # candle gap antara train & val
    rolling: bool = False       # False = expanding window
    min_train_size: int = 1000  # minimum candle untuk training
    val_size_pct: float = 0.0   # 0 = auto-calculate dari n_splits


@dataclass
class FoldResult:
    fold: int
    train_size: int
    val_size: int
    ic: float           # Spearman correlation pred vs actual
    mse: float
    dir_accuracy: float  # % predicted direction correct
    sharpe: float        # signal Sharpe
    predictions: np.ndarray = field(default=None, repr=False)
    actuals: np.ndarray = field(default=None, repr=False)


@dataclass
class WalkForwardSummary:
    n_folds: int
    ic_mean: float
    ic_std: float
    icir: float          # IC mean / IC std (stabilitas)
    dir_accuracy_mean: float
    sharpe_mean: float
    mse_mean: float
    fold_results: list[FoldResult]


def walk_forward_cv(
    df: pd.DataFrame,
    feature_cols: list[str],
    target_col: str,
    build_estimator_fn: Any,
    config: WalkForwardConfig | None = None,
) -> list[FoldResult]:
    """
    Jalankan walk-forward cross-validation.

    Args:
        df: DataFrame dengan fitur dan target
        feature_cols: list nama kolom fitur
        target_col: nama kolom target
        build_estimator_fn: callable() -> estimator (untrained)
        config: konfigurasi walk-forward
    """
    cfg = config or WalkForwardConfig()
    n = len(df)

    if n < cfg.min_train_size + cfg.gap_periods + 100:
        raise ValueError(
            f"Data terlalu sedikit ({n} baris) untuk {cfg.n_splits} fold "
            f"dengan min_train={cfg.min_train_size}"
        )

    X = df[feature_cols].values
    y = df[target_col].values

    # Hitung split boundaries
    val_size = max(100, (n - cfg.min_train_size) // (cfg.n_splits + 1))
    results: list[FoldResult] = []

    for fold in range(cfg.n_splits):
        if cfg.rolling:
            # Rolling: window bergeser
            train_start = fold * val_size
            train_end = cfg.min_train_size + fold * val_size
        else:
            # Expanding: window bertambah
            train_start = 0
            train_end = cfg.min_train_size + fold * val_size

        val_start = train_end + cfg.gap_periods
        val_end = val_start + val_size

        if val_end > n:
            log.warning(f"Fold {fold+1}: val_end={val_end} > n={n}, skip.")
            break

        X_train = X[train_start:train_end]
        y_train = y[train_start:train_end]
        X_val = X[val_start:val_end]
        y_val = y[val_start:val_end]

        log.info(
            f"Fold {fold+1}/{cfg.n_splits}: "
            f"train=[{train_start}:{train_end}] ({len(X_train)}), "
            f"gap={cfg.gap_periods}, "
            f"val=[{val_start}:{val_end}] ({len(X_val)})"
        )

        # Train
        estimator = build_estimator_fn()
        try:
            estimator.fit(X_train, y_train)
        except Exception as e:
            log.error(f"Fold {fold+1} training gagal: {e}")
            continue

        # Predict
        y_pred = estimator.predict(X_val)

        # Metrics
        ic, _ = spearmanr(y_val, y_pred)
        mse = float(np.mean((y_val - y_pred) ** 2))
        dir_acc = float(np.mean(np.sign(y_val) == np.sign(y_pred))) * 100

        # Signal Sharpe
        signal_returns = y_val * np.sign(y_pred)
        sharpe = (
            float(np.mean(signal_returns) / np.std(signal_returns) * np.sqrt(252))
            if np.std(signal_returns) > 0
            else 0.0
        )

        fold_result = FoldResult(
            fold=fold + 1,
            train_size=len(X_train),
            val_size=len(X_val),
            ic=float(ic) if not np.isnan(ic) else 0.0,
            mse=mse,
            dir_accuracy=dir_acc,
            sharpe=sharpe,
            predictions=y_pred,
            actuals=y_val,
        )
        results.append(fold_result)

        log.info(
            f"  → IC={fold_result.ic:.4f}, DirAcc={dir_acc:.1f}%, Sharpe={sharpe:.2f}"
        )

    return results


def aggregate_results(results: list[FoldResult]) -> WalkForwardSummary:
    """Agregasi hasil dari semua fold menjadi summary."""
    if not results:
        return WalkForwardSummary(
            n_folds=0, ic_mean=0, ic_std=0, icir=0,
            dir_accuracy_mean=0, sharpe_mean=0, mse_mean=0,
            fold_results=[],
        )

    ics = [r.ic for r in results]
    ic_mean = float(np.mean(ics))
    ic_std = float(np.std(ics))
    icir = ic_mean / ic_std if ic_std > 0 else 0.0

    summary = WalkForwardSummary(
        n_folds=len(results),
        ic_mean=ic_mean,
        ic_std=ic_std,
        icir=icir,
        dir_accuracy_mean=float(np.mean([r.dir_accuracy for r in results])),
        sharpe_mean=float(np.mean([r.sharpe for r in results])),
        mse_mean=float(np.mean([r.mse for r in results])),
        fold_results=results,
    )

    log.info(
        f"Walk-Forward Summary: IC={ic_mean:.4f}±{ic_std:.4f}, "
        f"ICIR={icir:.2f}, DirAcc={summary.dir_accuracy_mean:.1f}%, "
        f"Sharpe={summary.sharpe_mean:.2f}"
    )
    return summary
