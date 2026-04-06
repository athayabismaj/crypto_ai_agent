"""
consistency_check.py — Validasi Konsistensi Data
Cross-timeframe validation & feature drift detection (PSI).
"""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd

log = logging.getLogger(__name__)


def check_tf_consistency(
    df_low: pd.DataFrame,
    df_high: pd.DataFrame,
    src_tf: str,
    tgt_tf: str,
) -> tuple[bool, list[str]]:
    """
    Pastikan data timeframe tinggi bisa di-derive dari timeframe rendah.
    Contoh: close 4H terakhir harus sama dengan close 1H jam ke-4.
    """
    discrepancies: list[str] = []

    # Ambil sample 20 candle acak dari df_high, cek apakah close-nya
    # cocok dengan close terakhir di df_low pada window tsb
    sample_size = min(20, len(df_high))
    if sample_size == 0:
        return True, []

    indices = np.random.choice(len(df_high), size=sample_size, replace=False)
    for idx in indices:
        high_ts = df_high.iloc[idx]["timestamp"]
        high_close = df_high.iloc[idx]["close"]

        # Cari candle terakhir di df_low yang <= high_ts
        mask = df_low["timestamp"] <= high_ts
        if not mask.any():
            continue
        low_close = df_low.loc[mask, "close"].iloc[-1]

        # Toleransi 0.01% (rounding float)
        if abs(high_close - low_close) / high_close > 0.0001:
            discrepancies.append(
                f"Close mismatch at {high_ts}: {tgt_tf}={high_close}, {src_tf}={low_close}"
            )

    is_ok = len(discrepancies) <= 2  # toleransi max 2 discrepancy dari 20 sample
    if not is_ok:
        log.warning(f"TF consistency issues: {len(discrepancies)}/{sample_size}")

    return is_ok, discrepancies


def check_spot_futures(
    df_spot: pd.DataFrame,
    df_futures: pd.DataFrame,
) -> tuple[bool, float]:
    """
    Cek konsistensi harga spot vs futures.
    Basis (futures - spot) / spot seharusnya kecil (<2%).
    """
    if df_spot.empty or df_futures.empty:
        return True, 0.0

    # Merge on timestamp
    merged = pd.merge(
        df_spot[["timestamp", "close"]],
        df_futures[["timestamp", "close"]],
        on="timestamp",
        suffixes=("_spot", "_futures"),
        how="inner",
    )

    if merged.empty:
        return True, 0.0

    basis = (merged["close_futures"] - merged["close_spot"]) / merged["close_spot"] * 100
    max_basis = basis.abs().max()

    is_ok = max_basis < 5.0  # 5% threshold
    if not is_ok:
        log.warning(f"Spot-Futures basis terlalu lebar: {max_basis:.2f}%")

    return is_ok, float(max_basis)


def _compute_psi(expected: np.ndarray, actual: np.ndarray, n_bins: int = 10) -> float:
    """
    Population Stability Index.
    PSI < 0.1 = stabil, 0.1-0.2 = drift minor, >0.2 = drift signifikan.
    """
    # Buat bin dari expected
    breakpoints = np.linspace(
        min(expected.min(), actual.min()),
        max(expected.max(), actual.max()),
        n_bins + 1,
    )

    expected_hist, _ = np.histogram(expected, bins=breakpoints)
    actual_hist, _ = np.histogram(actual, bins=breakpoints)

    # Smooth (avoid division by zero)
    expected_pct = (expected_hist + 1) / (len(expected) + n_bins)
    actual_pct = (actual_hist + 1) / (len(actual) + n_bins)

    psi = np.sum((actual_pct - expected_pct) * np.log(actual_pct / expected_pct))
    return float(psi)


def check_feature_drift(
    df_old: pd.DataFrame,
    df_new: pd.DataFrame,
    feature_cols: list[str] | None = None,
) -> dict[str, float]:
    """
    Hitung PSI untuk setiap fitur antara data lama dan data baru.
    Return dict kolom → PSI score.

    PSI < 0.1:  Tidak ada drift (aman)
    0.1 - 0.2:  Drift minor (monitor)
    > 0.2:      Drift signifikan (investigasi)
    """
    cols = feature_cols or [
        c for c in df_old.columns
        if c not in ("timestamp",) and not c.startswith("target_")
        and df_old[c].dtype in (np.float64, np.float32, np.int64, float)
    ]

    psi_scores: dict[str, float] = {}
    for col in cols:
        if col not in df_old.columns or col not in df_new.columns:
            continue
        old_vals = df_old[col].dropna().values
        new_vals = df_new[col].dropna().values
        if len(old_vals) < 10 or len(new_vals) < 10:
            continue

        psi = _compute_psi(old_vals, new_vals)
        psi_scores[col] = psi

        if psi > 0.2:
            log.warning(f"Feature drift SIGNIFIKAN: {col} PSI={psi:.4f}")
        elif psi > 0.1:
            log.info(f"Feature drift minor: {col} PSI={psi:.4f}")

    return psi_scores
