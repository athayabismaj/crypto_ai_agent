import asyncio
import logging
from collections.abc import Callable, Coroutine
from datetime import datetime, timedelta
from typing import Any

from runtime.agent.utils.time_utils import utcnow  # type: ignore

log = logging.getLogger(__name__)


class CronSchedule:
    """Implementasi sederhana penjadwalan seperti Cron untuk daily/weekly."""

    def __init__(self, hour: int, minute: int, weekday: int | None = None):
        self.hour = hour
        self.minute = minute
        self.weekday = weekday  # 0=Senin, 6=Minggu, None=Setiap hari

    def next_run_after(self, now: datetime) -> datetime:
        """Kalkulasi waktu eksekusi berikutnya setelah 'now' (diasumsikan UTC)."""
        next_run = now.replace(hour=self.hour, minute=self.minute, second=0, microsecond=0)

        if next_run <= now:
            next_run += timedelta(days=1)

        if self.weekday is not None:
            while next_run.weekday() != self.weekday:
                next_run += timedelta(days=1)

        return next_run


class ScheduledTask:
    def __init__(
        self,
        coro_func: Callable[[], Coroutine],
        interval: timedelta | CronSchedule,
        timeout: int,
        on_error: Callable | None,
    ):
        self.coro_func = coro_func
        self.interval = interval
        self.timeout = timeout
        self.on_error = on_error
        self.last_run: datetime | None = None
        self.next_run: datetime | None = None


class Scheduler:
    """
    Mengelola semua task yang berjalan secara berkala.
    Tidak menggunakan cron eksternal, semua dijadwalkan dalam asyncio event loop yang sama.
    """

    def __init__(self, config: Any = None):
        self.config = config
        self._tasks: dict[str, ScheduledTask] = {}
        self._loop = asyncio.get_event_loop()
        self._running = False
        self._loop_task: asyncio.Task | None = None

    def register(
        self,
        task_id: str,
        coro_func: Callable[[], Coroutine],
        interval: timedelta | CronSchedule,
        timeout: int = 60,
        on_error: Callable | None = None,
    ) -> None:
        """Register task ke scheduler. Dipanggil di main.py saat startup."""
        task = ScheduledTask(coro_func, interval, timeout, on_error)
        now = utcnow()

        if isinstance(interval, CronSchedule):
            task.next_run = interval.next_run_after(now)
        else:
            task.next_run = now + interval

        self._tasks[task_id] = task

    def is_due(self, task_id: str) -> bool:
        """
        Cek apakah task sudah waktunya jalan.
        Digunakan di main_loop untuk task yang di-trigger per tick jika tidak di-loop scheduler.
        """
        task = self._tasks.get(task_id)
        if not task:
            return False

        now = utcnow()
        return task.next_run is not None and now >= task.next_run

    async def _execute_task(self, task_id: str, task: ScheduledTask):
        try:
            await asyncio.wait_for(task.coro_func(), timeout=task.timeout)
        except asyncio.TimeoutError:
            log.error(
                "Scheduler task timeout", extra={"task_id": task_id, "timeout_s": task.timeout}
            )
            on_err = task.on_error
            if on_err:
                on_err(asyncio.TimeoutError())
        except Exception as e:
            log.error("Scheduler task failed", extra={"task_id": task_id, "error": str(e)})
            on_err = task.on_error
            if on_err:
                on_err(e)
        finally:
            now = utcnow()
            task.last_run = now
            interval = task.interval
            if isinstance(interval, CronSchedule):
                task.next_run = interval.next_run_after(now)
            else:
                task.next_run = now + interval

    async def _scheduler_loop(self):
        self._running = True
        while self._running:
            now = utcnow()
            # Kumpulkan task yang due
            for task_id, task in self._tasks.items():
                if task.next_run is not None and now >= task.next_run:
                    # Jalankan langsung di background task
                    # Update next_run langsung untuk cegah double launch jika interval pendek
                    interval = task.interval
                    if isinstance(interval, CronSchedule):
                        task.next_run = interval.next_run_after(now)
                    else:
                        task.next_run = now + interval
                    asyncio.create_task(self._execute_task(task_id, task))

            await asyncio.sleep(1)  # resolusi 1 detik

    async def start(self) -> None:
        """Start semua background task. Dipanggil setelah semua layer siap."""
        if not self._loop_task:
            self._loop_task = asyncio.create_task(self._scheduler_loop())

    async def stop(self) -> None:
        """Graceful stop semua task. Tunggu task yang sedang berjalan selesai."""
        self._running = False
        loop_task = self._loop_task
        if loop_task:
            loop_task.cancel()
            try:
                await loop_task
            except asyncio.CancelledError:
                pass
