"""
Test Sync Layer — balance_sync, order_sync, position_sync, reconciliation.

Coverage:
- BalanceSync drift detection & update
- OrderSync reconciliation (FILLED, CANCELLED, PARTIAL)
- PositionSync ghost/zombie detection
- Reconciliation report generation & JSON save

Mengikuti checklist docs section 14.3.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from runtime.agent.models.enums import AlertSeverity
from runtime.agent.models.monitoring import (
    Alert,
    BalanceSyncReport,
    Discrepancy,
    OrderSyncReport,
    PositionSyncReport,
    ReconciliationReport,
)
from runtime.agent.sync.balance_sync import BalanceSync
from runtime.agent.sync.order_sync import OrderSync, OrderNotFoundError
from runtime.agent.sync.position_sync import PositionSync
from runtime.agent.sync.reconciliation import Reconciliation


# ══════════════════════════════════════════════════════════════════════
#  Mocks
# ══════════════════════════════════════════════════════════════════════


class MockExchangeBalance:
    """Mock exchange balance provider."""

    def __init__(self, balance: float = 10000.0):
        self._balance = balance

    async def get_balance_usdt(self) -> float:
        return self._balance


class MockCapital:
    """Mock CapitalManager."""

    def __init__(self, equity: float = 10000.0):
        self._equity = equity
        self.updated_value: float | None = None

    def get_current_equity(self) -> float:
        return self._equity

    def update_from_exchange(self, actual: float) -> None:
        self.updated_value = actual
        self._equity = actual


class MockAlertManager:
    """Mock AlertManager."""

    def __init__(self):
        self.alerts: list[Alert] = []

    async def send(self, alert: Alert) -> None:
        self.alerts.append(alert)


class MockOrderExchange:
    """Mock exchange for order status checks."""

    def __init__(self, statuses: dict[str, dict] | None = None):
        self._statuses = statuses or {}

    async def get_order_status(self, symbol: str,
                               client_order_id: str) -> dict:
        if client_order_id in self._statuses:
            return self._statuses[client_order_id]
        raise OrderNotFoundError(f"Order {client_order_id} not found")


class MockTradeStore:
    """Mock trade store."""

    def __init__(self, trades: list[dict] | None = None):
        self._trades = trades or []
        self.status_updates: list[tuple] = []

    def get_pending_trades(self) -> list[dict]:
        return self._trades

    def update_trade_status(self, trade_id: str, status: str,
                            filled_qty: float = 0.0,
                            avg_price: float = 0.0) -> None:
        self.status_updates.append((trade_id, status, filled_qty, avg_price))


class MockPositionExchange:
    """Mock exchange for position queries."""

    def __init__(self, positions: dict[str, dict] | None = None):
        self._positions = positions or {}

    async def get_exchange_positions(self) -> dict[str, dict]:
        return self._positions


class MockPositionStore:
    """Mock internal position store."""

    def __init__(self, positions: dict[str, dict] | None = None):
        self._positions = positions or {}
        self.closed_trades: list[str] = []

    def get_open_positions(self) -> dict[str, dict]:
        return self._positions

    def mark_closed(self, trade_id: str) -> None:
        self.closed_trades.append(trade_id)


class MockReconciliationProvider:
    """Mock data provider for reconciliation."""

    def __init__(self, **kwargs):
        self._balance_exchange = kwargs.get("balance_exchange", 10000.0)
        self._balance_internal = kwargs.get("balance_internal", 10000.0)
        self._open_internal = kwargs.get("open_internal", 2)
        self._open_exchange = kwargs.get("open_exchange", 2)
        self._pnl_internal = kwargs.get("pnl_internal", 100.0)
        self._pnl_computed = kwargs.get("pnl_computed", 100.0)
        self._commission = kwargs.get("commission", 5.0)
        self._trades_today = kwargs.get("trades_today", 3)
        self._win_rate = kwargs.get("win_rate", 0.67)
        self._discrepancies = kwargs.get("discrepancies", [])

    async def get_exchange_balance(self) -> float:
        return self._balance_exchange

    def get_internal_equity(self) -> float:
        return self._balance_internal

    def get_internal_open_count(self) -> int:
        return self._open_internal

    async def get_exchange_open_count(self) -> int:
        return self._open_exchange

    def get_daily_pnl_internal(self) -> float:
        return self._pnl_internal

    async def get_daily_pnl_computed(self) -> float:
        return self._pnl_computed

    def get_total_commission_today(self) -> float:
        return self._commission

    def get_total_trades_today(self) -> int:
        return self._trades_today

    def get_win_rate_today(self) -> float:
        return self._win_rate

    async def get_position_discrepancies(self) -> list[Discrepancy]:
        return self._discrepancies


# ══════════════════════════════════════════════════════════════════════
#  1. BALANCE SYNC
# ══════════════════════════════════════════════════════════════════════


class TestBalanceSync:
    """Checklist #14: balance_sync selalu update internal dari exchange."""

    @pytest.mark.asyncio
    async def test_sync_updates_internal(self):
        """#14: Internal diupdate dari exchange, bukan sebaliknya."""
        exchange = MockExchangeBalance(balance=9800.0)
        capital = MockCapital(equity=10000.0)
        sync = BalanceSync(exchange=exchange, capital=capital)

        report = await sync.run()

        assert report.updated is True
        assert capital.updated_value == 9800.0  # Updated dari exchange
        assert report.internal_before == 10000.0
        assert report.exchange_actual == 9800.0

    @pytest.mark.asyncio
    async def test_drift_calculation(self):
        """Drift dihitung dengan benar."""
        exchange = MockExchangeBalance(balance=9500.0)
        capital = MockCapital(equity=10000.0)
        sync = BalanceSync(exchange=exchange, capital=capital)

        report = await sync.run()

        # Drift = |9500 - 10000| / 10000 = 0.05
        assert report.drift_pct == pytest.approx(0.05, abs=0.001)

    @pytest.mark.asyncio
    async def test_alert_on_high_drift(self):
        """Drift > 5% → alert CRITICAL."""
        exchange = MockExchangeBalance(balance=9300.0)
        capital = MockCapital(equity=10000.0)
        alert_mgr = MockAlertManager()
        sync = BalanceSync(
            exchange=exchange, capital=capital,
            alert_manager=alert_mgr,
        )

        report = await sync.run()

        assert report.alert_sent is True
        assert len(alert_mgr.alerts) == 1
        assert alert_mgr.alerts[0].severity == AlertSeverity.CRITICAL

    @pytest.mark.asyncio
    async def test_no_alert_on_small_drift(self):
        """Drift < 1% → no alert."""
        exchange = MockExchangeBalance(balance=9950.0)
        capital = MockCapital(equity=10000.0)
        alert_mgr = MockAlertManager()
        sync = BalanceSync(
            exchange=exchange, capital=capital,
            alert_manager=alert_mgr,
        )

        report = await sync.run()

        assert report.alert_sent is False
        assert len(alert_mgr.alerts) == 0

    @pytest.mark.asyncio
    async def test_exchange_error_graceful(self):
        """Exchange error → updated=False, no crash."""

        class FailingExchange:
            async def get_balance_usdt(self) -> float:
                raise ConnectionError("timeout")

        capital = MockCapital(equity=10000.0)
        sync = BalanceSync(exchange=FailingExchange(), capital=capital)

        report = await sync.run()
        assert report.updated is False
        assert report.exchange_actual == 0.0


# ══════════════════════════════════════════════════════════════════════
#  2. ORDER SYNC
# ══════════════════════════════════════════════════════════════════════


class TestOrderSync:
    """Checklist #18: order_sync tidak retry order yang sudah CANCELLED."""

    @pytest.mark.asyncio
    async def test_sync_filled_order(self):
        """FILLED → update trade status to open."""
        exchange = MockOrderExchange(statuses={
            "ORD-001": {"status": "FILLED", "filled_qty": 0.001, "avg_price": 50000},
        })
        store = MockTradeStore(trades=[
            {"trade_id": "T1", "symbol": "BTCUSDT",
             "client_order_id": "ORD-001", "status": "submitted"},
        ])
        sync = OrderSync(exchange=exchange, store=store)

        report = await sync.run()

        assert report.checked == 1
        assert report.updated == 1
        assert len(store.status_updates) == 1
        assert store.status_updates[0] == ("T1", "open", 0.001, 50000)

    @pytest.mark.asyncio
    async def test_sync_cancelled_order(self):
        """CANCELLED → mark as cancelled, don't retry."""
        exchange = MockOrderExchange(statuses={
            "ORD-002": {"status": "CANCELLED"},
        })
        store = MockTradeStore(trades=[
            {"trade_id": "T2", "symbol": "BTCUSDT",
             "client_order_id": "ORD-002", "status": "submitted"},
        ])
        sync = OrderSync(exchange=exchange, store=store)

        report = await sync.run()

        assert report.updated == 1
        assert store.status_updates[0][1] == "cancelled"

    @pytest.mark.asyncio
    async def test_sync_partial_fill(self):
        """PARTIALLY_FILLED → update qty and status."""
        exchange = MockOrderExchange(statuses={
            "ORD-003": {"status": "PARTIALLY_FILLED",
                        "filled_qty": 0.0005, "avg_price": 49800},
        })
        store = MockTradeStore(trades=[
            {"trade_id": "T3", "symbol": "BTCUSDT",
             "client_order_id": "ORD-003", "status": "submitted"},
        ])
        sync = OrderSync(exchange=exchange, store=store)

        report = await sync.run()

        assert report.updated == 1
        assert store.status_updates[0] == ("T3", "partial", 0.0005, 49800)

    @pytest.mark.asyncio
    async def test_order_not_found_conflict(self):
        """Order tidak ditemukan → conflict, bukan crash."""
        exchange = MockOrderExchange(statuses={})
        store = MockTradeStore(trades=[
            {"trade_id": "T4", "symbol": "BTCUSDT",
             "client_order_id": "ORD-MISSING", "status": "submitted"},
        ])
        sync = OrderSync(exchange=exchange, store=store)

        report = await sync.run()

        assert report.checked == 1
        assert report.updated == 0
        assert len(report.conflicts) == 1

    @pytest.mark.asyncio
    async def test_no_pending_trades(self):
        """Tidak ada pending trades → skip."""
        exchange = MockOrderExchange()
        store = MockTradeStore(trades=[])
        sync = OrderSync(exchange=exchange, store=store)

        report = await sync.run()
        assert report.checked == 0
        assert report.updated == 0


# ══════════════════════════════════════════════════════════════════════
#  3. POSITION SYNC
# ══════════════════════════════════════════════════════════════════════


class TestPositionSync:
    """Checklist #15 & #16: Ghost & zombie position detection."""

    @pytest.mark.asyncio
    async def test_ghost_position_detected(self):
        """#15: GHOST_POSITION terdeteksi dan trigger alert CRITICAL."""
        exchange = MockPositionExchange(positions={
            "BTCUSDT": {"qty": 0.001, "side": "BUY", "entry_price": 50000},
        })
        store = MockPositionStore(positions={})  # internal kosong
        alert_mgr = MockAlertManager()
        sync = PositionSync(
            exchange=exchange, store=store,
            alert_manager=alert_mgr,
        )

        report = await sync.run()

        assert report.has_issues
        assert len(report.discrepancies) == 1
        assert report.discrepancies[0].type == "GHOST_POSITION"
        assert report.discrepancies[0].severity == "CRITICAL"
        assert len(alert_mgr.alerts) >= 1

    @pytest.mark.asyncio
    async def test_zombie_position_detected(self):
        """#16: ZOMBIE_POSITION di-mark CLOSED di internal."""
        exchange = MockPositionExchange(positions={})  # exchange kosong
        store = MockPositionStore(positions={
            "ETHUSDT": {"trade_id": "T1", "filled_qty": 0.01, "side": "BUY"},
        })
        sync = PositionSync(exchange=exchange, store=store)

        report = await sync.run()

        assert report.has_issues
        assert len(report.discrepancies) == 1
        assert report.discrepancies[0].type == "ZOMBIE_POSITION"
        assert "T1" in store.closed_trades  # marked as closed

    @pytest.mark.asyncio
    async def test_qty_mismatch_detected(self):
        """QTY berbeda > 1% → MEDIUM discrepancy."""
        exchange = MockPositionExchange(positions={
            "BTCUSDT": {"qty": 0.0012, "side": "BUY"},
        })
        store = MockPositionStore(positions={
            "BTCUSDT": {"trade_id": "T1", "filled_qty": 0.001, "side": "BUY"},
        })
        sync = PositionSync(exchange=exchange, store=store)

        report = await sync.run()

        assert len(report.discrepancies) == 1
        assert report.discrepancies[0].type == "QTY_MISMATCH"

    @pytest.mark.asyncio
    async def test_no_discrepancies(self):
        """Semua cocok → no discrepancies."""
        exchange = MockPositionExchange(positions={
            "BTCUSDT": {"qty": 0.001, "side": "BUY"},
        })
        store = MockPositionStore(positions={
            "BTCUSDT": {"trade_id": "T1", "filled_qty": 0.001, "side": "BUY"},
        })
        sync = PositionSync(exchange=exchange, store=store)

        report = await sync.run()

        assert not report.has_issues

    @pytest.mark.asyncio
    async def test_exchange_error_graceful(self):
        """Exchange error → graceful, no crash."""

        class FailingExchange:
            async def get_exchange_positions(self):
                raise ConnectionError("timeout")

        store = MockPositionStore(positions={"BTCUSDT": {"trade_id": "T1"}})
        sync = PositionSync(exchange=FailingExchange(), store=store)

        report = await sync.run()
        assert report.exchange_count == 0


# ══════════════════════════════════════════════════════════════════════
#  4. RECONCILIATION
# ══════════════════════════════════════════════════════════════════════


class TestReconciliation:
    """Checklist #17: reconciliation.run_daily() menghasilkan JSON report."""

    @pytest.mark.asyncio
    async def test_daily_report_all_ok(self, tmp_path):
        """#17: Report JSON disimpan ke disk."""
        provider = MockReconciliationProvider()
        reconciler = Reconciliation(
            data_provider=provider,
            log_dir=tmp_path,
        )

        report = await reconciler.run_daily()

        assert report.all_ok is True
        assert report.balance_drift_pct == 0.0

        # JSON file harus ada
        files = list(tmp_path.glob("reconciliation_*.json"))
        assert len(files) == 1

        data = json.loads(files[0].read_text(encoding="utf-8"))
        assert data["all_ok"] is True

    @pytest.mark.asyncio
    async def test_daily_report_with_issues(self, tmp_path):
        """Balance drift > 1% → issue reported."""
        provider = MockReconciliationProvider(
            balance_internal=10000.0,
            balance_exchange=9500.0,
        )
        alert_mgr = MockAlertManager()
        reconciler = Reconciliation(
            data_provider=provider,
            alert_manager=alert_mgr,
            log_dir=tmp_path,
        )

        report = await reconciler.run_daily()

        assert report.all_ok is False
        assert len(report.issues) > 0
        # Alert dikirim
        assert len(alert_mgr.alerts) == 1

    @pytest.mark.asyncio
    async def test_daily_report_with_discrepancies(self, tmp_path):
        """Discrepancies → issue reported."""
        provider = MockReconciliationProvider(
            discrepancies=[
                Discrepancy(type="GHOST_POSITION", symbol="BTCUSDT",
                            severity="CRITICAL"),
            ],
        )
        reconciler = Reconciliation(
            data_provider=provider,
            log_dir=tmp_path,
        )

        report = await reconciler.run_daily()

        assert report.all_ok is False
        assert len(report.position_discrepancies) == 1

    @pytest.mark.asyncio
    async def test_cached_last_report(self, tmp_path):
        """last_report returns cached."""
        provider = MockReconciliationProvider()
        reconciler = Reconciliation(
            data_provider=provider,
            log_dir=tmp_path,
        )

        assert reconciler.last_report is None
        report = await reconciler.run_daily()
        assert reconciler.last_report is report
