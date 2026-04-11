"""
Test Exit Layer — comprehensive tests for all exit components.

Coverage:
- SL check candle high/low (BUY & SELL)
- TP single & partial
- Trailing ratchet (hanya naik, tidak turun)
- Trailing activation threshold
- Break-even trigger & no-backward
- Timeout exit
- ExitManager 7-step priority
- register_open_positions recovery

Mengikuti checklist docs section 14.1.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from runtime.agent.exit_layer.break_even import BreakEvenManager
from runtime.agent.exit_layer.exit_manager import ExitManager
from runtime.agent.exit_layer.take_profit import TakeProfitManager
from runtime.agent.exit_layer.trailing import TrailingStopManager
from runtime.agent.models.enums import ExitAction
from runtime.agent.models.exit import HOLD_DECISION, ExitDecision

# ══════════════════════════════════════════════════════════════════════
#  Helpers
# ══════════════════════════════════════════════════════════════════════


def make_config(**overrides) -> SimpleNamespace:
    """Config minimal untuk testing."""
    defaults = {
        "trailing_method": "atr",
        "trail_atr_mult": 2.0,
        "trail_pct": 0.02,
        "trail_activation_r": 1.0,
        "partial_tp_enabled": True,
        "partial_tp_ratio": 0.50,
        "breakeven_trigger_r": 1.0,
        "breakeven_buffer_pct": 0.001,
        "max_hold_candles": 48,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


# ══════════════════════════════════════════════════════════════════════
#  1. STOP LOSS — Candle High/Low Check
# ══════════════════════════════════════════════════════════════════════


class TestStopLoss:
    """Checklist #1: SL check menggunakan candle high/low, bukan close."""

    def test_buy_sl_hit_by_low(self):
        """BUY: candle low < SL → EXIT_SL."""
        cfg = make_config()
        mgr = ExitManager(cfg)
        mgr.register_trade("T1", "BUY", 50000, 49000, 52000)

        result = mgr.evaluate(
            trade_id="T1",
            symbol="BTCUSDT",
            side="BUY",
            entry_price=50000,
            sl_price=49000,
            tp_price=52000,
            filled_qty=0.001,
            hold_candles=1,
            high=50500,
            low=48900,
            close=49200,
            atr=500,
        )
        assert result.action == ExitAction.EXIT_SL
        assert result.exit_price == 49000
        assert result.urgency == "urgent"

    def test_buy_sl_not_hit_when_low_above(self):
        """BUY: candle low > SL → HOLD."""
        cfg = make_config()
        mgr = ExitManager(cfg)
        mgr.register_trade("T2", "BUY", 50000, 49000, 52000)

        result = mgr.evaluate(
            trade_id="T2",
            symbol="BTCUSDT",
            side="BUY",
            entry_price=50000,
            sl_price=49000,
            tp_price=52000,
            filled_qty=0.001,
            hold_candles=1,
            high=50500,
            low=49100,
            close=50200,
            atr=500,
        )
        assert result.action == ExitAction.HOLD

    def test_sell_sl_hit_by_high(self):
        """SELL: candle high >= SL → EXIT_SL."""
        cfg = make_config()
        mgr = ExitManager(cfg)
        mgr.register_trade("T3", "SELL", 50000, 51000, 48000)

        result = mgr.evaluate(
            trade_id="T3",
            symbol="BTCUSDT",
            side="SELL",
            entry_price=50000,
            sl_price=51000,
            tp_price=48000,
            filled_qty=0.001,
            hold_candles=1,
            high=51100,
            low=49800,
            close=50500,
            atr=500,
        )
        assert result.action == ExitAction.EXIT_SL

    def test_sell_sl_not_hit_when_high_below(self):
        """SELL: candle high < SL → HOLD."""
        cfg = make_config()
        mgr = ExitManager(cfg)
        mgr.register_trade("T4", "SELL", 50000, 51000, 48000)

        result = mgr.evaluate(
            trade_id="T4",
            symbol="BTCUSDT",
            side="SELL",
            entry_price=50000,
            sl_price=51000,
            tp_price=48000,
            filled_qty=0.001,
            hold_candles=1,
            high=50800,
            low=49800,
            close=50200,
            atr=500,
        )
        assert result.action == ExitAction.HOLD

    def test_sl_zero_no_check(self):
        """SL price 0 → tidak check SL."""
        cfg = make_config()
        mgr = ExitManager(cfg)
        mgr.register_trade("T5", "BUY", 50000, 0, 52000)

        result = mgr.evaluate(
            trade_id="T5",
            symbol="BTCUSDT",
            side="BUY",
            entry_price=50000,
            sl_price=0,
            tp_price=52000,
            filled_qty=0.001,
            hold_candles=1,
            high=50500,
            low=100,
            close=50200,
            atr=500,
        )
        # Tidak EXIT_SL karena sl_price = 0
        assert result.action != ExitAction.EXIT_SL


# ══════════════════════════════════════════════════════════════════════
#  2. TRAILING STOP — Ratchet Effect & Activation
# ══════════════════════════════════════════════════════════════════════


class TestTrailingStop:
    """Checklist #2 & #3: Trailing ratchet + activation."""

    def test_trailing_ratchet_buy_only_up(self):
        """#2: Trailing SL hanya naik, tidak pernah turun."""
        cfg = make_config(trail_activation_r=0.0, trail_atr_mult=2.0)
        trailing = TrailingStopManager(cfg)
        trailing.register("T1", "BUY", 50000, 49000)

        # Harga naik → trail naik
        r1 = trailing.update("T1", "BUY", 50000, 49000, high=52000, low=51500, close=51800, atr=500)
        assert r1 is not None
        assert r1.action == ExitAction.UPDATE_SL
        old_trail = r1.new_sl
        assert old_trail > 49000  # trail has moved up

        # Harga turun tapi masih di atas trail → trail TIDAK ikut turun
        r2 = trailing.update(
            "T1",
            "BUY",
            50000,
            49000,
            high=51500,
            low=old_trail + 100,
            close=old_trail + 200,
            atr=500,
        )
        # Trailing tidak bergerak karena high tidak melebihi highest_high
        # Jadi harusnya None (no update) karena new trail <= current trail
        assert r2 is None  # ratchet: trail tidak turun

    def test_trailing_ratchet_sell_only_down(self):
        """#2: Trailing SL SELL hanya turun, tidak naik."""
        cfg = make_config(trail_activation_r=0.0)
        trailing = TrailingStopManager(cfg)
        trailing.register("T2", "SELL", 50000, 51000)

        # Harga turun → trail turun
        r1 = trailing.update(
            "T2", "SELL", 50000, 51000, high=49800, low=49000, close=49200, atr=500
        )
        assert r1 is not None
        assert r1.action == ExitAction.UPDATE_SL
        old_trail = r1.new_sl

        # Harga naik → trail TIDAK ikut naik
        r2 = trailing.update(
            "T2", "SELL", 50000, 51000, high=49800, low=49500, close=49700, atr=500
        )
        if r2 is not None:
            assert r2.new_sl <= old_trail

    def test_trailing_not_active_before_threshold(self):
        """#3: Trailing tidak aktif sebelum TRAIL_ACTIVATION_R tercapai."""
        cfg = make_config(trail_activation_r=1.0)
        trailing = TrailingStopManager(cfg)
        trailing.register("T3", "BUY", 50000, 49000)

        # Profit 0.5R (risk = 1000, profit = 500) → trailing TIDAK aktif
        result = trailing.update(
            "T3", "BUY", 50000, 49000, high=50500, low=50300, close=50500, atr=300
        )
        assert result is None
        state = trailing.get_state("T3")
        assert state is not None
        assert not state.activated

    def test_trailing_activates_at_threshold(self):
        """#3: Trailing aktif saat profit >= TRAIL_ACTIVATION_R."""
        cfg = make_config(trail_activation_r=1.0)
        trailing = TrailingStopManager(cfg)
        trailing.register("T4", "BUY", 50000, 49000)

        # Profit 1.0R (risk = 1000, profit = 1000) → trailing AKTIF
        result = trailing.update(
            "T4", "BUY", 50000, 49000, high=51000, low=50800, close=51000, atr=300
        )
        state = trailing.get_state("T4")
        assert state is not None
        assert state.activated

    def test_trailing_exit_when_hit(self):
        """Trail SL hit → EXIT_TRAIL."""
        cfg = make_config(trail_activation_r=0.0, trail_atr_mult=1.0)
        trailing = TrailingStopManager(cfg)
        trailing.register("T5", "BUY", 50000, 49000)

        # Harga naik → trail update
        trailing.update("T5", "BUY", 50000, 49000, high=52000, low=51500, close=51800, atr=500)

        # Harga turun menembus trail level
        state = trailing.get_state("T5")
        assert state is not None
        trail_level = state.current_trail

        result = trailing.update(
            "T5",
            "BUY",
            50000,
            49000,
            high=trail_level,
            low=trail_level - 100,
            close=trail_level - 50,
            atr=500,
        )
        assert result is not None
        assert result.action == ExitAction.EXIT_TRAIL

    def test_deregister(self):
        """Deregister menghapus state."""
        cfg = make_config()
        trailing = TrailingStopManager(cfg)
        trailing.register("T6", "BUY", 50000, 49000)
        assert trailing.get_state("T6") is not None
        trailing.deregister("T6")
        assert trailing.get_state("T6") is None


# ══════════════════════════════════════════════════════════════════════
#  3. TAKE PROFIT — Single & Partial
# ══════════════════════════════════════════════════════════════════════


class TestTakeProfit:
    """Checklist #4: Partial TP → SL geser ke entry."""

    def test_single_tp_full_close(self):
        """Non-partial mode: TP hit → EXIT_TP."""
        cfg = make_config(partial_tp_enabled=False)
        tp_mgr = TakeProfitManager(cfg)
        tp_mgr.register("T1", 52000)

        result = tp_mgr.check("T1", "BUY", 50000, 52000, 0.001, high=52100, low=51500)
        assert result is not None
        assert result.action == ExitAction.EXIT_TP

    def test_partial_tp_first_hit(self):
        """#4: Partial TP pertama → PARTIAL_CLOSE + SL ke entry."""
        cfg = make_config(partial_tp_enabled=True, partial_tp_ratio=0.50)
        tp_mgr = TakeProfitManager(cfg)
        tp_mgr.register("T2", 52000)

        result = tp_mgr.check("T2", "BUY", 50000, 52000, 0.002, high=52100, low=51800)
        assert result is not None
        assert result.action == ExitAction.PARTIAL_CLOSE
        assert result.close_qty == pytest.approx(0.001, abs=1e-6)  # 50%
        assert result.new_sl == 50000  # SL geser ke entry

    def test_partial_tp_second_hit(self):
        """Partial TP kedua → EXIT_TP (tutup semua sisa)."""
        cfg = make_config(partial_tp_enabled=True, partial_tp_ratio=0.50)
        tp_mgr = TakeProfitManager(cfg)
        tp_mgr.register("T3", 52000)

        # First hit → partial
        r1 = tp_mgr.check("T3", "BUY", 50000, 52000, 0.002, high=52100, low=51800)
        assert r1.action == ExitAction.PARTIAL_CLOSE

        # Second hit → full close
        r2 = tp_mgr.check("T3", "BUY", 50000, 52000, 0.001, high=52200, low=51900)
        assert r2 is not None
        assert r2.action == ExitAction.EXIT_TP

    def test_tp_zero_no_check(self):
        """TP price 0 → tidak check TP."""
        cfg = make_config()
        tp_mgr = TakeProfitManager(cfg)
        tp_mgr.register("T4", 0)

        result = tp_mgr.check("T4", "BUY", 50000, 0, 0.001, high=99999, low=50000)
        assert result is None

    def test_sell_tp_hit_by_low(self):
        """SELL: TP hit saat low <= tp_price."""
        cfg = make_config(partial_tp_enabled=False)
        tp_mgr = TakeProfitManager(cfg)
        tp_mgr.register("T5", 48000)

        result = tp_mgr.check("T5", "SELL", 50000, 48000, 0.001, high=49500, low=47900)
        assert result is not None
        assert result.action == ExitAction.EXIT_TP


# ══════════════════════════════════════════════════════════════════════
#  4. BREAK-EVEN
# ══════════════════════════════════════════════════════════════════════


class TestBreakEven:
    """Checklist #5: Breakeven tidak menggeser SL ke belakang."""

    def test_breakeven_trigger(self):
        """Breakeven saat profit >= 1R."""
        cfg = make_config(breakeven_trigger_r=1.0, breakeven_buffer_pct=0.001)
        be_mgr = BreakEvenManager(cfg)

        # profit = 1000 (entry=50000, close=51000), risk = 1000 (SL=49000)
        result = be_mgr.check("T1", "BUY", 50000, 49000, 51000)
        assert result is not None
        assert result.action == ExitAction.UPDATE_SL
        assert result.new_sl > 50000  # entry + buffer

    def test_breakeven_not_trigger_below_r(self):
        """Profit < 1R → tidak trigger."""
        cfg = make_config(breakeven_trigger_r=1.0)
        be_mgr = BreakEvenManager(cfg)

        # profit = 500, risk = 1000 → 0.5R
        result = be_mgr.check("T1", "BUY", 50000, 49000, 50500)
        assert result is None

    def test_breakeven_no_backward(self):
        """#5: SL sudah di atas entry → breakeven tidak ubah SL."""
        cfg = make_config(breakeven_trigger_r=1.0, breakeven_buffer_pct=0.001)
        be_mgr = BreakEvenManager(cfg)

        # SL sudah di 50500 (above entry 50000) → sudah at breakeven
        result = be_mgr.check("T1", "BUY", 50000, 50500, 51500)
        # Harus is_at_breakeven → added to _activated → return None
        assert result is None

    def test_breakeven_only_once(self):
        """Breakeven hanya trigger sekali per trade."""
        cfg = make_config(breakeven_trigger_r=1.0, breakeven_buffer_pct=0.001)
        be_mgr = BreakEvenManager(cfg)

        # First → trigger
        r1 = be_mgr.check("T1", "BUY", 50000, 49000, 51000)
        assert r1 is not None

        # Second → already activated
        r2 = be_mgr.check("T1", "BUY", 50000, 49000, 51500)
        assert r2 is None

    def test_breakeven_sell_trigger(self):
        """SELL breakeven: SL di bawah entry."""
        cfg = make_config(breakeven_trigger_r=1.0, breakeven_buffer_pct=0.001)
        be_mgr = BreakEvenManager(cfg)

        # SELL entry=50000, SL=51000, close=49000 → profit 1R
        result = be_mgr.check("T2", "SELL", 50000, 51000, 49000)
        assert result is not None
        assert result.action == ExitAction.UPDATE_SL
        assert result.new_sl < 50000  # entry - buffer

    def test_deregister(self):
        """Deregister menghapus state."""
        cfg = make_config()
        be_mgr = BreakEvenManager(cfg)
        be_mgr.check("T1", "BUY", 50000, 49000, 51000)
        assert be_mgr.is_activated("T1")
        be_mgr.deregister("T1")
        assert not be_mgr.is_activated("T1")


# ══════════════════════════════════════════════════════════════════════
#  5. TIMEOUT
# ══════════════════════════════════════════════════════════════════════


class TestTimeout:
    """Checklist #6: Timeout exit setelah MAX_HOLD_CANDLES."""

    def test_timeout_triggered(self):
        """#6: hold_candles >= max → EXIT_TIMEOUT."""
        cfg = make_config(max_hold_candles=48)
        mgr = ExitManager(cfg)
        mgr.register_trade("T1", "BUY", 50000, 49000, 52000)

        result = mgr.evaluate(
            trade_id="T1",
            symbol="BTCUSDT",
            side="BUY",
            entry_price=50000,
            sl_price=49000,
            tp_price=52000,
            filled_qty=0.001,
            hold_candles=48,
            high=50500,
            low=49100,
            close=50200,
            atr=500,
        )
        assert result.action == ExitAction.EXIT_TIMEOUT

    def test_timeout_not_triggered(self):
        """hold_candles < max → HOLD."""
        cfg = make_config(max_hold_candles=48)
        mgr = ExitManager(cfg)
        mgr.register_trade("T2", "BUY", 50000, 49000, 52000)

        result = mgr.evaluate(
            trade_id="T2",
            symbol="BTCUSDT",
            side="BUY",
            entry_price=50000,
            sl_price=49000,
            tp_price=52000,
            filled_qty=0.001,
            hold_candles=47,
            high=50500,
            low=49100,
            close=50200,
            atr=500,
        )
        assert result.action == ExitAction.HOLD


# ══════════════════════════════════════════════════════════════════════
#  6. EXIT MANAGER — Priority Order
# ══════════════════════════════════════════════════════════════════════


class TestExitManagerPriority:
    """SL harus mengalahkan TP — prioritas L1 > L2."""

    def test_sl_takes_precedence_over_tp(self):
        """Jika SL dan TP keduanya hit di candle yang sama, SL menang."""
        cfg = make_config()
        mgr = ExitManager(cfg)
        mgr.register_trade("T1", "BUY", 50000, 49000, 52000)

        # Candle besar: low < SL DAN high > TP
        result = mgr.evaluate(
            trade_id="T1",
            symbol="BTCUSDT",
            side="BUY",
            entry_price=50000,
            sl_price=49000,
            tp_price=52000,
            filled_qty=0.001,
            hold_candles=1,
            high=53000,
            low=48000,
            close=50000,
            atr=500,
        )
        assert result.action == ExitAction.EXIT_SL  # SL dievaluasi duluan

    def test_strategy_exit_processed(self):
        """Strategy exit signal diproses di L6."""
        cfg = make_config()
        mgr = ExitManager(cfg)
        mgr.register_trade("T2", "BUY", 50000, 49000, 52000)

        exit_signal = SimpleNamespace(
            reason="regime_change",
            urgency="urgent",
            confidence=0.9,
            exit_price=50500,
        )
        result = mgr.evaluate(
            trade_id="T2",
            symbol="BTCUSDT",
            side="BUY",
            entry_price=50000,
            sl_price=49000,
            tp_price=52000,
            filled_qty=0.001,
            hold_candles=1,
            high=50500,
            low=49500,
            close=50200,
            atr=500,
            strategy_exit=exit_signal,
        )
        assert result.action == ExitAction.EXIT_SIGNAL
        assert result.urgency == "urgent"

    def test_hold_when_nothing_triggers(self):
        """Semua check pass → HOLD."""
        cfg = make_config()
        mgr = ExitManager(cfg)
        mgr.register_trade("T3", "BUY", 50000, 49000, 52000)

        result = mgr.evaluate(
            trade_id="T3",
            symbol="BTCUSDT",
            side="BUY",
            entry_price=50000,
            sl_price=49000,
            tp_price=52000,
            filled_qty=0.001,
            hold_candles=1,
            high=50500,
            low=49100,
            close=50200,
            atr=500,
        )
        assert result.action == ExitAction.HOLD
        assert result.is_hold


# ══════════════════════════════════════════════════════════════════════
#  7. HOLD_DECISION Constant
# ══════════════════════════════════════════════════════════════════════


class TestHoldDecision:
    """HOLD_DECISION harus stabil."""

    def test_hold_decision_is_hold(self):
        assert HOLD_DECISION.action == ExitAction.HOLD
        assert HOLD_DECISION.is_hold
        assert not HOLD_DECISION.is_exit
        assert not HOLD_DECISION.is_urgent


# ══════════════════════════════════════════════════════════════════════
#  8. REGISTER_OPEN_POSITIONS — Recovery
# ══════════════════════════════════════════════════════════════════════


class TestRecovery:
    """Checklist #8: register_open_positions saat startup."""

    def test_register_open_positions(self):
        """Register semua posisi terbuka saat startup."""
        cfg = make_config()
        mgr = ExitManager(cfg)

        trades = [
            SimpleNamespace(
                trade_id="T1",
                side="BUY",
                avg_fill_price=50000,
                stop_loss=49000,
                take_profit=52000,
            ),
            SimpleNamespace(
                trade_id="T2",
                side="SELL",
                avg_fill_price=60000,
                stop_loss=61000,
                take_profit=58000,
            ),
        ]
        mgr.register_open_positions(trades)

        # Trailing state harus ada
        assert mgr.trailing.get_state("T1") is not None
        assert mgr.trailing.get_state("T2") is not None

        # TP state harus ada
        assert mgr.take_profit.get_state("T1") is not None
        assert mgr.take_profit.get_state("T2") is not None

    def test_deregister_trade(self):
        """Deregister membersihkan semua sub-manager state."""
        cfg = make_config()
        mgr = ExitManager(cfg)
        mgr.register_trade("T1", "BUY", 50000, 49000, 52000)

        # Sebelum deregister
        assert mgr.trailing.get_state("T1") is not None

        mgr.deregister_trade("T1")
        assert mgr.trailing.get_state("T1") is None
        assert mgr.take_profit.get_state("T1") is None


# ══════════════════════════════════════════════════════════════════════
#  9. PURE FUNCTION CHECK
# ══════════════════════════════════════════════════════════════════════


class TestPureFunction:
    """Checklist #7: evaluate() adalah pure function — tidak ada I/O."""

    def test_evaluate_returns_decision(self):
        """evaluate() harus mengembalikan ExitDecision, bukan coroutine."""
        cfg = make_config()
        mgr = ExitManager(cfg)
        mgr.register_trade("T1", "BUY", 50000, 49000, 52000)

        result = mgr.evaluate(
            trade_id="T1",
            symbol="BTCUSDT",
            side="BUY",
            entry_price=50000,
            sl_price=49000,
            tp_price=52000,
            filled_qty=0.001,
            hold_candles=1,
            high=50500,
            low=49100,
            close=50200,
            atr=500,
        )
        assert isinstance(result, ExitDecision)
        # Bukan coroutine — bisa dipanggil tanpa await
        import asyncio

        assert not asyncio.iscoroutine(result)


# ══════════════════════════════════════════════════════════════════════
#  10. EDGE CASES
# ══════════════════════════════════════════════════════════════════════


class TestEdgeCases:
    """Edge case coverage."""

    def test_sl_exact_hit(self):
        """SL exactly at candle low → EXIT_SL."""
        cfg = make_config()
        mgr = ExitManager(cfg)
        mgr.register_trade("T1", "BUY", 50000, 49000, 52000)

        result = mgr.evaluate(
            trade_id="T1",
            symbol="BTCUSDT",
            side="BUY",
            entry_price=50000,
            sl_price=49000,
            tp_price=52000,
            filled_qty=0.001,
            hold_candles=1,
            high=50200,
            low=49000,
            close=49100,  # low == sl
            atr=500,
        )
        assert result.action == ExitAction.EXIT_SL

    def test_tp_exact_hit(self):
        """TP exactly at candle high → EXIT_TP."""
        cfg = make_config(partial_tp_enabled=False)
        mgr = ExitManager(cfg)
        tp_mgr = TakeProfitManager(make_config(partial_tp_enabled=False))
        mgr.take_profit = tp_mgr
        mgr.register_trade("T2", "BUY", 50000, 49000, 52000)

        result = mgr.evaluate(
            trade_id="T2",
            symbol="BTCUSDT",
            side="BUY",
            entry_price=50000,
            sl_price=49000,
            tp_price=52000,
            filled_qty=0.001,
            hold_candles=1,
            high=52000,
            low=50500,
            close=51800,  # high == tp
            atr=500,
        )
        assert result.action == ExitAction.EXIT_TP

    def test_trailing_percentage_method(self):
        """Trailing percentage method bekerja."""
        cfg = make_config(
            trailing_method="percentage",
            trail_pct=0.02,
            trail_activation_r=0.0,
        )
        trailing = TrailingStopManager(cfg)
        trailing.register("T1", "BUY", 50000, 49000)

        result = trailing.update(
            "T1", "BUY", 50000, 49000, high=51000, low=50500, close=50800, atr=500
        )
        assert result is not None
        assert result.action == ExitAction.UPDATE_SL
        # Trail level = 51000 * (1 - 0.02) = 49980
        assert result.new_sl == pytest.approx(49980, abs=1)

    def test_zero_risk_breakeven(self):
        """Risk = 0 → breakeven tidak trigger."""
        cfg = make_config()
        be_mgr = BreakEvenManager(cfg)
        result = be_mgr.check("T1", "BUY", 50000, 50000, 51000)
        assert result is None
