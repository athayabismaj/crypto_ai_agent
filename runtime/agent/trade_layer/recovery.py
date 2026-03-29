"""
Modul Recovery T-09
Memeriksa status trade yang menggantung (PENDING, SUBMITTED, PARTIAL, OPEN)
Merekonsiliasi data internal sqlite dengan respons riil exchange API
"""

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from runtime.agent.trade_layer.store import TradeStore
from runtime.agent.trade_layer.trade import TradeStatus
from runtime.shared.utils import utcnow  # type: ignore

log = logging.getLogger(__name__)


class OrderNotFoundError(Exception):
    pass


@dataclass
class RecoveryReport:
    total_checked: int
    resolved_count: int
    unresolved_count: int
    actions_taken: list[str]
    warnings: list[str]
    success: bool
    duration_s: float
    timestamp: datetime


class RecoveryManager:
    def __init__(
        self,
        store: TradeStore,
        executor: Any,  # Exchanger API Executor
        safe_mode: Any,  # Emergency stop protocol module
    ):
        self._store = store
        self._executor = executor
        self._safe_mode = safe_mode

    async def restore(self) -> RecoveryReport:
        """Dipanggil SEKALI saat startup."""
        start = utcnow()
        actions = []
        warnings = []
        unresolved = []

        # Step 1: Load semua trade non-closed dari state.db
        active_trades = self._store.get_all_active()
        log.info(f"Recovery: found {len(active_trades)} active trades")

        for trade in active_trades:
            # Step 2: Query status dari exchange
            try:
                # Paper mode tidak perlu call exchange sungguhan
                if trade.mode == "paper":
                    if trade.status == TradeStatus.SUBMITTED:
                        self._store.update_status(
                            trade.trade_id, TradeStatus.OPEN, opened_at=utcnow()
                        )
                        actions.append(f"PAPER MOCK {trade.trade_id} -> OPEN")
                    continue

                ex_status = await self._executor.get_order_status(
                    trade.symbol, trade.client_order_id
                )

                # Step 3: Rekonsiliasi Status
                if ex_status.status == "FILLED" and not trade.is_open:
                    # Trade sudah terisi (filled) tapi di DB internal masih SUBMITTED/PENDING
                    self._store.update_status(
                        trade.trade_id,
                        TradeStatus.OPEN,
                        filled_qty=ex_status.filled_qty,
                        avg_fill_price=ex_status.avg_price,
                        exchange_order_id=ex_status.exchange_order_id,
                        opened_at=ex_status.timestamp,
                    )
                    actions.append(f"UPDATED to OPEN: {trade.trade_id}")

                elif ex_status.status == "CANCELLED":
                    self._store.update_status(trade.trade_id, TradeStatus.CANCELLED)
                    actions.append(f"SYNCED CANCELLED: {trade.trade_id}")

            except Exception as e:
                # Disimulasikan jika error adalah order tidak ketemu (Not found) atau timeout
                # "OrderNotFoundError" (sebagai pseudo-name error binance -2011 dll)
                err_msg = str(e)
                if "not found" in err_msg.lower() or "OrderNotFoundError" in str(type(e)):
                    if trade.status == TradeStatus.PENDING:
                        # order belum sempat masuk exchange sebelum system crash -> aman di-cancel
                        self._store.update_status(
                            trade.trade_id, TradeStatus.CANCELLED, updated_at=utcnow()
                        )
                        actions.append(f"PENDING -> CANCELLED: {trade.trade_id}")
                    else:
                        warnings.append(
                            f"Ghost trade [Tidak ada di exchange padahal SUBMITTED/OPEN]: {trade.trade_id}"
                        )
                        unresolved.append(trade)
                else:
                    # Timeout error
                    warnings.append(f"Network error on syncing {trade.trade_id}: {err_msg}")
                    unresolved.append(trade)

        # Step 4: Coba pastikan open trades di DB punya SL di exchange (misalkan kita letak SL di memory exchange)
        # log.warning("Pastikan Anda update risk config stop loss di Exchange.")

        success = len(unresolved) == 0
        if not success:
            log.error(f"Recovery: {len(unresolved)} unresolved trades!")
            # Trigger Emergency Safe_mode stop agar tidak jalan dengan DB gak sinkron
            if hasattr(self._safe_mode, "emergency_stop"):
                await self._safe_mode.emergency_stop(
                    "Recovery Failed! DB state dan Exchange asinkron."
                )

        return RecoveryReport(
            total_checked=len(active_trades),
            resolved_count=len(active_trades) - len(unresolved),
            unresolved_count=len(unresolved),
            actions_taken=actions,
            warnings=warnings,
            success=success,
            duration_s=(utcnow() - start).total_seconds(),
            timestamp=utcnow(),
        )
