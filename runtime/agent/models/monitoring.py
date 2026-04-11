"""
Monitoring & Sync Layer models.

Dataclass untuk HealthReport, Alert, SyncReport, Discrepancy, ReconciliationReport.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from runtime.agent.models.enums import (  # type: ignore
    AlertSeverity,
    ComponentStatus,
)

# ══════════════════════════════════════════════════════════════════════
#  Health Check
# ══════════════════════════════════════════════════════════════════════


@dataclass
class ComponentHealth:
    """Status kesehatan satu komponen."""

    name: str
    status: ComponentStatus
    value: float | str = ""  # nilai yang diukur
    message: str = ""
    latency_ms: float = 0.0


@dataclass
class HealthReport:
    """Laporan kesehatan keseluruhan sistem."""

    overall: ComponentStatus
    components: list[ComponentHealth] = field(default_factory=list)
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))

    @property
    def is_healthy(self) -> bool:
        return self.overall == ComponentStatus.HEALTHY

    @property
    def unhealthy_components(self) -> list[str]:
        return [c.name for c in self.components if c.status == ComponentStatus.UNHEALTHY]

    @property
    def degraded_components(self) -> list[str]:
        return [c.name for c in self.components if c.status == ComponentStatus.DEGRADED]


# ══════════════════════════════════════════════════════════════════════
#  Alerts
# ══════════════════════════════════════════════════════════════════════


@dataclass
class Alert:
    """Alert standar — dikirim oleh AlertManager."""

    severity: AlertSeverity
    title: str
    message: str
    component: str  # komponen yang mengirim
    data: dict = field(default_factory=dict)  # type: ignore[assignment]
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))


# ══════════════════════════════════════════════════════════════════════
#  Sync Reports
# ══════════════════════════════════════════════════════════════════════


@dataclass
class BalanceSyncReport:
    """Laporan sinkronisasi saldo."""

    internal_before: float  # equity internal sebelum sync
    exchange_actual: float  # balance aktual dari exchange
    drift_pct: float  # persentase perbedaan
    updated: bool  # True jika internal diupdate
    alert_sent: bool = False  # True jika drift > ALERT_DRIFT_PCT
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass
class OrderSyncReport:
    """Laporan sinkronisasi status order."""

    checked: int  # jumlah order yang dicek
    updated: int  # jumlah order yang diupdate
    conflicts: list[str] = field(default_factory=list)
    details: list[str] = field(default_factory=list)
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass
class Discrepancy:
    """Ketidaksesuaian antara internal state dan exchange."""

    type: str  # 'GHOST_POSITION' | 'ZOMBIE_POSITION' | 'QTY_MISMATCH' | 'SIDE_MISMATCH'
    symbol: str
    internal: dict | None = None  # data internal (None jika ghost)
    exchange: dict | None = None  # data exchange (None jika zombie)
    severity: str = "MEDIUM"  # 'MEDIUM' | 'HIGH' | 'CRITICAL'


@dataclass
class PositionSyncReport:
    """Laporan sinkronisasi posisi."""

    internal_count: int
    exchange_count: int
    discrepancies: list[Discrepancy] = field(default_factory=list)
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))

    @property
    def has_issues(self) -> bool:
        return len(self.discrepancies) > 0


@dataclass
class ReconciliationReport:
    """Laporan rekonsiliasi harian penuh."""

    date: str  # tanggal rekonsiliasi (UTC, format YYYY-MM-DD)

    # Balance
    balance_internal: float = 0.0
    balance_exchange: float = 0.0
    balance_drift_pct: float = 0.0

    # Positions
    open_internal: int = 0
    open_exchange: int = 0
    position_discrepancies: list[Discrepancy] = field(default_factory=list)

    # PnL
    daily_pnl_internal: float = 0.0
    daily_pnl_computed: float = 0.0
    pnl_drift: float = 0.0

    # Commission
    total_commission: float = 0.0
    total_trades_today: int = 0
    win_rate_today: float = 0.0

    # Status
    all_ok: bool = True
    issues: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    duration_s: float = 0.0
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))
