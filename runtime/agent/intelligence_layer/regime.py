"""
Market Regime Classification.

Mengklasifikasikan kondisi market:
- STRONG_TREND_UP, WEAK_TREND_UP
- SIDEWAYS
- WEAK_TREND_DOWN, STRONG_TREND_DOWN
- HIGH_VOLATILITY (override)
- UNDEFINED
"""

import pandas as pd  # type: ignore

from runtime.agent.models.enums import MarketRegime
from runtime.agent.models.market import RegimeResult


class RegimeClassifier:
    """Classifier rejim pasar berbasis ADX dan jarak EMA."""

    def __init__(self) -> None:
        """Inisialisasi konstanta."""
        self.adx_period = 14
        self.ema_trend_period = 50
        self.adx_strong_threshold = 30
        self.adx_weak_threshold = 20
        self.high_vol_percentile = 85.0
        self.stability_candles = 5
        self.min_history_candles = 200

    def classify(self, df: pd.DataFrame, atr_percentile: float = 0.0) -> RegimeResult:
        """Klasifikasi regimen pasar utama."""
        if df is None or len(df) < self.min_history_candles:
            return RegimeResult(regime=MarketRegime.UNDEFINED)

        adx_series, dip_series, dim_series = self._calculate_adx(df, self.adx_period)
        ema50_series = df["close"].ewm(span=self.ema_trend_period, adjust=False).mean()

        current_adx = float(adx_series.iloc[-1])
        current_close = float(df["close"].iloc[-1])
        current_ema50 = float(ema50_series.iloc[-1])

        # Hitung sejarah ADX slope
        adx_sma = adx_series.rolling(3).mean()
        if adx_sma.iloc[-1] > adx_sma.iloc[-2]:
            adx_trend = "rising"
        elif adx_sma.iloc[-1] < adx_sma.iloc[-2]:
            adx_trend = "falling"
        else:
            adx_trend = "flat"

        ema50_distance = ((current_close - current_ema50) / current_ema50) * 100

        # Tentukan base regime
        base_regime = self._determine_base_regime(current_adx, current_close, current_ema50)

        # Override Volatilitas
        final_regime = base_regime
        if atr_percentile > self.high_vol_percentile:
            final_regime = MarketRegime.HIGH_VOLATILITY

        # Evaluasi stabilitas (cek 5 candle terakhir)
        is_stable = self._check_stability(df, adx_series, ema50_series, atr_percentile)

        # Hitung confidence score
        confidence = self._calculate_confidence(
            current_adx,
            ema50_distance,
            adx_trend,
            is_stable,
        )

        return RegimeResult(
            regime=final_regime,
            confidence=confidence,
            adx=current_adx,
            adx_trend=adx_trend,
            ema50_distance=ema50_distance,
            is_stable=is_stable,
            lookback_candles=len(df),
        )

    def classify_multi_tf(self, df_h1: pd.DataFrame, df_h4: pd.DataFrame) -> RegimeResult:
        """
        Klasifikasi menggunakan dua timeframe.
        H4 = utama, H1 = konfirmasi.
        Bila bertentangan = Sideways
        """
        res_h4 = self.classify(df_h4, atr_percentile=50.0)  # simplify
        res_h1 = self.classify(df_h1, atr_percentile=50.0)

        # Konflik deteksi tren
        if ("UP" in res_h4.regime.value and "DOWN" in res_h1.regime.value) or (
            "DOWN" in res_h4.regime.value and "UP" in res_h1.regime.value
        ):
            return RegimeResult(
                regime=MarketRegime.SIDEWAYS,
                confidence=min(res_h4.confidence, res_h1.confidence) * 0.5,  # Reduced confidence
            )

        # Mengembalikan dari H4 dengan penyesuaian stabilitas
        return res_h4

    def is_regime_change(self, prev: MarketRegime, curr: MarketRegime) -> bool:
        """Sinyal pergerakan signifikan antara regime."""
        if prev == curr:
            return False
        # Sideways -> Weak is minor. Weak -> Strong is minor.
        # UP -> DOWN is major. Strong UP -> Sideways is major.
        # Pokoknya beda = True
        return True

    def _determine_base_regime(self, adx: float, close: float, ema50: float) -> MarketRegime:
        """Kondisional regime menurut spesifikasi dokumen."""
        if adx < self.adx_weak_threshold:
            return MarketRegime.SIDEWAYS

        if adx >= self.adx_strong_threshold:
            if close > ema50:
                return MarketRegime.STRONG_TREND_UP
            else:
                return MarketRegime.STRONG_TREND_DOWN

        if self.adx_weak_threshold <= adx < self.adx_strong_threshold:
            if close > ema50:
                return MarketRegime.WEAK_TREND_UP
            else:
                return MarketRegime.WEAK_TREND_DOWN

        return MarketRegime.SIDEWAYS

    def _check_stability(
        self,
        df: pd.DataFrame,
        adx_series: pd.Series,
        ema50_series: pd.Series,
        atr_percentile: float,
    ) -> bool:
        """Cek apakah 5 candle terakhir stabil regime-nya."""
        if len(df) < self.stability_candles:
            return False

        last_regimes = []
        for i in range(1, self.stability_candles + 1):
            idx = -i
            adx = float(adx_series.iloc[idx])
            close = float(df["close"].iloc[idx])
            ema50 = float(ema50_series.iloc[idx])
            br = self._determine_base_regime(adx, close, ema50)
            if atr_percentile > self.high_vol_percentile:
                br = MarketRegime.HIGH_VOLATILITY
            last_regimes.append(br)

        # Stable jika semua item dalam list tersebut sama
        return len(set(last_regimes)) == 1

    def _calculate_confidence(
        self, adx: float, ema_distance: float, adx_trend: str, stability: bool
    ) -> float:
        """Menghitung skor probabilitas ketepatan dari sinyal ADX."""
        score = 0.0

        if adx > 40:
            score += 0.40
        elif adx > 30:
            score += 0.30
        elif adx > 20:
            score += 0.20
        else:
            score += 0.05

        score += min(abs(ema_distance) / 5.0, 0.30)

        if adx_trend == "rising":
            score += 0.20
        elif adx_trend == "flat":
            score += 0.10

        if stability:
            score += 0.10

        return min(score, 1.0)

    def _calculate_adx(
        self, df: pd.DataFrame, period: int = 14
    ) -> tuple[pd.Series, pd.Series, pd.Series]:
        """Wilder's ADX Calculation."""
        high = df["high"]
        low = df["low"]
        close = df["close"]

        tr1 = high - low
        tr2 = (high - close.shift(1)).abs()
        tr3 = (low - close.shift(1)).abs()
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

        up_move = high - high.shift(1)
        down_move = low.shift(1) - low

        plus_dm = pd.Series(0.0, index=df.index)
        minus_dm = pd.Series(0.0, index=df.index)

        plus_dm[(up_move > down_move) & (up_move > 0)] = up_move
        minus_dm[(down_move > up_move) & (down_move > 0)] = down_move

        # Wilder's Smoothing
        def rma(series: pd.Series, window: int) -> pd.Series:
            return series.ewm(alpha=1 / window, min_periods=window, adjust=False).mean()

        atr = rma(tr, period)
        plus_di = 100 * (rma(plus_dm, period) / atr)
        minus_di = 100 * (rma(minus_dm, period) / atr)

        dx = 100 * ((plus_di - minus_di).abs() / (plus_di + minus_di).abs())  # type: ignore[attr-defined]
        adx = rma(dx, period)

        # fillna untuk menghindari NA
        return adx.fillna(0.0), plus_di.fillna(0.0), minus_di.fillna(0.0)  # type: ignore[attr-defined]
