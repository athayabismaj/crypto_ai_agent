"""
Portfolio Layer: RiskBudgetManager
=======================================================
Distribusi & Pengawasan Anggaran Risiko Per Strategi.

Filosofi Desain (Market Analyst View, 20+ tahun):
-----------
Pertanyaan yang benar bukan "Berapa lot yang bisa saya beli?" melainkan
"Berapa dolar yang siap saya HANGUSKAN hari ini tanpa merusak akun?"

RiskBudgetManager menjawab pertanyaan itu secara sistematis:

1. Setiap hari dimulai dengan budget kerugian maksimum (daily_risk_budget)
   Formula: equity × max_daily_loss_pct / jumlah_strategi (weighted)

2. Setiap trade MENGKONSUMSI budget tersebut sebesar (entry_price - SL) × qty
   = actual USD at risk — bukan notional, tapi risiko nyata

3. Jika budget hari ini HABIS → strategi tersebut SLEEPING sampai 00:00 UTC

4. Jika budget TOTAL (akumulasi) habis → strategi TERMINATE permanen
   (sampai manual reset oleh operator / Learning Layer)

Berbeda dari CircuitBreaker (di risk_layer) yang mengkill SEMUA trading,
RiskBudget pensiun strategi SATU PER SATU tanpa menghentikan yang lain.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timezone

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _today_utc() -> date:
    return datetime.now(timezone.utc).date()


# ── Data Classes ──────────────────────────────────────────────────────────────


@dataclass
class RiskBudgetStatus:
    """Snapshot status anggaran risiko untuk satu strategi."""

    strategy_id: str

    # ── Harian
    daily_risk_budget: float  # max loss per hari untuk strategi ini (USD)
    daily_risk_used: float  # realized + unrealized loss hari ini (USD)
    daily_remaining: float  # budget - used (USD)
    daily_risk_pct: float  # used / budget × 100 (%)

    # ── Total akumulasi (lifetime)
    total_risk_budget: float  # Akumulasi dari initial equity
    total_risk_used: float  # Total loss yang sudah direalisasi
    total_remaining: float  # total_risk_budget - total_risk_used

    # ── Status flags
    is_exhausted: bool  # True jika daily_remaining ≤ 0
    is_warning: bool  # True jika remaining < 20% budget
    is_terminated: bool  # True jika total_risk_budget habis permanen

    # ── Statistik kinerja hari ini
    trades_today: int
    wins_today: int
    losses_today: int
    win_rate_today: float

    timestamp: datetime

    @property
    def can_trade(self) -> bool:
        return not (self.is_exhausted or self.is_terminated)


@dataclass
class _StrategyState:
    """Internal state tracker per strategi."""

    strategy_id: str
    weight: float  # Bobot relatif alokasi budget

    # ── Harian (di-reset oleh reset_daily())
    daily_risk_used: float = 0.0
    open_risk_exposure: float = 0.0  # Risk dari posisi yang MASIH TERBUKA
    trades_today: int = 0
    wins_today: int = 0
    losses_today: int = 0
    last_reset: date = field(default_factory=_today_utc)

    # ── Total lifetime
    total_risk_used: float = 0.0  # Hanya realized losses (tidak termasuk wins)
    total_risk_budget_used_pct: float = 0.0

    # ── Status
    is_terminated: bool = False


class RiskBudgetManager:
    """
    Manajer anggaran risiko per strategi.

    Flow Integrasi:
    ──────────────
    1. risk_layer/position_size.py hitung risk_amount (USD at risk per trade)
    2. Panggil can_take_risk(strategy_id, risk_amount, equity) → True/False
    3. Jika True: panggil record_risk_taken(strategy_id, risk_amount) saat order submit
    4. Saat posisi close: panggil record_risk_realized(strategy_id, actual_pnl)
    5. Scheduler 00:00 UTC: panggil reset_daily()
    """

    def __init__(self, config: object) -> None:
        self.config = config

        # Persentase daily risk budget dari equity
        self.max_daily_loss_pct: float = getattr(config, "max_daily_loss_pct", 0.05)

        # Persentase total lifetime risk budget
        self.max_total_loss_pct: float = getattr(config, "max_total_loss_pct", 0.25)

        # Warning jika sisa budget < threshold ini
        self.warn_threshold: float = getattr(config, "budget_warn_threshold", 0.20)

        # Bobot per strategi {strategy_id: weight}
        # Jika strategi baru → auto-assign weight = 1.0
        self._strategy_weights: dict[str, float] = getattr(config, "strategy_weights", {})

        # Carry over profit ke budget besok?
        self.carry_over_wins: bool = getattr(config, "carry_over_wins", False)

        # Internal state per strategi
        self._states: dict[str, _StrategyState] = {}

        logger.info(
            f"[RiskBudgetManager] Init — daily_loss={self.max_daily_loss_pct:.1%}, "
            f"total_loss={self.max_total_loss_pct:.1%}"
        )

    # ── Public Interface ──────────────────────────────────────────────────────

    def can_take_risk(
        self,
        strategy_id: str,
        risk_amount: float,  # USD yang akan di-risk (entry - SL) × qty
        equity: float,
    ) -> tuple[bool, str]:
        """
        Gerbang utama: apakah strategi masih punya budget untuk trade ini?

        Dipanggil oleh risk_layer/position_size.py setelah kalkulasi sizing.
        """
        if equity <= 0:
            return False, "Equity is zero."

        state = self._get_or_create_state(strategy_id)

        if state.is_terminated:
            return False, (
                f"Strategy '{strategy_id}' is permanently terminated "
                f"(total risk budget exhausted)."
            )

        daily_budget = self._compute_daily_budget(strategy_id, equity)
        effective_used = state.daily_risk_used + state.open_risk_exposure

        daily_remaining = max(0.0, daily_budget - effective_used)

        if risk_amount > daily_remaining:
            return False, (
                f"Daily risk budget exceeded for '{strategy_id}'. "
                f"Requested: {risk_amount:.2f} USD. "
                f"Remaining today: {daily_remaining:.2f} USD "
                f"(Budget: {daily_budget:.2f}, Used: {effective_used:.2f})."
            )

        # Cek total lifetime budget
        total_budget = equity * self.max_total_loss_pct
        if (state.total_risk_used + risk_amount) > total_budget:
            state.is_terminated = True
            return False, (
                f"Strategy '{strategy_id}' TERMINATED: total lifetime risk "
                f"budget ({total_budget:.2f} USD) would be exceeded."
            )

        return True, ""

    def record_risk_taken(self, strategy_id: str, risk_amount: float) -> None:
        """
        Catat risiko yang di-commit saat order berhasil disubmit.
        Ini adalah open risk — belum tentu loss (mungkin hit TP).
        """
        state = self._get_or_create_state(strategy_id)
        state.open_risk_exposure += max(0.0, risk_amount)
        state.trades_today += 1
        logger.debug(
            f"[RiskBudget] {strategy_id}: risk_taken={risk_amount:.2f}, "
            f"open_exposure={state.open_risk_exposure:.2f}"
        )

    def record_risk_realized(self, strategy_id: str, actual_pnl: float) -> None:
        """
        Update saat posisi ditutup.
        - Win (pnl > 0): Bebaskan open_risk, tidak consume daily budget
        - Loss (pnl < 0): Consume daily budget sebesar actual loss
        """
        state = self._get_or_create_state(strategy_id)

        if actual_pnl >= 0:
            # WIN: Bebaskan risk exposure, tidak consume budget
            state.wins_today += 1
            if self.carry_over_wins:
                # Kompensasi: kurangi total_risk_used jika ada carry-over
                state.total_risk_used = max(0.0, state.total_risk_used - actual_pnl * 0.5)
        else:
            # LOSS: Konsumsi daily budget
            loss = abs(actual_pnl)
            state.daily_risk_used += loss
            state.total_risk_used += loss
            state.losses_today += 1

            logger.warning(
                f"[RiskBudget] {strategy_id}: Loss realized {loss:.2f} USD. "
                f"Daily used: {state.daily_risk_used:.2f}"
            )

        # Kurangi open risk exposure (trade sudah selesai)
        # Gunakan nilai minimum untuk menghindari negatif
        state.open_risk_exposure = max(0.0, state.open_risk_exposure - abs(actual_pnl))

    def get_status(self, strategy_id: str, equity: float) -> RiskBudgetStatus:
        """Return status lengkap untuk satu strategi. Untuk reporting & monitoring."""
        state = self._get_or_create_state(strategy_id)

        daily_budget = self._compute_daily_budget(strategy_id, equity)
        effective_used = state.daily_risk_used + state.open_risk_exposure
        daily_remaining = max(0.0, daily_budget - effective_used)
        daily_pct = (effective_used / daily_budget * 100) if daily_budget > 0 else 0.0

        total_budget = equity * self.max_total_loss_pct
        total_remaining = max(0.0, total_budget - state.total_risk_used)

        is_exhausted = daily_remaining <= 0
        is_warning = daily_remaining < (daily_budget * self.warn_threshold)
        is_terminated = state.is_terminated or (total_remaining <= 0)

        win_rate = 0.0
        total_closed = state.wins_today + state.losses_today
        if total_closed > 0:
            win_rate = state.wins_today / total_closed

        return RiskBudgetStatus(
            strategy_id=strategy_id,
            daily_risk_budget=daily_budget,
            daily_risk_used=effective_used,
            daily_remaining=daily_remaining,
            daily_risk_pct=daily_pct,
            total_risk_budget=total_budget,
            total_risk_used=state.total_risk_used,
            total_remaining=total_remaining,
            is_exhausted=is_exhausted,
            is_warning=is_warning,
            is_terminated=is_terminated,
            trades_today=state.trades_today,
            wins_today=state.wins_today,
            losses_today=state.losses_today,
            win_rate_today=win_rate,
            timestamp=_utcnow(),
        )

    def reset_daily(self) -> None:
        """
        Reset harian — dipanggil scheduler persis di UTC 00:00.
        Semua daily counters di-reset, halted strategies bisa trade lagi.
        """
        reset_date = _today_utc()
        reset_count = 0

        for state in self._states.values():
            if state.last_reset >= reset_date:
                continue  # Sudah di-reset hari ini

            # Jika strategy terminated secara total → jangan reset
            if state.is_terminated:
                continue

            prev_used = state.daily_risk_used
            state.daily_risk_used = 0.0
            state.open_risk_exposure = 0.0
            state.trades_today = 0
            state.wins_today = 0
            state.losses_today = 0
            state.last_reset = reset_date
            reset_count += 1

            logger.info(
                f"[RiskBudget] Daily reset: {state.strategy_id}. "
                f"Previous daily used: {prev_used:.2f} USD."
            )

        logger.info(f"[RiskBudget] Daily reset complete — {reset_count} strategies reset.")

    def get_portfolio_risk_summary(self, equity: float) -> dict:
        """
        Summary semua strategi untuk monitoring & alerting.
        Output cocok untuk Prometheus metrics / logging terstruktur.
        """
        summary: dict = {
            "timestamp": _utcnow().isoformat(),
            "total_equity": equity,
            "strategies": {},
        }

        total_daily_budget = 0.0
        total_daily_used = 0.0

        for strat_id in self._states:
            status = self.get_status(strat_id, equity)
            summary["strategies"][strat_id] = {
                "daily_budget": round(status.daily_risk_budget, 2),
                "daily_used": round(status.daily_risk_used, 2),
                "daily_remaining": round(status.daily_remaining, 2),
                "daily_pct": round(status.daily_risk_pct, 1),
                "total_used": round(status.total_risk_used, 2),
                "trades_today": status.trades_today,
                "win_rate": round(status.win_rate_today * 100, 1),
                "status": (
                    "TERMINATED"
                    if status.is_terminated
                    else "EXHAUSTED"
                    if status.is_exhausted
                    else "WARNING"
                    if status.is_warning
                    else "OK"
                ),
            }
            total_daily_budget += status.daily_risk_budget
            total_daily_used += status.daily_risk_used

        summary["portfolio"] = {
            "total_daily_budget": round(total_daily_budget, 2),
            "total_daily_used": round(total_daily_used, 2),
            "total_daily_remaining": round(max(0.0, total_daily_budget - total_daily_used), 2),
            "portfolio_utilization_pct": round(
                (total_daily_used / total_daily_budget * 100) if total_daily_budget > 0 else 0.0,
                1,
            ),
        }

        return summary

    def register_strategy(self, strategy_id: str, weight: float = 1.0) -> None:
        """
        Registrasi eksplisit strategi dengan bobot tertentu.
        Dipanggil saat agent startup untuk inisialisasi state.
        """
        if strategy_id not in self._states:
            self._states[strategy_id] = _StrategyState(
                strategy_id=strategy_id,
                weight=weight,
            )
            self._strategy_weights[strategy_id] = weight
            logger.info(
                f"[RiskBudget] Registered strategy '{strategy_id}' " f"with weight={weight}."
            )

    # ── Private Helpers ───────────────────────────────────────────────────────

    def _get_or_create_state(self, strategy_id: str) -> _StrategyState:
        if strategy_id not in self._states:
            weight = self._strategy_weights.get(strategy_id, 1.0)
            self._states[strategy_id] = _StrategyState(
                strategy_id=strategy_id,
                weight=weight,
            )
            logger.debug(f"[RiskBudget] Auto-registered strategy '{strategy_id}'.")
        return self._states[strategy_id]

    def _compute_daily_budget(self, strategy_id: str, equity: float) -> float:
        """
        Formula perhitungan budget harian per strategi.

        Total daily budget = equity × max_daily_loss_pct
        Per strategi = total × (weight_strat / sum_all_weights)
        """
        total_budget = equity * self.max_daily_loss_pct

        all_weights = sum(s.weight for s in self._states.values() if not s.is_terminated)
        if all_weights <= 0:
            all_weights = 1.0

        state = self._states.get(strategy_id)
        strat_weight = state.weight if state else 1.0

        return total_budget * (strat_weight / all_weights)
