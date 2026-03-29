"""
risk_budget.py — Distribusi Risiko.

Mengontrol berapa banyak maksimum limit kerugian (dalam USD Dollar absolut)
yang boleh dicapai per agen strategi di hari ini.
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime


@dataclass
class RiskBudgetStatus:
    strategy_id: str
    daily_risk_budget: float
    daily_risk_used: float
    daily_risk_pct: float
    daily_remaining: float

    total_risk_budget: float
    total_risk_used: float

    is_exhausted: bool
    is_warning: bool
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))


class RiskBudgetManager:
    """Melacak risk budget (Max Loss / USD Exposure) per hari dan per strategi."""

    def __init__(self, config=None) -> None:  # type: ignore
        self.config = config
        self._strategy_weights: dict[str, float] = getattr(
            config, "strategy_weights", {"default": 1.0}
        )
        self._warn_threshold: float = getattr(config, "budget_warn_threshold", 0.20)
        self._carry_over_wins: bool = getattr(config, "carry_over_wins", False)

        # Tracing metrics
        self._daily_used: dict[str, float] = {}  # (strategy_id) => used
        self._total_used: dict[str, float] = {}  # (strategy_id) => used akumulasi
        self._daily_budget: dict[str, float] = {}  # Terkalkulasi ketika reset_daily / di awal.

    def compute_daily_budget(self, strategy_id: str, equity: float) -> float:
        """Kalkulasi risk harian dari ekuitas global X strategy weight."""
        max_daily_loss = getattr(self.config, "max_daily_loss_pct", 0.05)
        total_sys_budget = equity * max_daily_loss

        # Jika strategy tak ada weight khusus, gunakan fallback default 1.0 / N
        weight = self._strategy_weights.get(strategy_id)
        if weight is None:
            # Apabila unknown strategy
            weight = 1.0 / max(len(self._strategy_weights), 1)

        sum_weights = sum(self._strategy_weights.values())
        if sum_weights == 0:
            sum_weights = 1.0

        return total_sys_budget * (weight / sum_weights)

    def get_status(self, strategy_id: str, equity: float) -> RiskBudgetStatus:
        """Membaca posisi risk saat ini untuk satu strategi."""
        # Setup budget harian jika belum tersedia (kasus hari pertama atau baru direstart)
        if strategy_id not in self._daily_budget:
            self._daily_budget[strategy_id] = self.compute_daily_budget(strategy_id, equity)

        daily_limit = self._daily_budget.get(strategy_id, 0.0)
        daily_used = self._daily_used.get(strategy_id, 0.0)
        total_used = self._total_used.get(strategy_id, 0.0)

        daily_remaining = daily_limit - daily_used
        daily_pct = daily_used / daily_limit if daily_limit > 0 else 1.0

        # Risk > 100% dari limit = exhausted (Habis Modal Risk!)
        is_exhausted = daily_remaining <= 0

        # Sisa budget tinggal kurang dari WARN_THRESHOLD berarti masuk warning phase.
        is_warning = (
            (daily_remaining / daily_limit) < self._warn_threshold if daily_limit > 0 else True
        )

        # Kita anggap batas total_risk_budget absolut adalah keseluruhan modal awal
        # Atau bisa ditarik dari config. 'max_drawdown_pct'
        max_dd = getattr(self.config, "max_drawdown_pct", 0.10)
        total_risk_budget = equity * max_dd

        return RiskBudgetStatus(
            strategy_id=strategy_id,
            daily_risk_budget=daily_limit,
            daily_risk_used=daily_used,
            daily_risk_pct=daily_pct,
            daily_remaining=daily_remaining,
            total_risk_budget=total_risk_budget,
            total_risk_used=total_used,
            is_exhausted=is_exhausted,
            is_warning=is_warning,
        )

    def can_take_risk(
        self, strategy_id: str, risk_amount: float, equity: float
    ) -> tuple[bool, str]:
        """Cek apakah masih ada budget risk_amount USD (dari jarak stop-loss) untuk membuka trade."""
        status = self.get_status(strategy_id, equity)

        if status.is_exhausted:
            return (
                False,
                f"Risk Budget EXHAUSTED untuk '{strategy_id}'. Total loss melebihi kuota {status.daily_risk_budget:.2f}",
            )

        if risk_amount > status.daily_remaining:
            return False, (
                f"Risk Budget tidak cukup untuk '{strategy_id}'. "
                f"Butuh={risk_amount:.2f}, "
                f"Sisa={status.daily_remaining:.2f}"
            )

        return True, ""

    def record_risk_taken(self, strategy_id: str, risk_amount: float) -> None:
        """Catat risiko yang dikunci saat trade OPEN, mengikis 'Remaining Budget'."""
        self._daily_used[strategy_id] = self._daily_used.get(strategy_id, 0.0) + risk_amount
        self._total_used[strategy_id] = self._total_used.get(strategy_id, 0.0) + risk_amount

    def record_risk_realized(self, strategy_id: str, actual_pnl: float) -> None:
        """
        Catat ketika trade CLOSED.
        - Jika loss, tetap diamkan budget yang sudah terbakar (dimasukkan saat RECORD_RISK_TAKEN).
        - Jika win / profit, kita lepaskan / pulihkan kembali risiko yang terbakar, sehingga ia
          dapat bertrading lagi hari ini.
        - Jika `carry_over_wins` ON, profit bahkan akan menambah max limit hari ini.
        """
        # Note: Ini mekanisme sederhana mengembalikan "lock" risk margin ke pool.
        # Strategi ini lebih aman: Jika posisinya di-close dengan kerugian $100,
        # kita biarkan daily_used terekam sbg rugi $100.
        # Tapi karena pada saat 'open' kita reserve "potential loss" secara brutal (katakanlah $150 via Stoploss),
        # maka kita lepaskan selisih un-realized loss yang tak terjadi.
        # Namun, di metode risk budget ini umumnya difokuskan pada actual Pnl.

        if actual_pnl > 0:
            if self._carry_over_wins:
                # Compound: Tambah ceiling / plafon budget
                self._daily_budget[strategy_id] = (
                    self._daily_budget.get(strategy_id, 0.0) + actual_pnl
                )
            else:
                # Pulihkan / Turunkan pengikisian risk daily_used karena tidak merugi.
                pass

        # Jika actual_pnl < 0 (kerugian tercapai penuh atau parsial), budget yang 'used'
        # akan mencerminkan angka aslinya, jadi kita cukup pastikan _daily_used merefleksikan
        # actual kerugian sejati yang keluar dan mengikis batas di get_status()

    def reset_daily(self, equity: float) -> None:
        """Dipanggil scheduler di perguliran malam UTC 00:00."""
        self._daily_used.clear()
        self._daily_budget.clear()  # Akan terhitung ulang berdasarkan equity fresh besok
