"""
Unit test for risk layer components. Target 100% branch coverage.
"""


import pytest

from runtime.agent.models.enums import CircuitState  # type: ignore
from runtime.agent.models.risk import TradeRequest  # type: ignore
from runtime.agent.risk_layer.circuit_breaker import CircuitBreaker
from runtime.agent.risk_layer.exposure_control import ExposureControl
from runtime.agent.risk_layer.leverage_control import LeverageControl
from runtime.agent.risk_layer.position_size import PositionSizer
from runtime.agent.risk_layer.pre_trade_check import PreTradeCheck
from runtime.agent.risk_layer.risk_manager import RiskManager
from runtime.agent.risk_layer.stoploss import StopLossManager


class DummyConfig:
    def __init__(self):
        self.allow_market_order = True
        self.require_client_order_id = True
        self.max_spread_pct = 0.01
        self.min_qty = 1e-8
        self.max_daily_loss_pct = 0.05
        self.max_drawdown_pct = 0.10
        self.max_consecutive_losses = 5
        self.halt_duration_minutes = 60
        self.warn_daily_loss_pct = 0.03
        self.global_max_leverage = 5
        self.max_open_positions = 5
        self.max_exposure_per_symbol_pct = 0.20
        self.max_total_exposure_pct = 0.80
        self.max_correlated_exposure_pct = 0.35
        self.sizing_method = "fixed_fractional"
        self.risk_per_trade_pct = 0.01
        self.max_position_pct = 0.10
        self.kelly_fraction = 0.5
        self.atr_multiplier = 2.0
        self.default_sl_pct = 0.02
        self.require_sl = True
        self.min_sl_distance_pct = 0.003
        self.max_sl_distance_pct = 0.15
        self.sl_atr_mult = 1.5
        self.max_sl_atr_mult = 4.0
        self.paper_mode = False


@pytest.fixture
def config():
    return DummyConfig()


@pytest.fixture
def empty_portfolio():
    return {
        "equity": 10000.0,
        "open_positions": {},
        "exchange_status": "normal",
        "market_halted": False,
        "last_price_BTCUSDT": 100000.0,
        "atr_BTCUSDT": 1500.0,
        "spread_pct_BTCUSDT": 0.001,
    }


def test_l1_pre_trade_check(config, empty_portfolio):
    checker = PreTradeCheck(config)

    # OK
    req = TradeRequest(
        symbol="BTCUSDT", side="BUY", quantity=0.1, price=100000.0, client_order_id="VALID_ID_1"
    )
    ok, msg = checker.validate(req, empty_portfolio)
    assert ok is True

    # QTY terlalu kecil
    req_qty = TradeRequest(
        symbol="BTCUSDT", side="BUY", quantity=1e-10, price=100000.0, client_order_id="VALID_ID_1"
    )
    ok, msg = checker.validate(req_qty, empty_portfolio)
    assert ok is False
    assert "INVALID_QTY" in msg

    # Market order false
    config.allow_market_order = False
    checker2 = PreTradeCheck(config)
    req_mo = TradeRequest(
        symbol="BTCUSDT", side="BUY", quantity=0.1, price=0.0, client_order_id="VALID_ID_1"
    )
    ok, msg = checker2.validate(req_mo, empty_portfolio)
    assert ok is False


def test_l2_circuit_breaker(config):
    cb = CircuitBreaker(config)
    assert cb.state == CircuitState.NORMAL

    state = cb.check(daily_pnl=-350.0, current_equity=10000.0, peak_equity=10500.0)
    assert state == CircuitState.WARNED  # daily_loss = 3.5%

    state = cb.check(daily_pnl=-600.0, current_equity=10000.0, peak_equity=10500.0)
    assert state == CircuitState.HALTED  # daily_loss = 6%


def test_l3_leverage_control(config):
    lc = LeverageControl(config)
    # BTC Max = 10, Global = 5. Effective = 5
    ok, msg = lc.validate(6, "BTCUSDT")
    assert ok is False

    ok, msg = lc.validate(4, "BTCUSDT")
    assert ok is True


def test_l4_exposure_control(config):
    ec = ExposureControl(config)
    req = TradeRequest(symbol="BTCUSDT", side="BUY", quantity=1.0, price=50000.0)
    # Equity 10000. Buy 50000 = 5.0 (500%). Limit max_per_symbol 20%
    portfolio = {"equity": 10000.0, "open_positions": {}}
    ok, msg = ec.validate(req, portfolio)
    assert ok is False
    assert "500.0%" in msg


def test_l5_position_size(config):
    config.max_position_pct = 0.50  # Prevent cap during test
    ps = PositionSizer(config)
    req = TradeRequest(
        symbol="BTCUSDT", side="BUY", quantity=0.0, price=100000.0, suggested_sl=95000.0
    )
    # Fixed fractional: risk = 0.01 * 10000 = 100. Stop dist = 5000. Qty = 100/5000 = 0.02
    qty, warn, risk = ps.calculate(req, 10000.0, {})
    assert qty == 0.02


def test_l6_stoploss(config):
    slm = StopLossManager(config)
    req = TradeRequest(
        symbol="BTCUSDT", side="BUY", quantity=0.1, suggested_sl=99000.0, price=100000.0
    )
    ok, msg, sl = slm.validate_and_compute(req, {})
    assert ok is True  # SL 1%, min 0.3%, max 15%

    req_no_sl = TradeRequest(
        symbol="BTCUSDT", side="BUY", quantity=0.1, suggested_sl=0.0, price=100000.0
    )
    ok, msg, sl = slm.validate_and_compute(req_no_sl, {})
    assert ok is False  # Default require sl
    assert sl > 0


def test_l7_risk_manager_integration(config, empty_portfolio):
    rm = RiskManager(config)
    req = TradeRequest(
        symbol="BTCUSDT",
        side="BUY",
        quantity=0.01,
        suggested_sl=99000.0,
        price=100000.0,
        client_order_id="TESTID",
    )
    # Equity 10K. Max position 10% = req notional max 1K. Target qty = 0.01 (limit by lot) / position sizer max cap
    result = rm.evaluate(req, empty_portfolio)
    assert result.is_approved is True
    assert result.approved_quantity > 0
    assert result.sl_price == 99000.0
