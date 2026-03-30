"""
Portfolio Layer: CorrelationController
=======================================================
Barikade Risiko Makro Institusional — Lapisan Penjaga Korelasi.

Filosofi Desain (Market Analyst View, 20+ tahun):
-----------
Mayoritas Flash Crash menghancurkan portfolio bukan karena satu posisi yang salah,
melainkan karena beberapa posisi di aset yang 'terlihat berbeda' (BTC, ETH, SOL)
ternyata semua bergerak ke arah yang sama pada saat krisis.

Saat panik sistemik terjadi, semua korelasi koin mendekati 1.0.
Solusi: Kelompokkan aset ke dalam 'Correlation Group' dan batasi
total exposure pada setiap grup tersebut.

Implementasi:
- Static Groups: Default berdasarkan observasi empiris 90 hari
- Dynamic Correlation: Hitung rolling correlation dari candlestick
  (aktifkan dengan dynamic_correlation=True di config)
- Cache 24 jam: Tidak recompute di setiap tick (CPU efficient)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone

import numpy as np

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ── Default Correlation Groups (Empiris Rolling-90-Day Crypto) ──────────────
# Didesain konservatif: lebih baik terlalu ketat daripada terlalu longgar.
# Bisa di-override via config YAML tanpa perlu restart agent.

DEFAULT_CORRELATION_GROUPS: list[dict] = [
    {
        "id": "btc_eth",
        "symbols": ["BTCUSDT", "ETHUSDT"],
        "corr": 0.85,
        "max_combined_pct": 0.35,  # Max 35% equity gabungan BTC+ETH
    },
    {
        "id": "eth_alts",
        "symbols": ["ETHUSDT", "BNBUSDT", "SOLUSDT", "LDOUSDT", "MATICUSDT"],
        "corr": 0.75,
        "max_combined_pct": 0.40,  # Max 40% equity gabungan ETH-family
    },
    {
        "id": "large_caps",
        "symbols": ["BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT"],
        "corr": 0.65,
        "max_combined_pct": 0.60,  # Max 60% gabungan semua Large Cap
    },
    {
        "id": "defi_tokens",
        "symbols": ["LDOUSDT", "AAVEUSDT", "UNIUSDT", "MKRUSDT"],
        "corr": 0.70,
        "max_combined_pct": 0.20,  # DeFi tokens berkorelasi kuat → limit ketat
    },
]


@dataclass
class CorrelationBreach:
    """Hasil pelanggaran batas korelasi yang terdeteksi."""

    group_id: str
    symbols_involved: list[str]
    current_combined_pct: float  # Sebelum posisi baru
    projected_combined_pct: float  # Setelah posisi baru
    limit_pct: float
    new_symbol: str
    new_notional: float


@dataclass
class CorrelationSnapshot:
    """Snapshot exposure per grup untuk monitoring & observability."""

    timestamp: datetime
    group_exposures: dict[str, float]  # group_id → pct_of_equity
    total_unhedged_exposure: float  # Total correlated exposure
    at_risk_groups: list[str]  # Groups yang mendekati limit (>80%)


class CorrelationController:
    """
    Gate korelasi institusional.

    Setiap signal BUY/SELL WAJIB melewati check() sebelum dikirim ke Risk Layer.
    Jika menambahkan posisi baru akan melanggar batas grup korelasi → BLOCK.
    """

    def __init__(self, config: object) -> None:
        self.config = config

        # Load correlation groups dari config atau gunakan default
        custom_groups = getattr(config, "correlation_groups", None)
        self._groups: list[dict] = (
            custom_groups if custom_groups else list(DEFAULT_CORRELATION_GROUPS)
        )

        # Dynamic correlation feature flag
        self._dynamic_enabled: bool = getattr(config, "dynamic_correlation", False)
        self._dynamic_window: int = getattr(config, "dynamic_corr_window", 90)

        # Cache dynamic matrix (computed daily by scheduler)
        # key = "SYM_A:SYM_B", value = correlation float
        self._dynamic_matrix: dict[str, float] = {}
        self._dynamic_matrix_ts: datetime | None = None

        logger.info(
            f"[CorrelationController] Initialized with {len(self._groups)} groups. "
            f"Dynamic={self._dynamic_enabled}"
        )

    # ── Public Interface ──────────────────────────────────────────────────────

    def check(
        self,
        new_symbol: str,
        new_side: str,  # 'BUY' | 'SELL'
        new_notional: float,
        positions: dict,  # {symbol: {notional: float, side: str, ...}}
        equity: float,
    ) -> tuple[bool, str]:
        """
        Validasi korelasi sebelum membuka posisi baru.

        Logic Penting:
        - Hanya long positions yang dihitung (short = hedge, tidak menambah risiko arah)
        - Cek SEMUA grup yang mengandung new_symbol
        - Jika SATU grup saja dilanggar → BLOCK

        Returns:
            (True, '') jika aman
            (False, alasan) jika ditolak
        """
        if equity <= 0:
            return False, "Equity zero — cannot validate correlation."

        if new_notional <= 0:
            return True, ""  # Tidak ada posisi yang dibuka

        # Short position / hedge tidak dicount sebagai directional risk
        # (Short justru mengurangi net exposure)
        if new_side == "SELL":
            return True, ""

        breaches = self._find_violations(new_symbol, new_notional, positions, equity)

        if breaches:
            breach = breaches[0]  # Laporkan pelanggaran pertama
            msg = (
                f"Correlation limit breach on group '{breach.group_id}'. "
                f"Adding {new_symbol} ({new_notional:.0f} USDT) would bring "
                f"combined exposure from {breach.current_combined_pct:.1%} "
                f"to {breach.projected_combined_pct:.1%}, "
                f"exceeding limit {breach.limit_pct:.1%}."
            )
            logger.warning(f"[CorrelationController] BLOCKED: {msg}")
            return False, msg

        return True, ""

    def get_group_exposure(
        self,
        group_id: str,
        positions: dict,
        equity: float,
    ) -> float:
        """Return total exposure % equity untuk satu grup korelasi."""
        for grp in self._groups:
            if grp["id"] == group_id:
                combined = self._calc_combined_notional(grp["symbols"], positions)
                return combined / equity if equity > 0 else 0.0
        return 0.0

    def get_snapshot(self, positions: dict, equity: float) -> CorrelationSnapshot:
        """
        Snapshot semua grup untuk monitoring dashboard.
        Dipanggil oleh health_check / observability layer.
        """
        group_exposures: dict[str, float] = {}
        at_risk: list[str] = []

        total_long_notional = sum(
            p.get("notional", 0.0) for p in positions.values() if p.get("side", "BUY") == "BUY"
        )

        for grp in self._groups:
            combined = self._calc_combined_notional(grp["symbols"], positions)
            pct = combined / equity if equity > 0 else 0.0
            group_exposures[grp["id"]] = pct

            # Mark "at risk" jika sudah > 80% dari limit
            if pct > grp["max_combined_pct"] * 0.80:
                at_risk.append(grp["id"])

        return CorrelationSnapshot(
            timestamp=_utcnow(),
            group_exposures=group_exposures,
            total_unhedged_exposure=total_long_notional / equity if equity > 0 else 0.0,
            at_risk_groups=at_risk,
        )

    def update_groups(self, new_groups: list[dict]) -> None:
        """
        Hot-reload correlation groups dari config YAML.
        Tidak perlu restart agent — dipanggil oleh scheduler harian.
        """
        self._groups = new_groups
        logger.info(f"[CorrelationController] Updated to {len(new_groups)} groups.")

    def update_dynamic_matrix(self, returns_df: object) -> None:
        """
        Hitung dan cache dynamic correlation matrix dari historical returns.
        Dipanggil scheduler harian (UTC 00:05) — bukan setiap tick!

        Args:
            returns_df: pd.DataFrame dengan kolom = simbol, baris = candle returns
        """
        if not self._dynamic_enabled:
            return

        try:
            # Lazy import pandas (agar tidak crash jika tidak tersedia)
            import pandas as pd  # noqa: PLC0415

            if not isinstance(returns_df, pd.DataFrame) or returns_df.empty:
                return

            window = min(self._dynamic_window, len(returns_df))
            corr_matrix = returns_df.tail(window).corr()

            # Flatten ke dict "SYM_A:SYM_B" → float
            new_matrix: dict[str, float] = {}
            for sym_a in corr_matrix.columns:
                for sym_b in corr_matrix.columns:
                    if sym_a != sym_b:
                        val = corr_matrix.loc[sym_a, sym_b]
                        if not np.isnan(val):
                            key = f"{sym_a}:{sym_b}"
                            new_matrix[key] = float(val)

            self._dynamic_matrix = new_matrix
            self._dynamic_matrix_ts = _utcnow()

            # Sesuaikan batas grup berdasarkan korelasi aktual
            self._adapt_group_limits()

            logger.info(
                f"[CorrelationController] Dynamic matrix updated: "
                f"{len(new_matrix)} pairs computed. Window={window} candles."
            )

        except Exception as exc:
            logger.error(
                f"[CorrelationController] Dynamic matrix update failed: {exc}. "
                "Falling back to static groups."
            )

    def get_pairwise_correlation(self, sym_a: str, sym_b: str) -> float:
        """Return korelasi antara dua simbol (dynamic jika tersedia, static fallback)."""
        if self._dynamic_enabled and self._dynamic_matrix:
            key = f"{sym_a}:{sym_b}"
            rev_key = f"{sym_b}:{sym_a}"
            val = self._dynamic_matrix.get(key) or self._dynamic_matrix.get(rev_key)
            if val is not None:
                return val

        # Static fallback: cari grup yang sama antara keduanya
        for grp in self._groups:
            if sym_a in grp["symbols"] and sym_b in grp["symbols"]:
                return grp["corr"]

        return 0.0  # Tidak dikenal → anggap tidak berkorelasi (aman)

    # ── Private Methods ───────────────────────────────────────────────────────

    def _find_violations(
        self,
        new_symbol: str,
        new_notional: float,
        positions: dict,
        equity: float,
    ) -> list[CorrelationBreach]:
        """Cek semua grup yang mengandung new_symbol dan temukan pelanggaran."""
        violations: list[CorrelationBreach] = []

        for grp in self._groups:
            if new_symbol not in grp["symbols"]:
                continue

            current_combined = self._calc_combined_notional(grp["symbols"], positions)
            projected_combined = current_combined + new_notional

            limit_usdt = equity * grp["max_combined_pct"]

            if projected_combined > limit_usdt:
                violations.append(
                    CorrelationBreach(
                        group_id=grp["id"],
                        symbols_involved=grp["symbols"],
                        current_combined_pct=current_combined / equity,
                        projected_combined_pct=projected_combined / equity,
                        limit_pct=grp["max_combined_pct"],
                        new_symbol=new_symbol,
                        new_notional=new_notional,
                    )
                )

        return violations

    def _calc_combined_notional(self, symbols: list[str], positions: dict) -> float:
        """
        Hitung total notional LONG yang terbuka di kumpulan simbol tertentu.
        Short positions tidak dihitung (mereka mengurangi net exposure).
        """
        total = 0.0
        for sym in symbols:
            pos = positions.get(sym)
            if pos is None:
                continue
            # Hanya long yang menambah directional risk
            if pos.get("side", "BUY") == "BUY":
                total += pos.get("notional", 0.0)
        return total

    def _adapt_group_limits(self) -> None:
        """
        Sesuaikan batas exposure grup secara dinamis berdasarkan korelasi aktual.
        - Korelasi aktual LEBIH TINGGI dari default → perkecil limit
        - Korelasi aktual LEBIH RENDAH dari default → perlonggar sedikit
        """
        if not self._dynamic_matrix:
            return

        for grp in self._groups:
            syms = grp["symbols"]
            # Hitung rata-rata pairwise correlation di grup
            pairs_corr: list[float] = []
            for i, s_a in enumerate(syms):
                for s_b in syms[i + 1 :]:
                    key = f"{s_a}:{s_b}"
                    rev = f"{s_b}:{s_a}"
                    val = self._dynamic_matrix.get(key) or self._dynamic_matrix.get(rev)
                    if val is not None:
                        pairs_corr.append(val)

            if not pairs_corr:
                continue

            avg_corr = float(np.mean(pairs_corr))
            static_corr = grp["corr"]
            base_limit = grp.get("_base_limit", grp["max_combined_pct"])
            grp["_base_limit"] = base_limit  # Simpan limit original

            if avg_corr > static_corr + 0.10:
                # Korelasi makin tinggi → ketatkan limit (-5%)
                grp["max_combined_pct"] = max(0.15, base_limit - 0.05)
                logger.warning(
                    f"[CorrelationController] Group '{grp['id']}' "
                    f"correlation elevated ({avg_corr:.2f} vs static {static_corr:.2f}). "
                    f"Limit tightened to {grp['max_combined_pct']:.0%}."
                )
            elif avg_corr < static_corr - 0.15:
                # Korelasi turun → longgarkan sedikit (+5%, capped at base)
                grp["max_combined_pct"] = min(base_limit, grp["max_combined_pct"] + 0.05)
