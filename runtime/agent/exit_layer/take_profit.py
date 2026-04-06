"""
take_profit.py — TakeProfitManager.

Mengelola dua mode take profit:
- Single TP: tutup semua sekaligus saat tp_price tercapai
- Partial TP: tutup sebagian di TP1, SL geser ke entry, sisa di-trailing

Pure function — tidak ada I/O.
"""

from __future__ import annotations

import logging

from runtime.agent.models.enums import ExitAction  # type: ignore
from runtime.agent.models.exit import ExitDecision, TPState  # type: ignore

logger = logging.getLogger(__name__)


class TakeProfitManager:
    """Manajemen take-profit per posisi.

    Config keys:
        partial_tp_enabled: bool  — default True
        partial_tp_ratio: float   — default 0.50 (50% ditutup saat TP1)
    """

    def __init__(self, config: object) -> None:
        self.partial_tp_enabled: bool = getattr(config, "partial_tp_enabled", True)
        self.partial_tp_ratio: float = getattr(config, "partial_tp_ratio", 0.50)
        self._states: dict[str, TPState] = {}

    # ── Public API ────────────────────────────────────────────────

    def register(self, trade_id: str, tp_price: float) -> None:
        """Inisialisasi TP state saat posisi dibuka."""
        self._states[trade_id] = TPState(
            trade_id=trade_id,
            tp1_hit=False,
            tp1_price=tp_price,
            tp2_price=0.0,
            partial_ratio=self.partial_tp_ratio,
        )

    def check(
        self,
        trade_id: str,
        side: str,
        entry_price: float,
        tp_price: float,
        filled_qty: float,
        high: float,
        low: float,
    ) -> ExitDecision | None:
        """Cek apakah TP tercapai.

        Return ExitDecision jika:
        - Single TP tercapai → EXIT_TP
        - Partial TP pertama kali → PARTIAL_CLOSE
        - Full TP setelah partial → EXIT_TP
        - Belum tercapai → None
        """
        if tp_price <= 0:
            return None

        # Cek apakah TP di-hit
        tp_hit = (
            (side == "BUY" and high >= tp_price)
            or (side == "SELL" and low <= tp_price)
        )

        if not tp_hit:
            return None

        state = self._states.get(trade_id)

        # Partial TP: tutup sebagian, lanjut trailing
        if self.partial_tp_enabled and state and not state.tp1_hit:
            state.tp1_hit = True
            close_qty = filled_qty * self.partial_tp_ratio
            logger.info(
                "[TakeProfit] Partial TP hit %s: closing %.4f (%.0f%%)",
                trade_id, close_qty, self.partial_tp_ratio * 100,
            )
            return ExitDecision(
                action=ExitAction.PARTIAL_CLOSE,
                exit_price=tp_price,
                new_sl=entry_price,  # geser SL ke entry (breakeven)
                close_qty=close_qty,
                reason=f"Partial TP: {self.partial_tp_ratio:.0%} closed at {tp_price:.2f}",
                urgency="normal",
                source="tp",
                confidence=1.0,
            )

        # Full TP
        logger.info("[TakeProfit] Full TP hit %s at %.2f", trade_id, tp_price)
        return ExitDecision(
            action=ExitAction.EXIT_TP,
            exit_price=tp_price,
            new_sl=0.0,
            close_qty=0.0,
            reason=f"TP hit: {tp_price:.2f}",
            urgency="normal",
            source="tp",
            confidence=1.0,
        )

    def deregister(self, trade_id: str) -> None:
        """Hapus state saat posisi ditutup."""
        self._states.pop(trade_id, None)

    def get_state(self, trade_id: str) -> TPState | None:
        return self._states.get(trade_id)
