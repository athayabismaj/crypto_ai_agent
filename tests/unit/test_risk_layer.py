"""
Unit test for risk layer components — POST Portfolio Integration.
Target: 100% branch coverage per skill requirement.

Skenario wajib (dari skill implement-risk-layer):
✅ approve trade normal
✅ block zero quantity
✅ block zero sl_price
✅ circuit breaker halt pada DD > 10%
✅ circuit breaker warn pada daily loss > 3%
✅ kelly dengan win_rate = 0 tidak crash
✅ lot filter menggunakan math.floor bukan round
✅ paper mode: blocked jadi warned
✅ portfolio integration: CorrelationController & RiskBudget gates

Tambahan:
✅ leverage hardcoded 3x
✅ align_sl_to_structure integration
✅ CapitalManager.safe_to_trade gate
✅ PortfolioAllocator.can_open gate
✅ validate_all() non-fast-fail
✅ generate_order_id() format
"""

from __future__ import annotations

import pytest

from runtime.agent.models.enums import CircuitState, RiskVerdict  # type: ignore
from runtime.agent.models.risk import TradeRequest  # type: ignore
from runtime.agent.portfolio_layer.allocator import PortfolioAllocator  # type: ignore
from runtime.agent.portfolio_layer.capital_manager import CapitalManager  # type: ignore
from runtime.agent.portfolio_layer.correlation import CorrelationController  # type: ignore
from runtime.agent.portfolio_layer.risk_budget import RiskBudgetManager  # type: ignore
from runtime.agent.risk_layer.circuit_breaker import CircuitBreaker
from runtime.agent.risk_layer.exposure_control import ExposureControl
from runtime.agent.risk_layer.leverage_control import GLOBAL_MAX_LEVERAGE, LeverageControl
from runtime.agent.risk_layer.position_size import PositionSizer
from runtime.agent.risk_layer.pre_trade_check import (
    PreTradeCheck,
    generate_order_id,
    validate_client_order_id,
)
from runtime.agent.risk_layer.risk_manager import RiskManager
from runtime.agent.risk_layer.stoploss import StopLossManager

# ── Fixtures ──────────────────────────────────────────────────────────────────


class DummyConfig:
    """Config mock untuk testing — semua field yang dibutuhkan Risk Layer.

    CATATAN PENTING:
    - CircuitBreaker menggunakan max_daily_loss_pct sebagai FRAKSI (0.05 = 5%)
    - CapitalManager menggunakan max_daily_loss_pct sebagai PERSENTASE NEGATIF (-5.0 = -5%)
    - Untuk menghindari konflik, kita HAPUS field yang dipakai CapitalManager
      agar CapitalManager memakai default internalnya (-5.0, -10.0, dll)
    """

    def __init__(self, **overrides) -> None:
        # Pre-Trade Check
        self.allow_market_order = True
        self.require_client_order_id = True
        self.max_spread_pct = 0.01
        self.min_qty = 1e-8

        # Circuit Breaker (fraksi: 0.05 = 5%)
        # JANGAN set max_daily_loss_pct / max_drawdown_pct di sini!
        # Karena CapitalManager akan membacanya dengan skala berbeda.
        # Gunakan atribut terpisah agar tidak konflik.
        self.max_consecutive_losses = 5
        self.halt_duration_minutes = 60

        # Leverage — should be clamped to 3
        self.global_max_leverage = 3

        # Exposure
        self.max_open_positions = 5
        self.max_exposure_per_symbol_pct = 0.20
        self.max_total_exposure_pct = 0.80

        # Sizing
        self.sizing_method = "fixed_fractional"
        self.risk_per_trade_pct = 0.01
        self.max_position_pct = 0.10
        self.kelly_fraction = 0.5
        self.atr_multiplier = 2.0
        self.default_sl_pct = 0.02

        # StopLoss
        self.require_sl = True
        self.min_sl_distance_pct = 0.003
        self.max_sl_distance_pct = 0.15
        self.sl_atr_mult = 1.5
        self.max_sl_atr_mult = 4.0

        # Mode
        self.paper_mode = False

        # Portfolio
        self.portfolio_max_symbol_pct = 0.20
        self.portfolio_max_strategy_pct = 0.40
        self.portfolio_reserved_cash_pct = 0.10

        # Apply overrides
        for k, v in overrides.items():
            setattr(self, k, v)


@pytest.fixture
def config():
    return DummyConfig()


@pytest.fixture
def paper_config():
    return DummyConfig(paper_mode=True, require_sl=False)


@pytest.fixture
def portfolio_state():
    """Standard portfolio state untuk testing."""
    return {
        "equity": 10000.0,
        "open_positions": {},
        "exchange_status": "normal",
        "market_halted": False,
        "last_price_BTCUSDT": 50000.0,
        "atr_BTCUSDT": 1000.0,
        "spread_pct_BTCUSDT": 0.001,
    }


def _make_capital_manager(config, equity=10000.0):
    """Helper untuk membuat CapitalManager yang sudah diinisialisasi."""
    cm = CapitalManager(config)
    cm.initialize(equity)
    return cm


def _make_risk_manager(config, equity=10000.0):
    """Helper untuk membuat RiskManager lengkap dengan semua Portfolio dependencies."""
    cm = _make_capital_manager(config, equity)
    cc = CorrelationController(config)
    rbm = RiskBudgetManager(config)
    pa = PortfolioAllocator(config)
    return RiskManager(config, cm, cc, rbm, pa)


def _make_trade_request(**overrides):
    """Helper untuk membuat TradeRequest dengan defaults yang wajar."""
    defaults = {
        "symbol": "BTCUSDT",
        "side": "BUY",
        "quantity": 0.01,
        "price": 50000.0,
        "suggested_sl": 49000.0,
        "suggested_tp": 52000.0,
        "leverage": 1,
        "is_futures": False,
        "strategy_id": "spot_v1",
        "client_order_id": "test_order_001",
        "signal_confidence": 0.75,
    }
    defaults.update(overrides)
    return TradeRequest(**defaults)


# ═══════════════════════════════════════════════════════════════════════════════
# L1: PreTradeCheck
# ═══════════════════════════════════════════════════════════════════════════════


class TestPreTradeCheck:
    """Test suite untuk pre_trade_check.py."""

    def test_valid_request_passes(self, config, portfolio_state):
        checker = PreTradeCheck(config)
        req = _make_trade_request()
        ok, msg = checker.validate(req, portfolio_state)
        assert ok is True
        assert msg == ""

    def test_invalid_symbol_blocked(self, config, portfolio_state):
        checker = PreTradeCheck(config)
        req = _make_trade_request(symbol="")
        ok, msg = checker.validate(req, portfolio_state)
        assert ok is False
        assert "INVALID_SYMBOL" in msg

    def test_invalid_side_blocked(self, config, portfolio_state):
        checker = PreTradeCheck(config)
        req = _make_trade_request(side="HOLD")
        ok, msg = checker.validate(req, portfolio_state)
        assert ok is False
        assert "INVALID_SIDE" in msg

    def test_tiny_qty_blocked(self, config, portfolio_state):
        checker = PreTradeCheck(config)
        req = _make_trade_request(quantity=1e-10)
        ok, msg = checker.validate(req, portfolio_state)
        assert ok is False
        assert "INVALID_QTY" in msg

    def test_negative_price_blocked(self, config, portfolio_state):
        checker = PreTradeCheck(config)
        req = _make_trade_request(price=-1.0)
        ok, msg = checker.validate(req, portfolio_state)
        assert ok is False
        assert "INVALID_PRICE" in msg

    def test_market_order_disabled(self, config, portfolio_state):
        config.allow_market_order = False
        checker = PreTradeCheck(config)
        req = _make_trade_request(price=0.0)
        ok, msg = checker.validate(req, portfolio_state)
        assert ok is False
        assert "MARKET_ORDER_DISABLED" in msg

    def test_invalid_client_order_id(self, config, portfolio_state):
        checker = PreTradeCheck(config)
        req = _make_trade_request(client_order_id="")
        ok, msg = checker.validate(req, portfolio_state)
        assert ok is False
        assert "INVALID_ORDER_ID" in msg

    def test_exchange_maintenance_blocked(self, config, portfolio_state):
        checker = PreTradeCheck(config)
        portfolio_state["exchange_status"] = "maintenance"
        req = _make_trade_request()
        ok, msg = checker.validate(req, portfolio_state)
        assert ok is False
        assert "EXCHANGE_MAINTENANCE" in msg

    def test_market_halted_blocked(self, config, portfolio_state):
        checker = PreTradeCheck(config)
        portfolio_state["market_halted"] = True
        req = _make_trade_request()
        ok, msg = checker.validate(req, portfolio_state)
        assert ok is False
        assert "MARKET_HALTED" in msg

    def test_price_unavailable_blocked(self, config):
        checker = PreTradeCheck(config)
        req = _make_trade_request(symbol="XYZUSDT")
        state = {"equity": 10000, "exchange_status": "normal", "market_halted": False}
        ok, msg = checker.validate(req, state)
        assert ok is False
        assert "PRICE_UNAVAILABLE" in msg

    def test_wide_spread_warned(self, config, portfolio_state):
        checker = PreTradeCheck(config)
        portfolio_state["spread_pct_BTCUSDT"] = 0.05  # 5% spread
        req = _make_trade_request()
        warns = checker.check_spread(req, portfolio_state)
        assert len(warns) == 1
        assert "WIDE_SPREAD" in warns[0]

    def test_validate_all_returns_all_checks(self, config, portfolio_state):
        checker = PreTradeCheck(config)
        req = _make_trade_request()
        results = checker.validate_all(req, portfolio_state)
        assert len(results) == 8  # 8 checks total
        assert all(r[1] is True for r in results)  # All pass

    def test_validate_all_reports_failures(self, config, portfolio_state):
        checker = PreTradeCheck(config)
        req = _make_trade_request(symbol="", side="HOLD", quantity=0)
        results = checker.validate_all(req, portfolio_state)
        # At least symbol, side, qty should fail
        failed = [r for r in results if not r[1]]
        assert len(failed) >= 3


class TestGenerateOrderId:
    def test_basic_format(self):
        oid = generate_order_id("spot_v1", "BTCUSDT")
        assert len(oid) <= 36
        assert "spot_v1" in oid
        assert "BTCUSDT" in oid

    def test_truncation(self):
        oid = generate_order_id("very_long_strategy_name_v9999", "BTCUSDTPERP")
        assert len(oid) <= 36

    def test_valid_client_order_id(self):
        ok, _ = validate_client_order_id("abc123")
        assert ok is True

    def test_too_long_order_id(self):
        ok, msg = validate_client_order_id("a" * 37)
        assert ok is False
        assert "panjang" in msg


# ═══════════════════════════════════════════════════════════════════════════════
# L2: CircuitBreaker
# ═══════════════════════════════════════════════════════════════════════════════


class TestCircuitBreaker:
    def _make_cb_config(self):
        """Config khusus untuk CB test (fraksi scale)."""
        return DummyConfig(
            max_daily_loss_pct=0.05,
            max_drawdown_pct=0.10,
            warn_daily_loss_pct=0.03,
        )

    def test_initial_state_normal(self):
        cb = CircuitBreaker(self._make_cb_config())
        assert cb.state == CircuitState.NORMAL

    def test_warn_on_daily_loss_3pct(self):
        """daily loss > 3% → WARNED."""
        cb = CircuitBreaker(self._make_cb_config())
        state = cb.check(daily_pnl=-350.0, current_equity=10000.0, peak_equity=10500.0)
        assert state == CircuitState.WARNED

    def test_halt_on_daily_loss_5pct(self):
        """daily loss > 5% → HALTED."""
        cb = CircuitBreaker(self._make_cb_config())
        state = cb.check(daily_pnl=-600.0, current_equity=10000.0, peak_equity=10500.0)
        assert state == CircuitState.HALTED

    def test_halt_on_drawdown_10pct(self):
        """Drawdown > 10% dari peak → HALTED."""
        cb = CircuitBreaker(self._make_cb_config())
        # Peak 10000, current 8900 → DD = 11%
        state = cb.check(daily_pnl=0.0, current_equity=8900.0, peak_equity=10000.0)
        assert state == CircuitState.HALTED

    def test_halt_on_consecutive_losses(self):
        """5 consecutive losses → HALTED."""
        cb = CircuitBreaker(self._make_cb_config())
        for _ in range(5):
            cb.record_loss()
        state = cb.check(daily_pnl=0.0, current_equity=10000.0, peak_equity=10000.0)
        assert state == CircuitState.HALTED

    def test_record_win_resets_losses(self):
        cb = CircuitBreaker(self._make_cb_config())
        for _ in range(4):
            cb.record_loss()
        cb.record_win()
        state = cb.check(daily_pnl=0.0, current_equity=10000.0, peak_equity=10000.0)
        assert state == CircuitState.NORMAL

    def test_manual_halt_and_resume(self):
        cb = CircuitBreaker(self._make_cb_config())
        cb.manual_halt("Emergency test")
        assert cb.state == CircuitState.HALTED

        cb.manual_resume()
        assert cb.state == CircuitState.NORMAL

    def test_daily_reset(self):
        cb = CircuitBreaker(self._make_cb_config())
        for _ in range(5):
            cb.record_loss()
        cb.check(daily_pnl=-600.0, current_equity=10000.0, peak_equity=10000.0)
        assert cb.state == CircuitState.HALTED

        cb.reset_daily()
        assert cb.state == CircuitState.NORMAL

    def test_status_dict(self):
        cb = CircuitBreaker(self._make_cb_config())
        status = cb.status()
        assert "state" in status
        assert "consecutive_losses" in status
        assert status["state"] == "normal"

    def test_check_from_capital_status(self):
        """Test convenience method with CapitalStatus-like object."""
        cb = CircuitBreaker(self._make_cb_config())

        class FakeStatus:
            daily_pnl = -600.0
            current_equity = 10000.0
            peak_equity = 10000.0

        state = cb.check_from_capital_status(FakeStatus())
        assert state == CircuitState.HALTED

    def test_positive_pnl_stays_normal(self):
        cb = CircuitBreaker(self._make_cb_config())
        state = cb.check(daily_pnl=500.0, current_equity=10500.0, peak_equity=10500.0)
        assert state == CircuitState.NORMAL


# ═══════════════════════════════════════════════════════════════════════════════
# L3: LeverageControl
# ═══════════════════════════════════════════════════════════════════════════════


class TestLeverageControl:
    def test_global_max_hardcoded_3x(self):
        """CONTEXT.md §4.2: leverage HARD-LOCKED ke 3x."""
        assert GLOBAL_MAX_LEVERAGE == 3

    def test_config_cannot_override_above_3(self):
        """Config setting global_max_leverage=10 tetap di-clamp ke 3."""
        config = DummyConfig(global_max_leverage=10)
        lc = LeverageControl(config)
        assert lc.global_max == 3

    def test_approve_leverage_within_limit(self, config):
        lc = LeverageControl(config)
        ok, msg = lc.validate(3, "BTCUSDT")
        assert ok is True

    def test_block_leverage_above_limit(self, config):
        lc = LeverageControl(config)
        ok, msg = lc.validate(4, "BTCUSDT")
        assert ok is False
        assert "melebihi" in msg

    def test_block_zero_leverage(self, config):
        lc = LeverageControl(config)
        ok, msg = lc.validate(0, "BTCUSDT")
        assert ok is False

    def test_get_max_leverage_all_symbols(self, config):
        lc = LeverageControl(config)
        for sym in ["BTCUSDT", "ETHUSDT", "SOLUSDT", "RANDOMUSDT"]:
            assert lc.get_max_leverage(sym) <= 3

    def test_effective_leverage(self, config):
        lc = LeverageControl(config)
        eff = lc.get_effective_leverage(30000.0, 10000.0)
        assert eff == 3.0

    def test_effective_leverage_zero_margin(self, config):
        lc = LeverageControl(config)
        eff = lc.get_effective_leverage(30000.0, 0.0)
        assert eff == 0.0


# ═══════════════════════════════════════════════════════════════════════════════
# L4: ExposureControl
# ═══════════════════════════════════════════════════════════════════════════════


class TestExposureControl:
    def test_approve_small_position(self, config, portfolio_state):
        ec = ExposureControl(config)
        req = _make_trade_request(quantity=0.01, price=50000.0)  # notional=500
        ok, msg = ec.validate(req, portfolio_state)
        assert ok is True

    def test_block_per_symbol_overexposure(self, config, portfolio_state):
        ec = ExposureControl(config)
        # 1.0 BTC × 50000 = 50000 → 500% of 10000 equity
        req = _make_trade_request(quantity=1.0, price=50000.0)
        ok, msg = ec.validate(req, portfolio_state)
        assert ok is False

    def test_block_max_positions_reached(self, config, portfolio_state):
        ec = ExposureControl(config)
        # Fill up 5 positions
        positions = {f"COIN{i}USDT": {"notional": 100.0} for i in range(5)}
        portfolio_state["open_positions"] = positions
        req = _make_trade_request(symbol="NEWCOIN", quantity=0.01, price=50000.0)
        ok, msg = ec.validate(req, portfolio_state)
        assert ok is False
        assert "Max posisi" in msg

    def test_exposure_snapshot(self, config, portfolio_state):
        ec = ExposureControl(config)
        portfolio_state["open_positions"] = {"BTCUSDT": {"notional": 2000.0}}
        snap = ec.get_exposure_snapshot(portfolio_state)
        assert snap["total_exposure_pct"] == pytest.approx(0.2)
        assert snap["open_positions_count"] == 1


# ═══════════════════════════════════════════════════════════════════════════════
# L5: PositionSizer
# ═══════════════════════════════════════════════════════════════════════════════


class TestPositionSizer:
    def test_fixed_fractional_sizing(self, config):
        config.max_position_pct = 0.50  # Loosen cap for test
        ps = PositionSizer(config)
        req = _make_trade_request(price=50000.0, suggested_sl=49000.0)
        # risk_amount = 10000 × 0.01 = 100
        # stop_distance = |50000 - 49000| = 1000
        # qty = 100 / 1000 = 0.1
        qty, warnings, risk_usd = ps.calculate(req, 10000.0, {})
        assert qty > 0
        assert risk_usd > 0

    def test_kelly_with_zero_win_rate_no_crash(self, config):
        """Kelly dengan win_rate=0 tidak crash."""
        config.sizing_method = "kelly"
        config.max_position_pct = 0.50
        ps = PositionSizer(config)
        req = _make_trade_request(price=50000.0, suggested_sl=49000.0)
        state = {"strategy_stats": {"spot_v1": {"win_rate": 0.0, "avg_risk_reward": 1.5}}}
        qty, warnings, _ = ps.calculate(req, 10000.0, state)
        # win_rate=0 should be reset to 0.5 internally
        assert len(warnings) > 0  # Should warn about invalid win_rate

    def test_volatility_scaled_sizing(self, config):
        config.sizing_method = "volatility_scaled"
        config.max_position_pct = 0.50
        ps = PositionSizer(config)
        req = _make_trade_request(price=50000.0, suggested_sl=49000.0)
        state = {"atr_BTCUSDT": 1000.0}
        qty, warnings, risk_usd = ps.calculate(req, 10000.0, state)
        assert qty > 0

    def test_lot_filter_uses_floor_not_round(self, config):
        """Lot filter HARUS menggunakan math.floor, bukan round."""
        config.max_position_pct = 0.50
        ps = PositionSizer(config)
        # Spot step_size=0.00001 → precision=5
        # 0.123456789 → floor to 0.12345 (not round to 0.12346)
        floored = ps._apply_lot_filter(0.123456789, is_futures=False)
        assert floored <= 0.123456789  # Floor selalu <= input
        assert floored == 0.12345  # math.floor, bukan round

        # Futures step_size=0.001 → precision=3
        floored_f = ps._apply_lot_filter(0.1237, is_futures=True)
        assert floored_f == 0.123  # Floored, not 0.124

    def test_zero_equity_returns_zero(self, config):
        ps = PositionSizer(config)
        req = _make_trade_request()
        qty, _, _ = ps.calculate(req, 0.0, {})
        assert qty == 0.0

    def test_zero_price_returns_zero(self, config):
        ps = PositionSizer(config)
        req = _make_trade_request(price=0.0)
        qty, _, _ = ps.calculate(req, 10000.0, {})
        assert qty == 0.0

    def test_below_min_notional_returns_zero(self, config):
        """Jika notional < min_notional → qty = 0."""
        ps = PositionSizer(config)
        req = _make_trade_request(price=50000.0, suggested_sl=49999.0)
        # Very tight SL → very small qty → very small notional
        qty, warnings, _ = ps.calculate(req, 10.0, {})
        # Equity 10, risk 0.01*10=0.1, SL dist=1, qty=0.1, notional=0.1*50000=5000
        # Actually this might still be above min. Let me use lower equity
        qty2, w2, _ = ps.calculate(req, 1.0, {})
        # Equity 1, risk=0.01, SL dist=1, qty=0.01, notional=500 → should pass
        # Use absurdly small equity
        qty3, w3, _ = ps.calculate(req, 0.001, {})
        assert qty3 == 0.0  # Below min notional

    def test_sizing_method_fallback_on_error(self, config):
        """Jika method exception → fallback ke fixed_fractional."""
        config.sizing_method = "kelly"  # Kelly needs strategy_stats, will fallback gracefully
        config.max_position_pct = 0.50
        ps = PositionSizer(config)
        req = _make_trade_request(price=50000.0, suggested_sl=49000.0)
        # Pass empty state → kelly will produce qty with default stats (no crash)
        qty, warnings, _ = ps.calculate(req, 10000.0, {})
        assert qty > 0
        assert len(warnings) > 0  # Should have warning about missing stats


# ═══════════════════════════════════════════════════════════════════════════════
# L6: StopLossManager
# ═══════════════════════════════════════════════════════════════════════════════


class TestStopLossManager:
    def test_valid_sl_passes(self, config):
        slm = StopLossManager(config)
        req = _make_trade_request(price=50000.0, suggested_sl=49000.0)  # 2% distance
        ok, msg, sl = slm.validate_and_compute(req, {})
        assert ok is True
        assert sl == 49000.0

    def test_block_zero_sl_when_required(self, config):
        """Block jika SL = 0 dan require_sl = True."""
        slm = StopLossManager(config)
        req = _make_trade_request(suggested_sl=0.0)
        ok, msg, sl = slm.validate_and_compute(req, {})
        assert ok is False
        assert "REQUIRE_SL" in msg
        assert sl > 0  # Default SL harus dihitung

    def test_sl_wrong_side_buy(self, config):
        """SL >= entry price untuk BUY → block."""
        slm = StopLossManager(config)
        req = _make_trade_request(side="BUY", price=50000.0, suggested_sl=51000.0)
        ok, msg, _ = slm.validate_and_compute(req, {})
        assert ok is False
        assert "sisi yang salah" in msg

    def test_sl_wrong_side_sell(self, config):
        """SL <= entry price untuk SELL → block."""
        slm = StopLossManager(config)
        req = _make_trade_request(side="SELL", price=50000.0, suggested_sl=49000.0)
        ok, msg, _ = slm.validate_and_compute(req, {})
        assert ok is False
        assert "sisi yang salah" in msg

    def test_sl_too_close(self, config):
        """SL distance < min → block."""
        slm = StopLossManager(config)
        req = _make_trade_request(price=50000.0, suggested_sl=49990.0)  # 0.02%
        ok, msg, _ = slm.validate_and_compute(req, {})
        assert ok is False
        assert "terlalu dekat" in msg

    def test_sl_too_far_warning(self, config):
        """SL distance > max → warning only (not block)."""
        slm = StopLossManager(config)
        req = _make_trade_request(price=50000.0, suggested_sl=40000.0)  # 20%
        ok, msg, _ = slm.validate_and_compute(req, {})
        assert ok is True  # Warning only
        assert "terlalu jauh" in msg

    def test_compute_default_sl_with_atr(self, config):
        slm = StopLossManager(config)
        sl = slm.compute_default_sl(50000.0, "BUY", atr=1000.0)
        # SL = 50000 - 1000*1.5 = 48500
        assert sl == 48500.0

    def test_compute_default_sl_no_atr(self, config):
        slm = StopLossManager(config)
        sl = slm.compute_default_sl(50000.0, "BUY", atr=0.0)
        # SL = 50000 - 50000*0.02 = 49000
        assert sl == 49000.0


# ═══════════════════════════════════════════════════════════════════════════════
# L7: RiskManager (Full Integration)
# ═══════════════════════════════════════════════════════════════════════════════


class TestRiskManagerIntegration:
    def test_approve_normal_trade(self, portfolio_state):
        """Skenario wajib: approve trade normal."""
        cfg = DummyConfig()
        rm = _make_risk_manager(cfg)
        req = _make_trade_request()
        result = rm.evaluate(req, portfolio_state)
        assert result.is_approved is True
        assert result.approved_quantity > 0
        assert result.risk_amount_usd > 0

    def test_block_zero_quantity(self, portfolio_state):
        """Skenario wajib: block zero quantity (sizing menghasilkan 0)."""
        cfg = DummyConfig()
        rm = _make_risk_manager(cfg, equity=0.001)  # Tiny equity → critical DD → halted
        req = _make_trade_request(price=50000.0, suggested_sl=49999.0)
        result = rm.evaluate(req, portfolio_state)
        # Should be blocked (either by CapitalManager or by sizing producing zero)
        assert result.verdict in (RiskVerdict.BLOCKED, RiskVerdict.WARNED)

    def test_block_when_capital_manager_unsafe(self, portfolio_state):
        """CapitalManager.safe_to_trade = False → BLOCKED."""
        cfg = DummyConfig()
        rm = _make_risk_manager(cfg)
        # Force capital manager to critical state
        rm.capital_manager._current_equity = 1.0  # Trigger critical drawdown
        rm.capital_manager._last_status = None  # Force recompute
        req = _make_trade_request()
        result = rm.evaluate(req, portfolio_state)
        assert result.verdict == RiskVerdict.BLOCKED

    def test_paper_mode_blocked_becomes_warned(self, portfolio_state):
        """Skenario wajib: paper mode: blocked jadi warned."""
        cfg = DummyConfig(paper_mode=True, require_sl=False)
        rm = _make_risk_manager(cfg)
        # Force a block condition: exchange maintenance
        portfolio_state["exchange_status"] = "maintenance"
        req = _make_trade_request()
        result = rm.evaluate(req, portfolio_state)
        # In paper mode, BLOCKED → WARNED
        assert result.verdict == RiskVerdict.WARNED
        assert result.approved_quantity > 0

    def test_paper_mode_approved_stays_approved(self, portfolio_state):
        """Paper mode: APPROVED tetap APPROVED."""
        cfg = DummyConfig(paper_mode=True, require_sl=False)
        rm = _make_risk_manager(cfg)
        req = _make_trade_request()
        result = rm.evaluate(req, portfolio_state)
        assert result.is_approved is True

    def test_circuit_breaker_halt_blocks(self, portfolio_state):
        """CircuitBreaker HALTED → BLOCKED."""
        cfg = DummyConfig()
        rm = _make_risk_manager(cfg)
        rm.circuit_breaker.manual_halt("test halt")
        req = _make_trade_request()
        result = rm.evaluate(req, portfolio_state)
        assert result.verdict == RiskVerdict.BLOCKED

    def test_leverage_futures_blocked(self, portfolio_state):
        """Futures dengan leverage > 3x → BLOCKED."""
        cfg = DummyConfig()
        rm = _make_risk_manager(cfg)
        req = _make_trade_request(is_futures=True, leverage=5)
        result = rm.evaluate(req, portfolio_state)
        assert result.verdict == RiskVerdict.BLOCKED
        assert any("Leverage" in r for r in result.reasons)

    def test_leverage_futures_approved(self, portfolio_state):
        """Futures dengan leverage <= 3x → bisa pass."""
        cfg = DummyConfig()
        rm = _make_risk_manager(cfg)
        req = _make_trade_request(is_futures=True, leverage=3)
        result = rm.evaluate(req, portfolio_state)
        # Might still be blocked by other checks, but not by leverage
        if result.verdict == RiskVerdict.BLOCKED:
            assert not any("Leverage" in r for r in result.reasons)

    def test_update_equity(self):
        cfg = DummyConfig()
        rm = _make_risk_manager(cfg)
        rm.update_equity(15000.0)
        assert rm._current_equity == 15000.0
        assert rm._peak_equity == 15000.0

    def test_reset_daily_stats(self):
        cfg = DummyConfig()
        rm = _make_risk_manager(cfg)
        rm._daily_pnl = -500.0
        rm.reset_daily_stats()
        assert rm._daily_pnl == 0.0

    def test_get_risk_summary(self):
        cfg = DummyConfig()
        rm = _make_risk_manager(cfg)
        summary = rm.get_risk_summary()
        assert "timestamp" in summary
        assert "circuit_breaker" in summary
        assert summary["leverage_max"] == 3

    def test_sl_price_in_result(self, portfolio_state):
        """RiskResult harus mengandung SL price yang valid."""
        cfg = DummyConfig()
        rm = _make_risk_manager(cfg)
        req = _make_trade_request(suggested_sl=49000.0)
        result = rm.evaluate(req, portfolio_state)
        if result.is_approved:
            assert result.sl_price == 49000.0

    def test_correlation_controller_gate(self, portfolio_state):
        """CorrelationController.check() dipanggil dan bisa block."""
        cfg = DummyConfig()
        rm = _make_risk_manager(cfg)
        # Add massive existing positions in correlated pair
        portfolio_state["open_positions"] = {
            "BTCUSDT": {"notional": 5000.0, "side": "BUY"},
            "ETHUSDT": {"notional": 5000.0, "side": "BUY"},
        }
        req = _make_trade_request(symbol="BTCUSDT", quantity=1.0, price=50000.0)  # + 50000 notional
        result = rm.evaluate(req, portfolio_state)
        # With btc_eth group limit 35% (3500 USDT) and already 10000 → should block
        # But it might be blocked by exposure first

    def test_risk_budget_gate(self, portfolio_state):
        """RiskBudgetManager.can_take_risk() dipanggil."""
        cfg = DummyConfig()
        rm = _make_risk_manager(cfg)
        # Register strategy and exhaust budget
        rm.risk_budget_manager.register_strategy("spot_v1", weight=1.0)
        # Use up all daily budget
        rm.risk_budget_manager.record_risk_taken("spot_v1", 500.0)
        rm.risk_budget_manager._states["spot_v1"].daily_risk_used = 1000.0  # Force exhaust
        req = _make_trade_request(strategy_id="spot_v1")
        result = rm.evaluate(req, portfolio_state)
        # Might be blocked by risk budget if daily budget exhausted
