"""
capital_manager.py — Manajemen Modal Global.

Melacak equity secara real-time dan mendeteksi kondisi berbahaya
sebelum Risk Layer di-trigger. Capital Manager adalah 'akuntansi' dari sistem.
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime


@dataclass
class CapitalStatus:
    # ── Equity snapshot ─────────────────────────────────────
    current_equity: float
    peak_equity: float
    initial_equity: float

    # ── PnL ─────────────────────────────────────────────────
    daily_pnl: float
    daily_pnl_pct: float
    total_pnl: float
    total_pnl_pct: float

    # ── Drawdown ────────────────────────────────────────────
    current_drawdown: float  # dari peak (pct)
    max_drawdown_today: float
    max_drawdown_ever: float

    # ── Exposure ────────────────────────────────────────────
    open_notional: float  # total nilai posisi terbuka
    exposure_pct: float  # open_notional / equity × 100
    unrealized_pnl: float  # PnL posisi yang masih terbuka

    # ── Status flags ────────────────────────────────────────
    safe_to_trade: bool
    warnings: list[str] = field(default_factory=list)
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))


class PaperEquityTracker:
    # Paper mode: equity tidak pernah berubah dari exchange balance.
    # Capital manager mensimulasikan compounding secara internal.

    def __init__(self, initial: float):
        self._equity = initial
        self._peak = initial

    def apply_trade(self, pnl_usd: float, commission: float) -> float:
        net_pnl = pnl_usd - commission
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
        if self._peak <= 0:
            return 0.0
        return (self._peak - self._equity) / self._peak


class CapitalManager:
    """Melacak equity, exposure risk, drawdowns dan PnL."""

    def __init__(self, initial_equity: float = 0.0) -> None:
        self.initial_equity = initial_equity
        self.current_equity = initial_equity
        self.peak_equity = initial_equity

        # Thresholds Configuration (Defaults)
        self.WARN_DAILY_LOSS_PCT = -0.03
        self.MAX_DAILY_LOSS_PCT = -0.05
        self.WARN_DRAWDOWN_PCT = -0.07
        self.MAX_DRAWDOWN_PCT = -0.10
        self.WARN_EXPOSURE_PCT = 0.70
        self.MAX_EXPOSURE_PCT = 0.85
        self.WARN_UNREALIZED_LOSS = -0.03
        self.MAX_UNREALIZED_LOSS = -0.06

        self._status_cache: CapitalStatus | None = None

        # Tracking state
        self.daily_start_equity = initial_equity
        self.max_drawdown_today = 0.0
        self.max_drawdown_ever = 0.0

    def update(self, realized_equity: float, open_positions: dict, current_prices: dict) -> CapitalStatus:  # type: ignore
        """
        Dipanggil periodik (scheduler / Sync Layer target) untuk refresh data
        kesehatan modal akun trading.
        """
        # Update Peak Realized
        self.current_equity = realized_equity
        if self.current_equity > self.peak_equity:
            self.peak_equity = self.current_equity

        # Hitung PnL
        total_pnl = self.current_equity - self.initial_equity
        total_pnl_pct = total_pnl / self.initial_equity if self.initial_equity > 0 else 0.0

        daily_pnl = self.current_equity - self.daily_start_equity
        daily_pnl_pct = daily_pnl / self.daily_start_equity if self.daily_start_equity > 0 else 0.0

        # Hitung Eksekusi / Unrealized PnL
        open_notional = 0.0
        unrealized_pnl = 0.0

        for p_id, pos in open_positions.items():
            sym = getattr(pos, "symbol", "")
            qty = getattr(pos, "filled_qty", 0.0)
            entry = getattr(pos, "avg_fill_price", 0.0)

            # Exposure Notional Total
            notional = qty * entry
            open_notional += notional

            # Unrealized (Mark to market dengan current_prices dict)
            mark_price = current_prices.get(sym, entry)
            side = getattr(pos, "side", "BUY")
            if side == "BUY":
                u_pnl = (mark_price - entry) * qty
            else:
                u_pnl = (entry - mark_price) * qty

            unrealized_pnl += u_pnl

        exposure_pct = open_notional / self.current_equity if self.current_equity > 0 else 0.0
        unrealized_pct = unrealized_pnl / self.current_equity if self.current_equity > 0 else 0.0

        # Drawdowns
        current_drawdown = (
            (self.current_equity - self.peak_equity) / self.peak_equity
            if self.peak_equity > 0
            else 0.0
        )

        # Track record max drawdown
        if current_drawdown < self.max_drawdown_ever:
            self.max_drawdown_ever = current_drawdown

        daily_drawdown = (
            (self.current_equity - self.daily_start_equity) / self.daily_start_equity
            if self.daily_start_equity > 0
            else 0.0
        )
        if daily_drawdown < self.max_drawdown_today:
            self.max_drawdown_today = daily_drawdown

        # Evaluation (Warnings / Blocked status)
        warnings = []
        safe_to_trade = True

        if daily_pnl_pct <= self.MAX_DAILY_LOSS_PCT:
            safe_to_trade = False
            warnings.append(
                f"CRITICAL: Daily PnL ({daily_pnl_pct:.2%}) melebihi max_daily_loss_pct ({self.MAX_DAILY_LOSS_PCT:.2%})"
            )
        elif daily_pnl_pct <= self.WARN_DAILY_LOSS_PCT:
            warnings.append(f"WARNING: Daily loss mendekati limit ({daily_pnl_pct:.2%})")

        if current_drawdown <= self.MAX_DRAWDOWN_PCT:
            safe_to_trade = False
            warnings.append(
                f"CRITICAL: Drop/Drawdown ({current_drawdown:.2%}) memicu batas maximum drawdown ({self.MAX_DRAWDOWN_PCT:.2%})"
            )
        elif current_drawdown <= self.WARN_DRAWDOWN_PCT:
            warnings.append(f"WARNING: Drawdown mendekati limit limit ({current_drawdown:.2%})")

        if exposure_pct >= self.MAX_EXPOSURE_PCT:
            safe_to_trade = False
            warnings.append(
                f"CRITICAL: Exposure modal terlalu padat ({exposure_pct:.2%}) > {self.MAX_EXPOSURE_PCT:.2%}"
            )
        elif exposure_pct >= self.WARN_EXPOSURE_PCT:
            warnings.append(f"WARNING: Exposure modal padat ({exposure_pct:.2%})")

        if unrealized_pct <= self.MAX_UNREALIZED_LOSS:
            safe_to_trade = False
            warnings.append(
                f"CRITICAL: Kumpulan Floating Loss menumpuk fatal ({unrealized_pct:.2%})"
            )

        self._status_cache = CapitalStatus(
            current_equity=self.current_equity,
            peak_equity=self.peak_equity,
            initial_equity=self.initial_equity,
            daily_pnl=daily_pnl,
            daily_pnl_pct=daily_pnl_pct,
            total_pnl=total_pnl,
            total_pnl_pct=total_pnl_pct,
            current_drawdown=current_drawdown,
            max_drawdown_today=self.max_drawdown_today,
            max_drawdown_ever=self.max_drawdown_ever,
            open_notional=open_notional,
            exposure_pct=exposure_pct,
            unrealized_pnl=unrealized_pnl,
            safe_to_trade=safe_to_trade,
            warnings=warnings,
            timestamp=datetime.now(UTC),
        )
        return self._status_cache

    def get_status(self) -> CapitalStatus:
        """Memanggil snapshot cache capital layer yang direkam pada proses update sebelumnya."""
        if self._status_cache is None:
            raise ValueError("Belum ada update initial yang terjadi pada CapitalManager")
        return self._status_cache

    def reset_daily_tracker(self) -> None:
        """Dipanggil setiap hari / 00:00 UTC, Set daily markers kembali ke current level."""
        self.daily_start_equity = self.current_equity
        self.max_drawdown_today = 0.0
