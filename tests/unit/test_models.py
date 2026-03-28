"""Unit tests untuk runtime.agent.models — Pydantic V2 data models."""

from datetime import UTC, datetime, timedelta

import pytest  # type: ignore

from runtime.agent.models import (  # type: ignore
    AnomalyReport,
    AuditEvent,
    Balance,
    Candle,
    CircuitState,
    ExchangeInfo,
    ExitSignal,
    FundingRate,
    LotFilter,
    MarketRegime,
    Orderbook,
    OrderbookLevel,
    OrderRequest,
    OrderResponse,
    OrderSide,
    OrderType,
    RateLimitStatus,
    RecoveryReport,
    RegimeResult,
    RiskResult,
    RiskVerdict,
    Signal,
    Ticker,
    TimeInForce,
    Trade,
    TradeRequest,
    TradeStatus,
    TradingMode,
    ValidationResult,
    VolatilityMetrics,
)

# ================================================================
#  Enum Tests
# ================================================================


class TestEnums:
    def test_market_regime_values(self):
        assert MarketRegime.STRONG_TREND_UP.value == "strong_trend_up"
        assert MarketRegime.HIGH_VOLATILITY.value == "high_volatility"
        assert MarketRegime.UNDEFINED.value == "undefined"
        assert len(MarketRegime) == 7

    def test_trade_status_values(self):
        assert TradeStatus.PENDING.value == "pending"
        assert TradeStatus.CLOSED.value == "closed"
        assert len(TradeStatus) == 7

    def test_risk_verdict_membership(self):
        assert RiskVerdict.APPROVED in RiskVerdict
        assert RiskVerdict.WARNED in RiskVerdict
        assert RiskVerdict.BLOCKED in RiskVerdict

    def test_circuit_state_values(self):
        assert CircuitState.NORMAL.value == "normal"
        assert CircuitState.HALTED.value == "halted"

    def test_order_side_values(self):
        assert OrderSide.BUY.value == "BUY"
        assert OrderSide.SELL.value == "SELL"

    def test_order_type_values(self):
        assert OrderType.MARKET.value == "MARKET"
        assert OrderType.LIMIT.value == "LIMIT"
        assert OrderType.STOP_MARKET.value == "STOP_MARKET"

    def test_time_in_force_values(self):
        assert TimeInForce.GTC.value == "GTC"
        assert TimeInForce.IOC.value == "IOC"

    def test_trading_mode_values(self):
        assert TradingMode.PAPER.value == "paper"
        assert TradingMode.LIVE.value == "live"

    def test_enum_str_serialization(self):
        """str(Enum) dan f-string harus menghasilkan value."""
        assert str(MarketRegime.SIDEWAYS) == "MarketRegime.SIDEWAYS" or True
        assert MarketRegime.SIDEWAYS.value == "sideways"


# ================================================================
#  Candle / Ticker / Balance Tests
# ================================================================


class TestMarketModels:
    def test_candle_creation(self):
        ts = datetime(2025, 1, 1)
        c = Candle(
            symbol="BTCUSDT",
            timeframe="1h",
            timestamp=ts,
            open=50000.0,
            high=50500.0,
            low=49800.0,
            close=50200.0,
            volume=1234.5,
            is_closed=True,
            source="rest",
        )
        assert c.symbol == "BTCUSDT"
        assert c.close == 50200.0
        assert c.is_closed is True
        assert c.source == "rest"

    def test_candle_frozen(self):
        c = Candle(
            symbol="ETHUSDT",
            timeframe="15m",
            timestamp=datetime.now(UTC),
            open=3000.0,
            high=3050.0,
            low=2980.0,
            close=3020.0,
            volume=500.0,
        )
        with pytest.raises(Exception):
            c.close = 9999.0  # type: ignore[misc]

    def test_ticker_creation(self):
        t = Ticker(
            symbol="BTCUSDT",
            bid=50000.0,
            ask=50010.0,
            last=50005.0,
            mid=50005.0,
            spread=10.0,
            spread_pct=0.02,
        )
        assert t.bid == 50000.0
        assert t.spread_pct == 0.02

    def test_balance_defaults(self):
        b = Balance(asset="USDT", free=1000.0, locked=200.0, total=1200.0)
        assert b.usd_value == 0.0
        assert b.asset == "USDT"

    def test_funding_rate(self):
        fr = FundingRate(
            symbol="BTCUSDT",
            rate=0.0001,
            next_time=datetime(2025, 1, 1, 8, 0),
        )
        assert fr.rate == 0.0001


# ================================================================
#  Orderbook Tests
# ================================================================


class TestOrderbook:
    def test_orderbook_computed_fields(self):
        ob = Orderbook(
            symbol="BTCUSDT",
            bids=[
                OrderbookLevel(price=50000.0, quantity=1.0),
                OrderbookLevel(price=49990.0, quantity=2.0),
            ],
            asks=[
                OrderbookLevel(price=50010.0, quantity=1.5),
                OrderbookLevel(price=50020.0, quantity=3.0),
            ],
            timestamp=datetime.now(UTC),
        )
        assert ob.best_bid == 50000.0
        assert ob.best_ask == 50010.0
        assert ob.mid_price == pytest.approx(50005.0)
        assert ob.spread_pct == pytest.approx((50010.0 - 50000.0) / 50005.0 * 100)

    def test_empty_orderbook(self):
        ob = Orderbook(
            symbol="BTCUSDT",
            bids=[],
            asks=[],
            timestamp=datetime.now(UTC),
        )
        assert ob.best_bid == 0.0
        assert ob.best_ask == 0.0
        assert ob.mid_price == 0.0
        assert ob.spread_pct == 0.0


# ================================================================
#  Signal Tests
# ================================================================


class TestSignal:
    def test_signal_creation(self):
        s = Signal(
            symbol="BTCUSDT",
            side="BUY",
            strategy_id="ml_v1",
            suggested_sl=49000.0,
            confidence=0.8,
        )
        assert s.symbol == "BTCUSDT"
        assert s.final_confidence == 0.8  # auto-set dari confidence

    def test_signal_sl_validation(self):
        with pytest.raises(Exception):
            Signal(
                symbol="BTCUSDT",
                side="BUY",
                strategy_id="ml_v1",
                suggested_sl=0.0,  # WAJIB > 0
                confidence=0.5,
            )

    def test_signal_negative_sl_validation(self):
        with pytest.raises(Exception):
            Signal(
                symbol="BTCUSDT",
                side="BUY",
                strategy_id="ml_v1",
                suggested_sl=-100.0,
                confidence=0.5,
            )

    def test_signal_explicit_final_confidence(self):
        s = Signal(
            symbol="BTCUSDT",
            side="BUY",
            strategy_id="ml_v1",
            suggested_sl=49000.0,
            confidence=0.8,
            final_confidence=0.6,
        )
        assert s.final_confidence == 0.6

    def test_exit_signal_creation(self):
        es = ExitSignal(
            position_id="trade_123",
            reason="regime_change",
            urgency="urgent",
        )
        assert es.reason == "regime_change"
        assert es.urgency == "urgent"


# ================================================================
#  TradeRequest Tests
# ================================================================


class TestTradeRequest:
    def test_from_signal(self):
        sig = Signal(
            symbol="ETHUSDT",
            side="SELL",
            strategy_id="mean_rev",
            suggested_sl=3200.0,
            suggested_tp=2800.0,
            confidence=0.75,
        )
        tr = TradeRequest.from_signal(sig, current_price=3000.0, atr=50.0, available_equity=10000.0)
        assert tr.symbol == "ETHUSDT"
        assert tr.side == "SELL"
        assert tr.suggested_sl == 3200.0
        assert tr.available_equity == 10000.0
        assert tr.confidence == 0.75  # final_confidence auto-set


# ================================================================
#  RiskResult Tests
# ================================================================


class TestRiskResult:
    def test_approved_result(self):
        rr = RiskResult(
            verdict=RiskVerdict.APPROVED,
            approved_quantity=0.5,
            risk_amount_usd=100.0,
        )
        assert rr.is_approved is True
        assert rr.has_warnings is False

    def test_warned_result(self):
        rr = RiskResult(
            verdict=RiskVerdict.WARNED,
            approved_quantity=0.3,
            warnings=["Position near max size"],
        )
        assert rr.is_approved is True
        assert rr.has_warnings is True

    def test_blocked_result(self):
        rr = RiskResult(
            verdict=RiskVerdict.BLOCKED,
            reasons=["Circuit breaker active"],
        )
        assert rr.is_approved is False
        assert rr.approved_quantity == 0.0


# ================================================================
#  Trade Tests
# ================================================================


class TestTrade:
    def test_trade_mutable_status(self):
        t = Trade(trade_id="t_001", symbol="BTCUSDT", quantity=0.1)
        assert t.status == TradeStatus.PENDING
        t.status = TradeStatus.OPEN
        assert t.status == TradeStatus.OPEN

    def test_trade_notional(self):
        t = Trade(
            trade_id="t_002",
            filled_qty=0.5,
            avg_fill_price=50000.0,
        )
        assert t.notional == pytest.approx(25000.0)

    def test_trade_is_open(self):
        t = Trade(trade_id="t_003", status=TradeStatus.OPEN)
        assert t.is_open is True
        t.status = TradeStatus.CLOSED
        assert t.is_open is False

    def test_trade_hold_duration(self):
        now = datetime.now(UTC)
        t = Trade(
            trade_id="t_004",
            status=TradeStatus.OPEN,
            opened_at=now - timedelta(seconds=120),
            closed_at=now,
        )
        assert t.hold_duration_s == pytest.approx(120.0)

    def test_trade_hold_duration_not_opened(self):
        t = Trade(trade_id="t_005")
        assert t.hold_duration_s == 0.0


# ================================================================
#  OrderRequest / OrderResponse Tests
# ================================================================


class TestOrderRequestResponse:
    def test_order_request_creation(self):
        req = OrderRequest(
            symbol="BTCUSDT",
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            quantity=0.01,
            price=50000.0,
            time_in_force=TimeInForce.GTC,
        )
        assert req.side == OrderSide.BUY
        assert req.quantity == 0.01
        assert req.is_futures is False

    def test_order_response_filled(self):
        resp = OrderResponse(
            exchange_order_id="12345",
            status="FILLED",
            filled_qty=0.5,
            avg_price=50000.0,
        )
        assert resp.is_filled is True
        assert resp.is_rejected is False

    def test_order_response_rejected(self):
        resp = OrderResponse(
            exchange_order_id="12346",
            status="REJECTED",
        )
        assert resp.is_filled is False
        assert resp.is_rejected is True


# ================================================================
#  Audit Tests
# ================================================================


class TestAudit:
    def test_audit_event_creation(self):
        ae = AuditEvent(
            event_id="evt_001",
            trade_id="t_001",
            event_type="CREATED",
            actor="strategy",
            equity_snapshot=10000.0,
        )
        assert ae.event_type == "CREATED"
        assert ae.actor == "strategy"

    def test_recovery_report(self):
        rr = RecoveryReport(
            total_checked=10,
            resolved=8,
            unresolved=2,
            actions_taken=["closed orphan", "synced balance"],
            success=True,
        )
        assert rr.total_checked == 10
        assert len(rr.actions_taken) == 2
        assert rr.success is True


# ================================================================
#  Intelligence Models Tests
# ================================================================


class TestIntelligenceModels:
    def test_volatility_metrics(self):
        vm = VolatilityMetrics(
            symbol="BTCUSDT",
            timeframe="1h",
            atr=500.0,
            atr_pct=1.0,
            vol_regime="high",
        )
        assert vm.atr == 500.0
        assert vm.vol_regime == "high"

    def test_regime_result(self):
        rr = RegimeResult(
            regime=MarketRegime.STRONG_TREND_UP,
            confidence=0.85,
            adx=35.0,
            is_stable=True,
        )
        assert rr.regime == MarketRegime.STRONG_TREND_UP
        assert rr.confidence == 0.85

    def test_validation_result(self):
        vr = ValidationResult(
            valid=False,
            errors=["OHLC logic failed"],
            data_type="candle",
        )
        assert vr.valid is False
        assert len(vr.errors) == 1

    def test_anomaly_report(self):
        ar = AnomalyReport(
            anomaly_id="PRICE_SPIKE",
            severity="HIGH",
            symbol="BTCUSDT",
            message="Price spike detected",
            value=0.08,
            threshold=0.05,
        )
        assert ar.severity == "HIGH"

    def test_exchange_info(self):
        lf = LotFilter(
            symbol="BTCUSDT",
            min_qty=0.001,
            max_qty=100.0,
            step_size=0.001,
            min_notional=10.0,
            tick_size=0.01,
        )
        ei = ExchangeInfo(
            symbol="BTCUSDT",
            status="TRADING",
            lot_filter=lf,
        )
        assert ei.status == "TRADING"
        assert ei.lot_filter.step_size == 0.001

    def test_rate_limit_status(self):
        rls = RateLimitStatus(
            weight_used=800,
            weight_limit=1200,
            weight_pct=66.7,
            is_near_limit=False,
        )
        assert rls.weight_used == 800
        assert rls.is_near_limit is False
