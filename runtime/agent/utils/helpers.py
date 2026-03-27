import asyncio
import math
import re
from functools import wraps
from typing import Callable, Tuple, TypeVar

from runtime.agent.utils.time_utils import VALID_TIMEFRAMES, utcnow_ms

T = TypeVar("T")

ORDER_ID_PATTERN = re.compile(r"^[a-zA-Z0-9_\-]{1,36}$")


def round_qty(qty: float, step_size: float) -> float:
    """
    Round quantity to step_size using FLOOR.
    NOT regular round - use floor to avoid over-ordering.
    Example:
        round_qty(0.1235, 0.001) -> 0.123
    """
    if step_size <= 0:
        raise ValueError(f"step_size must be > 0, got: {step_size}")
    precision = max(0, round(-math.log10(step_size)))
    return round(math.floor(qty / step_size) * step_size, precision)


def round_price(price: float, tick_size: float) -> float:
    """
    Round price to tick_size using ROUND.
    """
    if tick_size <= 0:
        raise ValueError(f"tick_size must be > 0, got: {tick_size}")
    precision = max(0, round(-math.log10(tick_size)))
    return round(round(price / tick_size) * tick_size, precision)


def clamp(value: float, min_val: float, max_val: float) -> float:
    """Ensure value in range [min_val, max_val]."""
    return max(min_val, min(max_val, value))


def safe_div(a: float, b: float, default: float = 0.0) -> float:
    """Safe division - return default if b = 0."""
    return a / b if b != 0 else default


def pct_change(old: float, new: float) -> float:
    """Percentage change from old to new."""
    return safe_div(new - old, abs(old)) * 100


def generate_order_id(strategy_id: str, symbol: str) -> str:
    """
    Generate unique deterministic client_order_id.
    Format: {strategy_prefix}_{symbol_lower}_{timestamp_ms}
    Automatically truncated to 36 chars.
    """
    prefix = strategy_id[:8].lower().replace(" ", "_")
    sym = symbol[:8].lower()
    ts = utcnow_ms()
    raw = f"{prefix}_{sym}_{ts}"
    return raw[:36]


def validate_order_id(cid: str) -> Tuple[bool, str]:
    """Return (True, '') if valid, (False, error_msg) if not."""
    if not cid:
        return False, "client_order_id empty"
    if len(cid) > 36:
        return False, f"Too long: {len(cid)} > 36"
    if not ORDER_ID_PATTERN.match(cid):
        return False, f"Invalid characters: {cid}"
    return True, ""


def truncate(text: str, max_len: int, suffix: str = "...") -> str:
    """Truncate string with suffix if too long."""
    if len(text) <= max_len:
        return text
    return text[: max_len - len(suffix)] + suffix


def mask_key(key: str, visible_chars: int = 6) -> str:
    """Mask API key for safe logging."""
    if len(key) <= visible_chars * 2:
        return "***"
    return key[:visible_chars] + "..." + key[-visible_chars:]


def calc_pnl(
    side: str,
    entry_price: float,
    exit_price: float,
    qty: float,
    commission: float = 0.0,
) -> Tuple[float, float]:
    """
    Return (pnl_usd, pnl_pct).
    BUY: pnl = (exit - entry) * qty - commission
    SELL: pnl = (entry - exit) * qty - commission
    """
    gross = (exit_price - entry_price) * qty
    if side == "SELL":
        gross = -gross

    pnl_usd = gross - commission
    cost = entry_price * qty
    pnl_pct = safe_div(pnl_usd, cost) * 100

    return pnl_usd, pnl_pct


def calc_risk_reward(entry: float, sl: float, tp: float) -> float:
    """Return risk:reward ratio."""
    risk = abs(entry - sl)
    reward = abs(tp - entry)
    return safe_div(reward, risk)


def calc_position_value(
    qty: float,
    price: float,
    leverage: int = 1,
) -> dict:
    """Return dict: notional, margin, leverage"""
    notional = qty * price
    margin = safe_div(notional, leverage)
    return {"notional": notional, "margin": margin, "leverage": leverage}


def annualize_return(period_return: float, days: int) -> float:
    """Annualized return."""
    if days <= 0:
        return 0.0
    return (1 + period_return) ** (365 / days) - 1


def is_valid_symbol(symbol: str) -> bool:
    """Validate Binance symbol format."""
    if not symbol or len(symbol) > 20:
        return False
    return symbol.isupper() and symbol.isalnum()


def is_valid_side(side: str) -> bool:
    """Validate side."""
    return side in ("BUY", "SELL")


def is_valid_timeframe(tf: str) -> bool:
    """Validate timeframe."""
    return tf in VALID_TIMEFRAMES


def validate_config_range(
    value: float,
    min_val: float,
    max_val: float,
    name: str,
) -> None:
    """Raise ValueError if value outside range."""
    if not (min_val <= value <= max_val):
        raise ValueError(f"{name} must be in range [{min_val}, {max_val}], " f"got: {value}")


async def retry_async(
    coro_fn: Callable,
    max_retry: int = 3,
    base_delay_s: float = 1.0,
    max_delay_s: float = 60.0,
    backoff: float = 2.0,
    exceptions: tuple = (Exception,),
) -> Any:
    """Retry coroutine with exponential backoff."""
    last_exc = None
    delay = base_delay_s
    for attempt in range(max_retry + 1):
        try:
            return await coro_fn()
        except exceptions as e:
            last_exc = e
            if attempt == max_retry:
                break
            await asyncio.sleep(min(delay, max_delay_s))
            delay *= backoff
    if last_exc is not None:
        raise last_exc
    raise RuntimeError("Max retries exceeded")


def retry_sync(
    max_retry: int = 3,
    base_delay_s: float = 0.5,
    exceptions: tuple = (Exception,),
):
    """Decorator for synchronous function retry with backoff."""

    def decorator(fn: Callable) -> Callable:
        @wraps(fn)
        def wrapper(*args, **kwargs):
            import time

            last_exc = None
            delay = base_delay_s
            for attempt in range(max_retry + 1):
                try:
                    return fn(*args, **kwargs)
                except exceptions as e:
                    last_exc = e
                    if attempt == max_retry:
                        break
                    time.sleep(delay)
                    delay *= 2
            if last_exc is not None:
                raise last_exc
            raise RuntimeError("Max retries exceeded")

        return wrapper

    return decorator
