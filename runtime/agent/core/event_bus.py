import asyncio
import logging
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from typing import Any

from runtime.agent.utils.time_utils import utcnow  # type: ignore

log = logging.getLogger(__name__)


class EventType(Enum):
    # Data
    CANDLE_READY = "candle_ready"
    ORDERBOOK_UPDATE = "orderbook_update"
    TICKER_UPDATE = "ticker_update"
    # Intelligence
    REGIME_CHANGED = "regime_changed"
    VOLATILITY_SPIKE = "volatility_spike"
    # Strategy
    SIGNAL_GENERATED = "signal_generated"
    SIGNAL_BLOCKED = "signal_blocked"
    # Trade
    TRADE_OPENED = "trade_opened"
    TRADE_CLOSED = "trade_closed"
    TRADE_UPDATED = "trade_updated"
    # Risk
    CIRCUIT_BREAKER = "circuit_breaker"
    RISK_VIOLATION = "risk_violation"
    EQUITY_UPDATE = "equity_update"
    # System
    SYSTEM_ERROR = "system_error"
    HEALTH_STATUS = "health_status"
    SAFE_MODE_TRIGGERED = "safe_mode_triggered"


@dataclass
class Event:
    event_type: EventType
    payload: Any
    timestamp: Any
    source: str


class EventBus:
    """Implementasi pub/sub untuk decouping antar layer."""

    def __init__(self):
        self._subscribers: defaultdict[EventType, list[Callable]] = defaultdict(list)  # type: ignore
        self._queue: asyncio.Queue[Event] = asyncio.Queue(maxsize=1000)
        self._running = False
        self._task: Any = None

    def subscribe(self, event_type: EventType, handler: Callable) -> None:
        """Register handler untuk event type tertentu."""
        self._subscribers[event_type].append(handler)

    def publish(self, event_type: EventType, payload: Any, source: str) -> None:
        """Non-blocking publish - masukkan ke queue."""
        event = Event(event_type, payload, utcnow(), source)
        try:
            self._queue.put_nowait(event)
        except asyncio.QueueFull:
            log.error("EventBus queue full - event dropped", extra={"event_type": str(event_type)})

    async def dispatch_loop(self) -> None:
        """Background loop yang mengambil event dari queue dan dispatch ke handlers."""
        self._running = True
        while self._running:
            try:
                event = await self._queue.get()
                for handler in self._subscribers[event.event_type]:
                    try:
                        res = handler(event)
                        if asyncio.iscoroutine(res):
                            await res
                    except Exception as e:
                        log.error("Handler error", extra={"handler": str(handler), "error": str(e)})

                self._queue.task_done()
            except asyncio.CancelledError:
                break

    def start(self):
        """Mulai dispatch loop background."""
        if not self._task:
            self._task = asyncio.create_task(self.dispatch_loop())

    def stop(self):
        """Hentikan dispatch loop."""
        self._running = False
        if self._task:
            self._task.cancel()
