"""
Market State Builder.

Merangkum dan memadukan parameter intelligence ke dalam satu MarketState obj.
"""

from typing import Any

import pandas as pd  # type: ignore

from runtime.agent.data_layer.anomaly_detector import AnomalyDetector
from runtime.agent.intelligence_layer.latency_guard import LatencyGuard
from runtime.agent.intelligence_layer.regime import RegimeClassifier
from runtime.agent.intelligence_layer.volatility import VolatilityCalculator
from runtime.agent.models.market import (
    Candle,
    MarketState,
    Orderbook,
    PortfolioState,
    Ticker,
)
from runtime.agent.utils.time_utils import utcnow


class RuntimeFeatureEngine:
    """Mock stub untuk pipeline yang menormalisasi DataFrame."""

    def __init__(self, metadata: dict[str, Any] | None = None) -> None:
        pass

    def compute(self, df: pd.DataFrame) -> pd.Series:
        # Mock compute returns series corresponding to the last row index.
        # Ideally it calls research layer models
        obj = df.iloc[-1].copy() if not df.empty else pd.Series()
        return obj


class MarketStateBuilder:
    """Orkestrator untuk merakit state dengan metrics lengkap."""

    def __init__(
        self,
        regime_clf: RegimeClassifier,
        vol_calc: VolatilityCalculator,
        anomaly_det: AnomalyDetector,
        feature_eng: RuntimeFeatureEngine | None,
        latency_guard: LatencyGuard,
    ) -> None:
        self.regime_clf = regime_clf
        self.vol_calc = vol_calc
        self.anomaly_det = anomaly_det
        self.feature_eng = feature_eng or RuntimeFeatureEngine()
        self.latency_guard = latency_guard

        # Store last state in memory per symbol.
        self._last_state_by_symbol: dict[str, MarketState] = {}

    async def build(
        self,
        candle: Candle,
        ticker: Ticker,
        orderbook: Orderbook,
        candles_df: pd.DataFrame,
        portfolio: PortfolioState,
    ) -> MarketState:
        """
        Urutkan pembuatan state:
        1. Validasi / Quality checks (is_safe_to_trade dari anomaly_det, latency_guard checks).
        2. Hitung Volatility
        3. Klasifikasi Regime (dengan volatility)
        4. Cek Anomaly
        5. Feature Engine
        6. Latency state update via Ticker lag approx
        """
        # Step 2: Volatility
        vol_metrics = self.vol_calc.calculate(
            candles_df, symbol=candle.symbol, timeframe=candle.timeframe
        )

        # Step 3: Regime
        regime_res = self.regime_clf.classify(candles_df, vol_metrics.atr_percentile)

        # Step 4: Anomaly
        is_safe_to_trade, active_anomalies_msg = self.anomaly_det.is_safe_to_trade(candle.symbol)
        active_anomalies = self.anomaly_det.get_active_anomalies(candle.symbol)

        # Step 5: FeatureEngine
        features_s = self.feature_eng.compute(candles_df)

        # Step 6: Data Quality Logic
        latency_stat = self.latency_guard.get_status()

        data_quality = "good"
        if latency_stat.overall_status in ("critical", "high") or not is_safe_to_trade:
            data_quality = "bad"
        elif latency_stat.overall_status == "degraded" or any(
            a.severity == "MEDIUM" for a in active_anomalies
        ):
            data_quality = "degraded"

        if data_quality == "bad":
            is_safe_to_trade = False

        state = MarketState(
            symbol=candle.symbol,
            timeframe=candle.timeframe,
            timestamp=utcnow(),
            last_price=candle.close,
            bid=ticker.bid,
            ask=ticker.ask,
            mid=ticker.mid,
            spread_pct=ticker.spread_pct,
            volume_24h=ticker.volume_24h,
            orderbook_imbalance=(
                orderbook.get_imbalance() if hasattr(orderbook, "get_imbalance") else 0.0
            ),
            latest_candle=candle,
            candles_df=candles_df,
            regime=regime_res.regime,
            regime_confidence=regime_res.confidence,
            regime_stable=regime_res.is_stable,
            vol_metrics=vol_metrics,
            features=features_s,
            open_positions=portfolio.open_positions,
            available_equity=portfolio.available_equity,
            equity=portfolio.total_equity,
            daily_pnl=portfolio.daily_pnl,
            active_anomalies=active_anomalies,
            is_safe_to_trade=is_safe_to_trade,
            data_quality=data_quality,
            latency_ms=latency_stat.tick_latency_ms,
        )

        self._last_state_by_symbol[candle.symbol] = state
        return self._last_state_by_symbol[candle.symbol]

    def get_last_state(self, symbol: str) -> MarketState | None:
        """Mendapatkan caching dari memori."""
        return self._last_state_by_symbol.get(symbol)

    def is_state_fresh(self, symbol: str, max_age_s: int = 5) -> bool:
        """Cek kelayakan umur state memori ini."""
        state = self.get_last_state(symbol)
        if state is None:
            return False
        age_s = (utcnow() - state.timestamp).total_seconds()
        return age_s <= max_age_s
