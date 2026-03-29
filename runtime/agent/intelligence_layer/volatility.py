"""
Volatility Calculator untuk Intelligence Layer.

Mengukur:
- ATR (Average True Range)
- Realized Volatility
- Bollinger Bands
- Volatility Regime
"""

import math
from datetime import UTC, datetime

import pandas as pd  # type: ignore

from runtime.agent.models.market import VolatilityMetrics


class VolatilityCalculator:
    """Kalkulator metrik volatilitas dari OHLCV."""

    def __init__(self) -> None:
        """Inisialisasi konfigurasi."""
        self.atr_period = 14
        self.atr_lookback = 252
        self.realized_vol_window = 20
        self.bb_period = 20
        self.bb_std_dev = 2.0
        self.high_vol_percentile = 75.0
        self.extreme_vol_percentile = 90.0

    def calculate(self, df: pd.DataFrame, symbol: str, timeframe: str) -> VolatilityMetrics:
        """Hitung semua metrik volatilitas."""
        if len(df) < max(
            self.atr_lookback + self.atr_period, self.bb_period, self.realized_vol_window
        ):
            # Not enough data for full lookback percentile, but we'll do our best
            # Returning zeros or partial if not enough data.
            pass

        # Ensure we have data
        if df.empty:
            return VolatilityMetrics(
                symbol=symbol,
                timeframe=timeframe,
                timestamp=datetime.now(UTC),
            )

        atr = self.get_atr(df, period=self.atr_period)
        atr_pct = self.get_atr_pct(df, period=self.atr_period)
        atr_percentile = self.get_atr_percentile(
            df, period=self.atr_period, lookback=self.atr_lookback
        )

        realized_vol_20 = self.get_realized_vol(df, window=self.realized_vol_window, tf=timeframe)
        realized_vol_5 = self.get_realized_vol(df, window=5, tf=timeframe)

        # Volatility regime logic
        vol_regime = "normal"
        if atr_percentile >= self.extreme_vol_percentile:
            vol_regime = "extreme"
        elif atr_percentile >= self.high_vol_percentile:
            vol_regime = "high"
        elif atr_percentile < 25.0:
            vol_regime = "low"

        # Bollinger Bands width & percentile
        bb_width, bb_percentile = self._get_bb_metrics(
            df, period=self.bb_period, std_dev=self.bb_std_dev
        )

        return VolatilityMetrics(
            symbol=symbol,
            timeframe=timeframe,
            timestamp=datetime.now(UTC),
            atr=atr,
            atr_pct=atr_pct,
            atr_percentile=atr_percentile,
            realized_vol_20=realized_vol_20,
            realized_vol_5=realized_vol_5,
            vol_regime=vol_regime,
            bb_width=bb_width,
            bb_percentile=bb_percentile,
        )

    def get_atr(self, df: pd.DataFrame, period: int = 14) -> float:
        """ATR absolut terakhir."""
        if len(df) < period:
            return 0.0

        high = df["high"]
        low = df["low"]
        close_prev = df["close"].shift(1)

        tr1 = high - low
        tr2 = (high - close_prev).abs()
        tr3 = (low - close_prev).abs()

        true_range = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        atr_series = true_range.rolling(window=period).mean()

        return float(atr_series.iloc[-1]) if not pd.isna(atr_series.iloc[-1]) else 0.0

    def get_atr_pct(self, df: pd.DataFrame, period: int = 14) -> float:
        """ATR dalam persentase harga close terkini."""
        atr = self.get_atr(df, period)
        if atr == 0.0:
            return 0.0
        close_price = df["close"].iloc[-1]
        if close_price == 0:
            return 0.0
        return float((atr / close_price) * 100)

    def get_atr_percentile(self, df: pd.DataFrame, period: int = 14, lookback: int = 252) -> float:
        """Percentile dari ATR terkini dibandingkan historical lookback."""
        if len(df) < period + lookback:
            # Fallback jika data tidak cukup
            lookback = len(df) - period
            if lookback <= 0:
                return 50.0  # Default neutral

        high = df["high"]
        low = df["low"]
        close_prev = df["close"].shift(1)

        tr1 = high - low
        tr2 = (high - close_prev).abs()
        tr3 = (low - close_prev).abs()

        true_range = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        atr_series = true_range.rolling(window=period).mean().dropna()

        if len(atr_series) == 0:
            return 50.0

        history = atr_series.tail(lookback)
        current_atr = history.iloc[-1]

        # Calculate rank as percentile
        percent_rank = (history < current_atr).mean() * 100
        return float(percent_rank)

    def get_realized_vol(
        self, df: pd.DataFrame, window: int = 20, tf: str = "1h", annualize: bool = True
    ) -> float:
        """Annualized realized volatility dari log returns."""
        import numpy as np

        if len(df) < window + 1:
            return 0.0

        log_returns = np.log(df["close"] / df["close"].shift(1))
        rolling_std = log_returns.rolling(window=window).std()

        val = rolling_std.iloc[-1]
        if pd.isna(val):
            return 0.0

        if annualize:
            candles_per_day_map = {
                "1m": 1440,
                "5m": 288,
                "15m": 96,
                "1h": 24,
                "4h": 6,
                "1d": 1,
            }
            # Jika tf tidak ada di map, anggap "1h"
            candles_per_day = candles_per_day_map.get(tf, 24)
            factor = math.sqrt(252 * candles_per_day)
            val *= factor

        return float(val)

    def _get_bb_metrics(
        self, df: pd.DataFrame, period: int = 20, std_dev: float = 2.0
    ) -> tuple[float, float]:
        """Menghitung bollinger band width dan historical percentile 100 candle."""
        if len(df) < period:
            return 0.0, 0.0

        close = df["close"]
        sma = close.rolling(window=period).mean()
        std = close.rolling(window=period).std()

        upper = sma + (std * std_dev)
        lower = sma - (std * std_dev)

        bb_widths = (upper - lower) / sma
        current_width = float(bb_widths.iloc[-1]) if not pd.isna(bb_widths.iloc[-1]) else 0.0

        bb_widths_clean = bb_widths.dropna()
        percentile = 0.0
        if len(bb_widths_clean) > 0:
            history = bb_widths_clean.tail(100)  # Standardize on 100 lookback for width percentile
            percentile = float((history < current_width).mean() * 100)

        return current_width, percentile
