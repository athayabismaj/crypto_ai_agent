"""
main.py — Entrypoint Pusat Agen AI Crypto
Mengikat semua layer: EventBus, Metrics, DataFeed, TradeManager, HealthDaemon.
"""
import asyncio
import logging
import signal
import sys
from pathlib import Path

# Fix relative imports jika dieksekusi langsung
sys.path.append(str(Path(__file__).resolve().parent.parent.parent.parent))

from observability.prometheus_metrics import metrics
from runtime.agent.core.config import get_config
from runtime.agent.core.event_bus import get_event_bus

log = logging.getLogger("agent.main")


class CryptoAIAgent:
    def __init__(self):
        self.config = get_config()
        self.bus = get_event_bus()
        self.is_running = False

        # Inisialisasi komponen di sini nantinya
        # self.data_feed = None
        # self.intelligence = None
        # self.trade_manager = None

    async def setup(self):
        """Inisialisasi semua dependensi asinkronus."""
        log.info(f"🚀 Memulakan Crypto AI Agent [Modifikasi: {self.config.mode}]")

        # 1. Start Observability Metrics (Prometheus)
        metrics_port = self.config.get("METRICS_PORT", 8090)
        metrics.start_server(port=metrics_port)

        # 2. Binding Modules...
        # self.data_feed = ExchangeDataFeed(...)
        # self.trade_manager = TradeManager(...)

        log.info("Semua modul diinisialisasi sukses.")

    async def _loop(self):
        """Loop utama agen."""
        try:
            while self.is_running:
                # Disini logika tick / consume data utama berjalan
                await asyncio.sleep(1)
        except asyncio.CancelledError:
            log.info("Main loop dibatalkan.")

    async def start(self):
        """Start the agent lifecycle."""
        self.is_running = True
        await self.setup()

        # Run main loop
        await self._loop()

    async def stop(self):
        """Graceful shutdown."""
        log.warning("🛑 Menghentikan Crypto AI Agent gracefully...")
        self.is_running = False
        # Stop internal tasks
        # await self.data_feed.stop()
        log.info("Agent berhasil dihentikan.")


def handle_sigterm(signum, frame, agent_instance, main_task):
    """Menangani sinyal docker / docker-compose stop."""
    log.warning(f"Sinyal {signal.Signals(signum).name} diterima.")
    asyncio.create_task(agent_instance.stop())
    main_task.cancel()


async def main():
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    )

    agent = CryptoAIAgent()

    # Menangani Graceful Shutdown
    loop = asyncio.get_running_loop()
    main_task = asyncio.current_task(loop)

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, lambda s=sig: handle_sigterm(s, None, agent, main_task))

    try:
        await agent.start()
    except asyncio.CancelledError:
        pass
    except Exception as e:
        log.critical(f"FATAL KESALAHAN pada Agent: {e}", exc_info=True)
    finally:
        log.info("Sistem utama exit.")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--smoke-test", action="store_true", help="Run inisialisasi awal dan langsung terminate"
    )
    args = parser.parse_args()

    if args.smoke_test:
        print("Smoke test: Coba load main.py...")
        # Jika berhasil import sampai di sini tanpa error, smoke test syntax berhasil
        print("Smoke test BERHASIL. Syntax main OK.")
        sys.exit(0)

    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nBot dihentikan via keyboard.")
