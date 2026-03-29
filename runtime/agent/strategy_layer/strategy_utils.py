"""
Helper functions untuk evaluasi strategi (pure functions, stateless).
"""

from runtime.agent.models import MarketRegime  # type: ignore


def score_signal(
    pred: float,
    regime: MarketRegime,
    vol_regime: str,
    spread_pct: float,
    imbalance: float,
) -> float:
    """
    Kalkulasi komposit skor dari 0.0 hingga 1.0 untuk validasi akhir
    dari sebuah signal sebelum masuk Portfolio/Risk layer.
    """
    score = 0.0

    # 1. Model prediction strength (40% weight)
    pred_score = min(abs(pred) / 0.01, 1.0)  # Normalize, 1% abs pred = 1.0 score
    score += pred_score * 0.40

    # 2. Regime Alignment (30% weight)
    regime_scores = {
        MarketRegime.STRONG_TREND_UP: 0.9,
        MarketRegime.STRONG_TREND_DOWN: 0.9,
        MarketRegime.WEAK_TREND_UP: 0.6,
        MarketRegime.WEAK_TREND_DOWN: 0.6,
        MarketRegime.SIDEWAYS: 0.3,
        MarketRegime.HIGH_VOLATILITY: 0.0,
        MarketRegime.UNDEFINED: 0.0,
    }
    score += regime_scores.get(regime, 0.0) * 0.30

    # 3. Volatility Score (20% weight) - lower vol is better precision
    vol_scores = {"low": 1.0, "normal": 0.7, "high": 0.4, "extreme": 0.0}
    score += vol_scores.get(vol_regime, 0.5) * 0.20

    # 4. Orderbook Imbalance (10% weight)
    if (pred > 0 and imbalance > 0) or (pred < 0 and imbalance < 0):
        # Arah sesuai dengan imbalance support
        score += min(abs(imbalance), 1.0) * 0.10

    return round(score, 4)


def calc_rr_ratio(entry: float, sl: float, tp: float) -> float:
    """Menghitung Risk:Reward Ratio aktual."""
    if entry == 0.0 or sl == 0.0:
        return 0.0
    risk = abs(entry - sl)
    if risk == 0:
        return 0.0
    reward = abs(tp - entry)
    return reward / risk


def tf_to_seconds(timeframe: str) -> int:
    """Parsing 'Xh', 'Xm' ke detik."""
    timeframe = timeframe.lower().strip()
    if timeframe.endswith("m"):
        return int(timeframe[:-1]) * 60
    elif timeframe.endswith("h"):
        return int(timeframe[:-1]) * 3600
    elif timeframe.endswith("d"):
        return int(timeframe[:-1]) * 86400
    return 3600


def round_price(price: float, tick_size: float = 0.01) -> float:
    """Membulatkan harga ke kelipatan tick terdekat yang valid."""
    import math

    if tick_size <= 0:
        return price
    precision = abs(math.floor(math.log10(tick_size)))
    rounded = round(round(price / tick_size) * tick_size, precision)
    return rounded
