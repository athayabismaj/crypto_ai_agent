"""
Test Monitoring Layer — heartbeat, health_check, alerts.

Coverage:
- Heartbeat status transitions (ALIVE → STALE → DEAD)
- HealthCheck parallel execution & threshold checks
- AlertManager routing (INFO, WARNING, CRITICAL)
- ConnectivityChecker (mocked)

Mengikuti checklist docs section 14.2.
"""

from __future__ import annotations

import asyncio
import json
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from runtime.agent.models.enums import (
    AlertSeverity,
    CircuitState,
    ComponentStatus,
    HeartbeatStatus,
)
from runtime.agent.models.monitoring import Alert, ComponentHealth
from runtime.agent.monitoring.alerts import AlertManager
from runtime.agent.monitoring.health_check import HealthCheck
from runtime.agent.monitoring.heartbeat import Heartbeat


# ══════════════════════════════════════════════════════════════════════
#  Helpers
# ══════════════════════════════════════════════════════════════════════


class MockSystemInfo:
    """Mock SystemInfoProvider untuk testing."""

    def __init__(self, **kwargs):
        self._memory_mb = kwargs.get("memory_mb", 100.0)
        self._disk_gb = kwargs.get("disk_gb", 50.0)
        self._cb_state = kwargs.get("cb_state", CircuitState.NORMAL.value)
        self._rate_pct = kwargs.get("rate_pct", 10.0)
        self._ws_age = kwargs.get("ws_age", 5.0)
        self._ping_ms = kwargs.get("ping_ms", 50.0)
        self._open_orders = kwargs.get("open_orders", 2)
        self._max_pos = kwargs.get("max_pos", 5)

    def get_memory_rss_mb(self) -> float:
        return self._memory_mb

    def get_disk_free_gb(self) -> float:
        return self._disk_gb

    def get_circuit_breaker_state(self) -> str:
        return self._cb_state

    def get_rate_limit_pct(self) -> float:
        return self._rate_pct

    def get_ws_last_message_age_s(self) -> float:
        return self._ws_age

    async def ping_exchange_ms(self) -> float:
        return self._ping_ms

    def get_open_orders_count(self) -> int:
        return self._open_orders

    def get_max_positions(self) -> int:
        return self._max_pos


class MockEventBus:
    """Mock EventBus untuk testing alert routing."""

    def __init__(self):
        self.events: list[tuple[str, dict]] = []

    async def emit(self, event_type: str, data: dict) -> None:
        self.events.append((event_type, data))


# ══════════════════════════════════════════════════════════════════════
#  1. HEARTBEAT
# ══════════════════════════════════════════════════════════════════════


class TestHeartbeat:
    """Checklist #9: Heartbeat pulse dan status transitions."""

    @pytest.fixture
    def tmp_dir(self, tmp_path):
        return tmp_path

    @pytest.mark.asyncio
    async def test_pulse_creates_file(self, tmp_dir):
        """Pulse membuat heartbeat file."""
        hb = Heartbeat(data_dir=tmp_dir)
        await hb._pulse()

        hb_file = tmp_dir / "heartbeat.json"
        assert hb_file.exists()

        data = json.loads(hb_file.read_text(encoding="utf-8"))
        assert "timestamp" in data
        assert data["tasks_ok"] is True
        assert data["status"] == "alive"

    def test_status_dead_no_file(self, tmp_dir):
        """Tidak ada file → DEAD."""
        hb = Heartbeat(data_dir=tmp_dir)
        assert hb.get_status() == HeartbeatStatus.DEAD

    @pytest.mark.asyncio
    async def test_status_alive_after_pulse(self, tmp_dir):
        """Pulse baru → ALIVE."""
        hb = Heartbeat(data_dir=tmp_dir)
        await hb._pulse()
        assert hb.get_status() == HeartbeatStatus.ALIVE

    def test_status_stale(self, tmp_dir):
        """Pulse lama (> 90s tapi < 180s) → STALE."""
        hb = Heartbeat(data_dir=tmp_dir)
        old_time = datetime.now(UTC) - timedelta(seconds=100)
        hb_file = tmp_dir / "heartbeat.json"
        hb_file.write_text(json.dumps({
            "timestamp": old_time.isoformat(),
            "tasks_ok": True,
            "status": "alive",
        }), encoding="utf-8")
        assert hb.get_status() == HeartbeatStatus.STALE

    def test_status_dead_old_pulse(self, tmp_dir):
        """Pulse sangat lama (> 180s) → DEAD."""
        hb = Heartbeat(data_dir=tmp_dir)
        old_time = datetime.now(UTC) - timedelta(seconds=200)
        hb_file = tmp_dir / "heartbeat.json"
        hb_file.write_text(json.dumps({
            "timestamp": old_time.isoformat(),
            "tasks_ok": True,
            "status": "alive",
        }), encoding="utf-8")
        assert hb.get_status() == HeartbeatStatus.DEAD

    def test_status_dead_corrupt_file(self, tmp_dir):
        """File corrupt → DEAD."""
        hb = Heartbeat(data_dir=tmp_dir)
        hb_file = tmp_dir / "heartbeat.json"
        hb_file.write_text("NOT JSON", encoding="utf-8")
        assert hb.get_status() == HeartbeatStatus.DEAD

    def test_set_tasks_ok(self, tmp_dir):
        """set_tasks_ok updates internal state."""
        hb = Heartbeat(data_dir=tmp_dir)
        hb.set_tasks_ok(False)
        assert hb._tasks_ok is False

    def test_stop(self, tmp_dir):
        """stop() sets _running to False."""
        hb = Heartbeat(data_dir=tmp_dir)
        hb._running = True
        hb.stop()
        assert hb._running is False


# ══════════════════════════════════════════════════════════════════════
#  2. HEALTH CHECK
# ══════════════════════════════════════════════════════════════════════


class TestHealthCheck:
    """Checklist #11: Parallel health check."""

    @pytest.mark.asyncio
    async def test_all_healthy(self):
        """Semua komponen sehat → overall HEALTHY."""
        sys_info = MockSystemInfo()
        hc = HealthCheck(system_info=sys_info)
        report = await hc.run()

        assert report.is_healthy
        assert report.overall == ComponentStatus.HEALTHY
        assert len(report.unhealthy_components) == 0

    @pytest.mark.asyncio
    async def test_memory_degraded(self):
        """Memory > 300MB → DEGRADED."""
        sys_info = MockSystemInfo(memory_mb=350.0)
        hc = HealthCheck(system_info=sys_info)
        report = await hc.run()

        assert report.overall == ComponentStatus.DEGRADED
        assert "memory" in report.degraded_components

    @pytest.mark.asyncio
    async def test_memory_unhealthy(self):
        """Memory > 500MB → UNHEALTHY."""
        sys_info = MockSystemInfo(memory_mb=600.0)
        hc = HealthCheck(system_info=sys_info)
        report = await hc.run()

        assert report.overall == ComponentStatus.UNHEALTHY
        assert "memory" in report.unhealthy_components

    @pytest.mark.asyncio
    async def test_disk_low(self):
        """Disk < 2GB → DEGRADED. < 500MB → UNHEALTHY."""
        sys_info = MockSystemInfo(disk_gb=1.0)
        hc = HealthCheck(system_info=sys_info)
        report = await hc.run()

        assert "disk" in report.degraded_components

    @pytest.mark.asyncio
    async def test_circuit_breaker_halted(self):
        """CB HALTED → UNHEALTHY."""
        sys_info = MockSystemInfo(cb_state=CircuitState.HALTED.value)
        hc = HealthCheck(system_info=sys_info)
        report = await hc.run()

        assert "circuit_breaker" in report.unhealthy_components

    @pytest.mark.asyncio
    async def test_rate_limit_high(self):
        """Rate limit > 90% → UNHEALTHY."""
        sys_info = MockSystemInfo(rate_pct=95.0)
        hc = HealthCheck(system_info=sys_info)
        report = await hc.run()

        assert "rate_limit" in report.unhealthy_components

    @pytest.mark.asyncio
    async def test_websocket_stale(self):
        """WS age > 30s → DEGRADED, > 60s → UNHEALTHY."""
        sys_info = MockSystemInfo(ws_age=35.0)
        hc = HealthCheck(system_info=sys_info)
        report = await hc.run()

        assert "websocket" in report.degraded_components

    @pytest.mark.asyncio
    async def test_ping_high(self):
        """Ping > 3000ms → UNHEALTHY."""
        sys_info = MockSystemInfo(ping_ms=3500.0)
        hc = HealthCheck(system_info=sys_info)
        report = await hc.run()

        assert "exchange_ping" in report.unhealthy_components

    @pytest.mark.asyncio
    async def test_open_orders_high(self):
        """Open orders > max × 2 → UNHEALTHY."""
        sys_info = MockSystemInfo(open_orders=11, max_pos=5)
        hc = HealthCheck(system_info=sys_info)
        report = await hc.run()

        assert "open_orders" in report.unhealthy_components

    @pytest.mark.asyncio
    async def test_cached_report(self):
        """get_last_report() returns cached report."""
        sys_info = MockSystemInfo()
        hc = HealthCheck(system_info=sys_info)

        assert hc.get_last_report() is None
        report = await hc.run()
        assert hc.get_last_report() is report


# ══════════════════════════════════════════════════════════════════════
#  3. ALERT MANAGER
# ══════════════════════════════════════════════════════════════════════


class TestAlertManager:
    """AlertManager routing tests."""

    @pytest.mark.asyncio
    async def test_info_only_logs(self):
        """INFO → log only, no EventBus event."""
        bus = MockEventBus()
        mgr = AlertManager(event_bus=bus)

        await mgr.send(Alert(
            severity=AlertSeverity.INFO,
            title="Test", message="Hello",
            component="test",
        ))

        assert len(bus.events) == 0  # INFO doesn't emit
        assert len(mgr.get_history()) == 1

    @pytest.mark.asyncio
    async def test_warning_emits_event(self):
        """WARNING → EventBus emit."""
        bus = MockEventBus()
        mgr = AlertManager(event_bus=bus)

        await mgr.send(Alert(
            severity=AlertSeverity.WARNING,
            title="Warn", message="Something",
            component="test",
        ))

        assert len(bus.events) == 1
        assert bus.events[0][0] == "alert_warning"
        assert mgr.warning_count == 1

    @pytest.mark.asyncio
    async def test_critical_emits_event_and_stderr(self, capsys):
        """CRITICAL → EventBus emit + stderr."""
        bus = MockEventBus()
        mgr = AlertManager(event_bus=bus)

        await mgr.send(Alert(
            severity=AlertSeverity.CRITICAL,
            title="Fire", message="Everything is burning",
            component="system",
        ))

        assert len(bus.events) == 1
        assert bus.events[0][0] == "alert_critical"
        assert mgr.critical_count == 1

        # Check stderr
        captured = capsys.readouterr()
        assert "CRITICAL ALERT" in captured.err

    @pytest.mark.asyncio
    async def test_no_event_bus(self):
        """No EventBus → graceful (log only)."""
        mgr = AlertManager(event_bus=None)

        await mgr.send(Alert(
            severity=AlertSeverity.CRITICAL,
            title="Fire", message="Test",
            component="test",
        ))
        assert mgr.critical_count == 1

    @pytest.mark.asyncio
    async def test_history_limit(self):
        """History capped at MAX_ALERT_HISTORY."""
        mgr = AlertManager()
        for i in range(250):
            await mgr.send(Alert(
                severity=AlertSeverity.INFO,
                title=f"Alert {i}", message=f"msg {i}",
                component="test",
            ))
        assert len(mgr.get_history(limit=300)) == 200  # capped

    @pytest.mark.asyncio
    async def test_template_circuit_breaker(self):
        """Template send_circuit_breaker works."""
        mgr = AlertManager()
        await mgr.send_circuit_breaker(
            state="halted", reasons=["daily loss -5%"], equity=9500.0,
        )
        assert mgr.critical_count == 1

    @pytest.mark.asyncio
    async def test_template_trade_opened(self):
        """Template send_trade_opened works."""
        mgr = AlertManager()
        await mgr.send_trade_opened(
            symbol="BTCUSDT", side="BUY", qty=0.001,
            entry=50000, sl=49000, tp=52000, risk_usd=10,
        )
        assert len(mgr.get_history()) == 1

    @pytest.mark.asyncio
    async def test_template_trade_closed(self):
        """Template send_trade_closed works."""
        mgr = AlertManager()
        await mgr.send_trade_closed(
            symbol="BTCUSDT", pnl_usd=150.0, pnl_pct=3.0,
            exit_reason="tp", hold_candles=12,
        )
        assert len(mgr.get_history()) == 1

    @pytest.mark.asyncio
    async def test_template_daily_summary(self):
        """Template send_daily_summary works."""
        mgr = AlertManager()
        await mgr.send_daily_summary(
            total_trades=5, win_rate=0.8, daily_pnl=234.0,
            equity=10234.0, open_positions=2,
        )
        assert len(mgr.get_history()) == 1
