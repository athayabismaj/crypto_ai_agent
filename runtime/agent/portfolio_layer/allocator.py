"""
Layer Portfolio: Allocator
Manajer Alokasi Dana (USDT) yang didistribusikan per kombinasi Strategi & Simbol.
Mencegah satu strategi memonopoli seluruh modal yang ada.
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timezone

from runtime.agent.core.config_schema import AgentConfig  # type: ignore

logger = logging.getLogger(__name__)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class AllocationBudget:
    strategy_id: str
    symbol: str
    max_usdt: float  # Quota maksimum dalam USDT
    used_usdt: float  # Yang sudah digunakan (Notional Margin)
    available_usdt: float  # Sisa saldo yang bisa dilempar
    pct_of_equity: float  # available / total_equity * 100


@dataclass
class AllocationMatrix:
    """Snapshot total dana dan distribusinya."""

    total_equity: float
    free_equity: float
    reserved_equity: float  # Dana mati yang diamankan
    allocations: dict[str, AllocationBudget]  # key: f"{strategy_id}:{symbol}"
    total_used_pct: float
    timestamp: datetime


class PortfolioAllocator:
    """Mesin penjaga pintu kuota modal (USDT)."""

    def __init__(self, config: AgentConfig) -> None:
        self.config = config

        # Threshold Limit (persentase)
        self.max_per_symbol_pct = getattr(config, "portfolio_max_symbol_pct", 0.20)
        self.max_per_strat_pct = getattr(config, "portfolio_max_strategy_pct", 0.40)
        self.reserved_cash_pct = getattr(config, "portfolio_reserved_cash_pct", 0.10)

        # State tracker
        # key = "{strategy_id}:{symbol}", value = float (USDT used)
        self._usage_tracker: dict[str, float] = {}

        # Helper Tracker untuk akumulasi
        self._symbol_usage: dict[str, float] = {}
        self._strat_usage: dict[str, float] = {}

    def _get_key(self, strat: str, sym: str) -> str:
        return f"{strat}:{sym}"

    def get_budget(
        self, strategy_id: str, symbol: str, equity: float, positions: dict
    ) -> AllocationBudget:
        """
        Kalkulasi alokasi maksimal yang bisa diberikan ke pasangan ini saat ini.
        Akan memotong jatah dari Limit Simbol dan Limit Strategi.
        """
        if equity <= 0:
            return AllocationBudget(strategy_id, symbol, 0.0, 0.0, 0.0, 0.0)

        # 1. Alokasi Absolute via Parameter Config
        sym_limit = equity * self.max_per_symbol_pct
        strat_limit = equity * self.max_per_strat_pct

        # 2. Penggunaan Saat Ini
        key = self._get_key(strategy_id, symbol)
        used_here = self._usage_tracker.get(key, 0.0)
        sym_used = self._symbol_usage.get(symbol, 0.0)
        strat_used = self._strat_usage.get(strategy_id, 0.0)

        # 3. Hitung Ruang Tersisa
        sym_rem = max(0.0, sym_limit - sym_used)
        strat_rem = max(0.0, strat_limit - strat_used)

        # 4. Batasan mutlak yang bisa diberikan
        # Ruang yang kita bisa pakai adalah sisa yang paling ketat ditambah yang sudah kita pakai di state ini
        available = min(sym_rem, strat_rem)
        max_possible = available + used_here

        pct_eq = (available / equity) * 100 if equity > 0 else 0.0

        return AllocationBudget(
            strategy_id=strategy_id,
            symbol=symbol,
            max_usdt=max_possible,
            used_usdt=used_here,
            available_usdt=available,
            pct_of_equity=pct_eq,
        )

    def record_opened(self, strategy_id: str, symbol: str, notional: float) -> None:
        """Tandai bahwa USDT telah disedot oleh pasar."""
        if notional <= 0:
            return

        key = self._get_key(strategy_id, symbol)
        self._usage_tracker[key] = self._usage_tracker.get(key, 0.0) + notional
        self._symbol_usage[symbol] = self._symbol_usage.get(symbol, 0.0) + notional
        self._strat_usage[strategy_id] = self._strat_usage.get(strategy_id, 0.0) + notional

        logger.debug(f"[Allocator] Allocated {notional:.2f} USDT to {key}")

    def record_closed(self, strategy_id: str, symbol: str, notional: float) -> None:
        """Lepaskan USDT kembali ke kas (Portfolio)."""
        if notional <= 0:
            return

        key = self._get_key(strategy_id, symbol)

        self._usage_tracker[key] = max(0.0, self._usage_tracker.get(key, 0.0) - notional)
        self._symbol_usage[symbol] = max(0.0, self._symbol_usage.get(symbol, 0.0) - notional)
        self._strat_usage[strategy_id] = max(
            0.0, self._strat_usage.get(strategy_id, 0.0) - notional
        )

        logger.debug(f"[Allocator] Freed {notional:.2f} USDT from {key}")

    def get_matrix(self, equity: float, positions: dict) -> AllocationMatrix:
        total_used = sum(self._usage_tracker.values())
        res_cash = equity * self.reserved_cash_pct
        free_eq = max(0.0, equity - total_used - res_cash)

        pct_used = (total_used / equity) * 100 if equity > 0 else 0.0

        budgets = {}
        for k, v in self._usage_tracker.items():
            strat, sym = k.split(":")
            if v > 0:
                # generate live budget untuk report
                bdg = self.get_budget(strat, sym, equity, positions)
                bdg.used_usdt = v  # force override
                budgets[k] = bdg

        return AllocationMatrix(
            total_equity=equity,
            free_equity=free_eq,
            reserved_equity=res_cash,
            allocations=budgets,
            total_used_pct=pct_used,
            timestamp=utcnow(),
        )

    def can_open(
        self, strategy_id: str, symbol: str, notional: float, equity: float, positions: dict
    ) -> tuple[bool, str]:
        """Validasi akhir: Apakah ada cukup uang untuk buka order sebesar 'notional' ini?"""
        if equity <= 0:
            return False, "Equity is zero or negative."

        if notional <= 0:
            return False, f"Invalid notional requested: {notional}"

        budget = self.get_budget(strategy_id, symbol, equity, positions)

        if notional > budget.available_usdt:
            return (
                False,
                f"Insufficient budget for {strategy_id}:{symbol}. Req: {notional:.2f}, Avail: {budget.available_usdt:.2f}",
            )

        # Cek ketersediaan Free Equity Murni setelah disisihkan Reserved Cash
        mx = self.get_matrix(equity, positions)
        if notional > mx.free_equity:
            return (
                False,
                f"Hit Global Reserve Limit. Free Eq: {mx.free_equity:.2f}, Req: {notional:.2f}",
            )

        return True, ""
