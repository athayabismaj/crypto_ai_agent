"""
Latency Guard.

Mengawasi network latency untuk perlindungan order execution.
"""

from collections import deque
from datetime import datetime
from typing import TypedDict

from runtime.agent.models.market import LatencyStatus
from runtime.agent.utils.time_utils import utcnow


class RollingStats(TypedDict):
    """Rolling stats dict format."""

    p50: float
    p95: float
    p99: float


class LatencyGuard:
    """Melacak dan melindungi trading selama masa latensi tinggi."""

    def __init__(self) -> None:
        """Inisialisasi konstanta guard dan buffer metrics."""
        self.ws_latency_warn_ms = 500.0
        self.ws_latency_block_ms = 2000.0
        self.rest_latency_warn_ms = 1000.0
        self.rest_latency_block_ms = 3000.0
        self.order_latency_warn_ms = 2000.0
        self.order_latency_block_ms = 5000.0
        self.tick_latency_warn_ms = 500.0
        self.tick_latency_block_ms = 1500.0

        self.latency_window = 20
        self.block_duration_s = 60.0

        self._ws_latencies: deque[float] = deque(maxlen=self.latency_window)
        self._rest_latencies: deque[float] = deque(maxlen=self.latency_window)
        self._order_latencies: deque[float] = deque(maxlen=self.latency_window)
        self._tick_latencies: deque[float] = deque(maxlen=self.latency_window)

        self._last_blocked_time: datetime | None = None
        self._last_block_reason: str = ""

    def record_ws_latency(self, exchange_ts: datetime, received_ts: datetime) -> None:
        """Record ws payload lag."""
        lat = (received_ts - exchange_ts).total_seconds() * 1000
        if lat >= 0:
            self._ws_latencies.append(lat)

    def record_rest_latency(self, duration_ms: float, endpoint: str = "") -> None:
        """Record http trip."""
        self._rest_latencies.append(duration_ms)

    def record_order_latency(self, submit_ts: datetime, fill_ts: datetime) -> None:
        """Record broker execution delay."""
        lat = (fill_ts - submit_ts).total_seconds() * 1000
        if lat >= 0:
            self._order_latencies.append(lat)

    def get_status(self) -> LatencyStatus:
        """Evaluate latencies and warnings."""
        ws_lat = self._avg(self._ws_latencies)
        rest_lat = self._avg(self._rest_latencies)
        order_lat = self._avg(self._order_latencies)
        tick_lat = self._avg(self._tick_latencies)

        warnings: list[str] = []
        should_block = False
        overall_status = "ok"

        # Check block condition
        if ws_lat > self.ws_latency_block_ms:
            warnings.append("WS latency EXTREME (>2000ms). Blocked!")
            should_block = True
            overall_status = "critical"
        elif rest_lat > self.rest_latency_block_ms:
            warnings.append("REST latency EXTREME (>3000ms). Blocked!")
            should_block = True
            overall_status = "critical"
        elif order_lat > self.order_latency_block_ms:
            warnings.append("Order execution EXTREME (>5000ms). Blocked!")
            should_block = True
            overall_status = "critical"

        # Check warnings
        if not should_block:
            if ws_lat > self.ws_latency_warn_ms:
                warnings.append("WS latency > 500ms warning.")
                overall_status = "degraded"
            if rest_lat > self.rest_latency_warn_ms:
                warnings.append("REST latency > 1000ms warning.")
                overall_status = "degraded"
            if order_lat > self.order_latency_warn_ms:
                warnings.append("Order execution > 2000ms warning.")
                overall_status = "degraded"

        if should_block:
            self._last_blocked_time = utcnow()
            self._last_block_reason = warnings[-1]

        # Reset block timeout checking (block until block_duration_s has passed)
        if self._last_blocked_time is not None:
            time_since_block = (utcnow() - self._last_blocked_time).total_seconds()
            if time_since_block < self.block_duration_s:
                should_block = True
                warnings.append(
                    f"In block-mode cooldown (remaining {self.block_duration_s - time_since_block:.1f}s) due to {self._last_block_reason}."
                )
            else:
                self._last_blocked_time = None

        return LatencyStatus(
            ws_latency_ms=ws_lat,
            rest_latency_ms=rest_lat,
            order_latency_ms=order_lat,
            tick_latency_ms=tick_lat,
            overall_status=overall_status,
            should_block_trading=should_block,
            warnings=warnings,
        )

    def should_block_order(self) -> tuple[bool, str]:
        """Tells application whether order execution should be halted due to latency."""
        stat = self.get_status()
        if stat.should_block_trading:
            return True, "; ".join(stat.warnings)
        return False, ""

    def get_rolling_stats(self, window: int = 20) -> RollingStats:
        """Calculate P50, P95, P99."""
        import numpy as np

        vals_list = list(self._ws_latencies)
        vals = vals_list[-window:] if vals_list else [0.0]  # type: ignore[misc]
        if not vals:
            return {"p50": 0.0, "p95": 0.0, "p99": 0.0}

        # Numpy percentile ignores None but we only have floats
        p50 = float(np.percentile(vals, 50))
        p95 = float(np.percentile(vals, 95))
        p99 = float(np.percentile(vals, 99))
        return {"p50": p50, "p95": p95, "p99": p99}

    def _avg(self, dq: deque[float]) -> float:
        """Safely fetch dict average."""
        if not dq:
            return 0.0
        return sum(dq) / len(dq)
