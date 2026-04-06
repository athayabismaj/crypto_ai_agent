"""
Utilitas Waktu Terpusat.
Semua pemrosesan waktu wajib melewati fungsi disini untuk menjamin konsistensi
dan kemampuan mocking saat testing.
"""

from datetime import datetime, timedelta, timezone
from typing import Optional

# Internal clock --- bisa di-override untuk testing
_mock_time: Optional[datetime] = None

VALID_TIMEFRAMES = {'1m', '3m', '5m', '15m', '30m', '1h', '2h', '4h', '6h', '1d'}


def utcnow() -> datetime:
    """Selalu kembalikan datetime timezone-aware UTC."""
    if _mock_time is not None:
        return _mock_time
    return datetime.now(timezone.utc)

def utcnow_ms() -> int:
    """Timestamp Unix dalam milidetik. Untuk client_order_id dll."""
    return int(utcnow().timestamp() * 1000)

def mock_time(dt: datetime) -> None:
    """Override untuk unit testing. HANYA di test code."""
    global _mock_time
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    _mock_time = dt

def reset_mock_time() -> None:
    """Reset ke real time setelah test selesai."""
    global _mock_time
    _mock_time = None

def tf_to_seconds(tf: str) -> int:
    mapping = {
        '1m': 60, '3m': 180, '5m': 300, '15m': 900, '30m': 1800,
        '1h': 3600, '2h': 7200, '4h': 14400, '6h': 21600,
        '1d': 86400
    }
    if tf not in mapping:
        raise ValueError(f'Timeframe tidak valid: {tf}. Valid: {VALID_TIMEFRAMES}')
    return mapping[tf]

def tf_to_ms(tf: str) -> int:
    """Konversi timeframe ke milidetik."""
    return tf_to_seconds(tf) * 1000

def candle_open_time(ts: datetime, tf: str) -> datetime:
    """Waktu pembukaan candle yang berisi timestamp ts."""
    sec = tf_to_seconds(tf)
    ts_sec = int(ts.timestamp())
    base_ts = ts_sec - (ts_sec % sec)
    return datetime.fromtimestamp(base_ts, tz=timezone.utc)

def candle_close_time(ts: datetime, tf: str) -> datetime:
    """Waktu penutupan candle (candle_open + tf - 1ms)."""
    open_t = candle_open_time(ts, tf)
    sec = tf_to_seconds(tf)
    # Penutupan asumsikan tepat sebelum candle baru buka (microsecond alignment)
    return open_t + timedelta(seconds=sec) - timedelta(milliseconds=1)

def is_new_candle(ts: datetime, tf: str, prev_ts: datetime) -> bool:
    """True jika ts dan prev_ts berada di candle berbeda."""
    return candle_open_time(ts, tf) != candle_open_time(prev_ts, tf)

def time_until_candle_close(tf: str) -> float:
    """Berapa detik sampai candle saat ini selesai."""
    now = utcnow()
    close = candle_close_time(now, tf)
    diff = (close - now).total_seconds()
    return max(0.0, diff)

def format_duration(seconds: int) -> str:
    """123 -> '2m 3s', 3700 -> '1h 1m 40s'."""
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds}s"
    
    m, s = divmod(seconds, 60)
    h, m = divmod(m, 60)
    
    parts = []
    if h > 0:
        parts.append(f"{h}h")
    if m > 0:
        parts.append(f"{m}m")
    if s > 0 or not parts:
        parts.append(f"{s}s")
        
    return " ".join(parts)

def is_daily_reset_due(last_reset: datetime) -> bool:
    """True jika UTC 00:00 sudah lewat sejak last_reset."""
    # Reset occur exactly at 00:00 UTC
    now = utcnow()
    # Jika hari berbeda, atau tahun/bulan berbeda -> reset due
    return now.date() > last_reset.date()
