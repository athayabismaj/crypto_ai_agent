"""
connectivity.py — Cek koneksi jaringan ke exchange dan internet.

Mengecek ketersediaan endpoint penting dan server time drift.
Digunakan oleh health_check.py.
"""

from __future__ import annotations

import asyncio
import logging
import time

from runtime.agent.models.enums import ConnStatus  # type: ignore

logger = logging.getLogger(__name__)

# Default timeout per endpoint
DEFAULT_TIMEOUT_S = 5.0
SLOW_THRESHOLD_MS = 2000.0


class ConnectivityChecker:
    """Cek konektivitas ke exchange endpoints.

    Endpoints:
        binance_api:  https://api.binance.com/api/v3/ping
        binance_fapi: https://fapi.binance.com/fapi/v1/ping
        internet:     https://httpbin.org/get

    Status:
        UP:   response < 2000ms
        SLOW: response >= 2000ms
        DOWN: timeout atau error
    """

    ENDPOINTS: dict[str, str] = {
        "binance_api": "https://api.binance.com/api/v3/ping",
        "binance_fapi": "https://fapi.binance.com/fapi/v1/ping",
        "internet": "https://httpbin.org/get",
    }

    def __init__(self, timeout_s: float = DEFAULT_TIMEOUT_S) -> None:
        self._timeout_s = timeout_s
        self._last_results: dict[str, ConnStatus] = {}

    async def check_all(self) -> dict[str, ConnStatus]:
        """Cek semua endpoints secara paralel.

        Return dict[endpoint_name, ConnStatus].
        """
        tasks = {name: self._check_endpoint(url) for name, url in self.ENDPOINTS.items()}

        results: dict[str, ConnStatus] = {}
        for name, coro in tasks.items():
            try:
                status = await coro
            except Exception:
                status = ConnStatus.DOWN
            results[name] = status

        self._last_results = results
        return results

    async def is_exchange_reachable(self, exchange: str = "binance") -> bool:
        """Quick check — digunakan oleh health_check."""
        key = f"{exchange}_api"
        url = self.ENDPOINTS.get(key)
        if not url:
            return False
        status = await self._check_endpoint(url)
        return status != ConnStatus.DOWN

    async def get_server_time_drift_ms(self) -> float:
        """Bandingkan waktu lokal dengan waktu server exchange.

        Drift > 1000ms bisa menyebabkan order ditolak (-1021 TIMESTAMP).
        Return drift dalam milidetik. Negatif berarti lokal di belakang server.
        """
        try:
            import aiohttp  # type: ignore[import-untyped]

            url = "https://api.binance.com/api/v3/time"
            async with aiohttp.ClientSession() as session:
                t_before = time.time() * 1000
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                    data = await resp.json()
                t_after = time.time() * 1000

                server_time = data.get("serverTime", 0)
                local_time = (t_before + t_after) / 2  # estimasi waktu saat request
                drift = local_time - server_time
                return drift
        except Exception as e:
            logger.warning("[Connectivity] Failed to check server time drift: %s", e)
            return 0.0

    @property
    def last_results(self) -> dict[str, ConnStatus]:
        return self._last_results

    # ── Private ───────────────────────────────────────────────────

    async def _check_endpoint(self, url: str) -> ConnStatus:
        """Ping satu endpoint, return ConnStatus."""
        try:
            import aiohttp  # type: ignore[import-untyped]

            t0 = time.monotonic()
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    url,
                    timeout=aiohttp.ClientTimeout(total=self._timeout_s),
                ) as resp:
                    await resp.read()
            latency_ms = (time.monotonic() - t0) * 1000

            if latency_ms >= SLOW_THRESHOLD_MS:
                return ConnStatus.SLOW
            return ConnStatus.UP

        except ImportError:
            # aiohttp belum diinstall — log dan return DOWN
            logger.debug("[Connectivity] aiohttp not installed, skipping %s", url)
            return ConnStatus.DOWN
        except asyncio.TimeoutError:
            return ConnStatus.DOWN
        except Exception as e:
            logger.debug("[Connectivity] %s → DOWN: %s", url, e)
            return ConnStatus.DOWN
