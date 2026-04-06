"""
Exit Layer models — ExitDecision, TrailingState, TPState.

Output dari exit_manager + state per-posisi untuk trailing dan take-profit.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from runtime.agent.models.enums import ExitAction  # type: ignore


@dataclass
class ExitDecision:
    """Keputusan exit untuk satu posisi — output dari ExitManager.evaluate()."""

    action: ExitAction
    exit_price: float = 0.0  # 0.0 = gunakan market price
    new_sl: float = 0.0  # hanya untuk UPDATE_SL
    close_qty: float = 0.0  # hanya untuk PARTIAL_CLOSE (0 = tutup semua)
    reason: str = ""  # teks untuk logging & audit
    urgency: str = "normal"  # 'normal' | 'urgent'
    source: str = ""  # 'sl' | 'tp' | 'trailing' | 'strategy' | 'timeout'
    confidence: float = 0.0  # 0.0–1.0 seberapa yakin harus keluar

    @property
    def is_exit(self) -> bool:
        """True jika ini keputusan keluar (bukan HOLD atau UPDATE_SL)."""
        return self.action not in (ExitAction.HOLD, ExitAction.UPDATE_SL)

    @property
    def is_urgent(self) -> bool:
        return self.urgency == "urgent"

    @property
    def is_hold(self) -> bool:
        return self.action == ExitAction.HOLD


# ── Factory untuk keputusan HOLD (paling sering dipakai) ──────────────
HOLD_DECISION = ExitDecision(
    action=ExitAction.HOLD,
    exit_price=0.0,
    new_sl=0.0,
    close_qty=0.0,
    reason="",
    urgency="normal",
    source="hold",
    confidence=0.0,
)


@dataclass
class TrailingState:
    """State trailing per posisi — dikelola oleh TrailingStopManager."""

    trade_id: str
    method: str = "atr"  # 'atr' | 'percentage' | 'chandelier'
    activated: bool = False  # True setelah profit >= activation threshold
    current_trail: float = 0.0  # level trailing SL saat ini
    highest_high: float = 0.0  # untuk BUY: high tertinggi sejak entry
    lowest_low: float = 0.0  # untuk SELL: low terendah sejak entry
    last_updated: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass
class TPState:
    """State take-profit per posisi — dikelola oleh TakeProfitManager."""

    trade_id: str
    tp1_hit: bool = False  # sudah partial close?
    tp1_price: float = 0.0  # harga TP pertama
    tp2_price: float = 0.0  # TP kedua (atau 0 jika single TP)
    partial_ratio: float = 0.50  # berapa persen yang ditutup di TP1
