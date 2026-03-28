"""
Audit & recovery models — AuditEvent dan RecoveryReport.

AuditEvent = event-sourcing record untuk setiap aksi trade.
RecoveryReport = hasil rekonsiliasi state.
"""

from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, Field  # type: ignore


class AuditEvent(BaseModel):
    """
    Immutable audit log untuk setiap event dalam lifecycle trade.

    Disimpan ke storage / event_bus untuk compliance dan debugging.
    """

    model_config = ConfigDict(frozen=True)

    event_id: str
    trade_id: str
    event_type: str  # 'CREATED' | 'SUBMITTED' | 'FILLED' | 'CLOSED' etc.
    actor: str = ""  # 'strategy' | 'risk' | 'trade_manager' | 'sync'
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))

    # Snapshot saat event terjadi
    equity_snapshot: float = 0.0
    price_at_event: float = 0.0
    details: dict = Field(default_factory=dict)  # type: ignore[assignment]
    checksum: str = ""  # hash integrity


class RecoveryReport(BaseModel):
    """Laporan hasil proses recovery / rekonsiliasi state."""

    model_config = ConfigDict(frozen=True)

    total_checked: int = 0
    resolved: int = 0
    unresolved: int = 0
    actions_taken: list[str] = Field(default_factory=list)
    success: bool = True
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    details: dict = Field(default_factory=dict)  # type: ignore[assignment]
