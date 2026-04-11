"""
order_sync.py — Sinkronisasi status order.

Dipanggil scheduler setiap 30 detik.
Memastikan status semua open order di internal sesuai dengan exchange.

Mendeteksi order yang filled, cancelled, atau rejected
tanpa notifikasi WebSocket.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Protocol

from runtime.agent.models.monitoring import OrderSyncReport  # type: ignore

logger = logging.getLogger(__name__)


class OrderStatusProvider(Protocol):
    """Interface untuk query status order di exchange."""

    async def get_order_status(
        self,
        symbol: str,
        client_order_id: str,
    ) -> dict:
        """Return dict dengan keys: status, filled_qty, avg_price.

        Status: 'NEW' | 'FILLED' | 'PARTIALLY_FILLED' | 'CANCELLED' | 'REJECTED'
        Raise OrderNotFoundError jika order tidak ada di exchange.
        """
        ...


class TradeStore(Protocol):
    """Interface untuk akses trade store internal."""

    def get_pending_trades(self) -> list[dict]:
        """Return list of trades dengan status SUBMITTED/PARTIAL.

        Each dict: {trade_id, symbol, client_order_id, status, filled_qty}
        """
        ...

    def update_trade_status(
        self,
        trade_id: str,
        status: str,
        filled_qty: float = 0.0,
        avg_price: float = 0.0,
    ) -> None:
        ...


class OrderNotFoundError(Exception):
    """Order tidak ditemukan di exchange."""


class OrderSync:
    """Sinkronisasi status order internal vs exchange.

    Focus pada trade dengan status SUBMITTED atau PARTIAL.
    """

    def __init__(
        self,
        exchange: OrderStatusProvider,
        store: TradeStore,
    ) -> None:
        self._exchange = exchange
        self._store = store
        self._last_report: OrderSyncReport | None = None

    async def run(self) -> OrderSyncReport:
        """Sync status order — dipanggil scheduler setiap 30 detik."""
        pending = self._store.get_pending_trades()
        updates: list[str] = []
        conflicts: list[str] = []

        for trade in pending:
            trade_id = trade.get("trade_id", "")
            symbol = trade.get("symbol", "")
            client_order_id = trade.get("client_order_id", "")

            try:
                ex_resp = await self._exchange.get_order_status(
                    symbol,
                    client_order_id,
                )
            except OrderNotFoundError:
                conflicts.append(
                    f"Order not found: {client_order_id} ({symbol})",
                )
                continue
            except Exception as e:
                conflicts.append(f"Error checking {client_order_id}: {e}")
                continue

            ex_status = ex_resp.get("status", "")

            if ex_status == "FILLED":
                self._store.update_trade_status(
                    trade_id,
                    "open",
                    filled_qty=ex_resp.get("filled_qty", 0.0),
                    avg_price=ex_resp.get("avg_price", 0.0),
                )
                updates.append(f"SYNCED FILLED: {trade_id}")
                logger.info("[OrderSync] FILLED: %s (%s)", trade_id, symbol)

            elif ex_status == "CANCELLED":
                self._store.update_trade_status(trade_id, "cancelled")
                updates.append(f"SYNCED CANCELLED: {trade_id}")
                logger.info("[OrderSync] CANCELLED: %s (%s)", trade_id, symbol)

            elif ex_status == "REJECTED":
                self._store.update_trade_status(trade_id, "failed")
                updates.append(f"SYNCED REJECTED: {trade_id}")
                logger.warning("[OrderSync] REJECTED: %s (%s)", trade_id, symbol)

            elif ex_status == "PARTIALLY_FILLED":
                self._store.update_trade_status(
                    trade_id,
                    "partial",
                    filled_qty=ex_resp.get("filled_qty", 0.0),
                    avg_price=ex_resp.get("avg_price", 0.0),
                )
                updates.append(f"SYNCED PARTIAL: {trade_id}")
                logger.info("[OrderSync] PARTIAL: %s (%s)", trade_id, symbol)

        report = OrderSyncReport(
            checked=len(pending),
            updated=len(updates),
            conflicts=conflicts,
            details=updates,
            timestamp=datetime.now(UTC),
        )
        self._last_report = report

        if conflicts:
            logger.warning("[OrderSync] %d conflicts: %s", len(conflicts), conflicts)

        return report

    @property
    def last_report(self) -> OrderSyncReport | None:
        return self._last_report
