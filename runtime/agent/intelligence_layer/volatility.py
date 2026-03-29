"""
Layer Intelligence: Volatility Engine
Pengukur Tensi (Volatility) Market melalui metode Realized Volatility 
serta percentile historikal True Range (ATR).
"""

import math
from dataclasses import dataclass, field
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from runtime.agent.core.config_schema import AgentConfig  # type: ignore


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class VolatilityMetrics:
    symbol: str
    timeframe: str
    timestamp: datetime = field(default_factory=utcnow)

    # ATR
    atr: float = 0.0
    atr_pct: float = 0.0
    atr_percentile: float = 0.0

    # Realized volatility
    realized_vol_20: float = 0.0
    realized_vol_5: float = 0.0

    # Volatility regime
    vol_regime: str = "normal"  # low | normal | high | extreme

    # BB
    bb_width: float = 0.0
    bb_percentile: float = 0.0


class VolatilityCalculator:
    """Mesin pengukur Volatilitas Kuantitatif Absolut."""

    def __init__(self, config: AgentConfig) -> None:
        self.config = config
        self.atr_period = getattr(config, "strat_atr_period", 14)
        self.atr_lookback = getattr(config, "strat_atr_lookback", 252)
        self.rv_window = getattr(config, "strat_realized_vol_window", 20)
        self.high_vol_percentile = getattr(config, "strat_high_vol_percentile", 75.0)
        self.extreme_vol_percentile = getattr(config, "strat_extreme_vol_percentile", 90.0)

    def _get_candles_per_day(self, tf: str) -> int:
        tf_map = {
            "1m": 1440,
            "3m": 480,
            "5m": 288,
            "15m": 96,
            "30m": 48,
            "1h": 24,
            "2h": 12,
            "4h": 6,
            "8h": 3,
            "12h": 2,
            "1d": 1,
        }
        return tf_map.get(tf, 24)

    def _calc_tr_numpy(self, high: np.ndarray, low: np.ndarray, close: np.ndarray) -> np.ndarray:
        """Hitung True Range secara vektor (sangat cepat)."""
        tr = np.zeros(len(close))
        tr[0] = high[0] - low[0]
        for i in range(1, len(close)):
            tr[i] = max(high[i] - low[i], abs(high[i] - close[i - 1]), abs(low[i] - close[i - 1]))
        return tr

    def _calc_atr_numpy(self, tr: np.ndarray, period: int) -> np.ndarray:
        atr = np.zeros(len(tr))
        atr[0] = tr[0]
        # Wilders Smoothing (RMA)
        for i in range(1, len(tr)):
            atr[i] = (atr[i - 1] * (period - 1) + tr[i]) / period
        return atr

    def calculate(self, df: pd.DataFrame, symbol: str, timeframe: str) -> VolatilityMetrics:
        """Kalkulasi tereksekusi pada frame OHLCV Pandas namun dioverlay ke Vector numpy."""

        if len(df) < max(self.atr_lookback, self.rv_window):
            return VolatilityMetrics(symbol=symbol, timeframe=timeframe)

        # Ekstrak vector
        high_vec = df["high"].to_numpy()
        low_vec = df["low"].to_numpy()
        close_vec = df["close"].to_numpy()

        # 1. TR & ATR Calculation
        tr = self._calc_tr_numpy(high_vec, low_vec, close_vec)
        atr = self._calc_atr_numpy(tr, self.atr_period)

        curr_atr = float(atr[-1])
        curr_close = float(close_vec[-1])
        curr_atr_pct = (curr_atr / curr_close) * 100 if curr_close > 0 else 0.0

        # 2. ATR Percentile
        # Ambil subset lookback history (misal 252 lilin)
        history_atr = atr[-self.atr_lookback :]
        # Real percentile calculation without scipy:
        sorted_atr = np.sort(history_atr)
        idx = np.searchsorted(sorted_atr, curr_atr)
        curr_percentile = (idx / len(sorted_atr)) * 100.0

        # 3. Regime Threshold
        vol_reg = "normal"
        if curr_percentile >= self.extreme_vol_percentile:
            vol_reg = "extreme"
        elif curr_percentile >= self.high_vol_percentile:
            vol_reg = "high"
        elif curr_percentile < 25.0:
            vol_reg = "low"

        # 4. Realized Volatility
        returns = pd.Series(close_vec).apply(np.log).diff().dropna()
        factor = math.sqrt(252 * self._get_candles_per_day(timeframe))

        if len(returns) >= 20:
            rv_20 = float(returns.tail(20).std() * factor)
        else:
            rv_20 = 0.0

        if len(returns) >= 5:
            rv_5 = float(returns.tail(5).std() * factor)
        else:
            rv_5 = 0.0

        # 5. BB Width
        ma = pd.Series(close_vec).rolling(20).mean()
        std = pd.Series(close_vec).rolling(20).std()
        upper = ma + (2.0 * std)
        lower = ma - (2.0 * std)

        width_series = (upper - lower) / ma
        curr_width = (
            float(width_series.iloc[-1])
            if not width_series.empty and not pd.isna(width_series.iloc[-1])
            else 0.0
        )

        bb_history = width_series.tail(100).dropna().to_numpy()
        if len(bb_history) > 0:
            sorted_bb = np.sort(bb_history)
            idx_bb = np.searchsorted(sorted_bb, curr_width)
            curr_bb_percentile = (idx_bb / len(sorted_bb)) * 100.0
        else:
            curr_bb_percentile = 0.0

        return VolatilityMetrics(
            symbol=symbol,
            timeframe=timeframe,
            timestamp=utcnow(),
            atr=curr_atr,
            atr_pct=curr_atr_pct,
            atr_percentile=curr_percentile,
            realized_vol_20=rv_20,
            realized_vol_5=rv_5,
            vol_regime=vol_reg,
            bb_width=curr_width,
            bb_percentile=curr_bb_percentile,
        )
