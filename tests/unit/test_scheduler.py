import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest  # type: ignore

from runtime.agent.core.scheduler import CronSchedule, Scheduler  # type: ignore


def test_cron_schedule():
    now = datetime(2024, 1, 1, 12, 0, 0)  # Monday
    cron = CronSchedule(hour=0, minute=0)
    next_run = cron.next_run_after(now)
    assert next_run == datetime(2024, 1, 2, 0, 0, 0)

    cron_weekly = CronSchedule(hour=0, minute=0, weekday=6)  # Sunday
    next_week = cron_weekly.next_run_after(now)
    assert next_week.weekday() == 6
    assert next_week > now


@pytest.mark.asyncio
async def test_scheduler_interval():
    s = Scheduler()
    flag = False

    async def my_task():
        nonlocal flag
        flag = True

    def on_err(e):
        pass

    s.register("task1", my_task, timedelta(milliseconds=10), on_error=on_err)

    # Task won't be due immediately if interval passes from current time
    assert s.is_due("task1") is False

    # Mock time
    with patch("runtime.agent.core.scheduler.utcnow") as mock_now:
        mock_now.return_value = datetime.now(timezone.utc) + timedelta(seconds=2)
        assert s.is_due("task1") is True


@pytest.mark.asyncio
async def test_scheduler_cron_registration():
    s = Scheduler()

    async def task_func():
        pass

    cron = CronSchedule(hour=1, minute=30)
    s.register("cron_task", task_func, cron)
    assert "cron_task" in s._tasks
    assert s.is_due("cron_task") is False


@pytest.mark.asyncio
async def test_scheduler_execute_timeout():
    s = Scheduler()

    async def slow_task():
        await asyncio.sleep(2)

    err_flag = False

    def on_err(e):
        nonlocal err_flag
        err_flag = True

    s.register("slow", slow_task, timedelta(milliseconds=1), timeout=0.1, on_error=on_err)

    task_obj = s._tasks["slow"]
    await s._execute_task("slow", task_obj)

    assert err_flag is True


@pytest.mark.asyncio
async def test_scheduler_loop_start_stop():
    s = Scheduler()
    await s.start()
    await asyncio.sleep(0.01)  # Yield to task
    assert s._running is True
    await s.stop()
    assert s._running is False


@pytest.mark.asyncio
async def test_scheduler_loop_execution():
    s = Scheduler()
    flag = False

    async def spy_task():
        nonlocal flag
        flag = True

    s.register("spy", spy_task, timedelta(milliseconds=1))

    # Force task due backwards
    s._tasks["spy"].next_run = datetime.now(timezone.utc) - timedelta(seconds=1)

    await s.start()
    await asyncio.sleep(0.1)  # Let loop tick and run the spy_task
    await s.stop()

    assert flag is True
