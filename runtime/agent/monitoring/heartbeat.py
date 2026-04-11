"""
heartbeat.py — Deteksi apakah agent masih hidup.

Pulse periodik yang membuktikan agent masih running.
Storage: file JSON sederhana (upgrade ke SQLite nanti jika perlu).

Thresholds:
    PULSE_INTERVAL_S = 30  — tulis setiap 30 detik
    STALE_THRESHOLD_S = 90 — STALE jika tidak ada pulse 90 detik
    DEAD_THRESHOLD_S = 180 — DEAD jika tidak ada pulse 3 menit
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime
from pathlib import Path

from runtime.agent.models.enums import HeartbeatStatus  # type: ignore

logger = logging.getLogger(__name__)


class Heartbeat:
    """Agent heartbeat — pulse periodik ke file."""

    PULSE_INTERVAL_S: float = 30.0
    STALE_THRESHOLD_S: float = 90.0
    DEAD_THRESHOLD_S: float = 180.0

    def __init__(self, data_dir: str | Path = "data") -> None:
        self._data_dir = Path(data_dir)
        self._data_dir.mkdir(parents=True, exist_ok=True)
        self._heartbeat_file = self._data_dir / "heartbeat.json"
        self._running = False
        self._task: asyncio.Task | None = None  # type: ignore[type-arg]
        self._last_pulse: datetime | None = None
        self._tasks_ok: bool = True

    # ── Public API ────────────────────────────────────────────────

    async def start(self) -> None:
        """Loop background yang berjalan seumur hidup agent."""
        self._running = True
        logger.info("[Heartbeat] Started, interval=%ss", self.PULSE_INTERVAL_S)
        while self._running:
            try:
                await self._pulse()
            except Exception:
                logger.exception("[Heartbeat] Pulse failed")
            await asyncio.sleep(self.PULSE_INTERVAL_S)

    def stop(self) -> None:
        """Hentikan loop heartbeat."""
        self._running = False

    async def _pulse(self) -> None:
        """Tulis timestamp ke heartbeat file."""
        now = datetime.now(UTC)
        self._last_pulse = now
        data = {
            "timestamp": now.isoformat(),
            "tasks_ok": self._tasks_ok,
            "status": HeartbeatStatus.ALIVE.value,
        }
        try:
            self._heartbeat_file.write_text(
                json.dumps(data, indent=2),
                encoding="utf-8",
            )
        except OSError:
            logger.error("[Heartbeat] Failed to write heartbeat file")
        logger.debug("[Heartbeat] Pulse at %s", now.isoformat())

    def get_status(self) -> HeartbeatStatus:
        """Baca status heartbeat dari file."""
        if not self._heartbeat_file.exists():
            return HeartbeatStatus.DEAD

        try:
            data = json.loads(self._heartbeat_file.read_text(encoding="utf-8"))
            ts_str = data.get("timestamp", "")
            last_ts = datetime.fromisoformat(ts_str)
        except (json.JSONDecodeError, ValueError, OSError):
            return HeartbeatStatus.DEAD

        elapsed = (datetime.now(UTC) - last_ts).total_seconds()
        if elapsed > self.DEAD_THRESHOLD_S:
            return HeartbeatStatus.DEAD
        if elapsed > self.STALE_THRESHOLD_S:
            return HeartbeatStatus.STALE
        return HeartbeatStatus.ALIVE

    def set_tasks_ok(self, ok: bool) -> None:
        """Update status tasks (dipanggil oleh main loop)."""
        self._tasks_ok = ok

    @property
    def last_pulse(self) -> datetime | None:
        return self._last_pulse
