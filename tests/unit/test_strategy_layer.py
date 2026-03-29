"""
Mengecek Strategy Layer
"""

import pandas as pd  # type: ignore
import pytest

from runtime.agent.models import (  # type: ignore
    Candle,
    MarketRegime,
    MarketState,
    Position,
    VolatilityMetrics,
)
from runtime.agent.strategy_layer.futures_strategy import FuturesStrategy
from runtime.agent.strategy_layer.spot_strategy import SpotStrategy
from runtime.agent.strategy_layer.strategy_utils import score_signal


class DummyConfig:
    pass


class MockModel:
    def __init__(self, pred: float):
        self.pred = pred

    def predict(self, features):  # type: ignore
        return [self.pred]


@pytest.fixture
def mock_state() -> MarketState:
    df = pd.DataFrame({"close": [100.0, 101.0, 102.0], "volume": [10, 10, 10]})
    return MarketState(
        symbol="BTCUSDT",
        is_safe_to_trade=True,
        latency_ms=10,
        data_quality="good",
        last_price=102.0,
        spread_pct=0.01,
        volume_24h_usd=20000.0,
        orderbook_imbalance=0.1,
        features=df,  # type: ignore
        atr=2.0,
        atr_percentile=50.0,
        realized_vol=10.0,
        bb_width=1.0,
        vol_regime="normal",
        regime=MarketRegime.STRONG_TREND_UP,
        regime_stable=True,
        regime_confidence=0.9,
        timeframe="1h",
        latest_candle=Candle(symbol="BTCUSDT", timeframe="1h", timestamp=0, open=100.0, high=102.0, low=99.0, close=102.0, volume=10.0),  # type: ignore
        vol_metrics=VolatilityMetrics(symbol="BTCUSDT", timeframe="1h", daily_vol=0.0, weekly_vol=0.0, vol_regime="normal", implied_vol=None),  # type: ignore
    )


def test_spot_strategy_allowed(mock_state: MarketState):
    cfg = DummyConfig()
    setattr(cfg, "allow_spot_short", False)
    cfg.min_signal_confidence = 0.1  # type: ignore

    # Prediksi Up = +1%
    model = MockModel(0.01)
    strat = SpotStrategy(cfg, model)
    sig = strat.generate_signal(mock_state)

    assert sig is not None
    assert sig.side == "BUY"
    assert sig.suggested_sl < 102.0
    assert sig.suggested_tp > 102.0


def test_spot_strategy_blocked_short(mock_state: MarketState):
    cfg = DummyConfig()
    setattr(cfg, "allow_spot_short", False)
    # Prediksi Down = -1%
    model = MockModel(-0.01)
    strat = SpotStrategy(cfg, model)
    sig = strat.generate_signal(mock_state)

    # Spot tidak support short
    assert sig is None


def test_futures_strategy_hedge(mock_state: MarketState):
    cfg = DummyConfig()
    setattr(cfg, "hedge_enabled", True)
    setattr(cfg, "hedge_threshold", 0.005)

    pos = Position(symbol="BTCUSDT", side="BUY", quantity=1.0, entry_price=102.0, filled_qty=1.0)  # type: ignore

    model = MockModel(-0.01)
    strat = FuturesStrategy(cfg, model)

    # Hasilkan hedge
    sig = strat.generate_hedge_signal(pos, mock_state)
    assert sig is not None
    assert sig.side == "SELL"
    assert sig.metadata.get("is_hedge") is True


def test_strategy_utils_score():
    score = score_signal(
        pred=0.01,  # Full score (0.4)
        regime=MarketRegime.STRONG_TREND_UP,  # 0.9 * 0.3 = 0.27
        vol_regime="low",  # 1.0 * 0.2 = 0.2
        spread_pct=0.01,
        imbalance=1.0,  # 1.0 * 0.1 = 0.1
    )
    # Harus ~ 0.97
    assert score > 0.90
