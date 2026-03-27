import asyncio
import math
import re
from collections.abc import Callable
from functools import wraps
from typing import Any, TypeVar, cast

from runtime.agent.utils.time_utils import VALID_TIMEFRAMES, utcnow_ms  # type: ignore

T = TypeVar("T")
F = TypeVar("F", bound=Callable[..., Any])

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
    precision = int(max(0, round(-math.log10(step_size))))
    val: float = math.floor(qty / step_size) * step_size
    return round(val, precision)  # type: ignore


def round_price(price: float, tick_size: float) -> float:
    """
    Round price to tick_size using ROUND.
    """
    if tick_size <= 0:
        raise ValueError(f"tick_size must be > 0, got: {tick_size}")
    precision = int(max(0, round(-math.log10(tick_size))))
    val: float = round(price / tick_size) * tick_size
    return round(val, precision)  # type: ignore


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
    prefix = strategy_id[:8].lower().replace(" ", "_")  # type: ignore
    sym = symbol[:8].lower()  # type: ignore
    ts = utcnow_ms()
    raw = f"{prefix}_{sym}_{ts}"
    return raw[:36]  # type: ignore


def validate_order_id(cid: str) -> tuple[bool, str]:
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
    return text[: max_len - len(suffix)] + suffix  # type: ignore


def mask_key(key: str, visible_chars: int = 6) -> str:
    """Mask API key for safe logging."""
    if len(key) <= visible_chars * 2:
        return "***"
    return key[:visible_chars] + "..." + key[-visible_chars:]  # type: ignore


def calc_pnl(
    side: str,
    entry_price: float,
    exit_price: float,
    qty: float,
    commission: float = 0.0,
) -> tuple[float, float]:
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
    coro_fn: Callable[..., Any],
    max_retry: int = 3,
    base_delay_s: float = 1.0,
    max_delay_s: float = 60.0,
    backoff: float = 2.0,
    exceptions: tuple[type[Exception], ...] = (Exception,),
) -> Any:
    """Retry coroutine with exponential backoff."""
    last_exc = None
    delay = base_delay_s
    for attempt in range(max_retry + 1):
        try:
            return await coro_fn()
        except exceptions as e:  # type: ignore
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
    exceptions: tuple[type[Exception], ...] = (Exception,),
) -> Callable[[F], F]:
    """Decorator for synchronous function retry with backoff."""

    def decorator(fn: F) -> F:
        @wraps(fn)  # type: ignore
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            import time

            last_exc = None
            delay = base_delay_s
            for attempt in range(max_retry + 1):
                try:
                    return fn(*args, **kwargs)  # type: ignore
                except exceptions as e:  # type: ignore
                    last_exc = e
                    if attempt == max_retry:
                        break
                    time.sleep(delay)
                    delay *= 2
            if last_exc is not None:
                raise last_exc
            raise RuntimeError("Max retries exceeded")

        return cast(F, wrapper)

    return decorator
