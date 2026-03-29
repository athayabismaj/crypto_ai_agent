"""
allocator.py — Portfolio Layer Allocation Control.

Mengontrol berapa banyak modal yang bisa digunakan oleh setiap kombinasi strategi + simbol.
Ini adalah lapisan pertama portfolio layer yang dikonsultasikan sebelum sizing.
"""

from dataclasses import dataclass
from datetime import UTC, datetime


@dataclass
class AllocationBudget:
    strategy_id: str
    symbol: str
    max_usdt: float
    used_usdt: float
    available_usdt: float
    pct_of_equity: float


@dataclass
class AllocationMatrix:
    total_equity: float
    free_equity: float
    reserved_equity: float
    allocations: dict[str, AllocationBudget]
    total_used_pct: float
    timestamp: datetime


class PortfolioAllocator:
    """Manajer kuota modal untuk Strategy-Simbol combo."""

    def __init__(self) -> None:
        self._allocations: dict[str, float] = {}  # key="strategy_id:symbol", value=used_notional

        # Default config limits (biasanya dioverride via AgentConfig)
        self.MAX_STRATEGY_PCT = 0.40
        self.MAX_SYMBOL_PCT = 0.20
        self.MIN_CASH_RESERVE_PCT = 0.10
        self.MAX_OPEN_POSITIONS = 5
        self.MAX_CORRELATED_PCT = 0.35

    def get_budget(self, strategy_id: str, symbol: str, equity: float, positions: dict) -> AllocationBudget:  # type: ignore
        """
        Return berapa USDT yang tersedia untuk kombinasi strategy+symbol.
        Dipanggil oleh risk_layer sebelum position sizing.
        """
        key = f"{strategy_id}:{symbol}"
        current_used = self._allocations.get(key, 0.0)

        # 1. Strategy Budget Limitation (Misal: 40% equity)
        strategy_budget = equity * self.MAX_STRATEGY_PCT
        # Berapa yang strategi ini pakai (gabungan seluruh simbol)
        strategy_used = sum(
            val for k, val in self._allocations.items() if k.startswith(f"{strategy_id}:")
        )
        strat_available = strategy_budget - strategy_used

        # 2. Symbol Budget Limitation (Misal: 20% equity)
        symbol_budget = equity * self.MAX_SYMBOL_PCT
        # Berapa yang semua strategi pakai untuk aset X ini
        symbol_used = sum(val for k, val in self._allocations.items() if k.endswith(f":{symbol}"))
        sym_available = symbol_budget - symbol_used

        # 3. Ketersediaan Uang Tunai (Minimal sisa 10%)
        total_alloc = sum(self._allocations.values())
        cash_reserve = equity * self.MIN_CASH_RESERVE_PCT
        cash_available = equity - total_alloc - cash_reserve

        # Budget final adalah bottleneck terendah dari semua limit diatas
        final_budget = max(min(strat_available, sym_available, cash_available), 0.0)

        return AllocationBudget(
            strategy_id=strategy_id,
            symbol=symbol,
            max_usdt=min(strategy_budget, symbol_budget),
            used_usdt=current_used,
            available_usdt=final_budget,
            pct_of_equity=final_budget / equity if equity > 0 else 0.0,
        )

    def record_opened(self, strategy_id: str, symbol: str, notional: float) -> None:
        """Update internal tracking saat posisi berhasil dibuka."""
        key = f"{strategy_id}:{symbol}"
        self._allocations[key] = self._allocations.get(key, 0.0) + notional

    def record_closed(self, strategy_id: str, symbol: str, notional: float) -> None:
        """Bebaskan alokasi saat posisi ditutup."""
        key = f"{strategy_id}:{symbol}"
        if key in self._allocations:
            self._allocations[key] = max(0.0, self._allocations[key] - notional)
            if self._allocations[key] == 0.0:
                del self._allocations[key]

    def can_open(self, strategy_id: str, symbol: str, notional: float, equity: float, positions: dict) -> tuple[bool, str]:  # type: ignore
        """
        Cek status apakah kita bisa buka posisi baru secara global.
        (Max Position dan Cash Reserve limitation).
        """
        if len(positions) >= self.MAX_OPEN_POSITIONS:
            return False, f"Max {self.MAX_OPEN_POSITIONS} positions limit terlampaui."

        budget = self.get_budget(strategy_id, symbol, equity, positions)
        if notional > budget.available_usdt:
            return (
                False,
                f"Notional {notional:.2f} membebani max limit alokasi {budget.available_usdt:.2f}.",
            )

        return True, ""

    def get_matrix(self, equity: float, positions: dict) -> AllocationMatrix:  # type: ignore
        """Snapshot seluruh situasi alokasi. Untuk monitoring & reporting."""
        total_used = sum(self._allocations.values())
        return AllocationMatrix(
            total_equity=equity,
            free_equity=equity - total_used,
            reserved_equity=equity * self.MIN_CASH_RESERVE_PCT,
            allocations={},
            total_used_pct=total_used / equity if equity > 0 else 0.0,
            timestamp=datetime.now(UTC),
        )
