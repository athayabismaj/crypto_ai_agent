"""
Lapis 2: CircuitBreaker — Penghenti Otomatis
===============================================
State machine yang menghentikan trading saat kondisi bahaya terdeteksi.
Ini adalah mekanisme self-preservation paling penting dalam sistem.

Filosofi (Market Analyst View, 20+ tahun):
    Trader terbaik yang saya kenal bukan yang paling sering menang,
    melainkan yang paling cepat berhenti saat kondisi melawan mereka.
    CircuitBreaker mengautomasi insting ini.

State Machine:
    NORMAL → WARNED → HALTED → NORMAL (auto-resume / manual reset)
    NORMAL → HALTED (langsung kritis tanpa warning)
    HALTED → NORMAL (hanya via auto-resume timeout atau manual_resume())

Integrasi:
    - CapitalManager.get_status() → ambil daily_pnl, equity, peak
    - Scheduler UTC 00:00 → reset_daily()
    - RiskManager → baca state setiap evaluate()
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from runtime.agent.core.config_schema import AgentConfig  # type: ignore
from runtime.agent.models.enums import CircuitState  # type: ignore

logger = logging.getLogger(__name__)


class CircuitBreaker:
    """
    State machine circuit breaker.

    Mencegah trading jika:
    - Daily loss > threshold (default -5%)
    - Drawdown dari peak > threshold (default -10%)
    - Consecutive losses > threshold (default 5)
    - Manual halt oleh operator

    Auto-resume setelah halt_duration_minutes (default 60 menit)
    kecuali manual halt yang butuh manual_resume().
    """

    def __init__(self, config: AgentConfig) -> None:
        # Thresholds — bisa di-override via config
        self.max_daily_loss_pct: float = getattr(config, "max_daily_loss_pct", 0.05)
        self.max_drawdown_pct: float = getattr(config, "max_drawdown_pct", 0.10)
        self.max_consecutive_losses: int = getattr(config, "max_consecutive_losses", 5)
        self.halt_duration_minutes: int = getattr(config, "halt_duration_minutes", 60)
        self.warn_daily_loss_pct: float = getattr(config, "warn_daily_loss_pct", 0.03)

        # Internal state
        self._state: CircuitState = CircuitState.NORMAL
        self._halted_at: datetime | None = None
        self._consecutive_losses: int = 0
        self._manual_halt: bool = False
        self._halt_reasons: list[str] = []

        logger.info(
            f"[CircuitBreaker] Initialized. "
            f"daily_loss={self.max_daily_loss_pct:.1%}, "
            f"drawdown={self.max_drawdown_pct:.1%}, "
            f"max_consec_losses={self.max_consecutive_losses}"
        )

    def check(
        self,
        daily_pnl: float,
        current_equity: float,
        peak_equity: float,
    ) -> CircuitState:
        """
        Evaluasi semua kondisi drawdown/loss dan return state terkini.
        Dipanggil setiap tick oleh RiskManager.

        Args:
            daily_pnl: PnL hari ini (negatif = loss)
            current_equity: Equity saat ini
            peak_equity: Equity tertinggi sepanjang waktu

        Returns:
            CircuitState terkini setelah evaluasi
        """
        # Cek auto resume dulu jika sedang halted
        if self._state == CircuitState.HALTED and self._auto_resume_due():
            self._resume()
            return self._state

        if self._state == CircuitState.HALTED:
            return self._state

        # Evaluasi pnl/drawdown metrics
        daily_loss_pct = (
            -daily_pnl / current_equity if daily_pnl < 0 and current_equity > 0 else 0.0
        )
        drawdown_pct = (peak_equity - current_equity) / peak_equity if peak_equity > 0 else 0.0

        reasons: list[str] = []
        is_halted = False

        if daily_loss_pct >= self.max_daily_loss_pct:
            is_halted = True
            reasons.append(
                f"Max daily loss ({self.max_daily_loss_pct:.1%}) terlampaui: {daily_loss_pct:.1%}"
            )

        if drawdown_pct >= self.max_drawdown_pct:
            is_halted = True
            reasons.append(
                f"Max drawdown ({self.max_drawdown_pct:.1%}) terlampaui: {drawdown_pct:.1%}"
            )

        if self._consecutive_losses >= self.max_consecutive_losses:
            is_halted = True
            reasons.append(f"Max consecutive losses ({self.max_consecutive_losses}) terlampaui")

        if is_halted:
            self._trigger_halt(reasons)
            return self._state

        # Cek warning level
        if daily_loss_pct >= self.warn_daily_loss_pct:
            self._state = CircuitState.WARNED
            self._halt_reasons = [
                f"Warn daily loss ({self.warn_daily_loss_pct:.1%}) terlampaui: {daily_loss_pct:.1%}"
            ]
            return self._state

        self._state = CircuitState.NORMAL
        return self._state

    def check_from_capital_status(self, capital_status: object) -> CircuitState:
        """
        Evaluasi dari CapitalStatus dataclass langsung.

        Ini adalah shortcut convenience agar RiskManager tidak perlu
        extract field satu per satu dari CapitalStatus.

        Args:
            capital_status: CapitalStatus dari CapitalManager.get_status()
        """
        daily_pnl = getattr(capital_status, "daily_pnl", 0.0)
        equity = getattr(capital_status, "current_equity", 0.0)
        peak = getattr(capital_status, "peak_equity", 0.0)
        return self.check(daily_pnl, equity, peak)

    def _trigger_halt(self, reasons: list[str]) -> None:
        self._state = CircuitState.HALTED
        self._halted_at = datetime.now(UTC)
        self._halt_reasons = reasons
        logger.critical(f"[CircuitBreaker] HALTED: {'; '.join(reasons)}")

    def record_loss(self) -> None:
        """Dipanggil setelah setiap trade yang merugi."""
        self._consecutive_losses += 1
        logger.debug(f"[CircuitBreaker] Loss recorded. Consecutive={self._consecutive_losses}")

    def record_win(self) -> None:
        """Reset consecutive loss counter setelah win."""
        self._consecutive_losses = 0

    def manual_halt(self, reason: str) -> None:
        """Halt permanen — hanya bisa resume via manual_resume()."""
        self._manual_halt = True
        self._trigger_halt([f"MANUAL: {reason}"])

    def manual_resume(self) -> None:
        """Resume dari manual halt. Butuh konfirmasi dari operator."""
        self._manual_halt = False
        self._resume()
        logger.info("[CircuitBreaker] Manual resume activated.")

    def reset_daily(self) -> None:
        """
        Reset counter harian — dipanggil scheduler UTC 00:00.

        PENTING: Drawdown dari peak TIDAK di-reset (tetap dihitung).
        Hanya daily counters (consecutive losses) yang di-reset.
        """
        self._consecutive_losses = 0

        # Resume otomatis jika bukan manual halt
        if self._state == CircuitState.HALTED and not self._manual_halt:
            self._resume()

        logger.info("[CircuitBreaker] Daily reset complete.")

    def _auto_resume_due(self) -> bool:
        if self._manual_halt or not self._halted_at:
            return False
        elapsed = datetime.now(UTC) - self._halted_at
        return elapsed >= timedelta(minutes=self.halt_duration_minutes)

    def _resume(self) -> None:
        self._state = CircuitState.NORMAL
        self._halted_at = None
        self._halt_reasons = []
        logger.info("[CircuitBreaker] Resumed to NORMAL state.")

    @property
    def state(self) -> CircuitState:
        """State terkini circuit breaker."""
        return self._state

    def status(self) -> dict:
        """Snapshot state untuk monitoring/healthcheck."""
        return {
            "state": self._state.value,
            "consecutive_losses": self._consecutive_losses,
            "halted_at": self._halted_at.isoformat() if self._halted_at else None,
            "manual_halt": self._manual_halt,
            "halt_reasons": self._halt_reasons,
        }
