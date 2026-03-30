"""
Portfolio Layer: CapitalManager (Production-Grade)
=======================================================
Pusat pelacakan modal — Sumber Kebenaran Tunggal (Single Source of Truth)
untuk equity, PnL, drawdown, dan exposure seluruh portfolio.

Filosofi Desain (Market Analyst View, 20+ tahun):
-----------
Akuntansi yang buruk membunuh trader lebih cepat dari sinyal yang salah.
Banyak bot gagal bukan karena strategi salah, tapi karena mereka tidak
tahu persis berapa modal yang tersisa setelah serangkaian rugi kecil.

CapitalManager menyelesaikan ini:
- Update real-time dari balance_sync (setiap tick sync interval)
- Lacak daily PnL, drawdown dari peak, total exposure
- Set safe_to_trade=False jika kondisi critical terlampaui
- Paper mode: simulasi compounding internal (tanpa exchange)
- Live mode: gunakan actual balance dari exchange

Warning Thresholds (dari docs):
- Daily loss > -3%  → WARNING
- Daily loss > -5%  → CRITICAL (safe_to_trade = False)
- Drawdown > -7%    → WARNING
- Drawdown > -10%   → CRITICAL
- Exposure > 70%    → WARNING
- Exposure > 85%    → CRITICAL
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


# ── Data Classes ───────────────────────────────────────────────────────────


@dataclass
class CapitalStatus:
    """Snapshot lengkap kondisi modal — diproduksi setiap tick."""

    # ── Equity snapshot
    current_equity: float  # Modal aktual saat ini
    peak_equity: float  # Titik tertinggi modal sepanjang waktu
    initial_equity: float  # Modal awal (hari pertama)
    daily_start_equity: float  # Modal awal hari ini (reset 00:00 UTC)

    # ── PnL
    daily_pnl: float  # PnL hari ini (realized + unrealized)
    daily_pnl_pct: float  # daily_pnl / daily_start_equity × 100
    total_pnl: float  # PnL total dari awal
    total_pnl_pct: float  # total_pnl / initial_equity × 100

    # ── Drawdown
    current_drawdown: float  # Dari peak ke saat ini (%)
    max_drawdown_today: float  # Max drawdown hari ini (%)
    max_drawdown_ever: float  # Max drawdown sepanjang waktu (%)

    # ── Exposure
    open_notional: float  # Total nilai posisi terbuka
    exposure_pct: float  # open_notional / equity × 100
    unrealized_pnl: float  # PnL posisi yang masih terbuka

    # ── Status flags
    safe_to_trade: bool
    warnings: list[str] = field(default_factory=list)
    timestamp: datetime = field(default_factory=_utcnow)


@dataclass
class _DailyStats:
    """State harian yang di-reset setiap UTC 00:00."""

    start_equity: float
    realized_pnl: float = 0.0
    max_drawdown: float = 0.0
    trades_count: int = 0
    date: date = field(default_factory=_today_utc)


# ── Paper Equity Tracker ─────────────────────────────────────────────────


class PaperEquityTracker:
    """
    Simulasi compounding untuk Paper Trading.

    Di paper mode, balance exchange tidak berubah (tidak ada order nyata).
    Tracker ini mensimulasikan pertumbuhan/penurunan modal secara internal.
    """

    def __init__(self, initial: float) -> None:
        self._equity: float = initial
        self._peak: float = initial

    def apply_trade(self, pnl_usd: float, commission: float = 0.0) -> float:
        """Update equity setelah trade close. Return equity baru."""
        net_pnl = pnl_usd - abs(commission)
        self._equity += net_pnl
        self._peak = max(self._peak, self._equity)
        return self._equity

    @property
    def equity(self) -> float:
        return self._equity

    @property
    def peak(self) -> float:
        return self._peak

    @property
    def drawdown(self) -> float:
        """Drawdown saat ini dari peak (fraction, bukan persen)."""
        if self._peak <= 0:
            return 0.0
        return (self._peak - self._equity) / self._peak


# ── Capital Manager ────────────────────────────────────────────────────────


class CapitalManager:
    """
    Manajer modal sentral. Single source of truth untuk equity agent.

    Integrasi:
    - balance_sync.py → sync_from_exchange() setiap N detik
    - trade_layer → record_trade_open/close setiap order
    - SignalProcessor → get_status() sebelum proses signal
    - Scheduler → reset_daily() setiap UTC 00:00
    """

    # ── Threshold dari dokumentasi (overridable via config)
    WARN_DAILY_LOSS_PCT: float = -3.0
    CRIT_DAILY_LOSS_PCT: float = -5.0
    WARN_DRAWDOWN_PCT: float = -7.0
    CRIT_DRAWDOWN_PCT: float = -10.0
    WARN_EXPOSURE_PCT: float = 70.0
    CRIT_EXPOSURE_PCT: float = 85.0
    WARN_UNREALIZED_LOSS_PCT: float = -3.0
    CRIT_UNREALIZED_LOSS_PCT: float = -6.0

    def __init__(self, config: object) -> None:
        self.config = config

        # Override thresholds jika ada di config
        self.WARN_DAILY_LOSS_PCT = getattr(config, "warn_daily_loss_pct", self.WARN_DAILY_LOSS_PCT)
        self.CRIT_DAILY_LOSS_PCT = getattr(config, "max_daily_loss_pct", self.CRIT_DAILY_LOSS_PCT)
        self.WARN_DRAWDOWN_PCT = getattr(config, "warn_drawdown_pct", self.WARN_DRAWDOWN_PCT)
        self.CRIT_DRAWDOWN_PCT = getattr(config, "max_drawdown_pct", self.CRIT_DRAWDOWN_PCT)
        self.WARN_EXPOSURE_PCT = getattr(config, "warn_exposure_pct", self.WARN_EXPOSURE_PCT)
        self.CRIT_EXPOSURE_PCT = getattr(config, "max_exposure_pct", self.CRIT_EXPOSURE_PCT)

        self._paper_mode: bool = getattr(config, "paper_mode", True)

        # ── Core state
        self._initial_equity: float = 0.0
        self._current_equity: float = 0.0
        self._peak_equity: float = 0.0
        self._max_drawdown_ever: float = 0.0

        # ── Daily state
        self._daily: _DailyStats | None = None

        # ── Exposure tracking (updated by trade_layer)
        self._open_notional: float = 0.0
        self._unrealized_pnl: float = 0.0

        # ── Paper mode tracker
        self._paper_tracker: PaperEquityTracker | None = None

        # ── Cache status terakhir
        self._last_status: CapitalStatus | None = None

        logger.info(f"[CapitalManager] Initialized. paper_mode={self._paper_mode}")

    # ── Initialization ─────────────────────────────────────────────────────

    def initialize(self, starting_capital: float) -> None:
        """
        Set modal awal. Dipanggil saat agent pertama kali start.

        Harus dipanggil SEKALI sebelum get_status() dipakai.
        """
        if starting_capital <= 0:
            raise ValueError(f"starting_capital must be > 0, got {starting_capital}")

        self._initial_equity = starting_capital
        self._current_equity = starting_capital
        self._peak_equity = starting_capital
        self._daily = _DailyStats(start_equity=starting_capital)

        if self._paper_mode:
            self._paper_tracker = PaperEquityTracker(starting_capital)

        logger.info(
            f"[CapitalManager] Initialized with {starting_capital:.2f} USDT. "
            f"Mode={'PAPER' if self._paper_mode else 'LIVE'}"
        )

    # ── Sync & Update ──────────────────────────────────────────────────────

    def sync_from_exchange(
        self,
        realized_balance: float,  # Balance bersih dari exchange (sudah realized)
        unrealized_pnl: float = 0.0,
    ) -> None:
        """
        Update dari balance_sync.py (setiap N detik).

        Live mode: gunakan actual exchange balance.
        Paper mode: abaikan realized_balance, gunakan PaperEquityTracker.
        """
        if self._paper_mode:
            # Paper mode: equity dihitung dari tracker internal
            if self._paper_tracker:
                self._current_equity = self._paper_tracker.equity + unrealized_pnl
        else:
            # Live mode: percayai exchange
            if realized_balance <= 0:
                logger.warning(
                    f"[CapitalManager] Suspicious sync: realized_balance={realized_balance}"
                )
                return
            self._current_equity = realized_balance + unrealized_pnl

        self._unrealized_pnl = unrealized_pnl
        self._update_peak()

    def record_trade_open(self, notional: float, risk_usd: float = 0.0) -> None:
        """
        Update exposure saat posisi dibuka.

        Args:
            notional: Total nilai posisi (qty × price)
            risk_usd: USD yang di-risk (untuk tracking)
        """
        self._open_notional += max(0.0, notional)
        logger.debug(
            f"[CapitalManager] Trade opened — notional={notional:.2f}, "
            f"total_open={self._open_notional:.2f}"
        )

    def record_trade_close(
        self,
        notional: float,
        pnl_usd: float,
        commission: float = 0.0,
    ) -> None:
        """
        Update PnL dan equity saat posisi ditutup.

        Args:
            notional: Nilai posisi yang ditutup
            pnl_usd: PnL gross (positif = profit, negatif = loss)
            commission: Biaya trading
        """
        self._open_notional = max(0.0, self._open_notional - notional)
        net_pnl = pnl_usd - abs(commission)

        if self._paper_mode and self._paper_tracker:
            self._paper_tracker.apply_trade(pnl_usd, commission)
            self._current_equity = self._paper_tracker.equity

        if self._daily:
            self._daily.realized_pnl += net_pnl
            self._daily.trades_count += 1

        self._update_peak()

        logger.debug(
            f"[CapitalManager] Trade closed — net_pnl={net_pnl:+.2f}, "
            f"equity={self._current_equity:.2f}"
        )

    # ── Status & Reporting ─────────────────────────────────────────────────

    def update(
        self,
        realized_equity: float,
        open_positions: dict,
        current_prices: dict,
    ) -> CapitalStatus:
        """
        Full update setiap tick. Hitung unrealized dari posisi aktif.

        Args:
            realized_equity: Balance dari exchange
            open_positions: {symbol: {"qty": float, "entry_price": float, "side": str}}
            current_prices: {symbol: float}

        Returns:
            CapitalStatus terbaru
        """
        # Hitung unrealized dari posisi aktif
        unrealized = 0.0
        open_notional = 0.0

        for sym, pos in open_positions.items():
            qty = pos.get("qty", 0.0)
            entry = pos.get("entry_price", 0.0)
            side = pos.get("side", "BUY")
            curr = current_prices.get(sym, entry)

            if qty > 0 and entry > 0:
                notional = qty * curr
                open_notional += notional

                if side == "BUY":
                    unrealized += (curr - entry) * qty
                else:
                    unrealized += (entry - curr) * qty

        self._open_notional = open_notional
        self._unrealized_pnl = unrealized
        self.sync_from_exchange(realized_equity, unrealized)

        status = self._compute_status()
        self._last_status = status
        return status

    def get_status(self, equity: float | None = None) -> CapitalStatus:
        """
        Return status terbaru (cached, tidak recompute).
        Jika belum ada status, buat satu dari state saat ini.
        """
        if self._last_status is not None:
            return self._last_status

        return self._compute_status()

    def get_compound_equity(self) -> float:
        """
        Equity untuk position sizing.
        Paper mode: gunakan paper tracker.
        Live mode: gunakan current_equity.
        """
        if self._paper_mode and self._paper_tracker:
            return self._paper_tracker.equity
        return self._current_equity

    def reset_daily(self) -> None:
        """
        Reset statistik harian — dipanggil scheduler UTC 00:00.
        """
        today = _today_utc()

        # Simpan max drawdown kemarin ke max_drawdown_ever
        if self._daily:
            self._max_drawdown_ever = max(self._max_drawdown_ever, self._daily.max_drawdown)

        self._daily = _DailyStats(
            start_equity=self._current_equity,
            date=today,
        )

        # Cache status harus di-reset
        self._last_status = None

        logger.info(f"[CapitalManager] Daily reset. New start equity: {self._current_equity:.2f}")

    # ── Private ────────────────────────────────────────────────────────────

    def _update_peak(self) -> None:
        """Update peak equity dan max drawdown."""
        if self._current_equity > self._peak_equity:
            self._peak_equity = self._current_equity

        if self._peak_equity > 0:
            dd = (self._peak_equity - self._current_equity) / self._peak_equity * 100
            if self._daily:
                self._daily.max_drawdown = max(self._daily.max_drawdown, dd)

    def _compute_status(self) -> CapitalStatus:
        """Kalkulasi CapitalStatus lengkap dari state internal."""
        if self._initial_equity <= 0:
            logger.warning("[CapitalManager] Not initialized! Call initialize() first.")
            return self._empty_status()

        equity = self._current_equity
        daily = self._daily

        # ── PnL calculations
        daily_start = daily.start_equity if daily else equity
        daily_pnl = (equity - daily_start) + self._unrealized_pnl
        daily_pnl_pct = (daily_pnl / daily_start * 100) if daily_start > 0 else 0.0

        total_pnl = equity - self._initial_equity
        total_pnl_pct = (
            (total_pnl / self._initial_equity * 100) if self._initial_equity > 0 else 0.0
        )

        # ── Drawdown
        current_dd = 0.0
        if self._peak_equity > 0:
            current_dd = (self._peak_equity - equity) / self._peak_equity * 100

        max_dd_today = daily.max_drawdown if daily else 0.0
        max_dd_ever = max(self._max_drawdown_ever, current_dd)

        # ── Exposure
        exposure_pct = (self._open_notional / equity * 100) if equity > 0 else 0.0
        unrealized_pnl_pct = (self._unrealized_pnl / equity * 100) if equity > 0 else 0.0

        # ── Warning & Critical evaluation
        warnings: list[str] = []
        safe_to_trade = True

        # Daily loss check
        if daily_pnl_pct <= self.CRIT_DAILY_LOSS_PCT:
            warnings.append(
                f"CRITICAL: Daily loss {daily_pnl_pct:.2f}% exceeds limit {self.CRIT_DAILY_LOSS_PCT:.1f}%"
            )
            safe_to_trade = False
        elif daily_pnl_pct <= self.WARN_DAILY_LOSS_PCT:
            warnings.append(f"WARNING: Daily loss {daily_pnl_pct:.2f}%")

        # Drawdown check
        if current_dd >= abs(self.CRIT_DRAWDOWN_PCT):
            warnings.append(
                f"CRITICAL: Drawdown {current_dd:.2f}% exceeds limit {abs(self.CRIT_DRAWDOWN_PCT):.1f}%"
            )
            safe_to_trade = False
        elif current_dd >= abs(self.WARN_DRAWDOWN_PCT):
            warnings.append(f"WARNING: Drawdown {current_dd:.2f}%")

        # Exposure check
        if exposure_pct >= self.CRIT_EXPOSURE_PCT:
            warnings.append(
                f"CRITICAL: Exposure {exposure_pct:.1f}% exceeds limit {self.CRIT_EXPOSURE_PCT:.0f}%"
            )
            safe_to_trade = False
        elif exposure_pct >= self.WARN_EXPOSURE_PCT:
            warnings.append(f"WARNING: High exposure {exposure_pct:.1f}%")

        # Unrealized loss check
        if unrealized_pnl_pct <= self.CRIT_UNREALIZED_LOSS_PCT:
            warnings.append(f"CRITICAL: Unrealized loss {unrealized_pnl_pct:.2f}%")
            safe_to_trade = False
        elif unrealized_pnl_pct <= self.WARN_UNREALIZED_LOSS_PCT:
            warnings.append(f"WARNING: Unrealized loss {unrealized_pnl_pct:.2f}%")

        if warnings:
            if safe_to_trade:
                logger.warning(f"[CapitalManager] {' | '.join(warnings)}")
            else:
                logger.critical(f"[CapitalManager] TRADING HALTED: {' | '.join(warnings)}")

        return CapitalStatus(
            current_equity=equity,
            peak_equity=self._peak_equity,
            initial_equity=self._initial_equity,
            daily_start_equity=daily_start,
            daily_pnl=daily_pnl,
            daily_pnl_pct=daily_pnl_pct,
            total_pnl=total_pnl,
            total_pnl_pct=total_pnl_pct,
            current_drawdown=current_dd,
            max_drawdown_today=max_dd_today,
            max_drawdown_ever=max_dd_ever,
            open_notional=self._open_notional,
            exposure_pct=exposure_pct,
            unrealized_pnl=self._unrealized_pnl,
            safe_to_trade=safe_to_trade,
            warnings=warnings,
            timestamp=_utcnow(),
        )

    def _empty_status(self) -> CapitalStatus:
        """Return status kosong saat belum diinisialisasi."""
        return CapitalStatus(
            current_equity=0.0,
            peak_equity=0.0,
            initial_equity=0.0,
            daily_start_equity=0.0,
            daily_pnl=0.0,
            daily_pnl_pct=0.0,
            total_pnl=0.0,
            total_pnl_pct=0.0,
            current_drawdown=0.0,
            max_drawdown_today=0.0,
            max_drawdown_ever=0.0,
            open_notional=0.0,
            exposure_pct=0.0,
            unrealized_pnl=0.0,
            safe_to_trade=False,
            warnings=["CapitalManager not initialized"],
        )
