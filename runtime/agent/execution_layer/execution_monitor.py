"""
Execution Monitor
Tugas background daemon asinkron yang jalan setiap X detik.
Bertugas melacak apakah ada trade yang gagal respon fill dari binance sehingga tertahan di SUBMITTED
ataupun memastikan PARTIAL_FILL diselesaikan atau dicancel memutar margin.
"""

import asyncio
import logging
from typing import Any

from runtime.agent.trade_layer.store import TradeStore
from runtime.agent.trade_layer.trade import TradeStatus
from runtime.shared.utils import utcnow  # type: ignore

log = logging.getLogger(__name__)


class ExecutionMonitor:
    PENDING_TIMEOUT_S = 30  # SUBMITTED > 30s tanpa update → cek manual (stuck_flight)
    PARTIAL_TIMEOUT_S = 300  # PARTIAL > 5 menit → cancel sisa dari order

    def __init__(self, store: TradeStore, executor: Any):
        self._store = store
        self._executor = executor

    async def monitor_loop(self) -> None:
        """Background loop runner"""
        log.info("ExecutionMonitor starting loop...")
        while True:
            try:
                await asyncio.sleep(10)  # jalan per 10dtk
                await self._check_submitted_trades()
                await self._check_partial_trades()
            except asyncio.CancelledError:
                log.info("ExecutionMonitor stopped.")
                break
            except Exception as e:
                log.error(f"ExecutionMonitor error: {e}", exc_info=True)

    async def _check_submitted_trades(self) -> None:
        """Kumpulkan semua transaksi menggantung."""
        active = self._store.get_all_active()
        now = utcnow()

        for t in active:
            if t.status == TradeStatus.SUBMITTED and t.submitted_at:
                delta = (now - t.submitted_at).total_seconds()

                if delta > self.PENDING_TIMEOUT_S:
                    log.warning(
                        f"Trade {t.trade_id} stuck at SUBMITTED for {delta:.1f}s. Syncing API..."
                    )
                    try:
                        ex_status = await self._executor.get_order_status(
                            t.symbol, t.client_order_id
                        )

                        if ex_status.status == "FILLED":
                            self._store.update_status(
                                t.trade_id,
                                TradeStatus.OPEN,
                                opened_at=ex_status.timestamp,
                                filled_qty=ex_status.filled_qty,
                                avg_fill_price=ex_status.avg_price,
                                exchange_order_id=ex_status.exchange_order_id,
                            )
                            log.info(f"Monitor recovered stuck trade {t.trade_id} to FILLED.")

                        elif ex_status.status == "REJECTED":
                            self._store.update_status(
                                t.trade_id, TradeStatus.FAILED, exit_reason="REJECTED_BY_EXCHANGE"
                            )
                            log.error(f"Trade {t.trade_id} was secretly REJECTED. Marking failed.")

                    except Exception as e:
                        # API rate limit atau timeout lagi di call ini di skip saja, nanti retried di loop bg
                        log.error(f"Failed to sync {t.trade_id}: {e}")

    async def _check_partial_trades(self) -> None:
        active = self._store.get_all_active()
        now = utcnow()

        for t in active:
            if t.status == TradeStatus.PARTIAL and t.opened_at:
                delta = (now - t.opened_at).total_seconds()

                if delta > self.PARTIAL_TIMEOUT_S:
                    log.warning(f"Partial fill timeout on {t.trade_id}. Cancelling remainder!")
                    try:
                        ok = await self._executor.cancel_order(t.symbol, t.client_order_id)
                        if ok:
                            # Jika berhasil dibatalkan sisanya, maka kita patenkan status menjadi OPEN
                            t.requested_qty = t.filled_qty  # asumsikan sisa nya buang
                            self._store.update_status(t.trade_id, TradeStatus.OPEN)
                            log.info(
                                f"Trade {t.trade_id} upgraded from PARTIAL to FULL OPEN after cancellation."
                            )
                    except Exception as e:
                        log.error(f"Failed to cancel remaining partial fill {t.trade_id}: {e}")
