"""
Lapis 4: ExposureControl — Penjaga Konsentrasi Portfolio
==========================================================
Mencegah konsentrasi ekstrem atau limit posisi berlebihan.

Perubahan Arsitektur Kunci:
    Versi lama: Hardcode CORRELATED_PAIRS sendiri + hitung sendiri.
    Versi baru: Delegasi korelasi ke CorrelationController (Portfolio Layer),
    delegasi kuota ke PortfolioAllocator. ExposureControl tetap menjaga:
    1. Max jumlah posisi terbuka
    2. Max exposure per-symbol (% equity)
    3. Max total exposure (% equity)
    4. Integrasi dengan PortfolioAllocator.can_open() untuk kuota budget

Filosofi (Market Analyst View, 20+ tahun):
    Diversifikasi yang sesungguhnya bukan hanya \"beli banyak koin\".
    Jika 5 posisi bergerak ke arah yang sama → itu satu taruhan besar.
    ExposureControl + CorrelationController bekerja sama:
    - ExposureControl: limit per symbol dan total
    - CorrelationController: limit per grup korelasi
"""

from __future__ import annotations

import logging

from runtime.agent.core.config_schema import AgentConfig  # type: ignore
from runtime.agent.models.risk import TradeRequest  # type: ignore

logger = logging.getLogger(__name__)


class ExposureControl:
    """
    Validator exposure portfolio.

    Fokus pada 3 hal:
    1. Jumlah posisi terbuka (max_positions)
    2. Konsentrasi per-symbol (max_per_symbol)
    3. Exposure total (max_total)

    Korelasi antar-aset DIDELEGASIKAN ke CorrelationController
    yang sudah production-grade di Portfolio Layer.
    """

    def __init__(self, config: AgentConfig) -> None:
        self.max_positions: int = getattr(config, "max_open_positions", 5)
        self.max_per_symbol: float = getattr(config, "max_exposure_per_symbol_pct", 0.20)
        self.max_total: float = getattr(config, "max_total_exposure_pct", 0.80)

    def validate(
        self,
        request: TradeRequest,
        portfolio_state: dict,
    ) -> tuple[bool, str]:
        """
        Validasi exposure sebelum membuka posisi baru.

        Args:
            request: TradeRequest
            portfolio_state: Dict berisi equity dan open_positions

        Returns:
            (True, '') jika aman
            (False, alasan) jika ditolak
        """
        equity = portfolio_state.get("equity", 1.0)
        positions: dict = portfolio_state.get("open_positions", {})

        # Harga terkini
        price = request.price or portfolio_state.get(f"last_price_{request.symbol}", 0.0)
        new_notional = request.quantity * price

        if equity <= 0:
            return False, "Equity tidak valid (<= 0)"

        # Cek 1: Jumlah max posisi terbuka
        if len(positions) >= self.max_positions:
            if request.symbol not in positions:
                return False, f"Max posisi ({self.max_positions}) sudah tercapai"

        # Cek 2: Max porsi per simbol
        existing = positions.get(request.symbol, {}).get("notional", 0.0)
        symbol_exposure_pct = (existing + new_notional) / equity
        if symbol_exposure_pct > self.max_per_symbol:
            return False, (
                f"{request.symbol} exposure {symbol_exposure_pct:.1%} "
                f"> max {self.max_per_symbol:.1%}"
            )

        # Cek 3: Max total keseluruhan
        total_current = sum(p.get("notional", 0.0) for p in positions.values())
        total_exposure_pct = (total_current + new_notional) / equity
        if total_exposure_pct > self.max_total:
            return False, (
                f"Total exposure {total_exposure_pct:.1%} " f"> max {self.max_total:.1%}"
            )

        return True, ""

    def get_exposure_snapshot(self, portfolio_state: dict) -> dict:
        """
        Snapshot exposure saat ini untuk monitoring.

        Returns:
            Dict berisi per-symbol exposure dan total.
        """
        equity = portfolio_state.get("equity", 1.0)
        positions: dict = portfolio_state.get("open_positions", {})

        if equity <= 0:
            return {"error": "zero_equity"}

        per_symbol: dict[str, float] = {}
        total_notional = 0.0

        for sym, pos in positions.items():
            notional = pos.get("notional", 0.0)
            per_symbol[sym] = notional / equity
            total_notional += notional

        return {
            "total_exposure_pct": total_notional / equity,
            "open_positions_count": len(positions),
            "max_positions": self.max_positions,
            "per_symbol": per_symbol,
            "total_notional": total_notional,
        }
