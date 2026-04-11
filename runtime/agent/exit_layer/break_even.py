"""
break_even.py — BreakEvenManager.

Menggeser SL ke harga entry (+buffer) saat profit mencapai threshold.
Memastikan posisi tidak bisa rugi setelah threshold tercapai.

Config keys:
    breakeven_trigger_r: float  — default 1.0 (aktif saat profit = 1R)
    breakeven_buffer_pct: float — default 0.001 (buffer 0.1% di atas entry)
"""

from __future__ import annotations

import logging

from runtime.agent.models.enums import ExitAction  # type: ignore
from runtime.agent.models.exit import ExitDecision  # type: ignore

logger = logging.getLogger(__name__)


class BreakEvenManager:
    """Geser SL ke entry + buffer saat profit mencapai threshold.

    ATURAN: SL hanya maju (BUY: naik, SELL: turun). Tidak pernah mundur.
    """

    def __init__(self, config: object) -> None:
        self.trigger_r: float = getattr(config, "breakeven_trigger_r", 1.0)
        self.buffer_pct: float = getattr(config, "breakeven_buffer_pct", 0.001)
        self._activated: set[str] = set()  # trade_id yang sudah breakeven

    def check(
        self,
        trade_id: str,
        side: str,
        entry_price: float,
        sl_price: float,
        close: float,
    ) -> ExitDecision | None:
        """Cek apakah breakeven harus diaktifkan.

        Return UPDATE_SL jika profit >= trigger_r dan SL belum di-breakeven.
        Return None jika sudah breakeven atau belum trigger.
        """
        # Sudah breakeven? Tidak perlu cek lagi.
        if trade_id in self._activated:
            return None

        # Sudah di atas entry?
        if self._is_at_breakeven(side, entry_price, sl_price):
            self._activated.add(trade_id)
            return None

        # Hitung profit dalam R
        risk = abs(entry_price - sl_price)
        if risk <= 0:
            return None

        if side == "BUY":
            profit = close - entry_price
            be_price = entry_price + (self.buffer_pct * close)
        else:
            profit = entry_price - close
            be_price = entry_price - (self.buffer_pct * close)

        profit_r = profit / risk

        if profit_r >= self.trigger_r:
            # Jangan geser SL ke belakang
            if side == "BUY" and be_price <= sl_price:
                return None
            if side == "SELL" and be_price >= sl_price:
                return None

            self._activated.add(trade_id)
            logger.info(
                "[BreakEven] Activated %s: profit %.1fR >= %.1fR, new SL=%.2f",
                trade_id,
                profit_r,
                self.trigger_r,
                be_price,
            )
            return ExitDecision(
                action=ExitAction.UPDATE_SL,
                exit_price=0.0,
                new_sl=be_price,
                close_qty=0.0,
                reason=f"Break-even: profit {profit_r:.1f}R >= {self.trigger_r}R",
                urgency="normal",
                source="break_even",
                confidence=1.0,
            )

        return None

    def deregister(self, trade_id: str) -> None:
        """Hapus state saat posisi ditutup."""
        self._activated.discard(trade_id)

    def is_activated(self, trade_id: str) -> bool:
        """Cek apakah trade sudah di-breakeven."""
        return trade_id in self._activated

    # ── Private ───────────────────────────────────────────────────

    @staticmethod
    def _is_at_breakeven(side: str, entry: float, sl: float) -> bool:
        """SL sudah di atas entry (BUY) atau di bawah entry (SELL)."""
        if side == "BUY":
            return sl >= entry
        if side == "SELL":
            return sl <= entry
        return False
