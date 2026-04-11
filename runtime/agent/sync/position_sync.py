"""
position_sync.py — Rekonsiliasi posisi internal vs exchange.

Komponen paling kritis di sync layer. Membandingkan posisi aktual
di exchange dengan posisi yang agent yakini terbuka.

Deteksi:
    GHOST_POSITION  — ada di exchange, tidak di internal (CRITICAL)
    ZOMBIE_POSITION — ada di internal, tidak di exchange (HIGH)
    QTY_MISMATCH    — quantity berbeda > 1% (MEDIUM)
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Protocol

from runtime.agent.models.enums import AlertSeverity  # type: ignore
from runtime.agent.models.monitoring import (  # type: ignore
    Alert,
    Discrepancy,
    PositionSyncReport,
)

logger = logging.getLogger(__name__)

QTY_TOLERANCE_PCT = 0.01  # 1%


class PositionProvider(Protocol):
    """Interface untuk fetch posisi dari exchange."""

    async def get_exchange_positions(self) -> dict[str, dict]:
        """Return dict[symbol, {qty, side, entry_price, ...}]."""
        ...


class InternalPositionStore(Protocol):
    """Interface untuk akses posisi internal."""

    def get_open_positions(self) -> dict[str, dict]:
        """Return dict[symbol, {trade_id, filled_qty, side, ...}]."""
        ...

    def mark_closed(self, trade_id: str) -> None:
        """Mark trade sebagai closed (untuk zombie position)."""
        ...


class PositionSync:
    """Rekonsiliasi posisi internal vs exchange.

    Exchange is source of truth. Ghost position = berbahaya.
    Zombie position = internal stale.
    """

    def __init__(
        self,
        exchange: PositionProvider,
        store: InternalPositionStore,
        alert_manager: object | None = None,
    ) -> None:
        self._exchange = exchange
        self._store = store
        self._alerts = alert_manager
        self._last_report: PositionSyncReport | None = None

    async def run(self) -> PositionSyncReport:
        """Sync posisi — dipanggil scheduler setiap 60 detik."""
        internal_positions = self._store.get_open_positions()

        try:
            exchange_positions = await self._exchange.get_exchange_positions()
        except Exception as e:
            logger.error("[PositionSync] Failed to fetch exchange positions: %s", e)
            return PositionSyncReport(
                internal_count=len(internal_positions),
                exchange_count=0,
                timestamp=datetime.now(UTC),
            )

        discrepancies: list[Discrepancy] = []

        # ── Ghost positions: ada di exchange, tidak di internal ──
        for symbol, ex_pos in exchange_positions.items():
            if symbol not in internal_positions:
                discrepancies.append(
                    Discrepancy(
                        type="GHOST_POSITION",
                        symbol=symbol,
                        internal=None,
                        exchange=ex_pos,
                        severity="CRITICAL",
                    )
                )
                logger.critical(
                    "[PositionSync] GHOST POSITION: %s ada di exchange tapi tidak di internal!",
                    symbol,
                )

        # ── Zombie positions: ada di internal, tidak di exchange ──
        for symbol, int_pos in internal_positions.items():
            if symbol not in exchange_positions:
                discrepancies.append(
                    Discrepancy(
                        type="ZOMBIE_POSITION",
                        symbol=symbol,
                        internal=int_pos,
                        exchange=None,
                        severity="HIGH",
                    )
                )
                # Mark sebagai closed
                trade_id = int_pos.get("trade_id", "")
                if trade_id:
                    self._store.mark_closed(trade_id)
                logger.warning(
                    "[PositionSync] ZOMBIE POSITION: %s ada di internal tapi tidak di exchange",
                    symbol,
                )

        # ── Qty mismatch ──────────────────────────────────────────
        for symbol in set(internal_positions) & set(exchange_positions):
            int_qty = internal_positions[symbol].get("filled_qty", 0.0)
            ex_qty = exchange_positions[symbol].get("qty", 0.0)

            if abs(int_qty - ex_qty) / max(int_qty, 1e-8) > QTY_TOLERANCE_PCT:
                discrepancies.append(
                    Discrepancy(
                        type="QTY_MISMATCH",
                        symbol=symbol,
                        internal=internal_positions[symbol],
                        exchange=exchange_positions[symbol],
                        severity="MEDIUM",
                    )
                )
                logger.warning(
                    "[PositionSync] QTY MISMATCH %s: internal=%.6f, exchange=%.6f",
                    symbol,
                    int_qty,
                    ex_qty,
                )

        # ── Handle discrepancies ──────────────────────────────────
        if discrepancies and self._alerts is not None:
            await self._send_alerts(discrepancies)

        report = PositionSyncReport(
            internal_count=len(internal_positions),
            exchange_count=len(exchange_positions),
            discrepancies=discrepancies,
            timestamp=datetime.now(UTC),
        )
        self._last_report = report
        return report

    @property
    def last_report(self) -> PositionSyncReport | None:
        return self._last_report

    # ── Private ───────────────────────────────────────────────────

    async def _send_alerts(self, discrepancies: list[Discrepancy]) -> None:
        """Kirim alert untuk setiap discrepancy."""
        try:
            send = getattr(self._alerts, "send", None)
            if send is None:
                return

            critical = [d for d in discrepancies if d.severity == "CRITICAL"]
            if critical:
                symbols = ", ".join(d.symbol for d in critical)
                await send(
                    Alert(
                        severity=AlertSeverity.CRITICAL,
                        title="Position discrepancy detected",
                        message=f"Ghost positions: {symbols}",
                        component="position_sync",
                        data={"discrepancies": len(discrepancies)},
                    )
                )

            high = [d for d in discrepancies if d.severity == "HIGH"]
            if high:
                symbols = ", ".join(d.symbol for d in high)
                await send(
                    Alert(
                        severity=AlertSeverity.WARNING,
                        title="Zombie positions detected",
                        message=f"Internal-only positions: {symbols}",
                        component="position_sync",
                        data={"discrepancies": len(high)},
                    )
                )
        except Exception:
            logger.debug("[PositionSync] Failed to send alerts")
