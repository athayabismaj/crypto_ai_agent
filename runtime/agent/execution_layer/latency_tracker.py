"""
Latency Tracker
Melakukan profilisasi dan memonitor P95 ping connection speed layer AI ke Binance API.
Berguna bagi model AI (latency_guard) untuk mendeteksi apabila server binance sedang lagging/DDoS
"""

import statistics
from collections import deque
from dataclasses import dataclass
from datetime import datetime

from runtime.shared.utils import utcnow  # type: ignore


@dataclass
class LatencyRecord:
    endpoint: str
    latency_ms: float
    timestamp: datetime
    exchange: str
    success: bool


@dataclass
class LatencyStats:
    p50_ms: float
    p95_ms: float
    p99_ms: float
    total_records: int


class LatencyTracker:
    WINDOW_SIZE = 50  # tracking the last 50 calls

    def __init__(self):
        self._records = deque(maxlen=self.WINDOW_SIZE)

    def record(
        self, endpoint: str, latency_ms: float, exchange: str = "binance", success: bool = True
    ) -> None:
        """
        Catat waktu tembus milidetik server.
        Implementasinya kelak diletakkan sebagai decorator httpx / aiohttp session.
        """
        self._records.append(
            LatencyRecord(
                endpoint=endpoint,
                latency_ms=latency_ms,
                timestamp=utcnow(),
                exchange=exchange,
                success=success,
            )
        )

    def get_stats(self, endpoint: str | None = None, exchange: str | None = None) -> LatencyStats:
        """Return statistik P50, P95, P99 time."""
        filtered = list(self._records)
        if endpoint:
            filtered = [r for r in filtered if r.endpoint == endpoint]
        if exchange:
            filtered = [r for r in filtered if r.exchange == exchange]

        latencies = [r.latency_ms for r in filtered]
        if not latencies:
            return LatencyStats(0.0, 0.0, 0.0, 0)

        latencies.sort()
        n = len(latencies)

        # simple percentiles calculation
        def p(pct):
            return latencies[int(n * pct) - 1] if n > 0 else 0.0

        return LatencyStats(
            p50_ms=statistics.median(latencies), p95_ms=p(0.95), p99_ms=p(0.99), total_records=n
        )

    def is_acceptable(self, threshold_ms: float = 2000.0) -> bool:
        """Dipanggil intelligence latency_guard sebelum allow pre_trade"""
        stats = self.get_stats()
        if stats.total_records < 5:
            return True  # belum ada bukti lag network
        return stats.p95_ms < threshold_ms
