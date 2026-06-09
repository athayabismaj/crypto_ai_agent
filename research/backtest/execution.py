"""
execution.py — Centralised fill-price and trade-level helpers.

Slippage convention (Approach A — embedded in fill prices):
    Fill prices already include slippage.  slippage_cost is recorded as
    an *informational* field for diagnostics but is NOT subtracted again
    in net_pnl.  Gross PnL uses the slipped fill prices directly.

Rules:
    BUY  action  → fill = raw_price × (1 + slippage_pct)   (adverse: pay more)
    SELL action  → fill = raw_price × (1 − slippage_pct)   (adverse: receive less)

    Long  entry = BUY ,  Long  exit = SELL
    Short entry = SELL,  Short exit = BUY
"""

from __future__ import annotations


def apply_adverse_slippage(
    raw_price: float,
    action: str,
    slippage_pct: float,
) -> float:
    """Return the adversely slipped fill price.

    Args:
        raw_price: Unslipped reference price (e.g. candle open).
        action: Transaction action — BUY or SELL.
        slippage_pct: Proportional slippage (0.001 = 0.1%).

    Returns:
        Fill price after adverse slippage.
    """
    if action == "BUY":
        return raw_price * (1.0 + slippage_pct)
    else:  # SELL
        return raw_price * (1.0 - slippage_pct)


def calc_slippage_cost(
    raw_price: float,
    fill_price: float,
    qty: float,
) -> float:
    """Informational slippage cost (always >= 0).

    This is the dollar amount "lost" to slippage.  Since fill prices
    already embed slippage, this value must NOT be subtracted again
    from net_pnl.
    """
    return abs(fill_price - raw_price) * qty


def calc_trade_levels(
    fill_price: float,
    side: str,
    stop_distance: float,
    target_rr: float,
) -> tuple[float, float]:
    """Calculate SL and TP from actual fill price.

    Args:
        fill_price: Realised entry fill (with slippage baked in).
        side: Position side — LONG or SHORT.
        stop_distance: Absolute price distance from fill to stop.
        target_rr: Target reward-to-risk ratio (e.g. 2.0).

    Returns:
        (stop_price, target_price)

    Raises:
        ValueError: If resulting levels are invalid
            (LONG: stop >= fill or target <= fill;
             SHORT: stop <= fill or target >= fill).
    """
    if stop_distance <= 0:
        raise ValueError(f"stop_distance must be > 0, got {stop_distance}")
    if target_rr <= 0:
        raise ValueError(f"target_rr must be > 0, got {target_rr}")

    if side == "LONG":
        stop_price = fill_price - stop_distance
        target_price = fill_price + stop_distance * target_rr
    else:  # SHORT
        stop_price = fill_price + stop_distance
        target_price = fill_price - stop_distance * target_rr

    # Validate
    if side == "LONG":
        if stop_price >= fill_price:
            raise ValueError(f"Invalid LONG levels: stop={stop_price} >= fill={fill_price}")
        if target_price <= fill_price:
            raise ValueError(f"Invalid LONG levels: target={target_price} <= fill={fill_price}")
    else:
        if stop_price <= fill_price:
            raise ValueError(f"Invalid SHORT levels: stop={stop_price} <= fill={fill_price}")
        if target_price >= fill_price:
            raise ValueError(f"Invalid SHORT levels: target={target_price} >= fill={fill_price}")

    return stop_price, target_price


def signal_to_distances(
    signal_sl: float,
    signal_tp: float,
    signal_close: float,
    side: str,
) -> tuple[float, float]:
    """Convert absolute signal SL/TP to (stop_distance, target_rr).

    Used as a backtest adapter so existing Signal dataclass (which
    carries absolute prices from the signal candle close) can be
    converted to fill-relative distances.

    Args:
        signal_sl: Absolute stop-loss price from signal.
        signal_tp: Absolute take-profit price from signal.
        signal_close: Close price of the candle that generated the signal.
        side: Position side.

    Returns:
        (stop_distance, target_rr)
    """
    if side == "LONG":
        stop_distance = signal_close - signal_sl
        reward_distance = signal_tp - signal_close
    else:  # SHORT
        stop_distance = signal_sl - signal_close
        reward_distance = signal_close - signal_tp

    if stop_distance <= 0:
        # Fallback: use 2% of signal close as stop distance
        stop_distance = signal_close * 0.02

    target_rr = reward_distance / stop_distance if stop_distance > 0 else 1.5

    if target_rr <= 0:
        target_rr = 1.5  # default RR when signal TP is missing/invalid

    return stop_distance, target_rr
