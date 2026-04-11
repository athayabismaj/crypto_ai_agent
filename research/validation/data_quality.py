"""
data_quality.py — Lapisan Pertama Validasi
Cek kelengkapan, range nilai, dan statistik dasar SEBELUM data diproses lanjut.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

import pandas as pd

log = logging.getLogger(__name__)


class DataQualityError(Exception):
    pass


@dataclass
class QualityReport:
    symbol: str
    timeframe: str
    total_rows: int
    missing_pct: dict[str, float]
    gap_count: int
    outlier_count: int
    ohlc_logic_errors: int
    passed: bool
    warnings: list[str] = field(default_factory=list)


def check_missing(df: pd.DataFrame) -> dict[str, float]:
    """Return dict kolom → % missing."""
    total = len(df)
    if total == 0:
        return {}
    return {col: (df[col].isna().sum() / total) * 100 for col in df.columns}


def check_price_range(df: pd.DataFrame, symbol: str) -> tuple[bool, list[str]]:
    """
    Cek harga tidak negatif dan volume wajar.
    """
    anomalies: list[str] = []
    for col in ["open", "high", "low", "close"]:
        if col not in df.columns:
            continue
        neg = (df[col] <= 0).sum()
        if neg > 0:
            anomalies.append(f"[{symbol}] {col} <= 0 pada {neg} baris")

    # Volume check
    if "volume" in df.columns:
        zero_vol = (df["volume"] == 0).sum()
        pct_zero = zero_vol / len(df) * 100
        if pct_zero > 0.5:
            anomalies.append(f"[{symbol}] Volume=0 pada {pct_zero:.2f}% candle")

    return len(anomalies) == 0, anomalies


def check_candle_gaps(df: pd.DataFrame, tf: str) -> list:
    """Deteksi gap (timestamp melompat lebih dari 1 candle)."""
    tf_seconds = {
        "1m": 60,
        "3m": 180,
        "5m": 300,
        "15m": 900,
        "30m": 1800,
        "1h": 3600,
        "2h": 7200,
        "4h": 14400,
        "6h": 21600,
        "1d": 86400,
    }
    expected_delta = pd.Timedelta(seconds=tf_seconds.get(tf, 3600))

    gaps = []
    if "timestamp" not in df.columns or len(df) < 2:
        return gaps

    ts = df["timestamp"]
    deltas = ts.diff()
    gap_mask = deltas > expected_delta * 1.5  # toleransi 50%
    for idx in df.index[gap_mask]:
        gaps.append(ts.iloc[idx])

    return gaps


def run_quality_report(
    df: pd.DataFrame,
    symbol: str,
    tf: str,
) -> QualityReport:
    """
    Jalankan semua check dan hasilkan QualityReport.
    Raise DataQualityError jika threshold kritis dilanggar.
    """
    warnings_list: list[str] = []
    total = len(df)

    # 1. Missing check
    missing = check_missing(df)
    for col, pct in missing.items():
        if pct > 5.0:
            raise DataQualityError(
                f"[{symbol} {tf}] Kolom '{col}' missing {pct:.2f}% (>5%). Data corrupt."
            )
        if pct > 1.0:
            warnings_list.append(f"Kolom '{col}' missing {pct:.2f}%")

    # 2. Price range
    price_ok, price_anomalies = check_price_range(df, symbol)
    warnings_list.extend(price_anomalies)

    # 3. OHLC logic
    ohlc_errors = 0
    if all(c in df.columns for c in ["open", "high", "low", "close"]):
        high_v = (df["high"] < df[["open", "close"]].max(axis=1)).sum()
        low_v = (df["low"] > df[["open", "close"]].min(axis=1)).sum()
        ohlc_errors = int(high_v + low_v)
        if ohlc_errors > 0:
            raise DataQualityError(
                f"[{symbol} {tf}] {ohlc_errors} OHLC logic errors. Data corrupt."
            )

    # 4. Gap check
    gaps = check_candle_gaps(df, tf)
    gap_count = len(gaps)
    gap_per_1000 = (gap_count / total * 1000) if total > 0 else 0
    if gap_per_1000 > 10:
        warnings_list.append(f"Gap tinggi: {gap_count} gaps ({gap_per_1000:.1f} per 1000 candle)")

    # 5. Outlier count (jika sudah ada kolom is_outlier)
    outlier_count = int(df["is_outlier"].sum()) if "is_outlier" in df.columns else 0

    passed = ohlc_errors == 0 and all(v <= 5.0 for v in missing.values())

    report = QualityReport(
        symbol=symbol,
        timeframe=tf,
        total_rows=total,
        missing_pct=missing,
        gap_count=gap_count,
        outlier_count=outlier_count,
        ohlc_logic_errors=ohlc_errors,
        passed=passed,
        warnings=warnings_list,
    )

    log.info(f"Quality Report [{symbol} {tf}]: passed={passed}, rows={total}, gaps={gap_count}")
    if warnings_list:
        for w in warnings_list:
            log.warning(f"  ⚠ {w}")

    return report
