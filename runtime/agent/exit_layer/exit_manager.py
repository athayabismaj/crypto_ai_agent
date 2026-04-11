"""
exit_manager.py — Koordinator Exit Layer.

Dipanggil setiap tick untuk setiap posisi aktif. Mengevaluasi semua
kondisi exit secara berurutan (7 langkah) dan mengembalikan satu
keputusan akhir.

EVALUASI BERHENTI DI PRIORITAS PERTAMA YANG TERPENUHI.
Jika SL tercapai, tidak perlu cek TP, trailing, dll.

Pure function — tidak ada I/O.
"""

from __future__ import annotations

import logging

from runtime.agent.exit_layer.break_even import BreakEvenManager  # type: ignore
from runtime.agent.exit_layer.take_profit import TakeProfitManager  # type: ignore
from runtime.agent.exit_layer.trailing import TrailingStopManager  # type: ignore
from runtime.agent.models.enums import ExitAction  # type: ignore
from runtime.agent.models.exit import HOLD_DECISION, ExitDecision  # type: ignore

logger = logging.getLogger(__name__)

# Default: force exit jika posisi terbuka > N candle tanpa progress
DEFAULT_MAX_HOLD_CANDLES = 48


class ExitManager:
    """Koordinator 7-langkah evaluasi exit.

    Di-inject dengan sub-managers (trailing, take_profit, break_even)
    dan config. Dipanggil dari main_loop setiap tick untuk setiap
    posisi aktif.

    Config keys:
        max_hold_candles: int — default 48
    """

    def __init__(
        self,
        config: object,
        trailing: TrailingStopManager | None = None,
        take_profit: TakeProfitManager | None = None,
        break_even: BreakEvenManager | None = None,
    ) -> None:
        self.config = config
        self.trailing = trailing or TrailingStopManager(config)
        self.take_profit = take_profit or TakeProfitManager(config)
        self.break_even = break_even or BreakEvenManager(config)
        self.max_hold_candles: int = getattr(
            config,
            "max_hold_candles",
            DEFAULT_MAX_HOLD_CANDLES,
        )

    # ── Public API ────────────────────────────────────────────────

    def evaluate(
        self,
        trade_id: str,
        symbol: str,
        side: str,
        entry_price: float,
        sl_price: float,
        tp_price: float,
        filled_qty: float,
        hold_candles: int,
        high: float,
        low: float,
        close: float,
        atr: float,
        strategy_exit: object | None = None,
    ) -> ExitDecision:
        """Evaluasi satu posisi — 7 langkah berurutan.

        Args:
            trade_id: ID unik posisi
            symbol: simbol trading (e.g. BTCUSDT)
            side: 'BUY' atau 'SELL'
            entry_price: harga entry rata-rata
            sl_price: harga stop-loss saat ini
            tp_price: harga take-profit (0 = tidak ada)
            filled_qty: kuantitas yang terisi
            hold_candles: jumlah candle sejak posisi dibuka
            high: candle high saat ini
            low: candle low saat ini
            close: candle close saat ini
            atr: ATR saat ini
            strategy_exit: ExitSignal dari strategy (None jika tidak ada)

        Returns:
            ExitDecision — aksi yang harus dilakukan
        """
        # ── L1: Hard SL ──────────────────────────────────────────
        sl_hit = self._check_sl(side, sl_price, high, low)
        if sl_hit is not None:
            logger.warning("[ExitManager] SL hit: %s %s", symbol, sl_hit.reason)
            return sl_hit

        # ── L2: Hard TP ──────────────────────────────────────────
        tp_hit = self.take_profit.check(
            trade_id=trade_id,
            side=side,
            entry_price=entry_price,
            tp_price=tp_price,
            filled_qty=filled_qty,
            high=high,
            low=low,
        )
        if tp_hit is not None:
            logger.info("[ExitManager] TP hit: %s %s", symbol, tp_hit.reason)
            return tp_hit

        # ── L3: Break-even ───────────────────────────────────────
        be_update = self.break_even.check(
            trade_id=trade_id,
            side=side,
            entry_price=entry_price,
            sl_price=sl_price,
            close=close,
        )
        if be_update is not None:
            logger.info("[ExitManager] Break-even: %s %s", symbol, be_update.reason)
            return be_update

        # ── L4: Trailing SL ──────────────────────────────────────
        trail = self.trailing.update(
            trade_id=trade_id,
            side=side,
            entry_price=entry_price,
            sl_price=sl_price,
            high=high,
            low=low,
            close=close,
            atr=atr,
        )
        if trail is not None:
            if trail.action == ExitAction.EXIT_TRAIL:
                logger.warning("[ExitManager] Trail SL hit: %s %s", symbol, trail.reason)
            else:
                logger.info("[ExitManager] Trail updated: %s %s", symbol, trail.reason)
            return trail

        # ── L5: Partial TP sudah dihandle di L2 oleh TakeProfitManager
        #    (TakeProfitManager mengembalikan PARTIAL_CLOSE jika applicable)

        # ── L6: Strategy exit signal ─────────────────────────────
        if strategy_exit is not None:
            reason = getattr(strategy_exit, "reason", "strategy_exit")
            urgency = getattr(strategy_exit, "urgency", "normal")
            confidence = getattr(strategy_exit, "confidence", 0.0)
            exit_price = getattr(strategy_exit, "exit_price", 0.0)
            logger.info("[ExitManager] Strategy exit: %s reason=%s", symbol, reason)
            return ExitDecision(
                action=ExitAction.EXIT_SIGNAL,
                exit_price=exit_price,
                new_sl=0.0,
                close_qty=0.0,
                reason=reason,
                urgency=urgency,
                source="strategy",
                confidence=confidence,
            )

        # ── L7: Timeout ──────────────────────────────────────────
        timeout = self._check_timeout(hold_candles)
        if timeout is not None:
            logger.info("[ExitManager] Timeout: %s after %d candles", symbol, hold_candles)
            return timeout

        # ── HOLD — tidak ada aksi ────────────────────────────────
        return HOLD_DECISION

    def register_trade(
        self,
        trade_id: str,
        side: str,
        entry_price: float,
        sl_price: float,
        tp_price: float,
    ) -> None:
        """Inisialisasi semua sub-managers untuk trade baru."""
        self.trailing.register(trade_id, side, entry_price, sl_price)
        self.take_profit.register(trade_id, tp_price)
        logger.debug("[ExitManager] Registered trade %s", trade_id)

    def deregister_trade(self, trade_id: str) -> None:
        """Hapus semua state saat posisi ditutup."""
        self.trailing.deregister(trade_id)
        self.take_profit.deregister(trade_id)
        self.break_even.deregister(trade_id)
        logger.debug("[ExitManager] Deregistered trade %s", trade_id)

    def register_open_positions(self, trades: list) -> None:
        """Inisialisasi trailing state untuk semua posisi saat startup.

        Dipanggil oleh main.py pada startup untuk recovery.
        """
        for trade in trades:
            trade_id = getattr(trade, "trade_id", "")
            side = getattr(trade, "side", "BUY")
            entry = getattr(trade, "avg_fill_price", getattr(trade, "entry_price", 0.0))
            sl = getattr(trade, "stop_loss", getattr(trade, "sl_price", 0.0))
            tp = getattr(trade, "take_profit", getattr(trade, "tp_price", 0.0))
            if trade_id:
                self.register_trade(trade_id, side, entry, sl, tp)
        logger.info("[ExitManager] Registered %d open positions for recovery", len(trades))

    # ── Private ───────────────────────────────────────────────────

    @staticmethod
    def _check_sl(
        side: str,
        sl_price: float,
        high: float,
        low: float,
    ) -> ExitDecision | None:
        """SL check menggunakan candle high/low, bukan hanya close.

        BUY: SL hit jika low <= sl_price
        SELL: SL hit jika high >= sl_price
        """
        if sl_price <= 0:
            return None

        if side == "BUY" and low <= sl_price:
            return ExitDecision(
                action=ExitAction.EXIT_SL,
                exit_price=sl_price,
                new_sl=0.0,
                close_qty=0.0,
                reason=f"SL hit: low {low:.2f} <= sl {sl_price:.2f}",
                urgency="urgent",
                source="sl",
                confidence=1.0,
            )

        if side == "SELL" and high >= sl_price:
            return ExitDecision(
                action=ExitAction.EXIT_SL,
                exit_price=sl_price,
                new_sl=0.0,
                close_qty=0.0,
                reason=f"SL hit: high {high:.2f} >= sl {sl_price:.2f}",
                urgency="urgent",
                source="sl",
                confidence=1.0,
            )

        return None

    def _check_timeout(self, hold_candles: int) -> ExitDecision | None:
        """Force exit jika posisi terlalu lama tanpa progress."""
        if hold_candles >= self.max_hold_candles:
            return ExitDecision(
                action=ExitAction.EXIT_TIMEOUT,
                exit_price=0.0,
                new_sl=0.0,
                close_qty=0.0,
                reason=f"Timeout: {hold_candles} candles >= max {self.max_hold_candles}",
                urgency="normal",
                source="timeout",
                confidence=0.8,
            )
        return None
