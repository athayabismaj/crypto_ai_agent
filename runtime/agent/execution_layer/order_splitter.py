"""
Order Splitter (Pemisah size raksasa)
Time-Weighted Average Price order splitting.
Dilengkapi Randomized Jitter untuk anti-front-run.
"""

import asyncio
import logging
import random
from typing import Any

from runtime.agent.execution_layer.exchange import BaseExchange, OrderRequest, OrderResponse

log = logging.getLogger(__name__)


class TWAPSplitter:
    def __init__(self, exchange: BaseExchange):
        self._exchange = exchange

    def split(self, order: OrderRequest, total_slices: int = 5) -> list[OrderRequest]:
        """
        Pecah order menjadi N slice.
        Cth: 0.1 BTC -> 5 x 0.02 BTC
        """
        base_qty = order.quantity / total_slices
        remainder = order.quantity - (base_qty * total_slices)
        slices = []
        for i in range(total_slices):
            qty = base_qty + (remainder if i == total_slices - 1 else 0)

            # Duplikasi _dataclass_
            slice_order = OrderRequest(**{k: v for k, v in vars(order).items()})
            # Round lot filter agar diterima format Bursa
            slice_order.quantity = self._exchange.round_quantity(qty, order.symbol)
            slice_order.client_order_id = f"{order.client_order_id}_twap_{i}"
            slices.append(slice_order)

        return slices

    async def execute(
        self,
        slices: list[OrderRequest],
        executor: Any,  # SpotExecutor/FuturesExecutor
        interval_s: int = 60,
    ) -> list[OrderResponse]:
        """
        Kirim slice berurut dengan delay.
        Fitur *Random Jitter (-5 hingga +5 detik)* disematkan untuk mengacaukan
        pendeteksian pola algoritma eksekusi oleh HFT bots rival.
        """
        responses = []
        for i, slice_order in enumerate(slices):
            if i > 0:
                # RANDOM JITTER (+/- 5 SECONDS)
                jitter = random.uniform(-5.0, 5.0)
                delay = max(5.0, interval_s + jitter)  # minimal 5s antar kiriman

                log.info(f"TWAP Delay + Jitter: menunggu {delay:.2f} detik")
                await asyncio.sleep(delay)

            resp = await executor.place_order(slice_order)
            responses.append(resp)

            log.info(
                f"TWAP slice sent slice={i+1} total={len(slices)} "
                f"qty={resp.filled_qty} price={resp.avg_price}"
            )

        return responses
