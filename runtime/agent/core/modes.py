from enum import Enum, auto
from typing import Any


class AgentMode(Enum):
    PAPER = "paper"
    SHADOW = "shadow"
    LIVE = "live"


class ModeConfig:
    """Mengontrol perilaku berbeda per mode."""

    @staticmethod
    def should_send_order(mode: AgentMode) -> bool:
        return mode in (AgentMode.SHADOW, AgentMode.LIVE)

    @staticmethod
    def use_real_money(mode: AgentMode) -> bool:
        return mode == AgentMode.LIVE

    @staticmethod
    def use_testnet(mode: AgentMode) -> bool:
        return mode == AgentMode.SHADOW

    @staticmethod
    def block_on_risk_violation(mode: AgentMode) -> bool:
        """Paper mode: log tapi tidak block. Live mode: block keras."""
        return mode == AgentMode.LIVE

    @staticmethod
    def require_sl(mode: AgentMode) -> bool:
        """SL wajib di live. Optional di paper (untuk eksperimen)."""
        return mode == AgentMode.LIVE


class TransitionResult(Enum):
    ALLOWED = auto()
    FORBIDDEN = auto()
    HAS_OPEN_POSITIONS = auto()
    HAS_PENDING_ORDERS = auto()


class ModeTransitionGuard:
    """
    Validasi sebelum mode bisa diubah.
    Dipanggil dari gateway/routes.py saat ada request ganti mode.
    """

    @staticmethod
    def validate_transition(
        from_mode: AgentMode, to_mode: AgentMode, current_state: Any
    ) -> TransitionResult:
        # Aturan transisi yang diizinkan:
        ALLOWED: dict[AgentMode, list[AgentMode]] = {
            AgentMode.PAPER: [AgentMode.SHADOW],
            AgentMode.SHADOW: [AgentMode.LIVE, AgentMode.PAPER],
            AgentMode.LIVE: [AgentMode.SHADOW],  # tidak langsung ke PAPER
        }

        if to_mode not in ALLOWED.get(from_mode, []):
            return TransitionResult.FORBIDDEN

        # Syarat wajib sebelum transisi:
        open_pos = getattr(current_state, "open_positions_count", 0)
        pending_orders = getattr(current_state, "pending_orders_count", 0)

        # Di Python, current_state mungkin sebuah dict kalo di mock test
        if isinstance(current_state, dict):
            open_pos = current_state.get("open_positions_count", 0)
            pending_orders = current_state.get("pending_orders_count", 0)

        if open_pos > 0:
            return TransitionResult.HAS_OPEN_POSITIONS

        if pending_orders > 0:
            return TransitionResult.HAS_PENDING_ORDERS

        return TransitionResult.ALLOWED
