"""
BinanceRateLimiter — Global API rate limiter.

Semua request ke Binance WAJIB melewati acquire() sebelum dieksekusi.
Melacak request weight (1 menit) dan order count (10s / 1d).
"""

import asyncio
import logging
from datetime import UTC, datetime

from runtime.agent.models import RateLimitStatus  # type: ignore

logger = logging.getLogger(__name__)

# ── Weight table — endpoint → default weight ──────────────────────
WEIGHT_TABLE: dict[str, int] = {
    "/api/v3/ticker/bookTicker": 2,
    "/api/v3/klines": 2,
    "/api/v3/account": 20,
    "/api/v3/openOrders": 6,
    "/api/v3/order": 1,
    "/api/v3/exchangeInfo": 20,
    "/api/v3/time": 1,
    "/api/v3/depth": 10,
    "/fapi/v2/account": 5,
    "/fapi/v2/positionRisk": 5,
    "/fapi/v1/premiumIndex": 1,
}


class BinanceRateLimiter:
    """
    Global rate limiter untuk Binance API.

    Dua jenis limit yang dilacak:
    1. Request weight — rolling 1 menit (default 1200)
    2. Order count — rolling 10 detik (default 50) + 1 hari
    """

    def __init__(
        self,
        weight_limit: int = 1200,
        safe_ratio: float = 0.80,
        order_limit_10s: int = 50,
        order_safe_ratio: float = 0.80,
        warn_at_pct: float = 0.70,
    ) -> None:
        self._weight_limit = weight_limit
        self._safe_ratio = safe_ratio
        self._order_limit_10s = order_limit_10s
        self._order_safe_ratio = order_safe_ratio
        self._warn_at_pct = warn_at_pct

        # Tracking state
        self._weight_used: int = 0
        self._weight_reset_at: float = 0.0  # timestamp detik
        self._orders_10s: int = 0
        self._orders_10s_reset_at: float = 0.0
        self._orders_1d: int = 0

        self._lock = asyncio.Lock()

    # ── Public API ─────────────────────────────────────────────

    async def acquire(
        self,
        endpoint: str = "",
        weight: int | None = None,
        is_order: bool = False,
    ) -> None:
        """
        Tunggu sampai ada slot yang tersedia.

        Jika current_weight + weight > limit × safe_ratio,
        sleep sampai window reset.
        """
        if weight is None:
            weight = WEIGHT_TABLE.get(endpoint, 1)

        async with self._lock:
            now = datetime.now(UTC).timestamp()
            self._maybe_reset_windows(now)

            # Cek weight limit
            max_weight = int(self._weight_limit * self._safe_ratio)
            if self._weight_used + weight > max_weight:
                wait_s = max(0.0, self._weight_reset_at - now)
                logger.warning(
                    "Rate limit: weight %d + %d > %d, waiting %.1fs",
                    self._weight_used,
                    weight,
                    max_weight,
                    wait_s,
                )
                if wait_s > 0:
                    await asyncio.sleep(wait_s)
                    self._weight_used = 0
                    self._weight_reset_at = datetime.now(UTC).timestamp() + 60.0

            # Cek order limit
            if is_order:
                max_orders = int(self._order_limit_10s * self._order_safe_ratio)
                if self._orders_10s + 1 > max_orders:
                    wait_s = max(0.0, self._orders_10s_reset_at - now)
                    logger.warning(
                        "Rate limit: orders %d >= %d, waiting %.1fs",
                        self._orders_10s,
                        max_orders,
                        wait_s,
                    )
                    if wait_s > 0:
                        await asyncio.sleep(wait_s)
                        self._orders_10s = 0
                        self._orders_10s_reset_at = datetime.now(UTC).timestamp() + 10.0

            # Record usage
            self._weight_used += weight
            if self._weight_reset_at == 0.0:
                self._weight_reset_at = now + 60.0

            if is_order:
                self._orders_10s += 1
                self._orders_1d += 1
                if self._orders_10s_reset_at == 0.0:
                    self._orders_10s_reset_at = now + 10.0

            # Warning log
            usage_pct = self._weight_used / self._weight_limit
            if usage_pct >= self._warn_at_pct:
                logger.warning(
                    "Rate limit warning: %.0f%% of weight limit used",
                    usage_pct * 100,
                )

    def update_from_headers(self, headers: dict[str, str]) -> None:
        """
        Sync weight aktual dari response headers Binance.

        Header yang di-parse:
        - X-MBX-USED-WEIGHT-1M
        - X-MBX-ORDER-COUNT-10S
        - X-MBX-ORDER-COUNT-1D
        """
        if "X-MBX-USED-WEIGHT-1M" in headers:
            try:
                self._weight_used = int(headers["X-MBX-USED-WEIGHT-1M"])
            except ValueError:
                pass

        if "X-MBX-ORDER-COUNT-10S" in headers:
            try:
                self._orders_10s = int(headers["X-MBX-ORDER-COUNT-10S"])
            except ValueError:
                pass

        if "X-MBX-ORDER-COUNT-1D" in headers:
            try:
                self._orders_1d = int(headers["X-MBX-ORDER-COUNT-1D"])
            except ValueError:
                pass

    def get_current_usage(self) -> RateLimitStatus:
        """Return snapshot penggunaan rate limit saat ini."""
        now = datetime.now(UTC).timestamp()
        self._maybe_reset_windows(now)
        seconds_to_reset = max(0.0, self._weight_reset_at - now)

        return RateLimitStatus(
            weight_used=self._weight_used,
            weight_limit=self._weight_limit,
            weight_pct=round(self._weight_used / self._weight_limit * 100, 1),  # type: ignore[call-overload]
            orders_10s=self._orders_10s,
            orders_1d=self._orders_1d,
            seconds_to_reset=round(seconds_to_reset, 1),  # type: ignore[call-overload]
            is_near_limit=self.is_near_limit(),
        )

    def is_near_limit(self, threshold: float = 0.80) -> bool:
        """Return True jika penggunaan > threshold × limit."""
        return self._weight_used > self._weight_limit * threshold

    # ── Internal ───────────────────────────────────────────────

    def _maybe_reset_windows(self, now: float) -> None:
        """Reset counters jika window sudah expired."""
        if self._weight_reset_at > 0 and now >= self._weight_reset_at:
            self._weight_used = 0
            self._weight_reset_at = 0.0

        if self._orders_10s_reset_at > 0 and now >= self._orders_10s_reset_at:
            self._orders_10s = 0
            self._orders_10s_reset_at = 0.0
