"""
Layer Intelligence: Latency Guard
Mengukur dan memberikan sinyal darurat jika Ping jaringan memburuk.
Mencegah eksekusi pada harga 'Bayangan' akibat delay Exchange.
"""

import logging
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone

from runtime.agent.core.config_schema import AgentConfig  # type: ignore

logger = logging.getLogger(__name__)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class LatencyStatus:
    ws_latency_ms: float
    rest_latency_ms: float
    order_latency_ms: float
    tick_latency_ms: float
    overall_status: str  # 'ok' | 'degraded' | 'high' | 'critical'
    should_block_trading: bool
    warnings: list[str]


class LatencyGuard:
    """Pemantau Lag Jaringan Waktu Nyata."""

    def __init__(self, config: AgentConfig) -> None:
        self.config = config

        # Thresholds
        self.ws_warn_ms = getattr(config, "ws_latency_warn_ms", 500.0)
        self.ws_block_ms = getattr(config, "ws_latency_block_ms", 2000.0)
        self.rest_warn_ms = getattr(config, "rest_latency_warn_ms", 1000.0)
        self.rest_block_ms = getattr(config, "rest_latency_block_ms", 3000.0)

        self.window_size = getattr(config, "latency_window", 20)
        self.block_dur_s = getattr(config, "latency_block_duration_s", 60.0)

        # Caches for rolling avg
        self._ws_queue: deque[float] = deque(maxlen=self.window_size)
        self._rest_queue: deque[float] = deque(maxlen=self.window_size)
        self._order_queue: deque[float] = deque(maxlen=self.window_size)

        # Blocking state
        self._last_block_time: float = 0.0

    def record_ws_latency(self, exchange_ts: datetime, received_ts: datetime) -> None:
        ms = (received_ts - exchange_ts).total_seconds() * 1000
        if ms < 0:
            ms = 0.0  # Server NTP desync correction
        self._ws_queue.append(ms)

    def record_rest_latency(self, duration_ms: float, endpoint: str) -> None:
        self._rest_queue.append(duration_ms)

    def record_order_latency(self, submit_ts: datetime, fill_ts: datetime) -> None:
        ms = (fill_ts - submit_ts).total_seconds() * 1000
        if ms > 0:
            self._order_queue.append(ms)

    def get_status(self) -> LatencyStatus:
        ws_avg = sum(self._ws_queue) / len(self._ws_queue) if self._ws_queue else 0.0
        rest_avg = sum(self._rest_queue) / len(self._rest_queue) if self._rest_queue else 0.0
        order_avg = sum(self._order_queue) / len(self._order_queue) if self._order_queue else 0.0

        warnings = []
        status = "ok"
        blocked = False

        # Check Blocking Conditions
        if ws_avg > self.ws_block_ms or rest_avg > self.rest_block_ms:
            status = "critical"
            blocked = True
            self._last_block_time = utcnow().timestamp()
            warnings.append(f"BLOCK: Latensi Tinggi (WS={ws_avg:.1f}ms, REST={rest_avg:.1f}ms)")

        elif ws_avg > self.ws_warn_ms:
            status = "high"
            warnings.append(f"WARN: WS Latency Tinggi ({ws_avg:.1f}ms)")

        elif rest_avg > self.rest_warn_ms:
            if status != "high":
                status = "degraded"
            warnings.append(f"WARN: REST Latency Lambat ({rest_avg:.1f}ms)")

        # Enforce lock duration if recently blocked
        now = utcnow().timestamp()
        if now - self._last_block_time < self.block_dur_s:
            blocked = True
            status = "critical"
            rem = self.block_dur_s - (now - self._last_block_time)
            warnings.append(f"LOCKED: Jaringan Pendinginan, Sisa {rem:.1f}s")

        if blocked and len(warnings) > 0:
            logger.warning(warnings[0])

        return LatencyStatus(
            ws_latency_ms=ws_avg,
            rest_latency_ms=rest_avg,
            order_latency_ms=order_avg,
            tick_latency_ms=0.0,
            overall_status=status,
            should_block_trading=blocked,
            warnings=warnings,
        )

    def should_block_order(self) -> tuple[bool, str]:
        st = self.get_status()
        if st.should_block_trading:
            reason = ", ".join(st.warnings) if st.warnings else "Koneksi Tidak Stabil"
            return True, reason
        return False, ""

    def get_rolling_stats(self) -> dict[str, float]:
        stats = {}
        if self._ws_queue:
            q = sorted(self._ws_queue)
            p50 = q[len(q) // 2]
            p95 = q[int(len(q) * 0.95)]
            stats["ws_p50"] = p50
            stats["ws_p95"] = p95
        return stats
