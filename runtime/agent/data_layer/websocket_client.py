"""
WebSocketClient — Multi-stream manager dengan reconnect state machine.

Mengelola multiple WebSocket connections ke Binance:
- Kline/candle per symbol
- Book ticker
- Diff depth (orderbook updates)
- User data stream

Koneksi actual (aiohttp/websockets) tidak di-implementasi di sini.
_ws_connect() adalah stub yang di-override saat integration.
"""

import asyncio
import enum
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

from runtime.agent.models import Candle, Orderbook, Ticker  # type: ignore

logger = logging.getLogger(__name__)


class StreamState(str, enum.Enum):
    """State machine untuk setiap connection."""

    CONNECTED = "connected"
    DISCONNECTED = "disconnected"
    RECONNECTING = "reconnecting"
    FAILED = "failed"
    STALE = "stale"


class StreamInfo:
    """Metadata tracking per stream."""

    def __init__(self, stream_id: str) -> None:
        self.stream_id = stream_id
        self.state = StreamState.DISCONNECTED
        self.reconnect_count: int = 0
        self.message_count: int = 0
        self.last_message_at: datetime | None = None
        self.connected_at: datetime | None = None
        self.task: asyncio.Task | None = None  # type: ignore[type-arg]


class WebSocketClient:
    """
    Multi-stream WebSocket manager.

    State machine per stream:
    CONNECTED → DISCONNECTED → RECONNECTING →
    CONNECTED / FAILED → (5 min cooldown) → RECONNECTING
    STALE: no message > stale_timeout → force disconnect
    """

    def __init__(
        self,
        max_reconnect: int = 10,
        reconnect_base_s: float = 1.0,
        reconnect_max_s: float = 60.0,
        stale_timeout_s: float = 30.0,
        ping_interval_s: float = 20.0,
        max_queue_size: int = 500,
    ) -> None:
        self._max_reconnect = max_reconnect
        self._reconnect_base_s = reconnect_base_s
        self._reconnect_max_s = reconnect_max_s
        self._stale_timeout_s = stale_timeout_s
        self._ping_interval_s = ping_interval_s
        self._max_queue_size = max_queue_size

        # Stream registry
        self._streams: dict[str, StreamInfo] = {}

        # Callbacks per stream type
        self._candle_callbacks: dict[str, Callable[[Candle], Awaitable[None]]] = {}
        self._ticker_callbacks: dict[str, Callable[[Ticker], Awaitable[None]]] = {}
        self._orderbook_callbacks: dict[str, Callable[[Orderbook], Awaitable[None]]] = {}

        # User stream callbacks
        self._on_order_update: Callable[..., Awaitable[None]] | None = None
        self._on_balance_update: Callable[..., Awaitable[None]] | None = None
        self._on_position_update: Callable[..., Awaitable[None]] | None = None

        self._running = False
        self._stale_checker_task: asyncio.Task | None = None  # type: ignore[type-arg]

    # ── Subscribe methods ──────────────────────────────────────

    def subscribe_candle(
        self,
        symbol: str,
        timeframe: str,
        callback: Callable[[Candle], Awaitable[None]],
    ) -> None:
        """Register callback untuk candle stream."""
        stream_id = f"{symbol.lower()}@kline_{timeframe}"
        self._candle_callbacks[stream_id] = callback
        self._ensure_stream(stream_id)
        logger.info("Subscribed candle: %s %s", symbol, timeframe)

    def subscribe_ticker(
        self,
        symbol: str,
        callback: Callable[[Ticker], Awaitable[None]],
    ) -> None:
        """Register callback untuk book ticker stream."""
        stream_id = f"{symbol.lower()}@bookTicker"
        self._ticker_callbacks[stream_id] = callback
        self._ensure_stream(stream_id)
        logger.info("Subscribed ticker: %s", symbol)

    def subscribe_orderbook(
        self,
        symbol: str,
        callback: Callable[[Orderbook], Awaitable[None]],
    ) -> None:
        """Register callback untuk depth stream."""
        stream_id = f"{symbol.lower()}@depth@100ms"
        self._orderbook_callbacks[stream_id] = callback
        self._ensure_stream(stream_id)
        logger.info("Subscribed orderbook: %s", symbol)

    def subscribe_user_stream(
        self,
        on_order_update: Callable[..., Awaitable[None]] | None = None,
        on_balance_update: Callable[..., Awaitable[None]] | None = None,
        on_position_update: Callable[..., Awaitable[None]] | None = None,
    ) -> None:
        """Register callbacks untuk user data stream."""
        self._on_order_update = on_order_update
        self._on_balance_update = on_balance_update
        self._on_position_update = on_position_update
        self._ensure_stream("userData")
        logger.info("Subscribed user stream")

    # ── Lifecycle ──────────────────────────────────────────────

    async def connect(self) -> None:
        """Buka semua koneksi yang terdaftar."""
        self._running = True
        for stream_id, info in self._streams.items():
            if info.state == StreamState.DISCONNECTED:
                await self._connect_stream(stream_id)

        # Start stale checker
        self._stale_checker_task = asyncio.create_task(self._stale_check_loop())
        logger.info("WebSocket connected: %d streams", len(self._streams))

    async def disconnect(self) -> None:
        """Tutup semua koneksi secara graceful."""
        self._running = False

        # Cancel stale checker
        if self._stale_checker_task and not self._stale_checker_task.done():  # type: ignore[union-attr]
            self._stale_checker_task.cancel()  # type: ignore[union-attr]

        # Cancel all stream tasks
        for info in self._streams.values():
            if info.task and not info.task.done():  # type: ignore[union-attr]
                info.task.cancel()  # type: ignore[union-attr]
            info.state = StreamState.DISCONNECTED

        logger.info("WebSocket disconnected: all streams closed")

    def is_connected(self, symbol: str | None = None) -> bool:
        """
        Cek status koneksi.
        None = semua stream harus connected.
        """
        if symbol is None:
            return (
                all(info.state == StreamState.CONNECTED for info in self._streams.values())
                and len(self._streams) > 0
            )

        # Cek stream untuk symbol tertentu
        prefix = symbol.lower()
        return any(
            sid.startswith(prefix) and info.state == StreamState.CONNECTED
            for sid, info in self._streams.items()
        )

    def get_connection_stats(self) -> dict[str, dict[str, object]]:
        """Return stats per stream."""
        stats: dict[str, dict[str, object]] = {}
        for sid, info in self._streams.items():
            stats[sid] = {
                "state": info.state.value,
                "reconnect_count": info.reconnect_count,
                "message_count": info.message_count,
                "last_message_at": (
                    info.last_message_at.isoformat()  # type: ignore[union-attr]
                    if info.last_message_at
                    else None
                ),
            }
        return stats

    # ── Internal: stream management ────────────────────────────

    def _ensure_stream(self, stream_id: str) -> None:
        """Ensure StreamInfo exists for stream_id."""
        if stream_id not in self._streams:
            self._streams[stream_id] = StreamInfo(stream_id)

    async def _connect_stream(self, stream_id: str) -> None:
        """Connect satu stream (stub — override untuk live)."""
        info = self._streams.get(stream_id)
        if info is None:
            return

        info.state = StreamState.CONNECTED
        info.connected_at = datetime.now(UTC)
        info.reconnect_count = 0
        logger.debug("Stream connected (stub): %s", stream_id)

    async def _reconnect_stream(self, stream_id: str) -> None:
        """Reconnect dengan exponential backoff."""
        info = self._streams.get(stream_id)
        if info is None:
            return

        info.state = StreamState.RECONNECTING

        for attempt in range(self._max_reconnect):
            if not self._running:
                return

            delay = min(
                self._reconnect_base_s * (2**attempt),
                self._reconnect_max_s,
            )
            logger.info(
                "Reconnecting %s (attempt %d/%d, delay %.1fs)",
                stream_id,
                attempt + 1,
                self._max_reconnect,
                delay,
            )
            await asyncio.sleep(delay)

            try:
                await self._connect_stream(stream_id)
                if info.state == StreamState.CONNECTED:
                    info.reconnect_count = attempt + 1
                    return
            except Exception as e:
                logger.warning("Reconnect failed %s: %s", stream_id, e)

        # Max retries exhausted
        info.state = StreamState.FAILED
        logger.error(
            "Stream FAILED after %d retries: %s",
            self._max_reconnect,
            stream_id,
        )

    def _record_message(self, stream_id: str) -> None:
        """Record bahwa stream menerima pesan."""
        info = self._streams.get(stream_id)
        if info:
            info.message_count += 1
            info.last_message_at = datetime.now(UTC)

    # ── Stale detection ────────────────────────────────────────

    async def _stale_check_loop(self) -> None:
        """Background loop untuk deteksi stale streams."""
        while self._running:
            try:
                await asyncio.sleep(self._stale_timeout_s / 2)
                now = datetime.now(UTC)

                for sid, info in self._streams.items():
                    if info.state != StreamState.CONNECTED:
                        continue

                    if info.last_message_at is None:
                        continue

                    age = (now - info.last_message_at).total_seconds()  # type: ignore[operator]
                    if age > self._stale_timeout_s:
                        logger.warning(
                            "Stream stale: %s (%.0fs no message)",
                            sid,
                            age,
                        )
                        info.state = StreamState.STALE
                        # Trigger reconnect
                        asyncio.create_task(self._reconnect_stream(sid))

            except asyncio.CancelledError:
                return
            except Exception as e:
                logger.error("Stale check error: %s", e)
