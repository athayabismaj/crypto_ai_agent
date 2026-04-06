"""
reconciliation.py — Audit harian penuh.

Dipanggil scheduler UTC 00:30 (30 menit setelah reset harian).
Full audit konsistensi antara internal state dan exchange.

Report disimpan ke:
1. logs/reconciliation_{date}.json — arsip mudah dibaca
2. Alert via AlertManager jika ada issues
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from runtime.agent.models.enums import AlertSeverity  # type: ignore
from runtime.agent.models.monitoring import (  # type: ignore
    Alert,
    Discrepancy,
    ReconciliationReport,
)

logger = logging.getLogger(__name__)


class ReconciliationDataProvider(Protocol):
    """Interface untuk mengumpulkan data rekonsiliasi."""

    async def get_exchange_balance(self) -> float: ...
    def get_internal_equity(self) -> float: ...
    def get_internal_open_count(self) -> int: ...
    async def get_exchange_open_count(self) -> int: ...
    def get_daily_pnl_internal(self) -> float: ...
    async def get_daily_pnl_computed(self) -> float: ...
    def get_total_commission_today(self) -> float: ...
    def get_total_trades_today(self) -> int: ...
    def get_win_rate_today(self) -> float: ...
    async def get_position_discrepancies(self) -> list[Discrepancy]: ...


class Reconciliation:
    """Audit harian penuh — cek semua konsistensi.

    Dijalankan UTC 00:30 via scheduler.
    """

    def __init__(
        self,
        data_provider: ReconciliationDataProvider,
        alert_manager: object | None = None,
        log_dir: str | Path = "logs",
    ) -> None:
        self._provider = data_provider
        self._alerts = alert_manager
        self._log_dir = Path(log_dir)
        self._log_dir.mkdir(parents=True, exist_ok=True)
        self._last_report: ReconciliationReport | None = None

    async def run_daily(self) -> ReconciliationReport:
        """Full reconciliation — dipanggil scheduler harian."""
        t0 = datetime.now(UTC)
        date_str = t0.strftime("%Y-%m-%d")
        issues: list[str] = []
        warnings: list[str] = []

        # ── Balance check ─────────────────────────────────────────
        try:
            balance_internal = self._provider.get_internal_equity()
            balance_exchange = await self._provider.get_exchange_balance()
            balance_drift = (
                abs(balance_internal - balance_exchange) /
                max(balance_internal, 1.0)
            )
            if balance_drift > 0.01:  # 1%
                issues.append(
                    f"Balance drift {balance_drift:.1%}: "
                    f"internal={balance_internal:.2f}, exchange={balance_exchange:.2f}"
                )
        except Exception as e:
            balance_internal = 0.0
            balance_exchange = 0.0
            balance_drift = 0.0
            issues.append(f"Failed to check balance: {e}")

        # ── Position check ────────────────────────────────────────
        try:
            open_internal = self._provider.get_internal_open_count()
            open_exchange = await self._provider.get_exchange_open_count()
            position_discrepancies = await self._provider.get_position_discrepancies()
            if position_discrepancies:
                issues.append(
                    f"{len(position_discrepancies)} position discrepancies found"
                )
        except Exception as e:
            open_internal = 0
            open_exchange = 0
            position_discrepancies = []
            issues.append(f"Failed to check positions: {e}")

        # ── PnL check ────────────────────────────────────────────
        try:
            daily_pnl_internal = self._provider.get_daily_pnl_internal()
            daily_pnl_computed = await self._provider.get_daily_pnl_computed()
            pnl_drift = abs(daily_pnl_internal - daily_pnl_computed)
            if pnl_drift > 0.1:
                warnings.append(
                    f"PnL drift: internal={daily_pnl_internal:.2f}, "
                    f"computed={daily_pnl_computed:.2f}"
                )
        except Exception as e:
            daily_pnl_internal = 0.0
            daily_pnl_computed = 0.0
            pnl_drift = 0.0
            warnings.append(f"Failed to check PnL: {e}")

        # ── Commission & trades ───────────────────────────────────
        try:
            total_commission = self._provider.get_total_commission_today()
            total_trades = self._provider.get_total_trades_today()
            win_rate = self._provider.get_win_rate_today()
        except Exception:
            total_commission = 0.0
            total_trades = 0
            win_rate = 0.0

        duration_s = (datetime.now(UTC) - t0).total_seconds()

        report = ReconciliationReport(
            date=date_str,
            balance_internal=balance_internal,
            balance_exchange=balance_exchange,
            balance_drift_pct=balance_drift,
            open_internal=open_internal,
            open_exchange=open_exchange,
            position_discrepancies=position_discrepancies,
            daily_pnl_internal=daily_pnl_internal,
            daily_pnl_computed=daily_pnl_computed,
            pnl_drift=pnl_drift,
            total_commission=total_commission,
            total_trades_today=total_trades,
            win_rate_today=win_rate,
            all_ok=len(issues) == 0,
            issues=issues,
            warnings=warnings,
            duration_s=duration_s,
        )

        # ── Save JSON report ──────────────────────────────────────
        self._save_report(report, date_str)

        # ── Send alert if issues ──────────────────────────────────
        if not report.all_ok and self._alerts is not None:
            await self._send_alert(report)

        self._last_report = report
        logger.info(
            "[Reconciliation] %s: all_ok=%s, issues=%d, duration=%.1fs",
            date_str, report.all_ok, len(issues), duration_s,
        )

        return report

    @property
    def last_report(self) -> ReconciliationReport | None:
        return self._last_report

    # ── Private ───────────────────────────────────────────────────

    def _save_report(self, report: ReconciliationReport, date_str: str) -> None:
        """Simpan sebagai JSON ke disk."""
        path = self._log_dir / f"reconciliation_{date_str}.json"
        try:
            # Serialize dataclass manually (avoid dataclasses.asdict issues)
            data = {
                "date": report.date,
                "balance_internal": report.balance_internal,
                "balance_exchange": report.balance_exchange,
                "balance_drift_pct": report.balance_drift_pct,
                "open_internal": report.open_internal,
                "open_exchange": report.open_exchange,
                "position_discrepancies": len(report.position_discrepancies),
                "daily_pnl_internal": report.daily_pnl_internal,
                "daily_pnl_computed": report.daily_pnl_computed,
                "pnl_drift": report.pnl_drift,
                "total_commission": report.total_commission,
                "total_trades_today": report.total_trades_today,
                "win_rate_today": report.win_rate_today,
                "all_ok": report.all_ok,
                "issues": report.issues,
                "warnings": report.warnings,
                "duration_s": report.duration_s,
                "timestamp": report.timestamp.isoformat(),
            }
            path.write_text(json.dumps(data, indent=2), encoding="utf-8")
            logger.info("[Reconciliation] Report saved to %s", path)
        except Exception as e:
            logger.error("[Reconciliation] Failed to save report: %s", e)

    async def _send_alert(self, report: ReconciliationReport) -> None:
        """Kirim alert jika ada issues."""
        try:
            send = getattr(self._alerts, "send", None)
            if send:
                await send(Alert(
                    severity=AlertSeverity.WARNING,
                    title=f"Rekonsiliasi {report.date}: ada isu",
                    message="\n".join(report.issues),
                    component="reconciliation",
                    data={
                        "date": report.date,
                        "issues": len(report.issues),
                        "warnings": len(report.warnings),
                    },
                ))
        except Exception:
            logger.debug("[Reconciliation] Failed to send alert")
