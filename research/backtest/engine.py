"""
engine.py — Event-Driven Backtest Engine
Proses candle satu per satu secara kronologis.
ATURAN: Signal candle T di-fill pada open candle T+1 (lookahead protection).

Slippage convention (Approach A — embedded in fill prices):
    Fill prices include adverse slippage.  slippage_cost is recorded as
    an informational field only — it is NOT subtracted again in net_pnl.
    Gross PnL uses the slipped fill prices directly.

    BUY  action → fill = raw_price × (1 + slippage_pct)
    SELL action → fill = raw_price × (1 − slippage_pct)

    Long  entry = BUY ,  Long  exit = SELL
    Short entry = SELL,  Short exit = BUY

Gap handling:
    If candle opens beyond the stop level, exit at the (adverse) open price,
    not the original stop.  If candle opens beyond the TP level, exit at the
    (favorable) open price, not the original TP.

Intrabar ambiguity:
    When the same candle touches both SL and TP, the intrabar_policy controls
    the assumed fill: "conservative" (SL first), "optimistic" (TP first),
    or "skip" (close at candle close, marked ambiguous).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Protocol

import pandas as pd

from research.backtest.execution import (
    apply_adverse_slippage,
    calc_slippage_cost,
    calc_trade_levels,
    signal_to_distances,
)

log = logging.getLogger(__name__)


@dataclass
class BacktestConfig:
    initial_capital: float = 10_000.0
    commission_pct: float = 0.001  # 0.1% taker fee Binance
    slippage_pct: float = 0.0005  # 0.05% slippage default
    execution_delay: int = 1  # candle delay sebelum fill
    max_position_pct: float = 0.10  # max 10% equity per posisi
    allow_short: bool = False
    sl_atr_multiplier: float = 2.0
    tp_atr_multiplier: float = 3.0
    trailing_activation_pct: float = 0.03  # 3% profit → activate trailing
    trailing_callback_pct: float = 0.015  # 1.5% pullback → close
    intrabar_policy: str = "conservative"  # "conservative" | "optimistic" | "skip"


@dataclass
class Signal:
    direction: str  # "BUY" | "SELL"
    confidence: float  # 0.0 - 1.0
    sl_price: float = 0.0
    tp_price: float = 0.0
    signal_close: float = 0.0  # close of the candle that generated this signal
    stop_distance: float = 0.0  # absolute price distance from close to SL
    target_rr: float = 0.0  # target reward-to-risk ratio


@dataclass
class Position:
    entry_price: float  # actual fill price (with slippage)
    qty: float
    side: str  # "LONG" | "SHORT"
    sl_price: float
    tp_price: float
    entry_bar: int
    raw_entry_price: float = 0.0  # raw price before slippage
    entry_slippage_cost: float = 0.0  # informational slippage on entry
    initial_stop_price: float = 0.0  # SL at entry time, never modified
    planned_rr: float = 0.0  # planned reward/risk ratio at entry
    peak_price: float = 0.0
    trailing_active: bool = False


@dataclass
class Trade:
    entry_bar: int
    exit_bar: int
    side: str
    entry_price: float
    exit_price: float
    qty: float
    gross_pnl: float
    net_pnl: float
    pnl_pct: float
    exit_reason: str
    entry_fee: float
    exit_fee: float
    slippage_cost: float
    funding_cost: float
    commission: float  # total commission (entry_fee + exit_fee), backward compat
    initial_risk_usd: float = 0.0
    r_multiple: float = 0.0
    planned_rr: float = 0.0
    ambiguous_bar: bool = False
    intrabar_policy_used: str = ""
    include_in_metrics: bool = True

    @property
    def pnl(self) -> float:
        """Alias for net_pnl. Retained for backward compatibility."""
        return self.net_pnl


@dataclass
class BacktestResult:
    trades: list[Trade]
    equity_curve: list[float]
    initial_capital: float
    final_equity: float
    config: BacktestConfig
    ambiguous_bars: int = 0


class StrategyProtocol(Protocol):
    """Kontrak minimal yang harus dipenuhi oleh strategy."""

    def on_candle(self, candle: dict, position: Position | None, equity: float) -> Signal | None:
        ...


# ── Internal helper for _check_exit return value ──────────────────────────


@dataclass
class _ExitInfo:
    """Intermediate result from _check_exit."""

    reason: str
    raw_price: float
    fill_price: float
    is_ambiguous: bool = False
    policy_used: str = ""

    @property
    def triggered(self) -> bool:
        return self.reason != ""


_NO_EXIT = _ExitInfo(reason="", raw_price=0.0, fill_price=0.0)


class BacktestEngine:
    """Event-driven backtest engine dengan lookahead protection."""

    def __init__(self, config: BacktestConfig | None = None):
        self.cfg = config or BacktestConfig()

    def run(
        self,
        df: pd.DataFrame,
        strategy: Any,
        feature_cols: list[str] | None = None,
        predict_fn: Any = None,
    ) -> BacktestResult:
        """
        Jalankan backtest pada DataFrame OHLCV + fitur.

        Args:
            df: DataFrame dengan kolom [timestamp, open, high, low, close, volume, + fitur]
            strategy: objek dengan method on_candle() ATAU None jika pakai predict_fn
            predict_fn: callable(features) -> signal_score (>0 = buy, <0 = sell)
        """
        equity = self.cfg.initial_capital
        position: Position | None = None
        trades: list[Trade] = []
        equity_curve: list[float] = [equity]
        pending_signal: Signal | None = None
        ambiguous_bars = 0

        n = len(df)
        log.info(f"Backtest dimulai: {n} candle, capital=${self.cfg.initial_capital:,.2f}")

        for i in range(n):
            row = df.iloc[i]
            candle = {
                "open": row["open"],
                "high": row["high"],
                "low": row["low"],
                "close": row["close"],
                "volume": row["volume"],
                "bar_index": i,
            }

            # ── STEP 1: Update posisi existing (SL/TP/Trailing) ──
            if position is not None:
                exit_info = self._check_exit(position, candle)
                if exit_info.triggered:
                    trade = self._close_position(
                        position,
                        exit_info.fill_price,
                        exit_info.raw_price,
                        i,
                        exit_info.reason,
                        equity,
                        exit_info.is_ambiguous,
                        exit_info.policy_used,
                    )
                    trades.append(trade)
                    equity += trade.net_pnl
                    position = None
                    if exit_info.is_ambiguous:
                        ambiguous_bars += 1
                else:
                    # Update trailing
                    self._update_trailing(position, candle)

            # ── STEP 2: Fill pending signal dari candle sebelumnya ──
            if pending_signal is not None and position is None:
                position = self._open_position(pending_signal, candle["open"], i, equity)
                pending_signal = None

            # ── STEP 3: Generate signal untuk candle INI ──
            # (akan di-fill di candle BERIKUTNYA → lookahead protection)
            if predict_fn is not None:
                features = {c: row[c] for c in (feature_cols or []) if c in row.index}
                score = predict_fn(features) if features else 0.0
                if score > 0.05 and position is None:
                    # Hitung SL/TP berbasis ATR jika tersedia
                    atr = row.get("atr_14", candle["close"] * 0.02)
                    close = candle["close"]
                    stop_dist = atr * self.cfg.sl_atr_multiplier
                    target_rr = self.cfg.tp_atr_multiplier / self.cfg.sl_atr_multiplier
                    pending_signal = Signal(
                        direction="BUY",
                        confidence=min(abs(score), 1.0),
                        sl_price=close - stop_dist,
                        tp_price=close + atr * self.cfg.tp_atr_multiplier,
                        signal_close=close,
                        stop_distance=stop_dist,
                        target_rr=target_rr,
                    )
                elif score < -0.05 and position is None and self.cfg.allow_short:
                    atr = row.get("atr_14", candle["close"] * 0.02)
                    close = candle["close"]
                    stop_dist = atr * self.cfg.sl_atr_multiplier
                    target_rr = self.cfg.tp_atr_multiplier / self.cfg.sl_atr_multiplier
                    pending_signal = Signal(
                        direction="SELL",
                        confidence=min(abs(score), 1.0),
                        sl_price=close + stop_dist,
                        tp_price=close - atr * self.cfg.tp_atr_multiplier,
                        signal_close=close,
                        stop_distance=stop_dist,
                        target_rr=target_rr,
                    )
            elif strategy is not None:
                sig = strategy.on_candle(candle, position, equity)
                if sig is not None and position is None:
                    pending_signal = sig

            # Update equity curve (mark-to-market)
            if position is not None:
                unrealized = self._calc_unrealized(position, candle["close"])
                equity_curve.append(equity + unrealized)
            else:
                equity_curve.append(equity)

        # Close any remaining position at last close (with exit slippage)
        if position is not None:
            last = df.iloc[-1]
            raw_exit = last["close"]
            exit_action = "SELL" if position.side == "LONG" else "BUY"
            exit_fill = apply_adverse_slippage(raw_exit, exit_action, self.cfg.slippage_pct)
            trade = self._close_position(
                position, exit_fill, raw_exit, n - 1, "end_of_data", equity
            )
            trades.append(trade)
            equity += trade.net_pnl

        log.info(
            f"Backtest selesai: {len(trades)} trades, "
            f"equity ${self.cfg.initial_capital:,.2f} → ${equity:,.2f}"
        )

        return BacktestResult(
            trades=trades,
            equity_curve=equity_curve,
            initial_capital=self.cfg.initial_capital,
            final_equity=equity,
            config=self.cfg,
            ambiguous_bars=ambiguous_bars,
        )

    def _open_position(self, signal: Signal, raw_open: float, bar: int, equity: float) -> Position:
        """Buka posisi baru dengan adverse slippage dan fill-based SL/TP.

        Entry action: BUY for LONG, SELL for SHORT.
        SL/TP are recalculated from the actual fill price.
        """
        side = "LONG" if signal.direction == "BUY" else "SHORT"
        entry_action = signal.direction  # BUY or SELL

        # Apply adverse slippage to entry
        fill_price = apply_adverse_slippage(raw_open, entry_action, self.cfg.slippage_pct)
        entry_slip_cost = calc_slippage_cost(raw_open, fill_price, 1.0)

        max_notional = equity * self.cfg.max_position_pct
        qty = max_notional / fill_price

        # Scale slippage cost to actual quantity
        entry_slip_cost = entry_slip_cost * qty

        # Determine stop_distance and target_rr
        if signal.stop_distance > 0 and signal.target_rr > 0:
            # Signal already carries distances
            stop_dist = signal.stop_distance
            target_rr = signal.target_rr
        elif signal.sl_price > 0 and signal.signal_close > 0:
            # Convert absolute signal SL/TP to distances
            stop_dist, target_rr = signal_to_distances(
                signal.sl_price, signal.tp_price, signal.signal_close, side
            )
        elif signal.sl_price > 0:
            # Legacy: signal has SL/TP but no signal_close.
            # Use the raw_open as proxy for signal_close.
            stop_dist, target_rr = signal_to_distances(
                signal.sl_price, signal.tp_price, raw_open, side
            )
        else:
            # No SL/TP information — use zero (no protective levels)
            stop_dist = 0.0
            target_rr = 0.0

        # Calculate SL/TP from actual fill price
        if stop_dist > 0 and target_rr > 0:
            sl_price, tp_price = calc_trade_levels(fill_price, side, stop_dist, target_rr)
        else:
            sl_price = signal.sl_price
            tp_price = signal.tp_price

        return Position(
            entry_price=fill_price,
            qty=qty,
            side=side,
            sl_price=sl_price,
            tp_price=tp_price,
            entry_bar=bar,
            raw_entry_price=raw_open,
            entry_slippage_cost=entry_slip_cost,
            initial_stop_price=sl_price,  # frozen at entry
            planned_rr=target_rr,
            peak_price=fill_price,
        )

    def _check_exit(self, pos: Position, candle: dict) -> _ExitInfo:
        """Check whether a position should be closed.

        Handles:
          1. Gap-through stops  — exit at adverse candle open, not the stop.
          2. Gap-through TPs    — exit at favorable candle open, not the TP.
          3. Intrabar ambiguity — when the same candle touches both SL and TP.
          4. Trailing stop.
        """
        open_px, high, low, close = (
            candle["open"],
            candle["high"],
            candle["low"],
            candle["close"],
        )
        slip = self.cfg.slippage_pct
        exit_action = "SELL" if pos.side == "LONG" else "BUY"

        # ── Determine individual trigger flags ────────────────────────

        if pos.side == "LONG":
            sl_gap = pos.sl_price > 0 and open_px <= pos.sl_price
            sl_normal = pos.sl_price > 0 and low <= pos.sl_price and not sl_gap
            tp_gap = pos.tp_price > 0 and open_px >= pos.tp_price
            tp_normal = pos.tp_price > 0 and high >= pos.tp_price and not tp_gap
        else:  # SHORT
            sl_gap = pos.sl_price > 0 and open_px >= pos.sl_price
            sl_normal = pos.sl_price > 0 and high >= pos.sl_price and not sl_gap
            tp_gap = pos.tp_price > 0 and open_px <= pos.tp_price
            tp_normal = pos.tp_price > 0 and low <= pos.tp_price and not tp_gap

        sl_hit = sl_gap or sl_normal
        tp_hit = tp_gap or tp_normal

        # ── Intrabar ambiguity ────────────────────────────────────────

        if sl_hit and tp_hit:
            # Both SL and TP triggered in the same candle
            policy = self.cfg.intrabar_policy

            if policy == "optimistic":
                # Assume TP hit first
                if tp_gap:
                    raw = open_px
                else:
                    raw = pos.tp_price
                fill = apply_adverse_slippage(raw, exit_action, slip)
                return _ExitInfo(
                    reason="take_profit",
                    raw_price=raw,
                    fill_price=fill,
                    is_ambiguous=True,
                    policy_used=policy,
                )
            elif policy == "skip":
                # Close at candle close (ambiguous, no fabricated order)
                raw = close
                fill = apply_adverse_slippage(raw, exit_action, slip)
                return _ExitInfo(
                    reason="ambiguous_close",
                    raw_price=raw,
                    fill_price=fill,
                    is_ambiguous=True,
                    policy_used=policy,
                )
            else:
                # "conservative" — assume SL hit first (default)
                if sl_gap:
                    raw = open_px
                else:
                    raw = pos.sl_price
                fill = apply_adverse_slippage(raw, exit_action, slip)
                return _ExitInfo(
                    reason="stop_loss",
                    raw_price=raw,
                    fill_price=fill,
                    is_ambiguous=True,
                    policy_used=policy,
                )

        # ── Single trigger (gap or normal) ────────────────────────────

        # SL gap-through
        if sl_gap:
            raw = open_px  # adverse gap: worse than stop
            fill = apply_adverse_slippage(raw, exit_action, slip)
            return _ExitInfo(reason="stop_loss", raw_price=raw, fill_price=fill)

        # TP gap-through
        if tp_gap:
            raw = open_px  # favorable gap: better than TP
            fill = apply_adverse_slippage(raw, exit_action, slip)
            return _ExitInfo(reason="take_profit", raw_price=raw, fill_price=fill)

        # Normal SL
        if sl_normal:
            raw = pos.sl_price
            fill = apply_adverse_slippage(raw, exit_action, slip)
            return _ExitInfo(reason="stop_loss", raw_price=raw, fill_price=fill)

        # Normal TP
        if tp_normal:
            raw = pos.tp_price
            fill = apply_adverse_slippage(raw, exit_action, slip)
            return _ExitInfo(reason="take_profit", raw_price=raw, fill_price=fill)

        # Trailing stop
        if pos.trailing_active:
            if pos.side == "LONG":
                trail_trigger = pos.peak_price * (1 - self.cfg.trailing_callback_pct)
                if low <= trail_trigger:
                    fill = apply_adverse_slippage(trail_trigger, exit_action, slip)
                    return _ExitInfo(
                        reason="trailing_stop",
                        raw_price=trail_trigger,
                        fill_price=fill,
                    )
            else:
                trail_trigger = pos.peak_price * (1 + self.cfg.trailing_callback_pct)
                if high >= trail_trigger:
                    fill = apply_adverse_slippage(trail_trigger, exit_action, slip)
                    return _ExitInfo(
                        reason="trailing_stop",
                        raw_price=trail_trigger,
                        fill_price=fill,
                    )

        return _NO_EXIT

    def _update_trailing(self, pos: Position, candle: dict) -> None:
        """Update peak price dan activate trailing jika threshold tercapai."""
        if pos.side == "LONG":
            if candle["high"] > pos.peak_price:
                pos.peak_price = candle["high"]
            profit_pct = (pos.peak_price - pos.entry_price) / pos.entry_price
            if profit_pct >= self.cfg.trailing_activation_pct:
                pos.trailing_active = True
        else:
            if candle["low"] < pos.peak_price or pos.peak_price == 0:
                pos.peak_price = candle["low"]
            profit_pct = (pos.entry_price - pos.peak_price) / pos.entry_price
            if profit_pct >= self.cfg.trailing_activation_pct:
                pos.trailing_active = True

    def _calc_unrealized(self, pos: Position, current_price: float) -> float:
        """Hitung unrealized PnL."""
        if pos.side == "LONG":
            return (current_price - pos.entry_price) * pos.qty
        else:
            return (pos.entry_price - current_price) * pos.qty

    def _close_position(
        self,
        pos: Position,
        exit_fill: float,
        raw_exit: float,
        bar: int,
        reason: str,
        equity: float,
        is_ambiguous: bool = False,
        policy_used: str = "",
    ) -> Trade:
        """Tutup posisi dan hitung realized PnL.

        This is the SINGLE central close function.  Every close path
        (stop_loss, take_profit, trailing_stop, ambiguous_close,
        end_of_data) must call this function.

        Slippage convention — Approach A (embedded in fill prices):
          entry_price and exit_fill already include adverse slippage.
          gross_pnl uses these slipped prices directly.
          slippage_cost is INFORMATIONAL only — NOT subtracted from net_pnl.

        Accounting:
          gross_pnl = price movement using slipped fill prices
          entry_fee = entry_notional × commission_pct
          exit_fee  = exit_notional × commission_pct
          slippage_cost = informational (entry + exit slippage in dollars)
          net_pnl   = gross_pnl - entry_fee - exit_fee - funding_cost
                      (slippage_cost is NOT subtracted — already in prices)

        Commission is deducted EXACTLY ONCE, inside net_pnl.
        Equity must be updated with net_pnl only.
        """
        # Gross PnL: uses slipped fill prices
        if pos.side == "LONG":
            gross_pnl = (exit_fill - pos.entry_price) * pos.qty
        else:
            gross_pnl = (pos.entry_price - exit_fill) * pos.qty

        # Cost breakdown
        entry_notional = pos.entry_price * pos.qty
        exit_notional = exit_fill * pos.qty
        entry_fee = entry_notional * self.cfg.commission_pct
        exit_fee = exit_notional * self.cfg.commission_pct
        funding_cost = 0.0  # placeholder for future funding rate model

        # Slippage cost: informational only (Approach A)
        # Entry slippage already recorded on position
        exit_slippage_cost = calc_slippage_cost(raw_exit, exit_fill, pos.qty)
        slippage_cost = pos.entry_slippage_cost + exit_slippage_cost

        # Net PnL: commission deducted once; slippage NOT subtracted
        # (already embedded in gross_pnl via fill prices)
        commission = entry_fee + exit_fee
        net_pnl = gross_pnl - entry_fee - exit_fee - funding_cost

        pnl_pct = gross_pnl / entry_notional * 100 if pos.entry_price > 0 else 0

        # Initial risk and R-multiple
        initial_risk_usd = abs(pos.entry_price - pos.initial_stop_price) * pos.qty
        if initial_risk_usd > 0:
            r_multiple = net_pnl / initial_risk_usd
        else:
            r_multiple = 0.0

        # Exclusion for ambiguous "skip" trades
        include_in_metrics = True
        if reason == "ambiguous_close":
            reason = "ambiguous_excluded"
            include_in_metrics = False

        return Trade(
            entry_bar=pos.entry_bar,
            exit_bar=bar,
            side=pos.side,
            entry_price=pos.entry_price,
            exit_price=exit_fill,
            qty=pos.qty,
            gross_pnl=gross_pnl,
            net_pnl=net_pnl,
            pnl_pct=pnl_pct,
            exit_reason=reason,
            entry_fee=entry_fee,
            exit_fee=exit_fee,
            slippage_cost=slippage_cost,
            funding_cost=funding_cost,
            commission=commission,
            initial_risk_usd=initial_risk_usd,
            r_multiple=r_multiple,
            planned_rr=pos.planned_rr,
            ambiguous_bar=is_ambiguous,
            intrabar_policy_used=policy_used,
            include_in_metrics=include_in_metrics,
        )
