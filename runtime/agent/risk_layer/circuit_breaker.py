"""
Lapis 2: CircuitBreaker
Mencegah trading jika kondisi drawdown/loss merugikan.
"""

from datetime import UTC, datetime, timedelta

from runtime.agent.core.config_schema import AgentConfig  # type: ignore
from runtime.agent.models.enums import CircuitState  # type: ignore


class CircuitBreaker:
    def __init__(self, config: AgentConfig):
        # Default limits jika ConfigDict tidak menyediakan
        self.max_daily_loss_pct = getattr(config, "max_daily_loss_pct", 0.05)
        self.max_drawdown_pct = getattr(config, "max_drawdown_pct", 0.10)
        self.max_consecutive_losses = getattr(config, "max_consecutive_losses", 5)
        self.halt_duration_minutes = getattr(config, "halt_duration_minutes", 60)
        self.warn_daily_loss_pct = getattr(config, "warn_daily_loss_pct", 0.03)

        self._state = CircuitState.NORMAL
        self._halted_at: datetime | None = None
        self._consecutive_losses = 0
        self._manual_halt = False
        self._halt_reasons: list[str] = []

    def check(self, daily_pnl: float, current_equity: float, peak_equity: float) -> CircuitState:
        """
        Evaluasi semua kondisi drawdown/loss dan return state terkini.
        Dipanggil setiap tick oleh RiskManager.
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

        reasons = []
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

    def _trigger_halt(self, reasons: list[str]) -> None:
        self._state = CircuitState.HALTED
        self._halted_at = datetime.now(UTC)
        self._halt_reasons = reasons

    def record_loss(self) -> None:
        self._consecutive_losses += 1

    def record_win(self) -> None:
        self._consecutive_losses = 0

    def manual_halt(self, reason: str) -> None:
        self._manual_halt = True
        self._trigger_halt([reason])

    def manual_resume(self) -> None:
        self._manual_halt = False
        self._resume()

    def reset_daily(self) -> None:
        """UTC 00:00 Scheduler reset."""
        self._consecutive_losses = 0

        # Resume otomatis jika bukan manual halt.
        if self._state == CircuitState.HALTED and not self._manual_halt:
            # Tetap tak reset drawdown!
            self._resume()

    def _auto_resume_due(self) -> bool:
        if self._manual_halt or not self._halted_at:
            return False
        elapsed = datetime.now(UTC) - self._halted_at
        return elapsed >= timedelta(minutes=self.halt_duration_minutes)

    def _resume(self) -> None:
        self._state = CircuitState.NORMAL
        self._halted_at = None
        self._halt_reasons = []

    @property
    def state(self) -> CircuitState:
        return self._state

    def status(self) -> dict:
        return {
            "state": self._state.value,
            "consecutive_losses": self._consecutive_losses,
            "halted_at": self._halted_at.isoformat() if self._halted_at else None,
            "manual_halt": self._manual_halt,
            "halt_reasons": self._halt_reasons,
        }
