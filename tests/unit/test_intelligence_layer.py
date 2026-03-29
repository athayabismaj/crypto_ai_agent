"""
Tests for Intelligence Layer components.
"""

from datetime import UTC, datetime, timedelta

import pandas as pd  # type: ignore
import pytest

from runtime.agent.data_layer.anomaly_detector import AnomalyDetector  # type: ignore
from runtime.agent.intelligence_layer.latency_guard import LatencyGuard  # type: ignore
from runtime.agent.intelligence_layer.market_state import MarketStateBuilder  # type: ignore
from runtime.agent.intelligence_layer.regime import RegimeClassifier  # type: ignore
from runtime.agent.intelligence_layer.volatility import VolatilityCalculator  # type: ignore
from runtime.agent.models.enums import MarketRegime  # type: ignore
from runtime.agent.models.market import (  # type: ignore
    Candle,
    Orderbook,
    OrderbookLevel,
    PortfolioState,
    Ticker,
)


@pytest.fixture
def mock_df() -> pd.DataFrame:
    """Generate 250 candles df."""
    dates = [datetime.now(UTC) - timedelta(hours=i) for i in range(250, 0, -1)]
    df = pd.DataFrame(
        {
            "timestamp": dates,
            "open": [100 + i * 0.1 for i in range(250)],
            "high": [101 + i * 0.1 for i in range(250)],
            "low": [99 + i * 0.1 for i in range(250)],
            "close": [100.5 + i * 0.1 for i in range(250)],
            "volume": [1000] * 250,
        }
    )
    return df


def test_volatility_calculator(mock_df: pd.DataFrame):
    calc = VolatilityCalculator()
    metrics = calc.calculate(mock_df, "BTCUSDT", "1h")

    assert metrics.symbol == "BTCUSDT"
    # Given the mock data is an artificial constant steady trend, volatility should be low
    assert metrics.atr > 0
    assert metrics.atr_pct > 0


def test_regime_classifier(mock_df: pd.DataFrame):
    clf = RegimeClassifier()
    # It takes 200 candles to classify
    res = clf.classify(mock_df, atr_percentile=50.0)

    # Should be STRONG_TREND_UP as price strictly goes up
    assert res.regime in (MarketRegime.STRONG_TREND_UP, MarketRegime.WEAK_TREND_UP)


def test_latency_guard():
    guard = LatencyGuard()

    now = datetime.now(UTC)
    guard.record_rest_latency(500)
    guard.record_ws_latency(now, now + timedelta(milliseconds=400))
    guard.record_order_latency(now, now + timedelta(milliseconds=1500))

    status = guard.get_status()
    assert status.overall_status == "ok"
    assert not status.should_block_trading

    # TRIGGER CRITICAL
    guard.record_rest_latency(6000)
    status_crit = guard.get_status()
    assert status_crit.overall_status == "critical"
    assert status_crit.should_block_trading

    should_block, reason = guard.should_block_order()
    assert should_block
    assert "REST latency EXTREME" in reason


@pytest.mark.asyncio
async def test_market_state_builder(mock_df: pd.DataFrame):
    clf = RegimeClassifier()
    calc = VolatilityCalculator()
    anom = AnomalyDetector()
    lg = LatencyGuard()
    builder = MarketStateBuilder(clf, calc, anom, None, lg)

    candle = Candle(
        symbol="BTCUSDT",
        timeframe="1h",
        timestamp=datetime.now(UTC),
        open=100.0,
        high=101.0,
        low=99.0,
        close=100.5,
        volume=1000,
    )
    ticker = Ticker(
        symbol="BTCUSDT",
        bid=100.0,
        ask=100.5,
        last=100.5,
        mid=100.25,
        spread=0.5,
        spread_pct=0.5,
        timestamp=datetime.now(UTC),
    )
    ob = Orderbook(
        symbol="BTCUSDT",
        bids=[OrderbookLevel(price=100.0, quantity=1.0)],
        asks=[OrderbookLevel(price=100.5, quantity=1.0)],
        timestamp=datetime.now(UTC),
    )
    port = PortfolioState(
        total_equity=10000, available_equity=10000, daily_pnl=0, open_positions={}
    )

    state = await builder.build(candle, ticker, ob, mock_df, port)
    assert state.symbol == "BTCUSDT"
    assert state.is_safe_to_trade
    assert state.latency_ms >= 0
    assert state.data_quality == "good"
