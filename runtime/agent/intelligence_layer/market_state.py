"""
Layer Intelligence: Market State
Merakit State Market dari potongan-potongan data Mentah + Engine Kuantitatif
Menjadi satu objek tunggal deterministik (`MarketState`) penyuplai Model Eksekusi.
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import pandas as pd

from runtime.agent.core.config_schema import AgentConfig  # type: ignore
from runtime.agent.data_layer.anomaly_detector import AnomalyDetector, AnomalyReport
from runtime.agent.data_layer.market import Candle, Ticker
from runtime.agent.data_layer.orderbook import Orderbook
from runtime.agent.intelligence_layer.latency_guard import LatencyGuard
from runtime.agent.intelligence_layer.regime import MarketRegime, RegimeClassifier
from runtime.agent.intelligence_layer.volatility import VolatilityCalculator, VolatilityMetrics

logger = logging.getLogger(__name__)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class MarketState:
    # Identitas
    symbol: str
    timeframe: str
    timestamp: datetime

    # Price & Market
    last_price: float
    bid: float
    ask: float
    mid: float
    spread_pct: float
    volume_24h: float
    orderbook_imbalance: float

    # Candle Data
    latest_candle: Candle
    candles_df: pd.DataFrame

    # Intelligence
    regime: MarketRegime
    regime_confidence: float
    regime_stable: bool
    vol_metrics: VolatilityMetrics

    # Portfolio Context
    open_positions: dict[str, Any]
    available_equity: float
    equity: float
    daily_pnl: float

    # Anomaly & Safety
    active_anomalies: list[AnomalyReport]
    is_safe_to_trade: bool

    # Metadata
    data_quality: str
    latency_ms: float

    # Features Engine (Optional/Reserved for ML Vector)
    features: pd.Series | None = None

    @property
    def atr(self) -> float:
        return self.vol_metrics.atr

    @property
    def atr_percentile(self) -> float:
        return self.vol_metrics.atr_percentile

    @property
    def vol_regime(self) -> str:
        return self.vol_metrics.vol_regime


class MarketStateBuilder:
    """The Nexus: Penggabung seluruh sensor data menjadi MarketState."""

    def __init__(
        self,
        config: AgentConfig,
        regime_clf: RegimeClassifier,
        vol_calc: VolatilityCalculator,
        anomaly_det: AnomalyDetector,
        latency_guard: LatencyGuard,
        feature_eng: Any = None,
    ) -> None:
        self.config = config
        self.regime_clf = regime_clf
        self.vol_calc = vol_calc
        self.anomaly_det = anomaly_det
        self.latency_guard = latency_guard
        self.feature_eng = feature_eng

        self._last_states: dict[str, MarketState] = {}

    async def build(
        self,
        candle: Candle,
        ticker: Ticker,
        orderbook: Orderbook,
        candles_df: pd.DataFrame,
        portfolio: Any,
    ) -> MarketState:
        t_start = utcnow()

        # 1. Anomaly Check
        curr_anomalies = self.anomaly_det.check_candle(candle, [])
        curr_anomalies.extend(self.anomaly_det.check_ticker(ticker))
        curr_anomalies.extend(self.anomaly_det.check_orderbook(orderbook))

        safe, anomalies = self.anomaly_det.is_safe_to_trade(candle.symbol)

        # 2. Volatility Engine
        vol = self.vol_calc.calculate(candles_df, candle.symbol, candle.timeframe)

        # 3. Regime Engine
        reg_res = self.regime_clf.classify(candles_df, current_atr_percentile=vol.atr_percentile)

        # 4. Latency Check
        block_trade, block_reason = self.latency_guard.should_block_order()
        if block_trade:
            safe = False
            anomalies.append(
                AnomalyReport(
                    "LATENCY_CRITICAL",
                    "HIGH",
                    candle.symbol,
                    block_reason,
                    0.0,
                    0.0,
                    recommended="Halt",
                )
            )

        # 5. Data Quality Assignment
        has_medium = any(a.severity == "MEDIUM" for a in anomalies)
        if not safe:
            qual = "bad"
        elif has_medium:
            qual = "degraded"
        else:
            qual = "good"

        lat_ms = (utcnow() - t_start).total_seconds() * 1000

        state = MarketState(
            symbol=candle.symbol,
            timeframe=candle.timeframe,
            timestamp=utcnow(),
            last_price=ticker.last,
            bid=ticker.bid,
            ask=ticker.ask,
            mid=ticker.mid,
            spread_pct=ticker.spread_pct,
            volume_24h=ticker.volume_24h,
            orderbook_imbalance=orderbook.get_imbalance(),
            latest_candle=candle,
            candles_df=candles_df,
            regime=reg_res.regime,
            regime_confidence=reg_res.confidence,
            regime_stable=reg_res.is_stable,
            vol_metrics=vol,
            open_positions=portfolio.get("open_positions", {}) if portfolio else {},
            available_equity=portfolio.get("available_equity", 0.0) if portfolio else 0.0,
            equity=portfolio.get("equity", 0.0) if portfolio else 0.0,
            daily_pnl=portfolio.get("daily_pnl", 0.0) if portfolio else 0.0,
            active_anomalies=anomalies,
            is_safe_to_trade=safe,
            data_quality=qual,
            latency_ms=lat_ms,
            features=None,
        )

        self._last_states[candle.symbol] = state
        return state

    def get_last_state(self, symbol: str) -> MarketState | None:
        return self._last_states.get(symbol)

    def is_state_fresh(self, symbol: str, max_age_s: int = 5) -> bool:
        st = self.get_last_state(symbol)
        if not st:
            return False

        return (utcnow() - st.timestamp).total_seconds() <= max_age_s
