"""
alerts.py — AlertManager.

Routing alert berdasarkan severity:
    INFO     → log only
    WARNING  → log + EventBus emit
    CRITICAL → log + EventBus emit + stderr

Notification Layer (Telegram/Discord) belum diimplementasi.
AlertManager pakai EventBus sebagai transport sementara.
"""

from __future__ import annotations

import inspect
import logging
import sys
from collections import deque
from datetime import UTC, datetime

from runtime.agent.models.enums import AlertSeverity  # type: ignore
from runtime.agent.models.monitoring import Alert  # type: ignore

logger = logging.getLogger(__name__)

# Maximum alerts yang disimpan di history
MAX_ALERT_HISTORY = 200


class AlertManager:
    """Routing alert berdasarkan severity.

    Semua alert di-log. WARNING dan CRITICAL di-emit ke EventBus
    (jika tersedia). CRITICAL juga ditulis ke stderr.

    Args:
        event_bus: Optional EventBus instance. Jika None, hanya log.
    """

    def __init__(self, event_bus: object | None = None) -> None:
        self._event_bus = event_bus
        self._history: deque[Alert] = deque(maxlen=MAX_ALERT_HISTORY)
        self._critical_count: int = 0
        self._warning_count: int = 0

    async def send(self, alert: Alert) -> None:
        """Kirim alert berdasarkan severity routing."""
        if alert.timestamp is None:
            alert.timestamp = datetime.now(UTC)

        self._history.append(alert)

        # Routing berdasarkan severity
        if alert.severity == AlertSeverity.INFO:
            logger.info("[Alert] %s: %s — %s", alert.component, alert.title, alert.message)

        elif alert.severity == AlertSeverity.WARNING:
            self._warning_count += 1
            logger.warning("[Alert] ⚠  %s: %s — %s", alert.component, alert.title, alert.message)
            await self._emit_event("alert_warning", alert)

        elif alert.severity == AlertSeverity.CRITICAL:
            self._critical_count += 1
            logger.critical("[Alert] 🚨 %s: %s — %s", alert.component, alert.title, alert.message)
            # Stderr untuk visibility
            print(
                f"🚨 CRITICAL ALERT [{alert.component}]: {alert.title} — {alert.message}",
                file=sys.stderr,
            )
            await self._emit_event("alert_critical", alert)

    # ── Template Methods ──────────────────────────────────────────

    async def send_circuit_breaker(
        self,
        state: str,
        reasons: list[str],
        equity: float,
    ) -> None:
        """Template untuk circuit breaker alert."""
        await self.send(
            Alert(
                severity=AlertSeverity.CRITICAL,
                title=f"Circuit Breaker: {state.upper()}",
                message=" | ".join(reasons),
                component="circuit_breaker",
                data={"state": state, "equity": equity, "reasons": reasons},
            )
        )

    async def send_trade_opened(
        self,
        symbol: str,
        side: str,
        qty: float,
        entry: float,
        sl: float,
        tp: float,
        risk_usd: float,
    ) -> None:
        """Template untuk notifikasi trade baru."""
        emoji = "🟢" if side == "BUY" else "🔴"
        await self.send(
            Alert(
                severity=AlertSeverity.INFO,
                title=f"{emoji} Trade Opened",
                message=(
                    f"{side} {symbol} | {qty} @ {entry:,.2f} | "
                    f"SL: {sl:,.2f} | TP: {tp:,.2f} | Risk: ${risk_usd:.2f}"
                ),
                component="trade_manager",
                data={
                    "symbol": symbol,
                    "side": side,
                    "qty": qty,
                    "entry": entry,
                    "sl": sl,
                    "tp": tp,
                },
            )
        )

    async def send_trade_closed(
        self,
        symbol: str,
        pnl_usd: float,
        pnl_pct: float,
        exit_reason: str,
        hold_candles: int,
    ) -> None:
        """Template untuk notifikasi trade ditutup."""
        emoji = "✅" if pnl_usd >= 0 else "❌"
        await self.send(
            Alert(
                severity=AlertSeverity.INFO,
                title=f"{emoji} Trade Closed",
                message=(
                    f"{symbol} {pnl_usd:+.2f} USDT ({pnl_pct:+.2f}%) | "
                    f"{hold_candles} candles | {exit_reason}"
                ),
                component="trade_manager",
                data={
                    "symbol": symbol,
                    "pnl_usd": pnl_usd,
                    "pnl_pct": pnl_pct,
                    "exit_reason": exit_reason,
                },
            )
        )

    async def send_daily_summary(
        self,
        total_trades: int,
        win_rate: float,
        daily_pnl: float,
        equity: float,
        open_positions: int,
    ) -> None:
        """Ringkasan harian."""
        await self.send(
            Alert(
                severity=AlertSeverity.INFO,
                title="📊 Daily Summary",
                message=(
                    f"Trades: {total_trades} | WR: {win_rate:.0%} | "
                    f"PnL: {daily_pnl:+.2f} USDT | Equity: {equity:,.2f} | "
                    f"Open: {open_positions}"
                ),
                component="daily_summary",
                data={
                    "total_trades": total_trades,
                    "win_rate": win_rate,
                    "daily_pnl": daily_pnl,
                    "equity": equity,
                },
            )
        )

    # ── Query ─────────────────────────────────────────────────────

    def get_history(self, limit: int = 50) -> list[Alert]:
        """Return recent alerts."""
        return list(self._history)[-limit:]

    @property
    def critical_count(self) -> int:
        return self._critical_count

    @property
    def warning_count(self) -> int:
        return self._warning_count

    # ── Private ───────────────────────────────────────────────────

    async def _emit_event(self, event_type: str, alert: Alert) -> None:
        """Emit ke EventBus jika tersedia."""
        if self._event_bus is None:
            return
        try:
            emit = getattr(self._event_bus, "emit", None)
            if emit is not None:
                if inspect.iscoroutinefunction(emit):
                    await emit(
                        event_type,
                        {
                            "severity": alert.severity.value,
                            "title": alert.title,
                            "message": alert.message,
                            "component": alert.component,
                        },
                    )
                else:
                    emit(
                        event_type,
                        {
                            "severity": alert.severity.value,
                            "title": alert.title,
                            "message": alert.message,
                            "component": alert.component,
                        },
                    )
        except Exception:
            logger.debug("[AlertManager] Failed to emit event %s", event_type)
