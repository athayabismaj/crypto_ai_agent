"""
clean_data.py — Pembersihan Data Mentah
Handle: missing candle, outlier harga, duplikat timestamp, normalisasi UTC.
ATURAN: Logika cleaning HARUS identik antara training dan runtime.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np
import pandas as pd

log = logging.getLogger(__name__)


@dataclass
class CleanConfig:
    max_ffill_candles: int = 3
    outlier_z_thresh: float = 4.0
    min_candle_volume: float = 0.0
    processed_dir: str = "research/data/processed"


def fix_timestamps(df: pd.DataFrame, tz: str = "UTC") -> pd.DataFrame:
    """Konversi ke UTC, hapus duplikat timestamp (ambil yg pertama)."""
    df = df.copy()
    if not pd.api.types.is_datetime64_any_dtype(df["timestamp"]):
        df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    elif df["timestamp"].dt.tz is None:
        df["timestamp"] = df["timestamp"].dt.tz_localize(tz)
    else:
        df["timestamp"] = df["timestamp"].dt.tz_convert(tz)

    # Duplikat: ambil kemunculan pertama (konsisten dgn behavior exchange)
    df = df.drop_duplicates(subset=["timestamp"], keep="first")
    df = df.sort_values("timestamp").reset_index(drop=True)
    return df


def validate_ohlc_logic(df: pd.DataFrame) -> tuple[bool, list[str]]:
    """
    Validasi: High >= max(Open,Close), Low <= min(Open,Close).
    Return (is_valid, list_of_error_descriptions).
    """
    errors: list[str] = []
    high_violation = df[df["high"] < df[["open", "close"]].max(axis=1)]
    if len(high_violation) > 0:
        errors.append(f"High < max(Open,Close) pada {len(high_violation)} baris")

    low_violation = df[df["low"] > df[["open", "close"]].min(axis=1)]
    if len(low_violation) > 0:
        errors.append(f"Low > min(Open,Close) pada {len(low_violation)} baris")

    negative_vol = df[df["volume"] < 0]
    if len(negative_vol) > 0:
        errors.append(f"Volume negatif pada {len(negative_vol)} baris")

    return len(errors) == 0, errors


def remove_outliers(df: pd.DataFrame, z_thresh: float = 4.0) -> pd.DataFrame:
    """
    Deteksi outlier via Z-score pada log-return (bukan harga absolut).
    Z > threshold -> baris ditandai, BUKAN dihapus (flag saja).
    """
    df = df.copy()
    # Hitung log return
    log_ret = np.log(df["close"] / df["close"].shift(1))
    mean_lr = log_ret.mean()
    std_lr = log_ret.std()

    if std_lr == 0 or np.isnan(std_lr):
        df["is_outlier"] = False
        return df

    z_scores = np.abs((log_ret - mean_lr) / std_lr)
    df["is_outlier"] = z_scores > z_thresh
    n_outliers = df["is_outlier"].sum()
    if n_outliers > 0:
        log.warning(f"Ditemukan {n_outliers} outlier (z > {z_thresh})")
    return df


def _fill_gaps(df: pd.DataFrame, tf: str, max_ffill: int) -> pd.DataFrame:
    """Isi gap candle kosong dengan forward-fill, max sejumlah max_ffill berturut."""
    tf_map = {
        "1m": "1min", "3m": "3min", "5m": "5min", "15m": "15min", "30m": "30min",
        "1h": "1h", "2h": "2h", "4h": "4h", "6h": "6h", "1d": "1D",
    }
    freq = tf_map.get(tf)
    if freq is None:
        log.warning(f"Timeframe {tf} tidak dikenali untuk gap-fill, skip.")
        return df

    df = df.set_index("timestamp")
    full_idx = pd.date_range(start=df.index.min(), end=df.index.max(), freq=freq, tz="UTC")
    df = df.reindex(full_idx)
    df.index.name = "timestamp"

    # Tandai mana yang gap
    gap_mask = df["open"].isna()
    # Hitung gap berturut
    gap_group = gap_mask.ne(gap_mask.shift()).cumsum()
    gap_lengths = gap_mask.groupby(gap_group).transform("sum")

    # Forward fill hanya jika gap <= max_ffill
    fillable = gap_mask & (gap_lengths <= max_ffill)
    # Fill yang bisa
    df.loc[fillable] = df.ffill().loc[fillable]

    # Gap di atas max_ffill -> hapus
    unfillable = gap_mask & (gap_lengths > max_ffill)
    if unfillable.sum() > 0:
        log.warning(f"Menghapus {unfillable.sum()} baris gap > {max_ffill} candle berturut.")
    df = df[~unfillable]
    df = df.reset_index()
    return df


def clean_ohlcv(
    df: pd.DataFrame,
    symbol: str,
    tf: str,
    config: CleanConfig | None = None,
) -> pd.DataFrame:
    """
    Pipeline cleaning utama:
    1. Fix timestamps
    2. Validate OHLC logic
    3. Fill gaps
    4. Remove outliers
    5. Filter volume
    """
    cfg = config or CleanConfig()

    # 1. Timestamp
    df = fix_timestamps(df)

    # 2. OHLC logic
    valid, errors = validate_ohlc_logic(df)
    if not valid:
        for err in errors:
            log.error(f"[{symbol}] OHLC Logic Error: {err}")
        # Koreksi: clamp High/Low
        df["high"] = df[["open", "high", "close"]].max(axis=1)
        df["low"] = df[["open", "low", "close"]].min(axis=1)
        log.info(f"[{symbol}] OHLC di-koreksi otomatis.")

    # 3. Gap fill
    df = _fill_gaps(df, tf, cfg.max_ffill_candles)

    # 4. Outlier detection
    df = remove_outliers(df, cfg.outlier_z_thresh)

    # 5. Volume filter
    if cfg.min_candle_volume > 0:
        before = len(df)
        df = df[df["volume"] >= cfg.min_candle_volume]
        removed = before - len(df)
        if removed > 0:
            log.info(f"[{symbol}] Menghapus {removed} candle dengan volume < {cfg.min_candle_volume}")

    df = df.reset_index(drop=True)
    log.info(f"[{symbol} {tf}] Cleaning selesai: {len(df)} baris.")
    return df


def save_processed(df: pd.DataFrame, symbol: str, tf: str, config: CleanConfig | None = None) -> str:
    """Simpan data bersih ke processed dir."""
    import os

    cfg = config or CleanConfig()
    os.makedirs(cfg.processed_dir, exist_ok=True)

    start_str = df["timestamp"].min().strftime("%Y%m%d")
    end_str = df["timestamp"].max().strftime("%Y%m%d")
    filename = f"{symbol}_{tf}_{start_str}_{end_str}_clean.parquet"
    path = os.path.join(cfg.processed_dir, filename)

    # Drop kolom bantuan sebelum simpan
    save_cols = [c for c in df.columns if c != "is_outlier"]
    df[save_cols].to_parquet(path, engine="pyarrow", index=False)
    log.info(f"Saved processed: {path}")
    return path
