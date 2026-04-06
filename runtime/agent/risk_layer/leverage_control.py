"""
Lapis 3: LeverageControl — Penjaga Leverage Futures
=====================================================
Mencegah penggunaan leverage berlebihan pada futures trade request.

Keputusan Arsitektur Final (CONTEXT.md §4.2):
    Leverage HARD-LOCKED ke maksimum **3x** untuk fase awal.
    Alasan: keamanan modal selama uji coba.
    Tidak ada simbol yang boleh melampaui batas ini.

Filosofi (Market Analyst View, 20+ tahun):
    Leverage tinggi membunuh akun lebih cepat dari sinyal buruk.
    Pada crypto, 10x leverage berarti likuidasi dalam fluktuasi 10%.
    Dengan 3x, kita punya ruang napas ~33% — masih agresif untuk crypto.
"""

from __future__ import annotations

import logging

from runtime.agent.core.config_schema import AgentConfig  # type: ignore

logger = logging.getLogger(__name__)

# ── Leverage Limit ──────────────────────────────────────────────────────────
# Keputusan FINAL: Semua simbol di-lock ke 3x maximum.
# Override per-simbol tetap tersedia tapi TIDAK BOLEH melebihi 3.
GLOBAL_MAX_LEVERAGE: int = 3

SYMBOL_MAX_LEVERAGE: dict[str, int] = {
    "BTCUSDT": 3,
    "ETHUSDT": 3,
    "BNBUSDT": 3,
    "SOLUSDT": 3,
    "DEFAULT": 3,
}


class LeverageControl:
    """
    Validator leverage untuk futures orders.

    Setiap trade request futures WAJIB melewati validate() sebelum
    dikirim ke exchange. Jika leverage melebihi batas → BLOCKED.
    """

    def __init__(self, config: AgentConfig) -> None:
        # Hard-lock ke 3x — config TIDAK bisa override lebih tinggi
        config_max = getattr(config, "global_max_leverage", GLOBAL_MAX_LEVERAGE)
        self.global_max: int = min(config_max, GLOBAL_MAX_LEVERAGE)

        # Jika ada override symbol leverage dari config dict
        cf_symbol_lev: dict[str, int] = getattr(config, "symbol_max_leverage", {})
        self.symbol_max_leverage: dict[str, int] = {**SYMBOL_MAX_LEVERAGE}

        # Merge config overrides tapi CLAMP ke global_max
        for sym, lev in cf_symbol_lev.items():
            self.symbol_max_leverage[sym] = min(lev, self.global_max)

        logger.info(
            f"[LeverageControl] Initialized. global_max={self.global_max}x "
            f"(HARD-LOCKED, tidak bisa di-override lebih tinggi)."
        )

    def validate(self, leverage: int, symbol: str) -> tuple[bool, str]:
        """
        Validasi leverage sebuah trade request.

        Returns:
            (True, '') jika aman.
            (False, alasan) jika leverage melebihi batas.
        """
        if leverage <= 0:
            return False, f"Leverage tidak valid: {leverage}"

        effective_max = self.get_max_leverage(symbol)

        if leverage > effective_max:
            return False, (
                f"Leverage {leverage}x melebihi batas {effective_max}x "
                f"untuk {symbol} (global_max={self.global_max}x, "
                f"HARD-LOCKED untuk keamanan fase awal)"
            )

        return True, ""

    def get_max_leverage(self, symbol: str) -> int:
        """Return leverage maksimum efektif untuk simbol tertentu."""
        symbol_max = self.symbol_max_leverage.get(
            symbol, self.symbol_max_leverage.get("DEFAULT", GLOBAL_MAX_LEVERAGE)
        )
        return min(self.global_max, symbol_max)

    def get_effective_leverage(self, notional: float, margin: float) -> float:
        """
        Hitung leverage efektif dari notional dan margin yang digunakan.

        Berguna untuk monitoring posisi yang sudah berjalan.
        """
        if margin <= 0:
            return 0.0
        return notional / margin
