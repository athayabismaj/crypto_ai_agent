"""
Layer Intelligence: Regime Classifier
Mengklasifikasikan Kondisi Trend Pasar berdasarkan Average Directional Index (ADX) 
dan Exponential Moving Average (EMA).
Dilengkapi dengan override Volatilitas Ekstrem.
"""

from dataclasses import dataclass
from enum import Enum

import numpy as np
import pandas as pd

from runtime.agent.core.config_schema import AgentConfig  # type: ignore


class MarketRegime(Enum):
    STRONG_TREND_UP = "strong_trend_up"
    WEAK_TREND_UP = "weak_trend_up"
    SIDEWAYS = "sideways"
    WEAK_TREND_DOWN = "weak_trend_down"
    STRONG_TREND_DOWN = "strong_trend_down"
    HIGH_VOLATILITY = "high_volatility"  # override semua regime lain
    UNDEFINED = "undefined"  # data tidak cukup


@dataclass
class RegimeResult:
    regime: MarketRegime
    confidence: float
    adx: float
    adx_trend: str
    ema50_distance: float
    is_stable: bool
    lookback_candles: int


class RegimeClassifier:
    """Mesin Klasifikasi Iklim Pasar (Trend/Sideways)."""

    def __init__(self, config: AgentConfig) -> None:
        self.config = config
        self.adx_period = getattr(config, "strat_adx_period", 14)
        self.ema_period = getattr(config, "strat_ema_trend_period", 50)
        self.adx_strong = getattr(config, "strat_adx_strong", 30.0)
        self.adx_weak = getattr(config, "strat_adx_weak", 20.0)
        self.high_vol_percentile = getattr(config, "strat_high_vol_percentile", 85.0)
        self.min_history = getattr(config, "strat_min_history_candles", 200)

        # state internal (track is_stable)
        self.last_regimes: list[MarketRegime] = []
        self.STABILITY_PERIOD = 5

    def _calculate_adx(self, df: pd.DataFrame, period: int) -> pd.DataFrame:
        """Kalkulasi ADX via numpy vector (RMA smoothing)."""
        high = df["high"].values
        low = df["low"].values
        close = df["close"].values

        # Plus/Minus Directional Movement (DM)
        up_move = np.zeros_like(high)
        down_move = np.zeros_like(low)

        up_move[1:] = high[1:] - high[:-1]
        down_move[1:] = low[:-1] - low[1:]

        pdm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
        ndm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)

        # True Range
        tr = np.zeros_like(close)
        tr[0] = high[0] - low[0]
        for i in range(1, len(close)):
            tr[i] = max(high[i] - low[i], abs(high[i] - close[i - 1]), abs(low[i] - close[i - 1]))

        # RMA Smoothing
        def rma(arr: np.ndarray, prd: int) -> np.ndarray:
            res = np.zeros_like(arr)
            # Init SMA (mock first prd)
            if len(arr) > prd:
                res[prd - 1] = np.mean(arr[:prd])
                for i in range(prd, len(arr)):
                    res[i] = (res[i - 1] * (prd - 1) + arr[i]) / prd
            return res

        tr_rma = rma(tr, period)
        pdm_rma = rma(pdm, period)
        ndm_rma = rma(ndm, period)

        # DI+, DI-
        pdi = np.zeros_like(tr_rma)
        ndi = np.zeros_like(tr_rma)

        valid = tr_rma > 0
        pdi[valid] = 100 * pdm_rma[valid] / tr_rma[valid]
        ndi[valid] = 100 * ndm_rma[valid] / tr_rma[valid]

        # DX & ADX
        dx = np.zeros_like(pdi)
        di_sum = pdi + ndi
        valid_di = di_sum > 0
        dx[valid_di] = 100 * np.abs(pdi[valid_di] - ndi[valid_di]) / di_sum[valid_di]

        adx = rma(dx, period)

        res_df = pd.DataFrame(index=df.index)
        res_df["adx"] = adx
        return res_df

    def _calculate_confidence(
        self, adx: float, ema_distance: float, adx_trend: str, stability: bool
    ) -> float:
        score = 0.0

        # ADX strength (0.0 -- 0.4)
        if adx > 40:
            score += 0.40
        elif adx > 30:
            score += 0.30
        elif adx > 20:
            score += 0.20
        else:
            score += 0.05

        # EMA distance (0.0 -- 0.3)
        score += min(abs(ema_distance) / 5.0, 0.30)

        # ADX trend (0.0 -- 0.2)
        if adx_trend == "rising":
            score += 0.20
        elif adx_trend == "flat":
            score += 0.10

        # Stability bonus (0.0 -- 0.1)
        if stability:
            score += 0.10

        return min(score, 1.0)

    def classify(self, df: pd.DataFrame, current_atr_percentile: float = 0.0) -> RegimeResult:
        """Menghitung regime untuk barisan harga terkini."""
        if len(df) < self.min_history:
            return RegimeResult(MarketRegime.UNDEFINED, 0.0, 0.0, "flat", 0.0, False, len(df))

        # 1. Base Regimes via ADX & EMA
        adx_df = self._calculate_adx(df, self.adx_period)

        curr_adx = float(adx_df["adx"].iloc[-1])
        prev_adx_3 = adx_df["adx"].iloc[-4:-1].mean()  # Rata-rata 3 candle lalu

        adx_trend_str = "flat"
        if curr_adx > prev_adx_3 + 1.0:
            adx_trend_str = "rising"
        elif curr_adx < prev_adx_3 - 1.0:
            adx_trend_str = "falling"

        # EMA
        ema = df["close"].ewm(span=self.ema_period, adjust=False).mean()
        curr_ema = float(ema.iloc[-1])
        curr_close = float(df["close"].iloc[-1])
        ema_dist = ((curr_close - curr_ema) / curr_ema) * 100.0

        # Classification rules
        if curr_adx > self.adx_strong and curr_close > curr_ema:
            regime = MarketRegime.STRONG_TREND_UP
        elif self.adx_weak <= curr_adx <= self.adx_strong and curr_close > curr_ema:
            regime = MarketRegime.WEAK_TREND_UP
        elif self.adx_weak <= curr_adx <= self.adx_strong and curr_close < curr_ema:
            regime = MarketRegime.WEAK_TREND_DOWN
        elif curr_adx > self.adx_strong and curr_close < curr_ema:
            regime = MarketRegime.STRONG_TREND_DOWN
        else:
            regime = MarketRegime.SIDEWAYS

        # 2. Volatility Override
        if current_atr_percentile >= self.high_vol_percentile:
            regime = MarketRegime.HIGH_VOLATILITY

        # 3. Track Stability
        self.last_regimes.append(regime)
        if len(self.last_regimes) > self.STABILITY_PERIOD:
            self.last_regimes.pop(0)

        is_stable_trend = False
        if len(self.last_regimes) == self.STABILITY_PERIOD:
            if all(r == regime for r in self.last_regimes):
                is_stable_trend = True

        # 4. Confidence
        conf = self._calculate_confidence(curr_adx, ema_dist, adx_trend_str, is_stable_trend)

        return RegimeResult(
            regime=regime,
            confidence=conf,
            adx=curr_adx,
            adx_trend=adx_trend_str,
            ema50_distance=ema_dist,
            is_stable=is_stable_trend,
            lookback_candles=len(df),
        )

    def is_regime_change(self, prev: MarketRegime, curr: MarketRegime) -> bool:
        if prev == curr:
            return False

        up_trends = [MarketRegime.STRONG_TREND_UP, MarketRegime.WEAK_TREND_UP]
        down_trends = [MarketRegime.STRONG_TREND_DOWN, MarketRegime.WEAK_TREND_DOWN]

        # Pindah derajat namun satu arah tidak dihitung pergeseran drastis
        if prev in up_trends and curr in up_trends:
            return False
        if prev in down_trends and curr in down_trends:
            return False

        return True
