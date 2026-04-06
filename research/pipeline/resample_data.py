"""
resample_data.py — Resampling Timeframe
Konversi OHLCV dari timeframe rendah ke tinggi (1m→5m, 1h→4h, dst).
Aturan: Open=first, High=max, Low=min, Close=last, Volume=sum.
Label timestamp = AWAL window.
"""
from __future__ import annotations

import logging

import pandas as pd

log = logging.getLogger(__name__)

TF_FREQ_MAP = {
    "1m": "1min", "3m": "3min", "5m": "5min", "15m": "15min", "30m": "30min",
    "1h": "1h", "2h": "2h", "4h": "4h", "6h": "6h", "1d": "1D",
}

TF_SECONDS = {
    "1m": 60, "3m": 180, "5m": 300, "15m": 900, "30m": 1800,
    "1h": 3600, "2h": 7200, "4h": 14400, "6h": 21600, "1d": 86400,
}


def resample_ohlcv(
    df: pd.DataFrame,
    source_tf: str,
    target_tf: str,
) -> pd.DataFrame:
    """
    Resample OHLCV dari source_tf ke target_tf.
    target_tf harus lebih besar dari source_tf.
    """
    if TF_SECONDS.get(target_tf, 0) <= TF_SECONDS.get(source_tf, 0):
        raise ValueError(
            f"target_tf ({target_tf}) harus lebih besar dari source_tf ({source_tf})"
        )

    freq = TF_FREQ_MAP.get(target_tf)
    if freq is None:
        raise ValueError(f"Timeframe target tidak dikenali: {target_tf}")

    df = df.copy()
    df = df.set_index("timestamp")

    resampled = df.resample(freq, label="left", closed="left").agg({
        "open": "first",
        "high": "max",
        "low": "min",
        "close": "last",
        "volume": "sum",
    })

    resampled = resampled.dropna(subset=["open"])
    resampled = resampled.reset_index()
    resampled.columns = ["timestamp", "open", "high", "low", "close", "volume"]

    log.info(
        f"Resampled {source_tf} → {target_tf}: {len(df)} → {len(resampled)} baris"
    )
    return resampled


def align_multi_tf(
    dfs: dict[str, pd.DataFrame],
) -> dict[str, pd.DataFrame]:
    """
    Align semua DataFrame ke rentang waktu yang sama (intersection).
    Key = timeframe, value = DataFrame.
    """
    if not dfs:
        return {}

    # Cari rentang waktu terbatas (intersection)
    global_start = max(df["timestamp"].min() for df in dfs.values())
    global_end = min(df["timestamp"].max() for df in dfs.values())

    result: dict[str, pd.DataFrame] = {}
    for tf, df in dfs.items():
        filtered = df[
            (df["timestamp"] >= global_start) & (df["timestamp"] <= global_end)
        ].copy()
        filtered = filtered.reset_index(drop=True)
        result[tf] = filtered
        log.info(f"Aligned {tf}: {len(filtered)} rows ({global_start} → {global_end})")

    return result
