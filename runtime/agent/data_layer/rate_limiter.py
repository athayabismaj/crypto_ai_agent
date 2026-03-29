"""
Layer Data: Rate Limiter
Mencegah IP Banned dari Binance dengan mematuhi Request Weight dan Order Count limits.
"""

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from runtime.agent.core.config_schema import AgentConfig  # type: ignore

logger = logging.getLogger(__name__)


@dataclass
class RateLimitStatus:
    weight_used: int
    weight_limit: int
    weight_pct: float
    orders_10s: int
    orders_1d: int
    seconds_to_reset: float
    is_near_limit: bool


class BinanceRateLimiter:
    """Implementasi Binance Limit per 1 Menit & 10 Detik."""

    # Defaults dari dokumen Binance
    DEFAULT_WEIGHT_LIMIT = 1200
    DEFAULT_ORDER_LIMIT_10S = 50

    # Weight Table standar
    ENDPOINT_WEIGHTS = {
        "/api/v3/ticker/bookTicker": 2,
        "/api/v3/klines": 2,
        "/api/v3/account": 20,
        "/api/v3/openOrders": 6,
        "/api/v3/order": 1,
        "/fapi/v2/account": 5,
        "/fapi/v2/positionRisk": 5,
        "/api/v3/exchangeInfo": 20,
    }

    def __init__(self, config: AgentConfig) -> None:
        self.config = config
        self._lock = asyncio.Lock()

        # Pengaturan Limit
        self.weight_limit = getattr(config, "ws_weight_limit", self.DEFAULT_WEIGHT_LIMIT)
        self.safe_ratio_weight = getattr(config, "ws_weight_safe_ratio", 0.80)
        self.order_limit_10s = getattr(config, "ws_order_limit_10s", self.DEFAULT_ORDER_LIMIT_10S)
        self.safe_ratio_order = getattr(config, "ws_order_safe_ratio", 0.80)
        self.warn_pct = getattr(config, "ws_warn_at_pct", 0.70)

        # State Internal
        self.current_weight = 0
        self.current_orders_10s = 0
        self.current_orders_1d = 0
        self.last_reset_time = self._current_time()
        self.last_10s_reset_time = self._current_time()

    def _current_time(self) -> float:
        return datetime.now(timezone.utc).timestamp()

    async def _reset_if_needed(self) -> None:
        now = self._current_time()
        # Reset Weight (1 Menit)
        if now - self.last_reset_time >= 60.0:
            self.current_weight = 0
            self.last_reset_time = now

        # Reset Order (10 Detik)
        if now - self.last_10s_reset_time >= 10.0:
            self.current_orders_10s = 0
            self.last_10s_reset_time = now

    def _get_weight(self, endpoint: str) -> int:
        for k, v in self.ENDPOINT_WEIGHTS.items():
            if k in endpoint:
                return v
        return 1  # Fallback

    async def acquire(
        self, endpoint: str, weight: int | None = None, is_order: bool = False
    ) -> None:
        """Tunggu sampai ada 'slot' yang tersedia."""
        req_weight = weight if weight is not None else self._get_weight(endpoint)

        async with self._lock:
            await self._reset_if_needed()

            # Cek Weight Limit
            max_safe_weight = self.weight_limit * self.safe_ratio_weight
            while self.current_weight + req_weight > max_safe_weight:
                now = self._current_time()
                sleep_time = max(0.1, 60.0 - (now - self.last_reset_time))
                logger.warning(
                    f"[RateLimiter] Mendekati Weight Limit ({self.current_weight}/{max_safe_weight}). "
                    f"Tidur {sleep_time:.2f}s..."
                )
                await asyncio.sleep(sleep_time)
                await self._reset_if_needed()

            self.current_weight += req_weight

            # Cek Order Count Limit
            if is_order:
                max_safe_orders = self.order_limit_10s * self.safe_ratio_order
                while self.current_orders_10s + 1 > max_safe_orders:
                    now = self._current_time()
                    sleep_time = max(0.1, 10.0 - (now - self.last_10s_reset_time))
                    logger.warning(
                        f"[RateLimiter] Mendekati Limit Order 10s ({self.current_orders_10s}/{max_safe_orders}). "
                        f"Tidur {sleep_time:.2f}s..."
                    )
                    await asyncio.sleep(sleep_time)
                    await self._reset_if_needed()

                self.current_orders_10s += 1
                self.current_orders_1d += (
                    1  # Kita catat, tidak memblokir karena daily limit besar (>160k)
                )

    def update_from_headers(self, headers: dict[str, Any]) -> None:
        """Parse header Binance X-MBX-USED-WEIGHT-1M untuk sinkronisasi paksa."""
        if not getattr(self.config, "ws_sync_from_headers", True):
            return

        header_keys = {k.lower(): v for k, v in headers.items()}
        for k, v in header_keys.items():
            if k == "x-mbx-used-weight-1m":
                try:
                    ext_weight = int(v)
                    # Sinkronkan jika hitungan mereka lebih tinggi (lebih aman)
                    if ext_weight > self.current_weight:
                        self.current_weight = ext_weight
                except ValueError:
                    continue
            elif k == "x-mbx-order-count-10s":
                try:
                    ext_orders_10s = int(v)
                    if ext_orders_10s > self.current_orders_10s:
                        self.current_orders_10s = ext_orders_10s
                except ValueError:
                    continue
            elif k == "x-mbx-order-count-1d":
                try:
                    self.current_orders_1d = max(self.current_orders_1d, int(v))
                except ValueError:
                    continue

    def get_current_usage(self) -> RateLimitStatus:
        pct = self.current_weight / self.weight_limit if self.weight_limit > 0 else 0.0
        now = self._current_time()
        sec_to_reset = max(0.0, 60.0 - (now - self.last_reset_time))

        return RateLimitStatus(
            weight_used=self.current_weight,
            weight_limit=self.weight_limit,
            weight_pct=pct,
            orders_10s=self.current_orders_10s,
            orders_1d=self.current_orders_1d,
            seconds_to_reset=sec_to_reset,
            is_near_limit=self.is_near_limit(self.warn_pct),
        )

    def is_near_limit(self, threshold: float = 0.8) -> bool:
        """Return True jika penggunaan > threshold * limit."""
        if (self.current_weight / self.weight_limit) >= threshold:
            return True
        if (self.current_orders_10s / self.order_limit_10s) >= threshold:
            return True
        return False
