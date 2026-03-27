import asyncio
import logging
import sys
from typing import Any

from runtime.agent.core.event_bus import EventType  # type: ignore

log = logging.getLogger(__name__)


class SafeMode:
    """
    Emergency Stop Mechanism
    Memastikan sistem berhenti dengan aman tanpa meninggalkan posisi tanpa pantauan.
    """

    def __init__(
        self,
        event_bus: Any = None,
        notifier: Any = None,
        trade_mgr: Any = None,
        portfolio: Any = None,
        scheduler: Any = None,
        db_manager: Any = None,
    ):
        self._halt_flag = asyncio.Event()
        self.event_bus = event_bus
        self.notifier = notifier
        self.trade_mgr = trade_mgr
        self.portfolio = portfolio
        self.scheduler = scheduler
        self.db_manager = db_manager

    def is_halted(self) -> bool:
        """Check if systems should halt."""
        return self._halt_flag.is_set()

    async def _close_all_positions(self):
        # TODO: call trade_layer
        pass

    async def _cancel_all_pending_orders(self):
        # TODO: call trade_layer
        pass

    async def _save_state_snapshot(self, reason: str):
        # TODO: call storage systems
        pass

    async def emergency_stop(
        self, reason: str, close_positions: bool = False, exit_code: int = 2
    ) -> None:
        """
        URUTAN SHUTDOWN DARURAT - JANGAN UBAH URUTANNYA:
        1. Set halt flag
        2. Publish event
        3. Opsional: close posisi
        4. Cancel pending orders
        5. State snapshot
        6. Notifikasi
        7. Stop scheduler
        8. Close DB
        9. Exit
        """
        # Step 1
        self._halt_flag.set()
        log.critical("SAFE MODE TRIGGERED", extra={"reason": reason})

        # Step 2
        if self.event_bus:
            self.event_bus.publish(EventType.SAFE_MODE_TRIGGERED, reason, "safe_mode")

        # Step 3
        if close_positions:
            await self._close_all_positions()

        # Step 4
        await self._cancel_all_pending_orders()

        # Step 5
        await self._save_state_snapshot(reason)

        # Step 6
        if self.notifier:
            # await notifier.send_emergency_alert(...)
            pass

        # Step 7
        if self.scheduler:
            await self.scheduler.stop()

        # Step 8
        if self.db_manager:
            # await db_manager.close_all()
            pass

        # Step 9
        log.info("Safe mode complete, exiting", extra={"code": exit_code})
        sys.exit(exit_code)

    async def graceful_shutdown(self) -> None:
        """
        Shutdown normal (maintenance, update):
        1. Hentikan terima signal baru
        2. Tunggu tick yang sedang berjalan selesai (timeout 30s)
        3. Stop scheduler
        4. Simpan state
        5. Exit code 0
        """
        log.info("Graceful shutdown initiated")
        self._halt_flag.set()

        if self.scheduler:
            await self.scheduler.stop()

        await self._save_state_snapshot("Graceful Shutdown")

        if self.db_manager:
            pass  # await db_manager.close_all()

        log.info("Graceful shutdown complete")
        sys.exit(0)
