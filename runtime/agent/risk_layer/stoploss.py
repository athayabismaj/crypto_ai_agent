"""
Lapis 6: StopLossManager — Validator & Kalkulator StopLoss
============================================================
Validasi keberadaan SL dan perhitungannya. Jika signal tidak
memberikan SL, kalkulasi dari ATR atau default persen.

Keputusan Arsitektur (CONTEXT.md §4.1):
    SL berbasis structure — align_sl_to_structure() dari strategy_utils
    menggeser SL ke posisi swing low/high terdekat (bukan flat ATR).
    Ini menghasilkan SL yang lebih masuk akal secara teknikal.

Filosofi (Market Analyst View, 20+ tahun):
    SL flat berdasarkan persentase/ATR sering kena noise.
    SL di belakang structure (support/resistance) bertahan lebih baik
    karena harga cenderung respect level-level tersebut.
"""

from __future__ import annotations

import logging

from runtime.agent.core.config_schema import AgentConfig  # type: ignore
from runtime.agent.models.risk import TradeRequest  # type: ignore
from runtime.agent.strategy_layer.strategy_utils import align_sl_to_structure  # type: ignore

logger = logging.getLogger(__name__)


class StopLossManager:
    """
    Validator dan kalkulator StopLoss.

    Alur:
    1. Apakah signal memberikan SL? Jika tidak → hitung default
    2. Apakah SL di sisi yang benar? (BUY: SL < price, SELL: SL > price)
    3. Apakah SL dalam jarak wajar? (min/max distance)
    4. Jika ada data structure → align ke swing low/high via strategy_utils
    5. Return (ok, message, final_sl_price)
    """

    def __init__(self, config: AgentConfig) -> None:
        self.require_sl: bool = getattr(config, "require_sl", True)
        self.default_sl_pct: float = getattr(config, "default_sl_pct", 0.02)
        self.min_sl_distance_pct: float = getattr(config, "min_sl_distance_pct", 0.003)
        self.max_sl_distance_pct: float = getattr(config, "max_sl_distance_pct", 0.15)
        self.sl_atr_mult: float = getattr(config, "sl_atr_mult", 1.5)
        self.max_sl_atr_mult: float = getattr(config, "max_sl_atr_mult", 4.0)

    def validate_and_compute(
        self, request: TradeRequest, portfolio_state: dict
    ) -> tuple[bool, str, float]:
        """
        Validasi dan komputasi SL final.

        Args:
            request: TradeRequest dengan suggested_sl dari signal
            portfolio_state: Dict berisi harga, ATR, dan data historis

        Returns:
            (ok, message, final_sl_price)
            - ok=True, msg='' → SL valid
            - ok=True, msg=<warning> → SL oke tapi ada catatan
            - ok=False, msg=<error> → SL tidak valid, block trade
        """
        price = request.price or portfolio_state.get(f"last_price_{request.symbol}", 0.0)

        if price <= 0:
            return False, "Harga tidak valid (<= 0)", 0.0

        sl = request.suggested_sl
        is_paper = getattr(portfolio_state.get("config", {}), "paper_mode", False)

        atr_key = f"atr_{request.symbol}"
        atr: float = portfolio_state.get(atr_key, 0.0)

        if sl <= 0:
            if self.require_sl:
                sl = self._compute_sl(price, request.side, atr, portfolio_state)
                if not is_paper:
                    return False, "REQUIRE_SL: Sinyal tidak memberikan StopLoss", sl
                else:
                    return True, "WARNED_SL: Menggunakan kalkulasi default di mode simulasi", sl
            else:
                sl = self._compute_sl(price, request.side, atr, portfolio_state)

        # Validasi Arah (Side)
        if request.side == "BUY" and sl >= price:
            return False, "SL di sisi yang salah untuk BUY (SL >= Entry)", sl
        if request.side == "SELL" and sl <= price:
            return False, "SL di sisi yang salah untuk SELL (SL <= Entry)", sl

        # Validasi Jarak Minimum/Maksimum
        distance = abs(price - sl) / price
        if distance < self.min_sl_distance_pct:
            return (
                False,
                f"SL terlalu dekat ({distance:.2%} < min {self.min_sl_distance_pct:.2%})",
                sl,
            )

        if distance > self.max_sl_distance_pct:
            # Warning saja — SL masih boleh dipakai tapi dicatat risikonya
            return (
                True,
                f"SL terlalu jauh dari price (Risk amat besar = {distance:.2%})",
                sl,
            )

        tight_ok, tight_msg = self.is_sl_tight_enough(price, sl, atr)
        if not tight_ok:
            return True, tight_msg, sl  # Warning juga

        return True, "", sl

    def _compute_sl(
        self,
        price: float,
        side: str,
        atr: float,
        portfolio_state: dict,
    ) -> float:
        """
        Hitung SL yang sesuai, preferensi structure-based, fallback ke ATR/persen.

        Urutan prioritas:
        1. align_sl_to_structure() jika ada data low/high historis
        2. ATR-based jika ATR tersedia
        3. Default persentase sebagai ultimate fallback
        """
        # Coba structure-based SL terlebih dahulu
        low_key = f"low_prices_{side}"
        high_key = f"high_prices_{side}"
        low_prices: list[float] = portfolio_state.get(low_key, [])
        high_prices: list[float] = portfolio_state.get(high_key, [])

        if (low_prices or high_prices) and atr > 0:
            try:
                sl = align_sl_to_structure(
                    price=price,
                    side=side,
                    low_prices=low_prices,
                    high_prices=high_prices,
                    atr=atr,
                    lookback=20,
                )
                logger.debug(
                    f"[StopLoss] Structure-based SL for {side}: {sl:.8f} "
                    f"(price={price:.2f}, atr={atr:.2f})"
                )
                return sl
            except Exception as exc:
                logger.warning(
                    f"[StopLoss] align_sl_to_structure gagal: {exc}. " "Fallback ke ATR/default."
                )

        # Fallback ke ATR atau default persen
        return self.compute_default_sl(price, side, atr)

    def compute_default_sl(self, price: float, side: str, atr: float = 0.0) -> float:
        """Hitung SL default berdasarkan ATR atau persentase flat."""
        if atr > 0:
            distance = atr * self.sl_atr_mult
        else:
            distance = price * self.default_sl_pct

        return price - distance if side == "BUY" else price + distance

    def is_sl_tight_enough(self, price: float, sl: float, atr: float) -> tuple[bool, str]:
        """Cek apakah SL distance masih dalam batas wajar terhadap ATR."""
        if atr <= 0:
            return True, ""

        distance = abs(price - sl)
        if distance > atr * self.max_sl_atr_mult:
            return (
                False,
                f"SL distance melebihi ATR maks ({distance:.2f} > {atr * self.max_sl_atr_mult:.2f})",
            )

        return True, ""
