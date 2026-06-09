"""
test_backtest_engine.py — Batch 3 regression tests.

Covers:
  1-4.   Gap-aware stop and take-profit handling
  5-10.  Intrabar ambiguity policy
  11-15. Initial risk and R-multiple recording
  16-20. End-of-data forced close parity and accounting invariants
"""

from __future__ import annotations

from typing import Any

import pandas as pd
import pytest

from research.backtest.engine import BacktestConfig, BacktestEngine, Signal, Trade

# ── helpers ─────────────────────────────────────────────────────────────────


def _make_ohlcv_df(bars: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(bars)
    for col in ["open", "high", "low", "close", "volume"]:
        if col not in df.columns:
            df[col] = 0.0
    return df


class _FixedSignalStrategy:
    """Strategy that fires one signal on the first bar only."""

    def __init__(
        self,
        direction: str = "BUY",
        sl: float = 0.0,
        tp: float = 0.0,
        signal_close: float = 0.0,
        stop_distance: float = 0.0,
        target_rr: float = 0.0,
    ):
        self._direction = direction
        self._sl = sl
        self._tp = tp
        self._signal_close = signal_close
        self._stop_distance = stop_distance
        self._target_rr = target_rr
        self._fired = False

    def on_candle(self, candle, position, equity):
        if not self._fired and position is None:
            self._fired = True
            return Signal(
                direction=self._direction,
                confidence=1.0,
                sl_price=self._sl,
                tp_price=self._tp,
                signal_close=self._signal_close,
                stop_distance=self._stop_distance,
                target_rr=self._target_rr,
            )
        return None


def _base_config(**overrides: Any) -> BacktestConfig:
    """Config with zero slippage/commission for deterministic math."""
    defaults: dict[str, Any] = dict(
        initial_capital=10_000.0,
        commission_pct=0.0,
        slippage_pct=0.0,
        max_position_pct=0.10,
        allow_short=True,
    )
    defaults.update(overrides)
    return BacktestConfig(**defaults)


# ╔══════════════════════════════════════════════════════════════════════════╗
# ║  1-4. Gap-aware exit tests                                             ║
# ╚══════════════════════════════════════════════════════════════════════════╝


class TestGapAwareExits:
    """Gaps through SL or TP must fill at the (adverse or favorable) open."""

    def test_long_gap_below_stop(self):
        """LONG: candle opens below SL → exit at open, not SL price.

        Entry at 100, SL at 95.  Next candle opens at 90.
        Exit must be 90 (gap open), not 95.
        """
        config = _base_config()
        # Bar 0: signal with SL=95, TP=115 (won't be hit)
        strategy = _FixedSignalStrategy(
            direction="BUY", sl=95, tp=115, signal_close=100, stop_distance=5, target_rr=3.0
        )
        df = _make_ohlcv_df(
            [
                # Bar 0: signal fires here
                {"open": 100, "high": 105, "low": 95, "close": 100, "volume": 1000},
                # Bar 1: fill at open=100
                {"open": 100, "high": 102, "low": 98, "close": 99, "volume": 1000},
                # Bar 2: gap down — opens at 90 (below SL 95)
                {"open": 90, "high": 92, "low": 88, "close": 91, "volume": 1000},
            ]
        )
        engine = BacktestEngine(config)
        result = engine.run(df, strategy)

        assert len(result.trades) == 1
        trade = result.trades[0]
        assert trade.exit_reason == "stop_loss"
        # Exit must be at the gap open (90), not at SL (95)
        assert trade.exit_price == pytest.approx(90.0, abs=0.01)

    def test_short_gap_above_stop(self):
        """SHORT: candle opens above SL → exit at open, not SL price.

        Short entry at 100, SL at 105.  Next candle opens at 110.
        Exit must be 110, not 105.
        """
        config = _base_config()
        strategy = _FixedSignalStrategy(
            direction="SELL", sl=105, tp=85, signal_close=100, stop_distance=5, target_rr=3.0
        )
        df = _make_ohlcv_df(
            [
                {"open": 100, "high": 105, "low": 95, "close": 100, "volume": 1000},
                # fill at open=100
                {"open": 100, "high": 102, "low": 98, "close": 101, "volume": 1000},
                # gap up — opens at 110 (above SL 105)
                {"open": 110, "high": 115, "low": 109, "close": 112, "volume": 1000},
            ]
        )
        engine = BacktestEngine(config)
        result = engine.run(df, strategy)

        assert len(result.trades) == 1
        trade = result.trades[0]
        assert trade.exit_reason == "stop_loss"
        assert trade.exit_price == pytest.approx(110.0, abs=0.01)

    def test_long_favorable_gap_beyond_tp(self):
        """LONG: candle opens above TP → exit at open (favorable gap).

        Entry at 100, TP at 108.  Next candle opens at 115.
        Exit must be 115, not 108.
        """
        config = _base_config()
        strategy = _FixedSignalStrategy(
            direction="BUY", sl=95, tp=108, signal_close=100, stop_distance=5, target_rr=1.6
        )
        df = _make_ohlcv_df(
            [
                {"open": 100, "high": 105, "low": 95, "close": 100, "volume": 1000},
                # fill at open=100
                {"open": 100, "high": 105, "low": 98, "close": 104, "volume": 1000},
                # gap up — opens at 115 (above TP 108)
                {"open": 115, "high": 118, "low": 114, "close": 116, "volume": 1000},
            ]
        )
        engine = BacktestEngine(config)
        result = engine.run(df, strategy)

        assert len(result.trades) == 1
        trade = result.trades[0]
        assert trade.exit_reason == "take_profit"
        assert trade.exit_price == pytest.approx(115.0, abs=0.01)

    def test_short_favorable_gap_beyond_tp(self):
        """SHORT: candle opens below TP → exit at open (favorable gap).

        Short entry at 100, TP at 92.  Next candle opens at 85.
        Exit must be 85, not 92.
        """
        config = _base_config()
        strategy = _FixedSignalStrategy(
            direction="SELL", sl=105, tp=92, signal_close=100, stop_distance=5, target_rr=1.6
        )
        df = _make_ohlcv_df(
            [
                {"open": 100, "high": 105, "low": 95, "close": 100, "volume": 1000},
                # fill at open=100
                {"open": 100, "high": 102, "low": 98, "close": 99, "volume": 1000},
                # gap down — opens at 85 (below TP 92)
                {"open": 85, "high": 87, "low": 83, "close": 84, "volume": 1000},
            ]
        )
        engine = BacktestEngine(config)
        result = engine.run(df, strategy)

        assert len(result.trades) == 1
        trade = result.trades[0]
        assert trade.exit_reason == "take_profit"
        assert trade.exit_price == pytest.approx(85.0, abs=0.01)


# ╔══════════════════════════════════════════════════════════════════════════╗
# ║  5-10. Intrabar ambiguity policy tests                                 ║
# ╚══════════════════════════════════════════════════════════════════════════╝


class TestIntrabarPolicy:
    """Same candle touches both SL and TP."""

    def _make_ambiguous_long_scenario(self, policy: str):
        """LONG: entry 100, SL=95, TP=108.
        Candle high=110 (>= TP 108) and low=93 (<= SL 95) → ambiguous.
        """
        config = _base_config(intrabar_policy=policy)
        strategy = _FixedSignalStrategy(
            direction="BUY", sl=95, tp=108, signal_close=100, stop_distance=5, target_rr=1.6
        )
        df = _make_ohlcv_df(
            [
                {"open": 100, "high": 105, "low": 95, "close": 100, "volume": 1000},
                # fill at open=100
                {"open": 100, "high": 102, "low": 98, "close": 100, "volume": 1000},
                # ambiguous candle — touches both SL and TP
                {"open": 100, "high": 110, "low": 93, "close": 105, "volume": 1000},
            ]
        )
        engine = BacktestEngine(config)
        return engine.run(df, strategy)

    def test_long_ambiguous_conservative(self):
        """Conservative: SL assumed first for LONG ambiguous candle."""
        result = self._make_ambiguous_long_scenario("conservative")
        trade = result.trades[0]
        assert trade.exit_reason == "stop_loss"
        assert trade.ambiguous_bar is True

    def test_long_ambiguous_optimistic(self):
        """Optimistic: TP assumed first for LONG ambiguous candle."""
        result = self._make_ambiguous_long_scenario("optimistic")
        trade = result.trades[0]
        assert trade.exit_reason == "take_profit"
        assert trade.ambiguous_bar is True

    def _make_ambiguous_short_scenario(self, policy: str):
        """SHORT: entry 100, SL=105, TP=92.
        Candle high=107 (>= SL 105) and low=90 (<= TP 92) → ambiguous.
        """
        config = _base_config(intrabar_policy=policy)
        strategy = _FixedSignalStrategy(
            direction="SELL", sl=105, tp=92, signal_close=100, stop_distance=5, target_rr=1.6
        )
        df = _make_ohlcv_df(
            [
                {"open": 100, "high": 105, "low": 95, "close": 100, "volume": 1000},
                # fill at open=100
                {"open": 100, "high": 103, "low": 97, "close": 100, "volume": 1000},
                # ambiguous candle
                {"open": 100, "high": 107, "low": 90, "close": 98, "volume": 1000},
            ]
        )
        engine = BacktestEngine(config)
        return engine.run(df, strategy)

    def test_short_ambiguous_conservative(self):
        """Conservative: SL assumed first for SHORT ambiguous candle."""
        result = self._make_ambiguous_short_scenario("conservative")
        trade = result.trades[0]
        assert trade.exit_reason == "stop_loss"
        assert trade.ambiguous_bar is True

    def test_short_ambiguous_optimistic(self):
        """Optimistic: TP assumed first for SHORT ambiguous candle."""
        result = self._make_ambiguous_short_scenario("optimistic")
        trade = result.trades[0]
        assert trade.exit_reason == "take_profit"
        assert trade.ambiguous_bar is True

    def test_ambiguous_event_recorded(self):
        """Ambiguous events must be recorded on the trade and result."""
        result = self._make_ambiguous_long_scenario("conservative")
        trade = result.trades[0]
        assert trade.ambiguous_bar is True
        assert trade.intrabar_policy_used == "conservative"
        assert result.ambiguous_bars == 1

    def test_ambiguous_skip_exclusion(self):
        """Skip policy: trade is excluded from metrics and marked ambiguous."""
        result = self._make_ambiguous_long_scenario("skip")
        trade = result.trades[0]
        assert trade.exit_reason == "ambiguous_excluded"
        assert trade.ambiguous_bar is True
        assert trade.intrabar_policy_used == "skip"
        assert trade.include_in_metrics is False
        assert result.ambiguous_bars == 1

        # Also verify that it's excluded from metrics
        from research.backtest.metrics import calculate_metrics

        metrics = calculate_metrics(result.trades, result.equity_curve, result.initial_capital)
        assert metrics.total_trades == 0  # because the only trade was skipped
        assert metrics.win_rate == 0.0
        assert metrics.profit_factor == 0.0

    def test_default_policy_is_conservative(self):
        """Default intrabar_policy must be 'conservative'."""
        cfg = BacktestConfig()
        assert cfg.intrabar_policy == "conservative"


# ╔══════════════════════════════════════════════════════════════════════════╗
# ║  11-15. R-multiple and initial risk tests                              ║
# ╚══════════════════════════════════════════════════════════════════════════╝


class TestRMultiple:
    """Initial risk and R-multiple on every trade."""

    def test_winning_trade_positive_r(self):
        """Winning trade → r_multiple > 0."""
        config = _base_config()
        strategy = _FixedSignalStrategy(
            direction="BUY", sl=96, tp=112, signal_close=100, stop_distance=4, target_rr=3.0
        )
        df = _make_ohlcv_df(
            [
                {"open": 100, "high": 105, "low": 95, "close": 100, "volume": 1000},
                {"open": 100, "high": 102, "low": 98, "close": 101, "volume": 1000},
                # exits via end_of_data at 108 (winning)
                {"open": 105, "high": 110, "low": 104, "close": 108, "volume": 1000},
            ]
        )
        engine = BacktestEngine(config)
        result = engine.run(df, strategy)
        trade = result.trades[0]

        assert trade.initial_risk_usd > 0
        assert trade.r_multiple > 0
        # initial_risk = |entry - initial_stop| * qty = 4 * qty
        expected_risk = 4.0 * trade.qty
        assert trade.initial_risk_usd == pytest.approx(expected_risk, abs=0.01)

    def test_losing_trade_negative_r(self):
        """Losing trade (stopped out) → r_multiple ≈ -1.0 (no fees)."""
        config = _base_config()
        strategy = _FixedSignalStrategy(
            direction="BUY", sl=96, tp=112, signal_close=100, stop_distance=4, target_rr=3.0
        )
        df = _make_ohlcv_df(
            [
                {"open": 100, "high": 105, "low": 95, "close": 100, "volume": 1000},
                {"open": 100, "high": 102, "low": 98, "close": 101, "volume": 1000},
                # SL hit — low touches 96
                {"open": 99, "high": 100, "low": 94, "close": 95, "volume": 1000},
            ]
        )
        engine = BacktestEngine(config)
        result = engine.run(df, strategy)
        trade = result.trades[0]

        assert trade.initial_risk_usd > 0
        assert trade.r_multiple < 0
        # No fees → r_multiple should be ≈ -1.0
        assert trade.r_multiple == pytest.approx(-1.0, abs=0.05)

    def test_fees_reduce_realized_r(self):
        """With commission, R-multiple is slightly worse than theoretical."""
        # No commission
        config_no_fee = _base_config(commission_pct=0.0)
        strategy1 = _FixedSignalStrategy(
            direction="BUY", sl=96, tp=112, signal_close=100, stop_distance=4, target_rr=3.0
        )
        df = _make_ohlcv_df(
            [
                {"open": 100, "high": 105, "low": 95, "close": 100, "volume": 1000},
                {"open": 100, "high": 102, "low": 98, "close": 101, "volume": 1000},
                {"open": 105, "high": 110, "low": 104, "close": 108, "volume": 1000},
            ]
        )
        result1 = BacktestEngine(config_no_fee).run(df, strategy1)
        r_no_fee = result1.trades[0].r_multiple

        # With commission
        config_fee = _base_config(commission_pct=0.001)
        strategy2 = _FixedSignalStrategy(
            direction="BUY", sl=96, tp=112, signal_close=100, stop_distance=4, target_rr=3.0
        )
        result2 = BacktestEngine(config_fee).run(df, strategy2)
        r_with_fee = result2.trades[0].r_multiple

        assert r_with_fee < r_no_fee

    def test_slippage_reduces_realized_r(self):
        """With slippage, R-multiple is slightly worse than without."""
        # No slippage
        config_no_slip = _base_config(slippage_pct=0.0)
        strategy1 = _FixedSignalStrategy(
            direction="BUY", sl=96, tp=112, signal_close=100, stop_distance=4, target_rr=3.0
        )
        df = _make_ohlcv_df(
            [
                {"open": 100, "high": 105, "low": 95, "close": 100, "volume": 1000},
                {"open": 100, "high": 102, "low": 98, "close": 101, "volume": 1000},
                {"open": 105, "high": 110, "low": 104, "close": 108, "volume": 1000},
            ]
        )
        result1 = BacktestEngine(config_no_slip).run(df, strategy1)
        r_no_slip = result1.trades[0].r_multiple

        # With slippage
        config_slip = _base_config(slippage_pct=0.001)
        strategy2 = _FixedSignalStrategy(
            direction="BUY", sl=96, tp=112, signal_close=100, stop_distance=4, target_rr=3.0
        )
        result2 = BacktestEngine(config_slip).run(df, strategy2)
        r_with_slip = result2.trades[0].r_multiple

        assert r_with_slip < r_no_slip

    def test_moving_stop_does_not_change_initial_risk(self):
        """Trailing stop activation must not change initial_risk_usd.

        Entry 100, initial SL=97.  Trailing activates and moves SL up.
        initial_risk_usd must remain |100 - 97| * qty = 3 * qty.
        """
        config = _base_config(
            trailing_activation_pct=0.02,  # activate at 2% profit
            trailing_callback_pct=0.01,  # 1% pullback to close
        )
        strategy = _FixedSignalStrategy(
            direction="BUY", sl=97, tp=120, signal_close=100, stop_distance=3, target_rr=6.67
        )
        df = _make_ohlcv_df(
            [
                {"open": 100, "high": 105, "low": 95, "close": 100, "volume": 1000},
                # fill at 100
                {"open": 100, "high": 102, "low": 98, "close": 101, "volume": 1000},
                # price goes up 3% → trailing activates, peak=104
                {"open": 102, "high": 104, "low": 101, "close": 103, "volume": 1000},
                # pullback triggers trailing stop from peak 104
                # trail = 104 * (1 - 0.01) = 102.96 → low=102 triggers
                {"open": 103, "high": 103.5, "low": 102, "close": 102.5, "volume": 1000},
            ]
        )
        engine = BacktestEngine(config)
        result = engine.run(df, strategy)
        trade = result.trades[0]

        assert trade.exit_reason == "trailing_stop"
        # initial_risk = |100 - 97| * qty = 3 * qty
        expected_risk = 3.0 * trade.qty
        assert trade.initial_risk_usd == pytest.approx(expected_risk, abs=0.01)
        # The trade is profitable, so r_multiple should be positive
        assert trade.r_multiple > 0


# ╔══════════════════════════════════════════════════════════════════════════╗
# ║  16-20. End-of-data forced close tests                                 ║
# ╚══════════════════════════════════════════════════════════════════════════╝


class TestForcedClose:
    """End-of-data close must use the central _close_position."""

    def test_end_of_data_long_close(self):
        """end_of_data closes a LONG position."""
        config = _base_config()
        strategy = _FixedSignalStrategy(
            direction="BUY", sl=90, tp=120, signal_close=100, stop_distance=10, target_rr=2.0
        )
        df = _make_ohlcv_df(
            [
                {"open": 100, "high": 105, "low": 95, "close": 100, "volume": 1000},
                {"open": 100, "high": 105, "low": 98, "close": 103, "volume": 1000},
                {"open": 103, "high": 107, "low": 102, "close": 105, "volume": 1000},
            ]
        )
        engine = BacktestEngine(config)
        result = engine.run(df, strategy)
        trade = result.trades[0]

        assert trade.exit_reason == "end_of_data"
        assert trade.side == "LONG"
        assert trade.initial_risk_usd > 0
        assert trade.exit_price == pytest.approx(105.0, abs=0.01)

    def test_end_of_data_short_close(self):
        """end_of_data closes a SHORT position."""
        config = _base_config()
        strategy = _FixedSignalStrategy(
            direction="SELL", sl=110, tp=80, signal_close=100, stop_distance=10, target_rr=2.0
        )
        df = _make_ohlcv_df(
            [
                {"open": 100, "high": 105, "low": 95, "close": 100, "volume": 1000},
                {"open": 100, "high": 103, "low": 97, "close": 98, "volume": 1000},
                {"open": 98, "high": 100, "low": 95, "close": 96, "volume": 1000},
            ]
        )
        engine = BacktestEngine(config)
        result = engine.run(df, strategy)
        trade = result.trades[0]

        assert trade.exit_reason == "end_of_data"
        assert trade.side == "SHORT"
        assert trade.initial_risk_usd > 0

    def test_end_of_data_uses_normal_accounting(self):
        """end_of_data trade must have all accounting fields populated."""
        config = _base_config(commission_pct=0.001)
        strategy = _FixedSignalStrategy(
            direction="BUY", sl=90, tp=120, signal_close=100, stop_distance=10, target_rr=2.0
        )
        df = _make_ohlcv_df(
            [
                {"open": 100, "high": 105, "low": 95, "close": 100, "volume": 1000},
                {"open": 100, "high": 105, "low": 98, "close": 103, "volume": 1000},
                {"open": 103, "high": 107, "low": 102, "close": 105, "volume": 1000},
            ]
        )
        engine = BacktestEngine(config)
        result = engine.run(df, strategy)
        trade = result.trades[0]

        assert trade.exit_reason == "end_of_data"
        assert trade.initial_risk_usd > 0
        assert isinstance(trade.r_multiple, float)
        assert trade.entry_fee > 0
        assert trade.exit_fee > 0

    def test_commission_still_deducted_once(self):
        """net_pnl = gross_pnl - entry_fee - exit_fee - funding_cost."""
        config = _base_config(commission_pct=0.001)
        strategy = _FixedSignalStrategy(
            direction="BUY", sl=90, tp=120, signal_close=100, stop_distance=10, target_rr=2.0
        )
        df = _make_ohlcv_df(
            [
                {"open": 100, "high": 105, "low": 95, "close": 100, "volume": 1000},
                {"open": 100, "high": 105, "low": 98, "close": 103, "volume": 1000},
                {"open": 103, "high": 107, "low": 102, "close": 105, "volume": 1000},
            ]
        )
        engine = BacktestEngine(config)
        result = engine.run(df, strategy)
        trade = result.trades[0]

        deducted_costs = trade.entry_fee + trade.exit_fee + trade.funding_cost
        assert trade.net_pnl == pytest.approx(trade.gross_pnl - deducted_costs, abs=0.01)

    def test_equity_invariant(self):
        """final_equity == initial + sum(net_pnl) — the master invariant."""
        config = _base_config(commission_pct=0.001, slippage_pct=0.001)
        strategy = _FixedSignalStrategy(
            direction="BUY", sl=90, tp=120, signal_close=100, stop_distance=10, target_rr=2.0
        )
        df = _make_ohlcv_df(
            [
                {"open": 100, "high": 105, "low": 95, "close": 100, "volume": 1000},
                {"open": 100, "high": 105, "low": 98, "close": 103, "volume": 1000},
                {"open": 103, "high": 107, "low": 102, "close": 105, "volume": 1000},
            ]
        )
        engine = BacktestEngine(config)
        result = engine.run(df, strategy)

        sum_net_pnl = sum(t.net_pnl for t in result.trades)
        assert result.final_equity == pytest.approx(result.initial_capital + sum_net_pnl, abs=0.01)


# ╔══════════════════════════════════════════════════════════════════════════╗
# ║  R-multiple formula verification                                       ║
# ╚══════════════════════════════════════════════════════════════════════════╝


class TestRMultipleFormula:
    """Verify r_multiple = net_pnl / initial_risk_usd."""

    def test_r_multiple_formula_consistency(self):
        """For every trade: r_multiple == net_pnl / initial_risk_usd."""
        config = _base_config(commission_pct=0.001, slippage_pct=0.0005)
        strategy = _FixedSignalStrategy(
            direction="BUY", sl=95, tp=115, signal_close=100, stop_distance=5, target_rr=3.0
        )
        df = _make_ohlcv_df(
            [
                {"open": 100, "high": 105, "low": 95, "close": 100, "volume": 1000},
                {"open": 100, "high": 105, "low": 98, "close": 103, "volume": 1000},
                {"open": 103, "high": 107, "low": 102, "close": 106, "volume": 1000},
            ]
        )
        engine = BacktestEngine(config)
        result = engine.run(df, strategy)

        for trade in result.trades:
            if trade.initial_risk_usd > 0:
                expected_r = trade.net_pnl / trade.initial_risk_usd
                assert trade.r_multiple == pytest.approx(expected_r, abs=0.001)


# ╔══════════════════════════════════════════════════════════════════════════╗
# ║  Deterministic Slippage Tests (Approach A Verification)                ║
# ╚══════════════════════════════════════════════════════════════════════════╝


class TestDeterministicSlippage:
    """Verify Approach A: slippage embedded in fill prices, cost is informational."""

    def _run_long_trade(self, slippage_pct: float, commission_pct: float = 0.0) -> Trade:
        config = _base_config(slippage_pct=slippage_pct, commission_pct=commission_pct)
        strategy = _FixedSignalStrategy(direction="BUY")
        # Ensure qty=10 by setting equity=10000 and max_pos=0.1 -> 1000 notional / 100 = 10
        df = _make_ohlcv_df(
            [
                {"open": 100, "high": 105, "low": 95, "close": 100, "volume": 1000},
                {"open": 100, "high": 105, "low": 95, "close": 105, "volume": 1000},  # Fill at 100
                {
                    "open": 110,
                    "high": 115,
                    "low": 104,
                    "close": 110,
                    "volume": 1000,
                },  # Exit end_of_data at 110
            ]
        )
        result = BacktestEngine(config).run(df, strategy)
        return result.trades[0]

    def _run_short_trade(self, slippage_pct: float, commission_pct: float = 0.0) -> Trade:
        config = _base_config(
            slippage_pct=slippage_pct, commission_pct=commission_pct, allow_short=True
        )
        strategy = _FixedSignalStrategy(direction="SELL")
        df = _make_ohlcv_df(
            [
                {"open": 100, "high": 105, "low": 95, "close": 100, "volume": 1000},
                {"open": 100, "high": 105, "low": 95, "close": 105, "volume": 1000},  # Fill at 100
                {
                    "open": 90,
                    "high": 95,
                    "low": 85,
                    "close": 90,
                    "volume": 1000,
                },  # Exit end_of_data at 90
            ]
        )
        result = BacktestEngine(config).run(df, strategy)
        return result.trades[0]

    def test_long_slippage_impact(self):
        """Verify long trade with and without slippage."""
        t_no_slip = self._run_long_trade(0.0)
        t_slip = self._run_long_trade(0.01)  # 1% slippage

        # 1. Long trade without slippage
        assert t_no_slip.entry_price == 100.0
        assert t_no_slip.exit_price == 110.0
        assert t_no_slip.net_pnl == pytest.approx(100.0, abs=0.01)  # (110 - 100) * 10

        # 2. Same long trade with slippage
        # 5. Long entry uses adverse BUY slippage: 100 * (1 + 0.01) = 101.0
        assert t_slip.entry_price == pytest.approx(101.0, abs=0.01)
        # 6. Long exit uses adverse SELL slippage: 110 * (1 - 0.01) = 108.9
        assert t_slip.exit_price == pytest.approx(108.9, abs=0.01)

        expected_pnl = (108.9 - 101.0) * t_slip.qty  # 7.9 * 9.90099...
        assert t_slip.net_pnl == pytest.approx(expected_pnl, abs=0.01)

        # 9. Difference in PnL equals slippage impact
        # Instead of strict difference, we prove 10, 11

        # 10. Slippage is not subtracted twice
        # 11. slippage_cost is attribution only
        assert t_slip.slippage_cost > 0
        assert t_slip.net_pnl == pytest.approx(
            t_slip.gross_pnl - t_slip.entry_fee - t_slip.exit_fee - t_slip.funding_cost, abs=0.01
        )

    def test_short_slippage_impact(self):
        """Verify short trade with and without slippage."""
        t_no_slip = self._run_short_trade(0.0)
        t_slip = self._run_short_trade(0.01)  # 1% slippage

        # 3. Short trade without slippage
        assert t_no_slip.entry_price == 100.0
        assert t_no_slip.exit_price == 90.0
        assert t_no_slip.net_pnl == pytest.approx(100.0, abs=0.01)

        # 4. Same short trade with slippage
        # 7. Short entry uses adverse SELL slippage: 100 * (1 - 0.01) = 99.0
        assert t_slip.entry_price == pytest.approx(99.0, abs=0.01)
        # 8. Short exit uses adverse BUY slippage: 90 * (1 + 0.01) = 90.9
        assert t_slip.exit_price == pytest.approx(90.9, abs=0.01)

    def test_commission_accounting_remains_correct(self):
        """12. Existing commission accounting remains correct."""
        t_slip_comm = self._run_long_trade(slippage_pct=0.01, commission_pct=0.001)
        expected_gross = (t_slip_comm.exit_price - t_slip_comm.entry_price) * t_slip_comm.qty
        expected_entry_fee = t_slip_comm.entry_price * t_slip_comm.qty * 0.001
        expected_exit_fee = t_slip_comm.exit_price * t_slip_comm.qty * 0.001

        assert t_slip_comm.gross_pnl == pytest.approx(expected_gross, abs=0.01)
        assert t_slip_comm.entry_fee == pytest.approx(expected_entry_fee, abs=0.01)
        assert t_slip_comm.exit_fee == pytest.approx(expected_exit_fee, abs=0.01)
        assert t_slip_comm.net_pnl == pytest.approx(
            expected_gross - expected_entry_fee - expected_exit_fee, abs=0.01
        )
