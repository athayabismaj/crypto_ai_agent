"""
feature_engineering.py — Komputasi Fitur ML
Menghitung semua fitur teknikal yang dibutuhkan model.
Menggunakan pandas-ta (pure Python, tanpa C dependency).

KRITIS: Target label shift(-N) HANYA digunakan untuk target, BUKAN fitur.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

log = logging.getLogger(__name__)


class InsufficientDataError(Exception):
    pass


class DataLeakageError(Exception):
    pass


@dataclass
class FeatureConfig:
    rsi_periods: list[int] = field(default_factory=lambda: [7, 14, 21])
    ema_periods: list[int] = field(default_factory=lambda: [9, 21, 50, 200])
    atr_period: int = 14
    bb_period: int = 20
    volume_ma_period: int = 20
    target_horizons: list[int] = field(default_factory=lambda: [1, 4, 8])
    include_funding: bool = False
    drop_na: bool = True


def _add_price_action(df: pd.DataFrame) -> pd.DataFrame:
    """Log return fitur."""
    df["log_return"] = np.log(df["close"] / df["close"].shift(1))
    df["log_return_5"] = np.log(df["close"] / df["close"].shift(5))
    df["log_return_20"] = np.log(df["close"] / df["close"].shift(20))
    # Range body
    df["body_pct"] = (df["close"] - df["open"]) / df["open"]
    df["wick_upper"] = (df["high"] - df[["open", "close"]].max(axis=1)) / df["close"]
    df["wick_lower"] = (df[["open", "close"]].min(axis=1) - df["low"]) / df["close"]
    return df


def _add_momentum(df: pd.DataFrame, config: FeatureConfig) -> pd.DataFrame:
    """RSI, Momentum, Rate of Change."""
    try:
        import pandas_ta as ta
    except ImportError:
        raise ImportError("Install pandas-ta: pip install pandas-ta")

    for period in config.rsi_periods:
        rsi = ta.rsi(df["close"], length=period)
        if rsi is not None:
            df[f"rsi_{period}"] = rsi

    # Momentum (selisih harga)
    df["mom_10"] = df["close"] - df["close"].shift(10)
    # Rate of Change
    df["roc_10"] = df["close"].pct_change(10)
    return df


def _add_trend(df: pd.DataFrame, config: FeatureConfig) -> pd.DataFrame:
    """EMA, MACD, EMA crossover ratios."""
    try:
        import pandas_ta as ta
    except ImportError:
        raise ImportError("Install pandas-ta: pip install pandas-ta")

    for period in config.ema_periods:
        ema = ta.ema(df["close"], length=period)
        if ema is not None:
            df[f"ema_{period}"] = ema

    # EMA crossover ratios
    if "ema_9" in df.columns and "ema_21" in df.columns:
        df["ema_ratio_9_21"] = df["ema_9"] / df["ema_21"]
    if "ema_21" in df.columns and "ema_50" in df.columns:
        df["ema_ratio_21_50"] = df["ema_21"] / df["ema_50"]

    # MACD
    macd_df = ta.macd(df["close"], fast=12, slow=26, signal=9)
    if macd_df is not None:
        for col in macd_df.columns:
            key = (
                col.replace("MACD_12_26_9", "macd")
                .replace("MACDh_12_26_9", "macd_hist")
                .replace("MACDs_12_26_9", "macd_signal")
            )
            df[key] = macd_df[col]

    return df


def _add_volatility(df: pd.DataFrame, config: FeatureConfig) -> pd.DataFrame:
    """ATR, Bollinger Band width, realized volatility."""
    try:
        import pandas_ta as ta
    except ImportError:
        raise ImportError("Install pandas-ta: pip install pandas-ta")

    # ATR
    atr = ta.atr(df["high"], df["low"], df["close"], length=config.atr_period)
    if atr is not None:
        df[f"atr_{config.atr_period}"] = atr
        df["atr_pct"] = atr / df["close"]

    # Bollinger Bands width
    bb = ta.bbands(df["close"], length=config.bb_period)
    if bb is not None:
        upper_col = [c for c in bb.columns if "BBU" in c]
        lower_col = [c for c in bb.columns if "BBL" in c]
        if upper_col and lower_col:
            df["bb_width"] = (bb[upper_col[0]] - bb[lower_col[0]]) / df["close"]

    # Realized volatility (rolling std of log returns)
    df["realized_vol_20"] = df["log_return"].rolling(20).std() * np.sqrt(252)
    return df


def _add_volume_features(df: pd.DataFrame, config: FeatureConfig) -> pd.DataFrame:
    """Volume ratio, OBV, VWAP."""
    try:
        import pandas_ta as ta
    except ImportError:
        raise ImportError("Install pandas-ta: pip install pandas-ta")

    # Volume ratio
    vol_ma = df["volume"].rolling(config.volume_ma_period).mean()
    df["volume_ratio"] = df["volume"] / vol_ma

    # OBV
    obv = ta.obv(df["close"], df["volume"])
    if obv is not None:
        df["obv"] = obv

    # VWAP approximate (intraday) — menggunakan cumulative
    typical_price = (df["high"] + df["low"] + df["close"]) / 3
    df["vwap_20"] = (typical_price * df["volume"]).rolling(20).sum() / df["volume"].rolling(
        20
    ).sum()

    return df


def _add_regime(df: pd.DataFrame) -> pd.DataFrame:
    """ADX trend strength."""
    try:
        import pandas_ta as ta
    except ImportError:
        raise ImportError("Install pandas-ta: pip install pandas-ta")

    adx_df = ta.adx(df["high"], df["low"], df["close"], length=14)
    if adx_df is not None:
        adx_col = [c for c in adx_df.columns if c.startswith("ADX")]
        dmp_col = [c for c in adx_df.columns if c.startswith("DMP")]
        dmn_col = [c for c in adx_df.columns if c.startswith("DMN")]

        if adx_col:
            df["adx_14"] = adx_df[adx_col[0]]
        if dmp_col:
            df["dmp_14"] = adx_df[dmp_col[0]]
        if dmn_col:
            df["dmn_14"] = adx_df[dmn_col[0]]

    return df


def add_target(
    df: pd.DataFrame,
    horizon: int,
    col: str = "close",
) -> pd.DataFrame:
    """
    Tambahkan target label: log-return N candle ke depan.
    MENGGUNAKAN shift(-N) → ini satu-satunya tempat shift negatif diizinkan.
    """
    df[f"target_return_{horizon}h"] = np.log(df[col].shift(-horizon) / df[col])
    return df


def build_features(
    df: pd.DataFrame,
    config: FeatureConfig | None = None,
) -> pd.DataFrame:
    """
    Pipeline utama feature engineering.
    Input: DataFrame dengan kolom [timestamp, open, high, low, close, volume].
    Output: DataFrame dengan semua fitur + target.
    """
    cfg = config or FeatureConfig()

    min_required = max(cfg.ema_periods) + 50  # buffer warmup
    if len(df) < min_required:
        raise InsufficientDataError(f"Data hanya {len(df)} baris, butuh minimal {min_required}")

    df = df.copy()

    # Step 1: Price action
    df = _add_price_action(df)
    # Step 2: Momentum
    df = _add_momentum(df, cfg)
    # Step 3: Trend
    df = _add_trend(df, cfg)
    # Step 4: Volatility
    df = _add_volatility(df, cfg)
    # Step 5: Volume
    df = _add_volume_features(df, cfg)
    # Step 6: Regime
    df = _add_regime(df)

    # Step 7: Target labels
    for horizon in cfg.target_horizons:
        df = add_target(df, horizon)

    # Step 8: Drop NaN rows dari warmup
    if cfg.drop_na:
        before = len(df)
        df = df.dropna().reset_index(drop=True)
        log.info(f"Dropped {before - len(df)} NaN rows (warmup). Sisa: {len(df)}")

    return df


def get_feature_names(config: FeatureConfig | None = None) -> list[str]:
    """
    Return daftar nama fitur yang dihasilkan (bukan target).
    PENTING: Urutan harus konsisten dengan build_features().
    """
    cfg = config or FeatureConfig()
    names = [
        "log_return",
        "log_return_5",
        "log_return_20",
        "body_pct",
        "wick_upper",
        "wick_lower",
    ]
    for p in cfg.rsi_periods:
        names.append(f"rsi_{p}")
    names.extend(["mom_10", "roc_10"])

    for p in cfg.ema_periods:
        names.append(f"ema_{p}")
    names.extend(["ema_ratio_9_21", "ema_ratio_21_50"])
    names.extend(["macd", "macd_hist", "macd_signal"])

    names.append(f"atr_{cfg.atr_period}")
    names.extend(["atr_pct", "bb_width", "realized_vol_20"])

    names.extend(["volume_ratio", "obv", "vwap_20"])
    names.extend(["adx_14", "dmp_14", "dmn_14"])

    return names


def validate_no_leakage(df: pd.DataFrame) -> bool:
    """
    Quick check: pastikan semua kolom 'target_*' di-generate dengan benar.
    Untuk check mendalam, gunakan validation/leakage_check.py.
    """
    target_cols = [c for c in df.columns if c.startswith("target_")]
    if not target_cols:
        raise DataLeakageError("Tidak ada kolom target ditemukan.")

    # Cek basic: target harus NaN di baris terakhir (karena shift negatif)
    for col in target_cols:
        last_val = df[col].iloc[-1] if len(df) > 0 else np.nan
        # Jika drop_na=True, target NaN sudah dihapus → cek via correlation
        # Di sini kita hanya check bahwa korelasi target dengan close saat ini < 0.95
        corr = df["close"].corr(df[col])
        if abs(corr) > 0.95:
            raise DataLeakageError(
                f"Korelasi mencurigakan antara close dan {col}: {corr:.3f}. " "Kemungkinan leakage."
            )
    return True
