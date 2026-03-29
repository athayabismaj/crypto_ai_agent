"""
Spot Executor
Wrapper tipis pembungkus BaseExchange dengan logic khas spot.
"""

import logging
from typing import Any

from runtime.agent.execution_layer.exchange import BaseExchange, OrderRequest, OrderResponse

log = logging.getLogger(__name__)


class InsufficientBalanceError(Exception):
    pass


def generate_order_id(prefix: str, symbol: str) -> str:
    from runtime.shared.utils import utcnow  # type: ignore

    ts_ms = int(utcnow().timestamp() * 1000)
    return f"{prefix}_{symbol}_{ts_ms}"


class SpotExecutor:
    def __init__(self, exchange: BaseExchange, config: Any):
        self._exchange = exchange
        self._config = config

    async def _get_last_price(self, symbol: str) -> float:
        # Panggil market data real
        return 50000.0

    async def place_order(self, order: OrderRequest) -> OrderResponse:
        """
        Pra-proses khusus Spot:
        1. Pastikan is_futures = False
        2. Rounding qty sesuai lot filter bursa
        3. Cek balance (USDT) cukup untuk trade
        4. Delegasikan ke _exchange
        """
        order.is_futures = False

        # Round Quantity (memastikan valid payload ke Binance API)
        order.quantity = self._exchange.round_quantity(order.quantity, order.symbol)

        # Check balance pre-flight (mencegah ditolak API -2010 dan kena pinalti weight)
        balance = await self._exchange.get_balance("USDT")

        last_price = await self._get_last_price(order.symbol)
        notional = order.quantity * (order.price or last_price)

        if balance < notional * 1.002:  # 0.2% buffer untuk komisinya
            raise InsufficientBalanceError(
                f"Balance {balance:.2f} < notional plus fee buffer {notional * 1.002:.2f}"
            )

        return await self._exchange.place_order(order)

    async def close_position(
        self, symbol: str, qty: float, urgency: str = "normal"
    ) -> OrderResponse:
        """
        Tutup posisi spot dengan SELL market order khusus SPOT
        urgency='urgent' → IOC order (immediate or cancel), jika tidak laku yaudah cancel aja sisa nya
        """
        order = OrderRequest(
            symbol=symbol,
            side="SELL",
            order_type="MARKET",
            quantity=self._exchange.round_quantity(qty, symbol),
            client_order_id=generate_order_id("close_spot", symbol),
            time_in_force="IOC" if urgency == "urgent" else "GTC",
            price=0.0,
            is_futures=False,
        )
        return await self._exchange.place_order(order)
