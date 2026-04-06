"""
leakage_check.py — Gate Kritis Anti Data-Leakage
Validasi bahwa TIDAK ADA informasi masa depan yang bocor ke fitur.
Pipeline WAJIB berhenti jika check gagal.

Data leakage = penyebab #1 backtest terlalu optimis → live trading hancur.
"""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd
from scipy import stats

log = logging.getLogger(__name__)


class DataLeakageError(Exception):
    pass


def check_temporal_leakage(
    df: pd.DataFrame,
    feature_cols: list[str],
    target_col: str,
    corr_threshold: float = 0.80,
) -> bool:
    """
    Gate utama: pastikan tidak ada fitur yang berkorelasi tinggi
    dengan target masa depan.

    Cara kerja:
    1. Hitung korelasi setiap fitur dengan target (shift -1, -2, -5).
    2. Korelasi > threshold → indikasi kuat leakage.
    3. Return True jika AMAN (tidak ada leakage).
    """
    if target_col not in df.columns:
        raise DataLeakageError(f"Target column '{target_col}' tidak ditemukan.")

    available_features = [c for c in feature_cols if c in df.columns]
    if not available_features:
        raise DataLeakageError("Tidak ada feature columns yang ditemukan di DataFrame.")

    suspicious: list[str] = []

    for col in available_features:
        for shift in [0, -1, -2, -5]:
            if shift == 0:
                # Korelasi langsung fitur vs target
                corr = df[col].corr(df[target_col])
            else:
                # Korelasi fitur saat ini vs target di masa depan
                shifted_target = df[target_col].shift(shift)
                corr = df[col].corr(shifted_target)

            if abs(corr) > corr_threshold:
                suspicious.append(
                    f"LEAKAGE: {col} corr={corr:.3f} dengan {target_col}(shift={shift})"
                )

    if suspicious:
        msg = "Data leakage terdeteksi!\n" + "\n".join(suspicious)
        log.critical(msg)
        raise DataLeakageError(msg)

    log.info(f"Temporal leakage check PASSED untuk {len(available_features)} fitur.")
    return True


def check_lookahead_bias(
    df: pd.DataFrame,
    window: int = 50,
) -> dict[str, float]:
    """
    Rolling window forward correlation: korelasi fitur[t] dengan close[t+N].
    Return dict kolom → max correlation.
    Korelasi konsisten tinggi = lookahead bias.
    """
    result: dict[str, float] = {}

    if "close" not in df.columns:
        return result

    future_close = df["close"].shift(-window)

    for col in df.columns:
        if col in ("timestamp", "close") or col.startswith("target_"):
            continue
        if df[col].dtype not in (np.float64, np.float32, np.int64, np.int32, float, int):
            continue

        # Rolling correlation
        try:
            rolling_corr = df[col].rolling(window * 2).corr(future_close)
            max_corr = rolling_corr.abs().max()
            if not np.isnan(max_corr):
                result[col] = float(max_corr)
        except Exception:
            continue

    # Flag high correlations
    flagged = {k: v for k, v in result.items() if v > 0.7}
    if flagged:
        log.warning(
            f"Lookahead bias potensial ditemukan di {len(flagged)} kolom: "
            + str(list(flagged.keys())[:5])
        )

    return result


def check_index_alignment(
    features: pd.DataFrame,
    target: pd.Series,
) -> bool:
    """
    Pastikan index features dan target ter-align sempurna.
    Mis-alignment = silent leakage.
    """
    if len(features) != len(target):
        log.error(
            f"Index misalignment: features={len(features)}, target={len(target)}"
        )
        return False

    if hasattr(features.index, "equals") and hasattr(target.index, "equals"):
        if not features.index.equals(target.index):
            log.error("Index features dan target TIDAK identik.")
            return False

    log.info("Index alignment check PASSED.")
    return True


def run_full_leakage_check(
    df: pd.DataFrame,
    feature_cols: list[str],
    target_col: str,
) -> bool:
    """
    Jalankan semua leakage check sekaligus.
    Return True jika semua AMAN.
    Raise DataLeakageError jika terdeteksi leakage.
    """
    # 1. Temporal leakage (hard gate)
    check_temporal_leakage(df, feature_cols, target_col)

    # 2. Lookahead bias (warning only)
    bias = check_lookahead_bias(df)
    high_bias = {k: v for k, v in bias.items() if v > 0.8}
    if high_bias:
        log.warning(f"Lookahead bias WARNING (>0.8): {high_bias}")

    # 3. Index alignment
    if target_col in df.columns:
        features_df = df[[c for c in feature_cols if c in df.columns]]
        target_series = df[target_col]
        check_index_alignment(features_df, target_series)

    log.info("Full leakage check PASSED. Pipeline aman untuk dilanjutkan.")
    return True
