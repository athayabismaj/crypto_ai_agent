"""
helpers.py — Fungsi Umum Stateless
Kumpulan operasi pure function yang dipakai di seluruh codebase tanpa I/O.
"""

import asyncio
import math
from functools import wraps
from typing import Any, Callable, TypeVar

T = TypeVar('T')

def round_qty(qty: float, step_size: float) -> float:
    """
    Bulatkan quantity ke step_size menggunakan FLOOR.
    BUKAN round biasa — floor untuk menghindari over-order ketika balance mepet.
    """
    if step_size <= 0:
        raise ValueError(f'step_size harus > 0, dapat: {step_size}')
        
    precision = max(0, round(-math.log10(step_size)))
    # Float precision guard
    return round(math.floor((qty + 1e-9) / step_size) * step_size, precision)

def round_price(price: float, tick_size: float) -> float:
    """
    Bulatkan harga ke tick_size menggunakan ROUND biasa.
    """
    if tick_size <= 0:
        raise ValueError(f'tick_size harus > 0, dapat: {tick_size}')
        
    precision = max(0, round(-math.log10(tick_size)))
    return round(round(price / tick_size) * tick_size, precision)

def clamp(value: float, min_val: float, max_val: float) -> float:
    """Pastikan value dalam range [min_val, max_val]."""
    return max(min_val, min(max_val, value))

def safe_div(a: float, b: float, default: float = 0.0) -> float:
    """Bagi dengan aman — return default jika b = 0."""
    return a / b if b != 0 else default

def pct_change(old: float, new: float) -> float:
    """Return persentase perubahan dari old ke new."""
    return safe_div(new - old, abs(old)) * 100

async def retry_async(
    func: Callable[..., Any], 
    max_attempts: int = 3, 
    base_delay_s: float = 1.0, 
    *args: Any, 
    **kwargs: Any
) -> Any:
    """
    Wrapper untuk melakukan exponential backoff eksekusi.
    """
    for attempt in range(1, max_attempts + 1):
        try:
            return await func(*args, **kwargs)
        except Exception as e:
            if attempt == max_attempts:
                raise e
            import logging
            log = logging.getLogger("retry_async")
            wait_t = base_delay_s * (2 ** (attempt - 1))
            log.warning(f"Error {e}. Retrying {attempt}/{max_attempts} in {wait_t}s...")
            await asyncio.sleep(wait_t)
