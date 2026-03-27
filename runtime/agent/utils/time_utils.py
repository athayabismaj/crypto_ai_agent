from datetime import datetime, timedelta, timezone
from typing import Optional

# Internal clock - can be overridden for testing
_mock_time: Optional[datetime] = None

VALID_TIMEFRAMES = {"1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "6h", "1d"}


def utcnow() -> datetime:
    """Always return tz-aware UTC datetime."""
    if _mock_time is not None:
        return _mock_time
    return datetime.now(timezone.utc)


def utcnow_ms() -> int:
    """Unix timestamp in milliseconds."""
    return int(utcnow().timestamp() * 1000)


def mock_time(dt: datetime) -> None:
    """Override for unit testing. ONLY in test code."""
    global _mock_time
    # Ensure it's timezone-aware UTC
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    _mock_time = dt


def reset_mock_time() -> None:
    """Reset to real time after test ends."""
    global _mock_time
    _mock_time = None


def tf_to_seconds(tf: str) -> int:
    """Convert timeframe string to seconds."""
    mapping = {
        "1m": 60,
        "3m": 180,
        "5m": 300,
        "15m": 900,
        "30m": 1800,
        "1h": 3600,
        "2h": 7200,
        "4h": 14400,
        "6h": 21600,
        "1d": 86400,
    }
    tf = tf.lower()
    if tf not in mapping:
        raise ValueError(f"Invalid timeframe: {tf}. Valid: {VALID_TIMEFRAMES}")
    return mapping[tf]


def tf_to_ms(tf: str) -> int:
    """Convert timeframe string to milliseconds."""
    return tf_to_seconds(tf) * 1000


def candle_open_time(ts: datetime, tf: str) -> datetime:
    """Time of the candle opening that contains the given timestamp."""
    seconds = tf_to_seconds(tf)
    ts_seconds = int(ts.timestamp())
    open_ts_seconds = (ts_seconds // seconds) * seconds
    return datetime.fromtimestamp(open_ts_seconds, tz=timezone.utc)


def candle_close_time(ts: datetime, tf: str) -> datetime:
    """Time of the candle closing (open + tf - 1ms)."""
    open_time = candle_open_time(ts, tf)
    # Candle close is right before the next open
    ms_delta = tf_to_ms(tf) - 1
    return open_time + timedelta(milliseconds=ms_delta)


def is_new_candle(ts: datetime, tf: str, prev_ts: Optional[datetime]) -> bool:
    """True if ts and prev_ts are in different candles."""
    if prev_ts is None:
        return True
    return candle_open_time(ts, tf) != candle_open_time(prev_ts, tf)


def time_until_candle_close(tf: str) -> float:
    """Seconds until current candle closes."""
    now = utcnow()
    close_time = candle_close_time(now, tf)
    return (close_time - now).total_seconds()


def format_duration(seconds: int) -> str:
    """Format seconds for logging (e.g. 123 -> '2m 3s')."""
    if seconds < 60:
        return f"{seconds}s"
    minutes = seconds // 60
    secs = seconds % 60
    if minutes < 60:
        return f"{minutes}m {secs}s" if secs else f"{minutes}m"
    hours = minutes // 60
    mins = minutes % 60
    if mins > 0:
        return f"{hours}h {mins}m {secs}s" if secs else f"{hours}h {mins}m"
    return f"{hours}h {secs}s" if secs else f"{hours}h"


def is_daily_reset_due(last_reset: Optional[datetime]) -> bool:
    """True if UTC 00:00 has passed since last_reset."""
    now = utcnow()
    if last_reset is None:
        return True

    # We strip times to just dates
    now_date = now.date()
    last_reset_date = last_reset.date()

    return now_date > last_reset_date
