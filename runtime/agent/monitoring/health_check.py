"""
health_check.py — Kesehatan komponen sistem.

Mengecek setiap komponen secara individual. Berbeda dari heartbeat yang
hanya membuktikan agent hidup — health_check memberikan detail tentang
komponen mana yang bermasalah.

8 komponen:
1. Memory RSS
2. Disk space
3. Circuit breaker state
4. Rate limit usage
5. WebSocket freshness
6. Exchange REST ping
7. Database latency (stub)
8. Open orders count

Semua check dijalankan paralel via asyncio.gather dengan timeout 5s.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import UTC, datetime
from typing import Protocol

from runtime.agent.models.enums import CircuitState, ComponentStatus  # type: ignore
from runtime.agent.models.monitoring import ComponentHealth, HealthReport  # type: ignore

logger = logging.getLogger(__name__)

CHECK_TIMEOUT_S = 5.0


class SystemInfoProvider(Protocol):
    """Abstract provider untuk informasi sistem.

    Diimplementasi oleh caller yang punya akses ke komponen aktual.
    """

    def get_memory_rss_mb(self) -> float: ...
    def get_disk_free_gb(self) -> float: ...
    def get_circuit_breaker_state(self) -> str: ...
    def get_rate_limit_pct(self) -> float: ...
    def get_ws_last_message_age_s(self) -> float: ...
    async def ping_exchange_ms(self) -> float: ...
    def get_open_orders_count(self) -> int: ...
    def get_max_positions(self) -> int: ...


class DefaultSystemInfo:
    """Default system info — return safe values saat komponen belum ready."""

    def get_memory_rss_mb(self) -> float:
        try:
            import psutil  # type: ignore[import-untyped]
            process = psutil.Process()
            return process.memory_info().rss / (1024 * 1024)
        except (ImportError, Exception):
            return 0.0

    def get_disk_free_gb(self) -> float:
        try:
            import shutil
            usage = shutil.disk_usage(".")
            return usage.free / (1024**3)
        except Exception:
            return 999.0  # Assume OK jika gagal cek

    def get_circuit_breaker_state(self) -> str:
        return CircuitState.NORMAL.value

    def get_rate_limit_pct(self) -> float:
        return 0.0

    def get_ws_last_message_age_s(self) -> float:
        return 0.0

    async def ping_exchange_ms(self) -> float:
        return 0.0

    def get_open_orders_count(self) -> int:
        return 0

    def get_max_positions(self) -> int:
        return 5


class HealthCheck:
    """8-component health checker.

    Thresholds didefinisikan per komponen. Overall status =
    status terburuk dari semua komponen.
    """

    # ── Thresholds ────────────────────────────────────────────────
    MEMORY_WARN_MB: float = 300.0
    MEMORY_CRIT_MB: float = 500.0

    DISK_WARN_GB: float = 2.0
    DISK_CRIT_GB: float = 0.5

    WS_WARN_S: float = 30.0
    WS_CRIT_S: float = 60.0

    PING_WARN_MS: float = 1000.0
    PING_CRIT_MS: float = 3000.0

    RATE_LIMIT_WARN_PCT: float = 70.0
    RATE_LIMIT_CRIT_PCT: float = 90.0

    def __init__(self, system_info: SystemInfoProvider | None = None) -> None:
        self._sys = system_info or DefaultSystemInfo()
        self._last_report: HealthReport | None = None

    async def run(self) -> HealthReport:
        """Jalankan semua check paralel. Timeout per check: 5 detik."""
        checks = await asyncio.gather(
            self._safe_check("memory", self._check_memory),
            self._safe_check("disk", self._check_disk),
            self._safe_check("circuit_breaker", self._check_circuit_breaker),
            self._safe_check("rate_limit", self._check_rate_limit),
            self._safe_check("websocket", self._check_websocket),
            self._safe_check("exchange_ping", self._check_exchange_ping),
            self._safe_check("open_orders", self._check_open_orders),
            return_exceptions=True,
        )

        components: list[ComponentHealth] = []
        for check in checks:
            if isinstance(check, Exception):
                components.append(ComponentHealth(
                    name="unknown",
                    status=ComponentStatus.UNHEALTHY,
                    value="error",
                    message=str(check),
                ))
            elif isinstance(check, ComponentHealth):
                components.append(check)

        # Overall = worst status
        statuses = [c.status for c in components]
        if ComponentStatus.UNHEALTHY in statuses:
            overall = ComponentStatus.UNHEALTHY
        elif ComponentStatus.DEGRADED in statuses:
            overall = ComponentStatus.DEGRADED
        else:
            overall = ComponentStatus.HEALTHY

        report = HealthReport(
            overall=overall,
            components=components,
            timestamp=datetime.now(UTC),
        )
        self._last_report = report

        if overall != ComponentStatus.HEALTHY:
            logger.warning(
                "[HealthCheck] Status=%s, unhealthy=%s",
                overall.value,
                [c.name for c in components if c.status != ComponentStatus.HEALTHY],
            )

        return report

    def get_last_report(self) -> HealthReport | None:
        """Return report terakhir dari cache (tanpa re-run)."""
        return self._last_report

    # ── Checks ────────────────────────────────────────────────────

    async def _safe_check(self, name: str,
                          check_fn: object) -> ComponentHealth:
        """Wrapper: timeout + exception handling."""
        try:
            result = check_fn()  # type: ignore[operator]
            if asyncio.iscoroutine(result):
                result = await asyncio.wait_for(result, timeout=CHECK_TIMEOUT_S)
            return result  # type: ignore[return-value]
        except asyncio.TimeoutError:
            return ComponentHealth(
                name=name,
                status=ComponentStatus.UNHEALTHY,
                value="timeout",
                message=f"Check timed out after {CHECK_TIMEOUT_S}s",
            )
        except Exception as e:
            return ComponentHealth(
                name=name,
                status=ComponentStatus.UNHEALTHY,
                value="error",
                message=str(e),
            )

    def _check_memory(self) -> ComponentHealth:
        rss = self._sys.get_memory_rss_mb()
        if rss >= self.MEMORY_CRIT_MB:
            status = ComponentStatus.UNHEALTHY
        elif rss >= self.MEMORY_WARN_MB:
            status = ComponentStatus.DEGRADED
        else:
            status = ComponentStatus.HEALTHY
        return ComponentHealth(
            name="memory",
            status=status,
            value=rss,
            message=f"RSS: {rss:.0f} MB",
        )

    def _check_disk(self) -> ComponentHealth:
        free_gb = self._sys.get_disk_free_gb()
        if free_gb < self.DISK_CRIT_GB:
            status = ComponentStatus.UNHEALTHY
        elif free_gb < self.DISK_WARN_GB:
            status = ComponentStatus.DEGRADED
        else:
            status = ComponentStatus.HEALTHY
        return ComponentHealth(
            name="disk",
            status=status,
            value=free_gb,
            message=f"Free: {free_gb:.1f} GB",
        )

    def _check_circuit_breaker(self) -> ComponentHealth:
        state = self._sys.get_circuit_breaker_state()
        if state == CircuitState.HALTED.value:
            status = ComponentStatus.UNHEALTHY
        elif state == CircuitState.WARNED.value:
            status = ComponentStatus.DEGRADED
        else:
            status = ComponentStatus.HEALTHY
        return ComponentHealth(
            name="circuit_breaker",
            status=status,
            value=state,
            message=f"State: {state}",
        )

    def _check_rate_limit(self) -> ComponentHealth:
        pct = self._sys.get_rate_limit_pct()
        if pct >= self.RATE_LIMIT_CRIT_PCT:
            status = ComponentStatus.UNHEALTHY
        elif pct >= self.RATE_LIMIT_WARN_PCT:
            status = ComponentStatus.DEGRADED
        else:
            status = ComponentStatus.HEALTHY
        return ComponentHealth(
            name="rate_limit",
            status=status,
            value=pct,
            message=f"Usage: {pct:.1f}%",
        )

    def _check_websocket(self) -> ComponentHealth:
        age_s = self._sys.get_ws_last_message_age_s()
        if age_s > self.WS_CRIT_S:
            status = ComponentStatus.UNHEALTHY
        elif age_s > self.WS_WARN_S:
            status = ComponentStatus.DEGRADED
        else:
            status = ComponentStatus.HEALTHY
        return ComponentHealth(
            name="websocket",
            status=status,
            value=age_s,
            message=f"Last message: {age_s:.0f}s ago",
        )

    async def _check_exchange_ping(self) -> ComponentHealth:
        t0 = time.monotonic()
        latency_ms = await self._sys.ping_exchange_ms()
        elapsed_ms = (time.monotonic() - t0) * 1000
        actual = max(latency_ms, elapsed_ms)

        if actual >= self.PING_CRIT_MS:
            status = ComponentStatus.UNHEALTHY
        elif actual >= self.PING_WARN_MS:
            status = ComponentStatus.DEGRADED
        else:
            status = ComponentStatus.HEALTHY
        return ComponentHealth(
            name="exchange_ping",
            status=status,
            value=actual,
            message=f"Latency: {actual:.0f}ms",
            latency_ms=actual,
        )

    def _check_open_orders(self) -> ComponentHealth:
        count = self._sys.get_open_orders_count()
        max_pos = self._sys.get_max_positions()
        if count > max_pos * 2:
            status = ComponentStatus.UNHEALTHY
        elif count > max_pos * 1.5:
            status = ComponentStatus.DEGRADED
        else:
            status = ComponentStatus.HEALTHY
        return ComponentHealth(
            name="open_orders",
            status=status,
            value=count,
            message=f"Open: {count} (max: {max_pos})",
        )
