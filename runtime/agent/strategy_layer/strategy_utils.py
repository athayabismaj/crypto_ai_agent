"""
Strategy Layer: strategy_utils.py
=======================================================
Kumpulan pure functions untuk komputasi signal scoring,
deteksi support/resistance, dan helper kalkulasi.

ATURAN KERAS:
- Semua fungsi di sini adalah PURE FUNCTION
- Tidak ada I/O, tidak ada side effect, tidak ada state
- Bisa dipanggil 100.000x per detik tanpa masalah
- Parameter harus typed, return harus typed

Sebagai analis market 20+ tahun:
- score_signal() adalah fungsi terpenting di sini
- Ini adalah "juri" yang menilai seberapa bagus sebuah sinyal
- Bila skor di bawah 0.60 → tidak worth it risk vs reward-nya
"""

from __future__ import annotations

import math
from typing import Any, Optional

# ── Timeframe Utilities ────────────────────────────────────────────────────


def tf_to_seconds(timeframe: str) -> int:
    """
    Konversi string timeframe ke detik.

    Contoh:
        '1m'  → 60
        '5m'  → 300
        '15m' → 900
        '1h'  → 3600
        '4h'  → 14400
        '1d'  → 86400
    """
    tf = timeframe.strip().lower()
    if tf.endswith("d"):
        return int(tf[:-1]) * 86400
    if tf.endswith("h"):
        return int(tf[:-1]) * 3600
    if tf.endswith("m"):
        return int(tf[:-1]) * 60
    if tf.endswith("s"):
        return int(tf[:-1])
    return 3600  # Default fallback = 1 jam


def tf_to_minutes(timeframe: str) -> int:
    """Konversi timeframe ke menit."""
    return tf_to_seconds(timeframe) // 60


# ── Price Utilities ────────────────────────────────────────────────────────


def round_price(price: float, tick_size: float) -> float:
    """
    Bulatkan harga ke tick_size terdekat (sesuai Binance lot filter).

    Contoh:
        round_price(29847.65, 0.10) → 29847.60
        round_price(0.04567, 0.0001) → 0.0457
    """
    if tick_size <= 0:
        return round(price, 8)
    precision = max(0, round(-math.log10(tick_size)))
    return round(round(price / tick_size) * tick_size, precision)


def calc_rr_ratio(entry: float, sl: float, tp: float) -> float:
    """
    Hitung Risk:Reward ratio aktual dari harga entry, SL, dan TP.

    Return:
        Ratio float (min 0.0). Contoh: 2.5 = 1:2.5 RR
        0.0 jika SL distance = 0 (invalid)
    """
    if entry <= 0 or sl <= 0 or tp <= 0:
        return 0.0

    risk = abs(entry - sl)
    if risk == 0:
        return 0.0

    reward = abs(tp - entry)
    return round(reward / risk, 2)


def safe_div(numerator: float, denominator: float, default: float = 0.0) -> float:
    """Division aman tanpa ZeroDivisionError."""
    if denominator == 0:
        return default
    return numerator / denominator


# ── Signal Quality Scoring ─────────────────────────────────────────────────

_REGIME_SCORE_MAP: dict[str, float] = {
    "STRONG_TREND_UP": 0.90,  # Clear uptrend — kondisi ideal BUY
    "WEAK_TREND_UP": 0.65,  # Uptrend lemah — masih bisa, tapi hati-hati
    "SIDEWAYS": 0.25,  # Choppy market — probabilitas tinggi kena noise
    "WEAK_TREND_DOWN": 0.65,  # Downtrend lemah — bagus untuk SHORT
    "STRONG_TREND_DOWN": 0.90,  # Clear downtrend — ideal SHORT
    "HIGH_VOLATILITY": 0.0,  # Regime berbahaya — jangan trade sama sekali
    "UNDEFINED": 0.0,  # Unknown regime — tidak cukup data
}

_VOL_SCORE_MAP: dict[str, float] = {
    "low": 1.0,  # Volatilitas rendah = entry lebih presisi
    "normal": 0.70,  # Normal = oke
    "high": 0.35,  # Tinggi = SL sering kena noise → kurang bagus
    "extreme": 0.0,  # Ekstrem = tidak worth it
}


def score_signal(
    pred: float,
    regime: Any,  # MarketRegime enum atau string
    vol_regime: str,
    spread_pct: float,
    imbalance: float,  # Orderbook imbalance -1.0 s/d +1.0
    rr_ratio: float = 2.0,  # Actual Risk:Reward dari harga SL/TP
    regime_stable: bool = True,  # Apakah regime sudah stabil (minimal N candle)
) -> float:
    """
    Composite Score 0.0–1.0 dari semua faktor kualitas sinyal.

    Bobot:
    - 40%: Kekuatan prediksi model (pred magnitude)
    - 25%: Alignment dengan market regime
    - 15%: Kondisi volatilitas
    - 10%: Orderbook imbalance (demand/supply pressure)
    - 10%: Biaya spread (semakin lebar spread → skor berkurang)

    Bonus:
    - +0.05 jika regime stabil sudah > N candles
    - +0.05 jika RR ≥ 3.0 (risk:reward sangat baik)

    Return:
        float 0.0–1.0 (di-clip, tidak bisa > 1.0 atau < 0.0)
    """
    score = 0.0

    # ── Component 1: Kekuatan prediksi (40%) ──────────────────────────────
    # pred = return yang diprediksi model (float)
    # 1% pred = skor penuh pada komponen ini
    pred_magnitude = min(abs(pred) / 0.01, 1.0)
    score += pred_magnitude * 0.40

    # ── Component 2: Regime alignment (25%) ───────────────────────────────
    # Konversi MarketRegime enum atau string ke nama
    regime_name = getattr(regime, "name", str(regime)) if regime else "UNDEFINED"
    regime_score = _REGIME_SCORE_MAP.get(regime_name, 0.0)

    # Pastikan arah sinyal sesuai regime
    if regime_name in ("STRONG_TREND_UP", "WEAK_TREND_UP") and pred < 0:
        regime_score *= 0.3  # Kontra-trend → penalti berat
    elif regime_name in ("STRONG_TREND_DOWN", "WEAK_TREND_DOWN") and pred > 0:
        regime_score *= 0.3  # Kontra-trend → penalti berat

    score += regime_score * 0.25

    # ── Component 3: Volatility kondisi (15%) ─────────────────────────────
    vol_score = _VOL_SCORE_MAP.get(vol_regime, 0.5)
    score += vol_score * 0.15

    # ── Component 4: Orderbook imbalance (10%) ────────────────────────────
    # Imbalance positif = lebih banyak buyer → mendukung BUY
    # Imbalance negatif = lebih banyak seller → mendukung SELL
    if (pred > 0 and imbalance > 0) or (pred < 0 and imbalance < 0):
        imbalance_score = min(abs(imbalance), 1.0)  # Cap 1.0
        score += imbalance_score * 0.10

    # ── Component 5: Spread cost penalty (10%) ────────────────────────────
    # Spread 0.1% → tidak ada penalti
    # Spread 0.5% → penalti penuh (skor komponen = 0)
    max_acceptable_spread = 0.5
    spread_score = max(0.0, 1.0 - (spread_pct / max_acceptable_spread))
    score += spread_score * 0.10

    # ── Bonus: Regime stability ────────────────────────────────────────────
    if regime_stable and regime_name not in ("UNDEFINED", "HIGH_VOLATILITY"):
        score += 0.05

    # ── Bonus: Excellent RR ratio ──────────────────────────────────────────
    if rr_ratio >= 3.0:
        score += 0.05

    # Clip ke 0.0–1.0
    return round(min(max(score, 0.0), 1.0), 4)


def build_reasoning(
    pred: float,
    regime: Any,
    vol_regime: str,
    score: float,
    side: str,
    symbol: str,
    metadata: Optional[dict] = None,
) -> str:
    """
    Generate teks reasoning untuk logging & LLM review.

    Format:
        "[BUY BTCUSDT] Pred=+0.52% | Regime=STRONG_TREND_UP | Vol=normal | Score=0.78"
    """
    pred_pct = pred * 100
    regime_name = getattr(regime, "name", str(regime))
    meta_str = ""

    if metadata:
        meta_parts = [f"{k}={v}" for k, v in list(metadata.items())[:3]]
        meta_str = " | " + ", ".join(meta_parts)

    return (
        f"[{side} {symbol}] "
        f"Pred={pred_pct:+.3f}% | "
        f"Regime={regime_name} | "
        f"Vol={vol_regime} | "
        f"Score={score:.2f}"
        f"{meta_str}"
    )


# ── Support & Resistance Detection ────────────────────────────────────────


def is_near_resistance(
    price: float,
    high_prices: list[float],
    margin_pct: float = 0.02,
) -> bool:
    """
    Deteksi apakah harga mendekati resistance (high historis).

    Logika:
    - Ambil rolling max dari high_prices (window terakhir)
    - Jika harga dalam margin_pct dari max → near resistance

    Args:
        price: Harga saat ini
        high_prices: List harga high historis (urutan chronologis)
        margin_pct: Toleransi jarak (default 2%)

    Returns:
        True jika dekat resistance
    """
    if not high_prices or price <= 0:
        return False

    # Ambil 50 lilin terakhir untuk local top
    window = high_prices[-50:]
    local_high = max(window)

    if local_high <= 0:
        return False

    distance_pct = abs(local_high - price) / local_high
    return distance_pct <= margin_pct


def is_near_support(
    price: float,
    low_prices: list[float],
    margin_pct: float = 0.02,
) -> bool:
    """
    Deteksi apakah harga mendekati support (low historis).

    Args:
        price: Harga saat ini
        low_prices: List harga low historis
        margin_pct: Toleransi jarak (default 2%)

    Returns:
        True jika dekat support
    """
    if not low_prices or price <= 0:
        return False

    window = low_prices[-50:]
    local_low = min(window)

    if local_low <= 0:
        return False

    distance_pct = abs(price - local_low) / local_low
    return distance_pct <= margin_pct


def get_trend_strength(
    close_prices: list[float],
    period: int = 14,
) -> float:
    """
    Estimasi kekuatan trend 0.0–1.0 menggunakan ADX proxy.

    Implementasi sederhana (tanpa pandas/scipy):
    - Hitung true range rata-rata
    - Estimasi directional movement
    - Return normalized DX

    Untuk production: gunakan pandas-ta atau ta-lib.
    Fungsi ini adalah fallback ringan yang tidak memerlukan dependency eksternal.

    Returns:
        float 0.0–1.0 (0 = tidak ada trend, 1 = trend sangat kuat)
    """
    if len(close_prices) < period + 1:
        return 0.5  # Tidak cukup data

    # Simple momentum-based proxy: |slope| dari EMA
    prices = close_prices[-(period + 1) :]
    ema_first = prices[0]
    ema_last = prices[-1]

    if ema_first <= 0:
        return 0.0

    momentum = abs((ema_last - ema_first) / ema_first)

    # Normalize: 5% movement over period = max strength
    strength = min(momentum / 0.05, 1.0)
    return round(strength, 4)


def align_sl_to_structure(
    price: float,
    side: str,
    low_prices: list[float],
    high_prices: list[float],
    atr: float,
    lookback: int = 20,
) -> float:
    """
    Geser SL ke dan sejajarkan dengan swing high/low terdekat.

    Logika:
    - BUY: SL di bawah swing low terdekat (bukan ATR flat)
    - SELL: SL di atas swing high terdekat

    Contoh:
        Harga 30,000, ATR 200, SL awal 29,700
        Swing low terdekat di 29,500 → SL = 29,480 (sedikit di bawah)

    Args:
        price: Entry price
        side: 'BUY' | 'SELL'
        low_prices: List harga low historis
        high_prices: List harga high historis
        atr: ATR saat ini (untuk buffer)
        lookback: Jumlah candle untuk mencari swing

    Returns:
        SL price yang disesuaikan dengan market structure
    """
    buffer = atr * 0.25  # Buffer kecil di balik structure

    if side == "BUY":
        if not low_prices:
            return price - (atr * 1.5)
        recent_lows = low_prices[-lookback:]
        swing_low = min(recent_lows)
        return round(swing_low - buffer, 8)

    else:  # SELL
        if not high_prices:
            return price + (atr * 1.5)
        recent_highs = high_prices[-lookback:]
        swing_high = max(recent_highs)
        return round(swing_high + buffer, 8)
