"""
Layer Data: WebSocket Client
Pengendali Single-Connection Multiplexed Streams untuk Feed Data Exchange.
Memiliki State Machine Reconnect: CONNECTED -> DISCONNECTED -> RECONNECTING -> STALE / FAILED.
"""

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class WsState(Enum):
    CONNECTED = "CONNECTED"
    DISCONNECTED = "DISCONNECTED"
    RECONNECTING = "RECONNECTING"
    FAILED = "FAILED"
    STALE = "STALE"


@dataclass
class ConnectionStats:
    state: WsState
    messages_received: int
    last_message_ts: datetime
    connect_time: datetime | None = None
    reconnect_attempts: int = 0


class ListenKeyManager:
    """Manajer Kunci Akses Stream Khusus User (Order Fills, Posisi, Saldo)."""

    REFRESH_INTERVAL = timedelta(minutes=30)

    def __init__(self) -> None:
        self.listen_key: str | None = None
        self._refresh_task: asyncio.Task | None = None
        self._last_refresh = utcnow()

    async def get_or_create(self) -> str:
        # MOCK HTTP CALL /api/v3/userDataStream
        if not self.listen_key:
            self.listen_key = "MOCK_LISTEN_KEY_123"
            logger.info(f"[ListenKey] Dibuat: {self.listen_key}")
        return self.listen_key

    async def refresh(self, listen_key: str) -> bool:
        # MOCK HTTP PUT /api/v3/userDataStream
        logger.debug(f"[ListenKey] Diperpanjang: {listen_key}")
        self._last_refresh = utcnow()
        return True

    async def delete(self, listen_key: str) -> bool:
        # MOCK HTTP DELETE /api/v3/userDataStream
        self.listen_key = None
        return True

    async def start_auto_refresh(self) -> None:
        while True:
            await asyncio.sleep(self.REFRESH_INTERVAL.total_seconds())
            if self.listen_key:
                success = await self.refresh(self.listen_key)
                if not success:
                    logger.warning(
                        "[ListenKey] Gagal refresh! Akan membuat baru pada siklus berikutnya."
                    )
                    self.listen_key = None


class WebSocketClient:
    """
    Klien WebSocket Utama.
    Sistem menggunakan 'Multiplexed stream' di Binance untuk menghemat TCP Connects.
    """

    def __init__(self) -> None:
        self.state = WsState.DISCONNECTED
        self.stats = ConnectionStats(WsState.DISCONNECTED, 0, utcnow())
        self.listen_key_mgr = ListenKeyManager()

        # Callbacks registries
        self._candle_cbs: dict[str, list[Callable[[Any], Awaitable[None]]]] = {}
        self._ticker_cbs: dict[str, list[Callable[[Any], Awaitable[None]]]] = {}
        self._orderbook_cbs: dict[str, list[Callable[[Any], Awaitable[None]]]] = {}

        # User stream callbacks
        self._user_order_cbs: list[Callable[[Any], Awaitable[None]]] = []
        self._user_balance_cbs: list[Callable[[Any], Awaitable[None]]] = []
        self._user_pos_cbs: list[Callable[[Any], Awaitable[None]]] = []

        # Internal Tasks
        self._main_task: asyncio.Task | None = None
        self._watchdog_task: asyncio.Task | None = None

        self.MAX_RECONNECT = 10
        self.STALE_TIMEOUT_S = 30.0

    async def connect(self) -> None:
        if self.state in [WsState.CONNECTED, WsState.RECONNECTING]:
            return

        self.state = WsState.RECONNECTING
        logger.info("[WebSocket] Mencoba inisialisasi koneksi multiplexed...")

        # Setup Listen Key for User Stream
        _ = await self.listen_key_mgr.get_or_create()
        asyncio.create_task(self.listen_key_mgr.start_auto_refresh())

        self.state = WsState.CONNECTED
        self.stats.connect_time = utcnow()
        self.stats.state = self.state

        # Mulai Watchdog
        if not self._watchdog_task or self._watchdog_task.done():
            self._watchdog_task = asyncio.create_task(self._watchdog())

    async def disconnect(self) -> None:
        logger.info("[WebSocket] Memutus koneksi gracefully.")
        self.state = WsState.DISCONNECTED
        if self.listen_key_mgr.listen_key:
            await self.listen_key_mgr.delete(self.listen_key_mgr.listen_key)
        if self._watchdog_task:
            self._watchdog_task.cancel()

    async def _handle_reconnect(self) -> None:
        """Sistem Exponential Backoff Reconnect."""
        self.state = WsState.RECONNECTING
        attempt = 0
        while attempt < self.MAX_RECONNECT:
            attempt += 1
            delay = min(60, 1.0 * (2 ** (attempt - 1)))
            logger.warning(
                f"[WebSocket] Reconnecting {attempt}/{self.MAX_RECONNECT} dalam {delay}s..."
            )
            await asyncio.sleep(delay)
            # Dummy Success For Now
            self.state = WsState.CONNECTED
            self.stats.reconnect_attempts = attempt
            return

        self.state = WsState.FAILED
        logger.error("[WebSocket] CRITICAL: Reconnect Maksimum Tercapai. Aliran Harga Padam.")

    async def _watchdog(self) -> None:
        """Background task pendeteksi Stale Data (Jaringan nyangkut namun Socket masih terbuka)."""
        while True:
            await asyncio.sleep(5)
            if self.state == WsState.CONNECTED:
                now = utcnow()
                elapsed = (now - self.stats.last_message_ts).total_seconds()
                if elapsed > self.STALE_TIMEOUT_S and self.stats.messages_received > 0:
                    logger.warning(
                        "[WebSocket] STALE TERDETEKSI. Tidak ada pesan > 30s. Force Reconnect!"
                    )
                    self.state = WsState.STALE
                    asyncio.create_task(self._handle_reconnect())

    def subscribe_candle(
        self, symbol: str, tf: str, callback: Callable[[Any], Awaitable[None]]
    ) -> None:
        key = f"{symbol.lower()}@kline_{tf}"
        if key not in self._candle_cbs:
            self._candle_cbs[key] = []
        self._candle_cbs[key].append(callback)

    def subscribe_ticker(self, symbol: str, callback: Callable[[Any], Awaitable[None]]) -> None:
        key = f"{symbol.lower()}@bookTicker"
        if key not in self._ticker_cbs:
            self._ticker_cbs[key] = []
        self._ticker_cbs[key].append(callback)

    def subscribe_orderbook(self, symbol: str, callback: Callable[[Any], Awaitable[None]]) -> None:
        key = f"{symbol.lower()}@depth@100ms"
        if key not in self._orderbook_cbs:
            self._orderbook_cbs[key] = []
        self._orderbook_cbs[key].append(callback)

    def subscribe_user_stream(
        self,
        on_order_update: Callable[[Any], Awaitable[None]],
        on_balance_update: Callable[[Any], Awaitable[None]],
        on_position_update: Callable[[Any], Awaitable[None]],
    ) -> None:
        self._user_order_cbs.append(on_order_update)
        self._user_balance_cbs.append(on_balance_update)
        self._user_pos_cbs.append(on_position_update)

    def is_connected(self) -> bool:
        return self.state == WsState.CONNECTED

    def get_connection_stats(self) -> ConnectionStats:
        self.stats.state = self.state
        return self.stats
