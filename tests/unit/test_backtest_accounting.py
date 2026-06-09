"""
test_backtest_accounting.py — Regression tests for backtest PnL accounting.

These tests verify the accounting invariant:
  gross_pnl = price movement before costs
  net_pnl   = gross_pnl - entry_fee - exit_fee - slippage_cost - funding_cost
  final_equity = initial_equity + sum(trade.net_pnl)

Commission must be deducted EXACTLY ONCE.
"""

from __future__ import annotations

import pandas as pd
import pytest

from research.backtest.engine import BacktestConfig, BacktestEngine


def _make_ohlcv_df(bars: list[dict]) -> pd.DataFrame:
    """Helper: buat DataFrame OHLCV minimal dari list of dicts."""
    df = pd.DataFrame(bars)
    for col in ["open", "high", "low", "close", "volume"]:
        if col not in df.columns:
            df[col] = 0.0
    return df


class _FixedSignalStrategy:
    """Strategy yang mengeluarkan BUY signal tepat sekali di bar pertama."""

    def __init__(self, direction: str = "BUY", sl: float = 0.0, tp: float = 0.0):
        self._direction = direction
        self._sl = sl
        self._tp = tp
        self._fired = False

    def on_candle(self, candle, position, equity):
        from research.backtest.engine import Signal

        if not self._fired and position is None:
            self._fired = True
            return Signal(
                direction=self._direction,
                confidence=1.0,
                sl_price=self._sl,
                tp_price=self._tp,
            )
        return None


class TestAccountingWinningLong:
    """
    Scenario:
      Initial equity: 10,000
      Side: LONG
      Entry price: 100
      Exit price: 110
      Quantity: 10
      Commission rate: 0.1% (0.001)
      Entry fee: 100 * 10 * 0.001 = 1.0
      Exit fee:  110 * 10 * 0.001 = 1.1
      Total commission = 2.1
      Slippage: 0
      Funding: 0

      Expected gross PnL: (110 - 100) * 10 = 100
      Expected net PnL:   100 - 2.1 = 97.9
      Expected final equity: 10,000 + 97.9 = 10,097.9
    """

    def setup_method(self):
        """Set up the winning long trade scenario."""
        # Use commission_pct=0.001 (0.1%), slippage=0, max_position to get qty=10
        # With equity=10000, max_position_pct=0.10 → notional=1000, qty=1000/100=10
        self.config = BacktestConfig(
            initial_capital=10_000.0,
            commission_pct=0.001,
            slippage_pct=0.0,  # no slippage for deterministic test
            max_position_pct=0.10,
            allow_short=False,
            sl_atr_multiplier=2.0,
            tp_atr_multiplier=3.0,
        )

        # Bar 0: signal generated (strategy fires here)
        # Bar 1: fill at open=100 (pending signal from bar 0)
        # Bar 2: exit at close=110 via end_of_data
        self.df = _make_ohlcv_df(
            [
                {"open": 100, "high": 105, "low": 95, "close": 100, "volume": 1000},
                {"open": 100, "high": 105, "low": 95, "close": 105, "volume": 1000},
                {"open": 105, "high": 112, "low": 104, "close": 110, "volume": 1000},
            ]
        )
        self.strategy = _FixedSignalStrategy(direction="BUY")

    def test_single_winning_trade_net_pnl(self):
        """Net PnL must equal gross_pnl - total_commission, deducted once."""
        engine = BacktestEngine(self.config)
        result = engine.run(self.df, self.strategy)

        assert len(result.trades) == 1
        trade = result.trades[0]

        # Verify entry/exit prices
        assert trade.entry_price == 100.0
        assert trade.exit_price == 110.0
        assert trade.qty == pytest.approx(10.0)

        # Accounting invariant
        expected_gross_pnl = (110 - 100) * 10.0  # = 100
        expected_entry_fee = 100 * 10 * 0.001  # = 1.0
        expected_exit_fee = 110 * 10 * 0.001  # = 1.1
        expected_total_commission = expected_entry_fee + expected_exit_fee  # = 2.1
        expected_net_pnl = expected_gross_pnl - expected_total_commission  # = 97.9

        # trade must expose gross and net separately
        assert trade.gross_pnl == pytest.approx(expected_gross_pnl, abs=0.01)
        assert trade.net_pnl == pytest.approx(expected_net_pnl, abs=0.01)
        assert trade.entry_fee == pytest.approx(expected_entry_fee, abs=0.01)
        assert trade.exit_fee == pytest.approx(expected_exit_fee, abs=0.01)

        # Net PnL invariant
        total_costs = trade.entry_fee + trade.exit_fee + trade.slippage_cost + trade.funding_cost
        assert trade.net_pnl == pytest.approx(trade.gross_pnl - total_costs, abs=0.01)

    def test_single_winning_trade_final_equity(self):
        """Final equity must equal initial + sum(net_pnl), no double deduction."""
        engine = BacktestEngine(self.config)
        result = engine.run(self.df, self.strategy)

        trade = result.trades[0]
        expected_final_equity = 10_000.0 + trade.net_pnl

        assert result.final_equity == pytest.approx(expected_final_equity, abs=0.01)

    def test_equity_invariant(self):
        """final_equity == initial_equity + sum(trade.net_pnl) for all trades."""
        engine = BacktestEngine(self.config)
        result = engine.run(self.df, self.strategy)

        sum_net_pnl = sum(t.net_pnl for t in result.trades)
        assert result.final_equity == pytest.approx(result.initial_capital + sum_net_pnl, abs=0.01)


class TestAccountingLosingLong:
    """
    Scenario:
      Initial equity: 10,000
      Side: LONG
      Entry price: 100
      Exit price: 95
      Quantity: 10
      Commission rate: 0.1% (0.001)
      Entry fee: 100 * 10 * 0.001 = 1.0
      Exit fee:  95 * 10 * 0.001 = 0.95
      Total commission = 1.95
      Slippage: 0
      Funding: 0

      Expected gross PnL: (95 - 100) * 10 = -50
      Expected net PnL:   -50 - 1.95 = -51.95
      Expected final equity: 10,000 + (-51.95) = 9,948.05
    """

    def setup_method(self):
        self.config = BacktestConfig(
            initial_capital=10_000.0,
            commission_pct=0.001,
            slippage_pct=0.0,
            max_position_pct=0.10,
            allow_short=False,
        )

        # Bar 0: signal generated
        # Bar 1: fill at open=100
        # Bar 2: exit at close=95 via end_of_data
        self.df = _make_ohlcv_df(
            [
                {"open": 100, "high": 105, "low": 95, "close": 100, "volume": 1000},
                {"open": 100, "high": 102, "low": 98, "close": 99, "volume": 1000},
                {"open": 99, "high": 100, "low": 93, "close": 95, "volume": 1000},
            ]
        )
        self.strategy = _FixedSignalStrategy(direction="BUY")

    def test_single_losing_trade_net_pnl(self):
        """Losing trade: net_pnl must equal gross_pnl - total_commission."""
        engine = BacktestEngine(self.config)
        result = engine.run(self.df, self.strategy)

        assert len(result.trades) == 1
        trade = result.trades[0]

        expected_gross_pnl = (95 - 100) * 10.0  # = -50
        expected_entry_fee = 100 * 10 * 0.001  # = 1.0
        expected_exit_fee = 95 * 10 * 0.001  # = 0.95
        expected_net_pnl = expected_gross_pnl - expected_entry_fee - expected_exit_fee  # = -51.95

        assert trade.gross_pnl == pytest.approx(expected_gross_pnl, abs=0.01)
        assert trade.net_pnl == pytest.approx(expected_net_pnl, abs=0.01)

    def test_single_losing_trade_final_equity(self):
        """Final equity with a loss."""
        engine = BacktestEngine(self.config)
        result = engine.run(self.df, self.strategy)

        trade = result.trades[0]
        expected_final_equity = 10_000.0 + trade.net_pnl  # 10000 + (-51.95) = 9948.05

        assert result.final_equity == pytest.approx(expected_final_equity, abs=0.01)

    def test_losing_trade_equity_invariant(self):
        """Equity invariant holds for losing trades too."""
        engine = BacktestEngine(self.config)
        result = engine.run(self.df, self.strategy)

        sum_net_pnl = sum(t.net_pnl for t in result.trades)
        assert result.final_equity == pytest.approx(result.initial_capital + sum_net_pnl, abs=0.01)


class TestAccountingNetPnlInvariant:
    """Verify the decomposition: net_pnl == gross_pnl - all_costs."""

    def test_net_pnl_decomposition(self):
        """net_pnl must equal gross_pnl minus all cost components."""
        config = BacktestConfig(
            initial_capital=10_000.0,
            commission_pct=0.001,
            slippage_pct=0.0,
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
            total_costs = (
                trade.entry_fee + trade.exit_fee + trade.slippage_cost + trade.funding_cost
            )
            assert trade.net_pnl == pytest.approx(trade.gross_pnl - total_costs, abs=0.01), (
                f"net_pnl invariant violated: "
                f"net_pnl={trade.net_pnl}, gross_pnl={trade.gross_pnl}, costs={total_costs}"
            )
