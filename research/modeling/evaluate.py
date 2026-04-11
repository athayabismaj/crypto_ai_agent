"""
evaluate.py — Evaluasi Model ML
Menghitung semua metrik dan menerapkan threshold minimum.
Model TIDAK BOLEH di-deploy jika gagal di sini.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np
from scipy.stats import spearmanr

log = logging.getLogger(__name__)


class ModelNotReadyError(Exception):
    """Raised ketika model gagal melewati threshold minimum."""

    pass


@dataclass
class EvaluationReport:
    ic_mean: float
    icir: float
    dir_accuracy: float
    sharpe_signal: float
    max_dd_signal: float
    passed: bool
    failures: list[str]


# Threshold minimum sebelum model boleh lanjut
THRESHOLDS = {
    "ic_mean": 0.05,
    "icir": 0.5,
    "dir_accuracy": 52.0,
    "sharpe_signal": 0.8,
    "max_dd_signal": 30.0,  # max % drawdown (harus < ini)
}


def _calc_max_drawdown(equity_curve: np.ndarray) -> float:
    """Hitung max drawdown dari equity curve dalam persen."""
    if len(equity_curve) == 0:
        return 0.0
    peak = np.maximum.accumulate(equity_curve)
    dd = (peak - equity_curve) / peak * 100
    return float(np.max(dd)) if len(dd) > 0 else 0.0


def evaluate_model(
    y_true: np.ndarray,
    y_pred: np.ndarray,
    ic_values: list[float] | None = None,
) -> EvaluationReport:
    """
    Evaluasi komprehensif model.
    Return EvaluationReport.
    Raise ModelNotReadyError jika threshold minimum gagal.
    """
    failures: list[str] = []

    # 1. IC (Information Coefficient) — Spearman correlation
    ic, _ = spearmanr(y_true, y_pred)
    ic = float(ic) if not np.isnan(ic) else 0.0

    if ic < THRESHOLDS["ic_mean"]:
        failures.append(f"IC={ic:.4f} < {THRESHOLDS['ic_mean']} (model tidak prediktif)")

    # 2. ICIR (IC Information Ratio) — stabilitas IC antar fold
    if ic_values and len(ic_values) > 1:
        ic_mean_wf = float(np.mean(ic_values))
        ic_std_wf = float(np.std(ic_values))
        icir = ic_mean_wf / ic_std_wf if ic_std_wf > 0 else 0.0
    else:
        icir = 0.0

    if icir < THRESHOLDS["icir"] and ic_values:
        failures.append(f"ICIR={icir:.2f} < {THRESHOLDS['icir']} (IC tidak stabil)")

    # 3. Directional Accuracy
    dir_acc = float(np.mean(np.sign(y_true) == np.sign(y_pred))) * 100
    if dir_acc < THRESHOLDS["dir_accuracy"]:
        failures.append(f"DirAcc={dir_acc:.1f}% < {THRESHOLDS['dir_accuracy']}%")

    # 4. Signal Sharpe
    signal_returns = y_true * np.sign(y_pred)
    sharpe = 0.0
    if np.std(signal_returns) > 0:
        sharpe = float(np.mean(signal_returns) / np.std(signal_returns) * np.sqrt(252))

    if sharpe < THRESHOLDS["sharpe_signal"]:
        failures.append(f"Sharpe={sharpe:.2f} < {THRESHOLDS['sharpe_signal']}")

    # 5. Max Drawdown (signal equity)
    cum_returns = np.cumsum(signal_returns)
    equity = 10000 + cum_returns * 10000  # hypothetical $10k
    max_dd = _calc_max_drawdown(equity)

    if max_dd > THRESHOLDS["max_dd_signal"]:
        failures.append(f"MaxDD={max_dd:.1f}% > {THRESHOLDS['max_dd_signal']}%")

    passed = len(failures) == 0

    report = EvaluationReport(
        ic_mean=ic,
        icir=icir,
        dir_accuracy=dir_acc,
        sharpe_signal=sharpe,
        max_dd_signal=max_dd,
        passed=passed,
        failures=failures,
    )

    if passed:
        log.info(
            f"✅ Model LULUS evaluasi: IC={ic:.4f}, ICIR={icir:.2f}, "
            f"DirAcc={dir_acc:.1f}%, Sharpe={sharpe:.2f}, MaxDD={max_dd:.1f}%"
        )
    else:
        msg = "❌ Model GAGAL evaluasi:\n" + "\n".join(f"  - {f}" for f in failures)
        log.error(msg)
        raise ModelNotReadyError(msg)

    return report
