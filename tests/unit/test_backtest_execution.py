"""
test_backtest_execution.py — Tests for adverse slippage, fill-based SL/TP,
and planned RR preservation.

Slippage convention (Approach A):
    Fill prices embed slippage.  slippage_cost is informational.
    It is NOT subtracted again from net_pnl.

    BUY  → fill = raw × (1 + slippage)
    SELL → fill = raw × (1 − slippage)

    Long  entry = BUY ,  Long  exit = SELL
    Short entry = SELL,  Short exit = BUY
"""

from __future__ import annotations

import pandas as pd
import pytest

from research.backtest.engine import BacktestConfig, BacktestEngine, Signal
from research.backtest.execution import (
    apply_adverse_slippage,
    calc_trade_levels,
    signal_to_distances,
)

# ── helpers ─────────────────────────────────────────────────────────────────


def _make_ohlcv_df(bars: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(bars)
    for col in ["open", "high", "low", "close", "volume"]:
        if col not in df.columns:
            df[col] = 0.0
    return df


class _FixedSignalStrategy:
    """Strategy that fires a single signal on the first bar only."""

    def __init__(
        self,
        direction: str = "BUY",
        sl: float = 0.0,
        tp: float = 0.0,
    ):
        self._direction = direction
        self._sl = sl
        self._tp = tp
        self._fired = False

    def on_candle(self, candle, position, equity):
        if not self._fired and position is None:
            self._fired = True
            return Signal(
                direction=self._direction,
                confidence=1.0,
                sl_price=self._sl,
                tp_price=self._tp,
            )
        return None


# ╔══════════════════════════════════════════════════════════════════════════╗
# ║  1–4. Adverse slippage unit tests (pure function)                      ║
# ╚══════════════════════════════════════════════════════════════════════════╝


class TestAdverseSlippage:
    """Tests 1-4: adverse slippage for every transaction action."""

    def test_long_entry_adverse_slippage(self):
        """BUY action: fill = raw × (1 + slippage) → pays more."""
        fill = apply_adverse_slippage(100.0, "BUY", 0.001)
        assert fill == pytest.approx(100.10, abs=0.01)

    def test_short_entry_adverse_slippage(self):
        """SELL action: fill = raw × (1 − slippage) → receives less."""
        fill = apply_adverse_slippage(100.0, "SELL", 0.001)
        assert fill == pytest.approx(99.90, abs=0.01)

    def test_long_exit_adverse_slippage(self):
        """Long exit = SELL: fill = raw × (1 − slippage)."""
        fill = apply_adverse_slippage(110.0, "SELL", 0.001)
        assert fill == pytest.approx(109.89, abs=0.01)

    def test_short_exit_adverse_slippage(self):
        """Short exit = BUY: fill = raw × (1 + slippage)."""
        fill = apply_adverse_slippage(90.0, "BUY", 0.001)
        assert fill == pytest.approx(90.09, abs=0.01)

    def test_zero_slippage(self):
        """Zero slippage returns the raw price unchanged."""
        assert apply_adverse_slippage(100.0, "BUY", 0.0) == 100.0
        assert apply_adverse_slippage(100.0, "SELL", 0.0) == 100.0


# ╔══════════════════════════════════════════════════════════════════════════╗
# ║  5–6. Fill-based SL/TP calculation                                     ║
# ╚══════════════════════════════════════════════════════════════════════════╝


class TestFillBasedLevels:
    """Tests 5-6: SL/TP calculated from the actual fill price."""

    def test_long_sl_tp_from_fill(self):
        """LONG: stop = fill − distance, target = fill + distance × RR."""
        sl, tp = calc_trade_levels(
            fill_price=100.10,
            side="LONG",
            stop_distance=4.0,
            target_rr=2.0,
        )
        assert sl == pytest.approx(96.10, abs=0.01)
        assert tp == pytest.approx(108.10, abs=0.01)

    def test_short_sl_tp_from_fill(self):
        """SHORT: stop = fill + distance, target = fill − distance × RR."""
        sl, tp = calc_trade_levels(
            fill_price=99.90,
            side="SHORT",
            stop_distance=4.0,
            target_rr=2.0,
        )
        assert sl == pytest.approx(103.90, abs=0.01)
        assert tp == pytest.approx(91.90, abs=0.01)


# ╔══════════════════════════════════════════════════════════════════════════╗
# ║  7–8. Planned RR preserved relative to fill                           ║
# ╚══════════════════════════════════════════════════════════════════════════╝


class TestPlannedRRPreserved:
    """Tests 7-8: RR stays ~2.0 regardless of slippage."""

    def test_long_rr_preserved(self):
        """Long: fill=103, stop_dist=4 → SL=99, TP=111, RR=2.0."""
        sl, tp = calc_trade_levels(
            fill_price=103.0,
            side="LONG",
            stop_distance=4.0,
            target_rr=2.0,
        )
        assert sl == pytest.approx(99.0, abs=0.01)
        assert tp == pytest.approx(111.0, abs=0.01)

        risk = 103.0 - sl
        reward = tp - 103.0
        actual_rr = reward / risk
        assert actual_rr == pytest.approx(2.0, abs=0.01)

    def test_short_rr_preserved(self):
        """Short: fill=97, stop_dist=4 → SL=101, TP=89, RR=2.0."""
        sl, tp = calc_trade_levels(
            fill_price=97.0,
            side="SHORT",
            stop_distance=4.0,
            target_rr=2.0,
        )
        assert sl == pytest.approx(101.0, abs=0.01)
        assert tp == pytest.approx(89.0, abs=0.01)

        risk = sl - 97.0
        reward = 97.0 - tp
        actual_rr = reward / risk
        assert actual_rr == pytest.approx(2.0, abs=0.01)


# ╔══════════════════════════════════════════════════════════════════════════╗
# ║  9–10. Invalid stop rejected                                          ║
# ╚══════════════════════════════════════════════════════════════════════════╝


class TestInvalidStopRejected:
    """Tests 9-10: invalid SL/TP raises ValueError."""

    def test_invalid_long_stop_rejected(self):
        """LONG with stop_distance=0 or negative → error."""
        with pytest.raises(ValueError, match="stop_distance must be > 0"):
            calc_trade_levels(100.0, "LONG", stop_distance=0.0, target_rr=2.0)

        with pytest.raises(ValueError, match="stop_distance must be > 0"):
            calc_trade_levels(100.0, "LONG", stop_distance=-1.0, target_rr=2.0)

    def test_invalid_short_stop_rejected(self):
        """SHORT with stop_distance=0 or negative → error."""
        with pytest.raises(ValueError, match="stop_distance must be > 0"):
            calc_trade_levels(100.0, "SHORT", stop_distance=0.0, target_rr=2.0)

    def test_invalid_target_rr_rejected(self):
        """target_rr must be > 0."""
        with pytest.raises(ValueError, match="target_rr must be > 0"):
            calc_trade_levels(100.0, "LONG", stop_distance=4.0, target_rr=0.0)


# ╔══════════════════════════════════════════════════════════════════════════╗
# ║  11. Commission accounting regression                                 ║
# ╚══════════════════════════════════════════════════════════════════════════╝


class TestCommissionRegressionWithSlippage:
    """Test 11: Batch 1 accounting still holds when slippage is enabled."""

    def test_net_pnl_decomposition_with_slippage(self):
        """Approach A: slippage is embedded in fill prices.

        net_pnl == gross_pnl - entry_fee - exit_fee - funding_cost
        slippage_cost is informational only — NOT subtracted from net_pnl.
        """
        config = BacktestConfig(
            initial_capital=10_000.0,
            commission_pct=0.001,
            slippage_pct=0.001,  # slippage enabled
            max_position_pct=0.10,
        )
        df = _make_ohlcv_df(
            [
                {"open": 100, "high": 105, "low": 95, "close": 100, "volume": 1000},
                {"open": 100, "high": 105, "low": 95, "close": 105, "volume": 1000},
                {"open": 105, "high": 115, "low": 104, "close": 110, "volume": 1000},
            ]
        )
        strategy = _FixedSignalStrategy(direction="BUY")
        engine = BacktestEngine(config)
        result = engine.run(df, strategy)

        for trade in result.trades:
            # Approach A invariant: slippage_cost is NOT subtracted
            deducted_costs = trade.entry_fee + trade.exit_fee + trade.funding_cost
            assert trade.net_pnl == pytest.approx(trade.gross_pnl - deducted_costs, abs=0.01), (
                f"net_pnl invariant violated: "
                f"net_pnl={trade.net_pnl}, gross_pnl={trade.gross_pnl}, "
                f"deducted_costs={deducted_costs}"
            )
            # slippage_cost should be positive when slippage is enabled
            assert trade.slippage_cost >= 0


# ╔══════════════════════════════════════════════════════════════════════════╗
# ║  12. Final equity invariant                                            ║
# ╚══════════════════════════════════════════════════════════════════════════╝


class TestEquityInvariantWithSlippage:
    """Test 12: final_equity == initial + sum(net_pnl), even with slippage."""

    def test_equity_invariant_long_with_slippage(self):
        config = BacktestConfig(
            initial_capital=10_000.0,
            commission_pct=0.001,
            slippage_pct=0.001,
            max_position_pct=0.10,
        )
        df = _make_ohlcv_df(
            [
                {"open": 100, "high": 105, "low": 95, "close": 100, "volume": 1000},
                {"open": 100, "high": 105, "low": 95, "close": 105, "volume": 1000},
                {"open": 105, "high": 115, "low": 104, "close": 110, "volume": 1000},
            ]
        )
        strategy = _FixedSignalStrategy(direction="BUY")
        engine = BacktestEngine(config)
        result = engine.run(df, strategy)

        sum_net_pnl = sum(t.net_pnl for t in result.trades)
        assert result.final_equity == pytest.approx(result.initial_capital + sum_net_pnl, abs=0.01)

    def test_equity_invariant_short_with_slippage(self):
        config = BacktestConfig(
            initial_capital=10_000.0,
            commission_pct=0.001,
            slippage_pct=0.001,
            max_position_pct=0.10,
            allow_short=True,
        )
        df = _make_ohlcv_df(
            [
                {"open": 100, "high": 105, "low": 95, "close": 100, "volume": 1000},
                {"open": 100, "high": 102, "low": 96, "close": 97, "volume": 1000},
                {"open": 97, "high": 98, "low": 88, "close": 90, "volume": 1000},
            ]
        )
        strategy = _FixedSignalStrategy(direction="SELL")
        engine = BacktestEngine(config)
        result = engine.run(df, strategy)

        sum_net_pnl = sum(t.net_pnl for t in result.trades)
        assert result.final_equity == pytest.approx(result.initial_capital + sum_net_pnl, abs=0.01)


# ╔══════════════════════════════════════════════════════════════════════════╗
# ║  Integration: engine produces correct fill prices                      ║
# ╚══════════════════════════════════════════════════════════════════════════╝


class TestEngineSlippageIntegration:
    """Verify the engine applies adverse slippage correctly end-to-end."""

    def test_long_entry_fill_has_adverse_slippage(self):
        """Long entry fill = open × (1 + slippage)."""
        config = BacktestConfig(
            initial_capital=10_000.0,
            commission_pct=0.0,
            slippage_pct=0.001,  # 0.1%
            max_position_pct=0.10,
        )
        df = _make_ohlcv_df(
            [
                {"open": 100, "high": 105, "low": 95, "close": 100, "volume": 1000},
                {"open": 100, "high": 105, "low": 95, "close": 105, "volume": 1000},
                {"open": 105, "high": 115, "low": 104, "close": 110, "volume": 1000},
            ]
        )
        strategy = _FixedSignalStrategy(direction="BUY")
        engine = BacktestEngine(config)
        result = engine.run(df, strategy)

        trade = result.trades[0]
        expected_fill = 100.0 * (1 + 0.001)  # 100.10
        assert trade.entry_price == pytest.approx(expected_fill, abs=0.01)

    def test_short_entry_fill_has_adverse_slippage(self):
        """Short entry fill = open × (1 − slippage)."""
        config = BacktestConfig(
            initial_capital=10_000.0,
            commission_pct=0.0,
            slippage_pct=0.001,  # 0.1%
            max_position_pct=0.10,
            allow_short=True,
        )
        df = _make_ohlcv_df(
            [
                {"open": 100, "high": 105, "low": 95, "close": 100, "volume": 1000},
                {"open": 100, "high": 102, "low": 96, "close": 97, "volume": 1000},
                {"open": 97, "high": 98, "low": 88, "close": 90, "volume": 1000},
            ]
        )
        strategy = _FixedSignalStrategy(direction="SELL")
        engine = BacktestEngine(config)
        result = engine.run(df, strategy)

        trade = result.trades[0]
        expected_fill = 100.0 * (1 - 0.001)  # 99.90
        assert trade.entry_price == pytest.approx(expected_fill, abs=0.01)


class TestSignalToDistances:
    """Verify the adapter converts signal absolute prices to distances."""

    def test_long_signal_conversion(self):
        """LONG: stop_distance = close - sl, RR = (tp - close) / stop_distance."""
        sd, rr = signal_to_distances(
            signal_sl=96.0,
            signal_tp=112.0,
            signal_close=100.0,
            side="LONG",
        )
        assert sd == pytest.approx(4.0, abs=0.01)
        assert rr == pytest.approx(3.0, abs=0.01)

    def test_short_signal_conversion(self):
        """SHORT: stop_distance = sl - close, RR = (close - tp) / stop_distance."""
        sd, rr = signal_to_distances(
            signal_sl=104.0,
            signal_tp=88.0,
            signal_close=100.0,
            side="SHORT",
        )
        assert sd == pytest.approx(4.0, abs=0.01)
        assert rr == pytest.approx(3.0, abs=0.01)

    def test_fallback_on_zero_stop_distance(self):
        """When SL == close, fallback to 2% of close."""
        sd, rr = signal_to_distances(
            signal_sl=100.0,  # same as close → zero distance
            signal_tp=106.0,
            signal_close=100.0,
            side="LONG",
        )
        assert sd == pytest.approx(2.0, abs=0.01)  # 2% of 100
        assert rr > 0
