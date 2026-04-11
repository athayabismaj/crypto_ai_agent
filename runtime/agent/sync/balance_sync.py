"""
balance_sync.py — Sinkronisasi saldo internal vs exchange.

Dipanggil scheduler setiap 60 detik.
Prinsip: exchange is source of truth. Internal selalu mengikuti exchange.

Thresholds:
    MAX_DRIFT_PCT = 0.01   — 1% perbedaan masih toleransi (no alert)
    ALERT_DRIFT_PCT = 0.05 — 5% → alert kritis
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Protocol

from runtime.agent.models.enums import AlertSeverity  # type: ignore
from runtime.agent.models.monitoring import Alert, BalanceSyncReport  # type: ignore

logger = logging.getLogger(__name__)


class ExchangeBalanceProvider(Protocol):
    """Interface untuk fetch balance dari exchange."""

    async def get_balance_usdt(self) -> float:
        ...


class CapitalUpdater(Protocol):
    """Interface untuk update equity di CapitalManager."""

    def get_current_equity(self) -> float:
        ...

    def update_from_exchange(self, actual: float) -> None:
        ...


class BalanceSync:
    """Sinkronisasi saldo internal dengan exchange.

    Exchange is source of truth. Internal diupdate dari exchange,
    bukan sebaliknya.
    """

    MAX_DRIFT_PCT: float = 0.01  # 1%
    ALERT_DRIFT_PCT: float = 0.05  # 5%

    def __init__(
        self,
        exchange: ExchangeBalanceProvider,
        capital: CapitalUpdater,
        alert_manager: object | None = None,
    ) -> None:
        self._exchange = exchange
        self._capital = capital
        self._alerts = alert_manager
        self._last_report: BalanceSyncReport | None = None

    async def run(self) -> BalanceSyncReport:
        """Sync saldo — dipanggil scheduler setiap 60 detik."""
        internal = self._capital.get_current_equity()

        try:
            actual = await self._exchange.get_balance_usdt()
        except Exception as e:
            logger.error("[BalanceSync] Failed to fetch exchange balance: %s", e)
            return BalanceSyncReport(
                internal_before=internal,
                exchange_actual=0.0,
                drift_pct=0.0,
                updated=False,
                alert_sent=False,
                timestamp=datetime.now(UTC),
            )

        drift_pct = abs(actual - internal) / max(internal, 1.0)

        alert_sent = False
        if drift_pct > self.ALERT_DRIFT_PCT:
            logger.critical(
                "[BalanceSync] DRIFT SIGNIFIKAN: internal=%.2f, exchange=%.2f, drift=%.1f%%",
                internal,
                actual,
                drift_pct * 100,
            )
            alert_sent = True
            if self._alerts is not None:
                try:
                    send = getattr(self._alerts, "send", None)
                    if send:
                        await send(
                            Alert(
                                severity=AlertSeverity.CRITICAL,
                                title="Balance drift signifikan",
                                message=(
                                    f"Internal: {internal:.2f}, Exchange: {actual:.2f}, "
                                    f"Drift: {drift_pct:.1%}"
                                ),
                                component="balance_sync",
                                data={
                                    "internal": internal,
                                    "exchange": actual,
                                    "drift_pct": drift_pct,
                                },
                            )
                        )
                except Exception:
                    logger.debug("[BalanceSync] Failed to send alert")
        elif drift_pct > self.MAX_DRIFT_PCT:
            logger.warning(
                "[BalanceSync] Drift detected: internal=%.2f, exchange=%.2f, drift=%.1f%%",
                internal,
                actual,
                drift_pct * 100,
            )

        # Update internal dengan nilai aktual (exchange is truth)
        self._capital.update_from_exchange(actual)

        report = BalanceSyncReport(
            internal_before=internal,
            exchange_actual=actual,
            drift_pct=drift_pct,
            updated=True,
            alert_sent=alert_sent,
            timestamp=datetime.now(UTC),
        )
        self._last_report = report
        return report

    @property
    def last_report(self) -> BalanceSyncReport | None:
        return self._last_report
