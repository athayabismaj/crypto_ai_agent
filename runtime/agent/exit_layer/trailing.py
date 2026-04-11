"""
trailing.py — TrailingStopManager.

Menggeser SL secara otomatis mengikuti pergerakan harga yang menguntungkan,
mengunci profit sambil memberikan ruang untuk volatilitas normal.

3 metode: ATR-based, Percentage, Chandelier.
Ratchet effect: trail hanya naik (BUY) / hanya turun (SELL).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from runtime.agent.models.enums import ExitAction  # type: ignore
from runtime.agent.models.exit import ExitDecision, TrailingState  # type: ignore

logger = logging.getLogger(__name__)


class TrailingStopManager:
    """Manajemen trailing stop per posisi.

    Config keys:
        trailing_method: str   — 'atr' | 'percentage' | 'chandelier'
        trail_atr_mult: float  — default 2.0, jarak trail = ATR × mult
        trail_pct: float       — default 0.02, jarak 2% dari high/low
        trail_activation_r: float — default 1.0, aktif setelah profit = 1R
    """

    def __init__(self, config: object) -> None:
        self.method: str = getattr(config, "trailing_method", "atr")
        self.atr_mult: float = getattr(config, "trail_atr_mult", 2.0)
        self.trail_pct: float = getattr(config, "trail_pct", 0.02)
        self.activation_r: float = getattr(config, "trail_activation_r", 1.0)
        self._states: dict[str, TrailingState] = {}

    # ── Public API ────────────────────────────────────────────────

    def register(self, trade_id: str, side: str, entry_price: float, sl_price: float) -> None:
        """Inisialisasi TrailingState saat posisi dibuka."""
        self._states[trade_id] = TrailingState(
            trade_id=trade_id,
            method=self.method,
            activated=False,
            current_trail=sl_price,
            highest_high=entry_price,
            lowest_low=entry_price,
            last_updated=datetime.now(UTC),
        )
        logger.debug(
            "[Trailing] Registered %s, method=%s, initial_trail=%.2f",
            trade_id,
            self.method,
            sl_price,
        )

    def update(
        self,
        trade_id: str,
        side: str,
        entry_price: float,
        sl_price: float,
        high: float,
        low: float,
        close: float,
        atr: float,
    ) -> ExitDecision | None:
        """Update trailing state dan return ExitDecision jika:
        - Trail level diperbarui → UPDATE_SL
        - Harga menembus trail → EXIT_TRAIL
        - Trailing belum aktif → None
        """
        state = self._states.get(trade_id)
        if state is None:
            return None

        # Cek aktivasi trail
        if not state.activated:
            profit_r = self._calc_profit_r(side, entry_price, sl_price, close)
            if profit_r >= self.activation_r:
                state.activated = True
                logger.info("[Trailing] Activated %s, profit_r=%.2f", trade_id, profit_r)
            else:
                return None

        if side == "BUY":
            return self._update_buy_trail(trade_id, state, high, low, atr)
        else:
            return self._update_sell_trail(trade_id, state, high, low, atr)

    def deregister(self, trade_id: str) -> None:
        """Hapus state saat posisi ditutup."""
        self._states.pop(trade_id, None)

    def get_state(self, trade_id: str) -> TrailingState | None:
        return self._states.get(trade_id)

    # ── Private ───────────────────────────────────────────────────

    def _calc_profit_r(self, side: str, entry: float, sl: float, close: float) -> float:
        """Hitung profit dalam R (risk units)."""
        risk = abs(entry - sl)
        if risk <= 0:
            return 0.0
        if side == "BUY":
            return (close - entry) / risk
        else:
            return (entry - close) / risk

    def _calc_trail_level(self, reference: float, atr: float, direction: str) -> float:
        """Hitung trail level berdasarkan metode."""
        if self.method == "percentage":
            if direction == "buy":
                return reference * (1 - self.trail_pct)
            else:
                return reference * (1 + self.trail_pct)
        else:  # atr & chandelier
            if direction == "buy":
                return reference - (atr * self.atr_mult)
            else:
                return reference + (atr * self.atr_mult)

    def _update_buy_trail(
        self,
        trade_id: str,
        state: TrailingState,
        high: float,
        low: float,
        atr: float,
    ) -> ExitDecision | None:
        # Update highest high
        if high > state.highest_high:
            state.highest_high = high

        new_trail = self._calc_trail_level(state.highest_high, atr, "buy")

        # Trail hanya boleh naik (ratchet effect)
        if new_trail > state.current_trail:
            old_trail = state.current_trail
            state.current_trail = new_trail
            state.last_updated = datetime.now(UTC)
            return ExitDecision(
                action=ExitAction.UPDATE_SL,
                exit_price=0.0,
                new_sl=new_trail,
                close_qty=0.0,
                reason=f"Trailing SL naik: {old_trail:.2f} → {new_trail:.2f}",
                urgency="normal",
                source="trailing",
                confidence=1.0,
            )

        # Cek apakah harga menembus trail level
        if low <= state.current_trail:
            return ExitDecision(
                action=ExitAction.EXIT_TRAIL,
                exit_price=state.current_trail,
                new_sl=0.0,
                close_qty=0.0,
                reason=f"Trail SL hit: low {low:.2f} <= trail {state.current_trail:.2f}",
                urgency="urgent",
                source="trailing",
                confidence=1.0,
            )

        return None

    def _update_sell_trail(
        self,
        trade_id: str,
        state: TrailingState,
        high: float,
        low: float,
        atr: float,
    ) -> ExitDecision | None:
        # Update lowest low
        if low < state.lowest_low:
            state.lowest_low = low

        new_trail = self._calc_trail_level(state.lowest_low, atr, "sell")

        # Trail hanya boleh turun (ratchet effect untuk SELL)
        if new_trail < state.current_trail:
            old_trail = state.current_trail
            state.current_trail = new_trail
            state.last_updated = datetime.now(UTC)
            return ExitDecision(
                action=ExitAction.UPDATE_SL,
                exit_price=0.0,
                new_sl=new_trail,
                close_qty=0.0,
                reason=f"Trailing SL turun: {old_trail:.2f} → {new_trail:.2f}",
                urgency="normal",
                source="trailing",
                confidence=1.0,
            )

        # Cek apakah harga menembus trail level
        if high >= state.current_trail:
            return ExitDecision(
                action=ExitAction.EXIT_TRAIL,
                exit_price=state.current_trail,
                new_sl=0.0,
                close_qty=0.0,
                reason=f"Trail SL hit: high {high:.2f} >= trail {state.current_trail:.2f}",
                urgency="urgent",
                source="trailing",
                confidence=1.0,
            )

        return None
