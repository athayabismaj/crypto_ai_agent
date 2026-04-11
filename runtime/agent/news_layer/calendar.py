"""
news_layer/calendar.py — Macro Economic Event Calendar
Pulls high-impact events (CPI, FOMC, NFP, GDP) from Financial Modeling Prep.

The crown jewel of this module: the PRE-EVENT FREEZE mechanism.
When a HIGH_IMPACT event is within `freeze_minutes_before` of firing,
the calendar signals the Risk Layer to HALT all new order entries.
After the event passes (+ `thaw_minutes_after`), trading resumes.

This prevents the #1 cause of retail wipeout: holding positions
into a binary macro event where volatility explodes 500% in seconds.
"""

import asyncio
import logging
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from enum import Enum

import aiohttp

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data Models
# ---------------------------------------------------------------------------


class EventImpact(str, Enum):
    HIGH = "High"
    MEDIUM = "Medium"
    LOW = "Low"


@dataclass
class EconomicEvent:
    name: str  # e.g. "CPI", "FOMC Rate Decision", "Non-Farm Payrolls"
    country: str  # e.g. "US", "EU", "JP"
    scheduled_at: datetime
    impact: EventImpact
    actual: str = ""  # actual value post-release
    forecast: str = ""  # consensus forecast
    previous: str = ""  # prior reading


@dataclass
class CalendarStatus:
    """Snapshot of the current macro risk posture."""

    is_frozen: bool  # True = HALT new orders
    freeze_reason: str  # "" if not frozen
    next_high_impact: EconomicEvent | None
    minutes_until_next: float  # minutes to next HIGH event; -1 if none today
    events_today: int
    high_impact_today: int
    last_updated: datetime


# ---------------------------------------------------------------------------
# Calendar Engine
# ---------------------------------------------------------------------------


class EconomicCalendar:
    """Institutional-grade macro event awareness engine.

    Design principles:
    - Fetches events once per day (06:00 UTC) — only 1 API call.
    - In-memory cache with 12-hour TTL.
    - Pre-event freeze: blocks new trades N minutes before HIGH events.
    - Post-event thaw: re-enables trading N minutes after.
    - Full graceful degradation: if FMP is down, system assumes NO freeze.
    """

    FMP_URL = "https://financialmodelingprep.com/api/v3/economic_calendar"

    def __init__(
        self,
        fmp_api_key: str | None = None,
        freeze_minutes_before: int = 30,
        thaw_minutes_after: int = 30,
        cache_ttl_s: int = 43200,  # 12 hours
        request_timeout_s: int = 10,
    ) -> None:
        self._fmp_key = fmp_api_key or os.environ.get("FMP_API_KEY", "")
        self._freeze_before = freeze_minutes_before
        self._thaw_after = thaw_minutes_after
        self._cache_ttl_s = cache_ttl_s
        self._timeout = aiohttp.ClientTimeout(total=request_timeout_s)

        # In-memory cache
        self._events: list[EconomicEvent] = []
        self._events_ts: float = 0.0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def fetch_today(self) -> list[EconomicEvent]:
        """Fetch today's economic calendar events from FMP.

        Only fetches once per TTL window. Returns stale cache on failure.
        """
        if self._is_cache_fresh():
            return self._events

        if not self._fmp_key:
            log.warning("FMP_API_KEY not set — economic calendar disabled.")
            return self._events

        now = datetime.now(timezone.utc)
        date_str = now.strftime("%Y-%m-%d")

        try:
            params = {
                "from": date_str,
                "to": date_str,
                "apikey": self._fmp_key,
            }

            async with aiohttp.ClientSession(timeout=self._timeout) as session:
                async with session.get(self.FMP_URL, params=params) as resp:
                    if resp.status != 200:
                        log.warning("FMP Calendar API returned %d", resp.status)
                        return self._events
                    data = await resp.json()

            if not isinstance(data, list):
                log.warning("FMP returned unexpected format: %s", type(data))
                return self._events

            events: list[EconomicEvent] = []
            for item in data:
                impact_raw = item.get("impact", "Low")
                try:
                    impact = EventImpact(impact_raw)
                except ValueError:
                    impact = EventImpact.LOW

                date_val = item.get("date", "")
                try:
                    sched = datetime.fromisoformat(date_val.replace("Z", "+00:00"))
                except (ValueError, AttributeError):
                    sched = now

                events.append(
                    EconomicEvent(
                        name=item.get("event", "Unknown"),
                        country=item.get("country", ""),
                        scheduled_at=sched,
                        impact=impact,
                        actual=str(item.get("actual", "")),
                        forecast=str(item.get("estimate", "")),
                        previous=str(item.get("previous", "")),
                    )
                )

            # Sort by time ascending
            events.sort(key=lambda e: e.scheduled_at)
            self._events = events
            self._events_ts = time.monotonic()

            high_count = sum(1 for e in events if e.impact == EventImpact.HIGH)
            log.info(
                "Loaded %d economic events for %s (%d HIGH impact).",
                len(events),
                date_str,
                high_count,
            )

        except asyncio.TimeoutError:
            log.warning("FMP Calendar request timed out — using stale cache.")
        except Exception as exc:
            log.warning("FMP Calendar fetch error: %s — using stale cache.", exc)

        return self._events

    def get_status(self) -> CalendarStatus:
        """Compute the current macro risk posture.

        This is the method the Risk Layer calls every tick to decide
        whether to freeze or thaw order execution.
        """
        now = datetime.now(timezone.utc)
        high_events = [e for e in self._events if e.impact == EventImpact.HIGH]

        # Find next upcoming HIGH event
        upcoming = [e for e in high_events if e.scheduled_at > now - timedelta(minutes=self._thaw_after)]
        next_event = upcoming[0] if upcoming else None

        is_frozen = False
        freeze_reason = ""
        minutes_until = -1.0

        if next_event:
            delta = (next_event.scheduled_at - now).total_seconds() / 60.0
            minutes_until = delta

            # PRE-EVENT FREEZE: block trades N minutes before
            if 0 < delta <= self._freeze_before:
                is_frozen = True
                freeze_reason = (
                    f"PRE-EVENT FREEZE: {next_event.name} ({next_event.country}) "
                    f"in {delta:.0f} min. No new orders."
                )

            # POST-EVENT FREEZE: block trades N minutes after
            elif delta <= 0 and abs(delta) <= self._thaw_after:
                is_frozen = True
                freeze_reason = (
                    f"POST-EVENT FREEZE: {next_event.name} ({next_event.country}) "
                    f"fired {abs(delta):.0f} min ago. Waiting for volatility to settle."
                )

        return CalendarStatus(
            is_frozen=is_frozen,
            freeze_reason=freeze_reason,
            next_high_impact=next_event,
            minutes_until_next=minutes_until,
            events_today=len(self._events),
            high_impact_today=len(high_events),
            last_updated=now,
        )

    def should_freeze(self) -> tuple[bool, str]:
        """Convenience: returns (is_frozen, reason) for Risk Layer integration."""
        status = self.get_status()
        return status.is_frozen, status.freeze_reason

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _is_cache_fresh(self) -> bool:
        if self._events_ts == 0.0:
            return False
        return (time.monotonic() - self._events_ts) < self._cache_ttl_s
