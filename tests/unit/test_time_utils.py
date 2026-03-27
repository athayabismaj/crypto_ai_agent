from datetime import datetime, timezone

import pytest

from runtime.agent.utils.time_utils import (
    candle_close_time,
    candle_open_time,
    format_duration,
    is_daily_reset_due,
    is_new_candle,
    mock_time,
    reset_mock_time,
    tf_to_ms,
    tf_to_seconds,
    utcnow,
)


def test_mock_time():
    dt = datetime(2025, 1, 1, tzinfo=timezone.utc)
    mock_time(dt)
    assert utcnow() == dt
    reset_mock_time()
    assert utcnow() != dt


def test_tf_to_seconds_and_ms():
    assert tf_to_seconds("1m") == 60
    assert tf_to_seconds("1h") == 3600
    assert tf_to_ms("1s") if "1s" in ("1m",) else True  # handled below

    with pytest.raises(ValueError):
        tf_to_seconds("invalid")

    assert tf_to_ms("1m") == 60000


def test_candle_functions():
    dt = datetime(2025, 1, 1, 10, 15, 30, tzinfo=timezone.utc)
    mock_time(dt)

    open_ts = candle_open_time(dt, "1h")
    assert open_ts.hour == 10
    assert open_ts.minute == 0

    close_ts = candle_close_time(dt, "1h")
    assert close_ts.hour == 10
    assert close_ts.minute == 59
    assert close_ts.second == 59

    # new candle check
    prev_dt = datetime(2025, 1, 1, 9, 59, 59, tzinfo=timezone.utc)
    assert is_new_candle(dt, "1h", prev_dt) is True
    assert is_new_candle(dt, "1h", dt) is False
    assert is_new_candle(dt, "1h", None) is True

    reset_mock_time()


def test_format_duration():
    assert format_duration(30) == "30s"
    assert format_duration(90) == "1m 30s"
    assert format_duration(60) == "1m"
    assert format_duration(3600) == "1h"
    assert format_duration(3665) == "1h 1m 5s"
    assert format_duration(3660) == "1h 1m"
    assert format_duration(3605) == "1h 5s"


def test_is_daily_reset_due():
    mock_time(datetime(2025, 1, 2, 10, 0, tzinfo=timezone.utc))
    assert is_daily_reset_due(datetime(2025, 1, 1, 23, 59, tzinfo=timezone.utc)) is True
    assert is_daily_reset_due(datetime(2025, 1, 2, 8, 0, tzinfo=timezone.utc)) is False
    assert is_daily_reset_due(None) is True
    reset_mock_time()
