"""
Futures Executor
Wrapper tipis pembungkus BaseExchange dengan logic khas Futures-M
Perbedaannya adalah check leverage dan mode margin sebelum trade
"""

import logging
from typing import Any

from runtime.agent.execution_layer.exchange import BaseExchange, OrderRequest, OrderResponse

log = logging.getLogger(__name__)


class InsufficientMarginError(Exception):
    pass


class LeverageSetError(Exception):
    pass


def generate_order_id(prefix: str, symbol: str) -> str:
    from runtime.shared.utils import utcnow  # type: ignore

    ts_ms = int(utcnow().timestamp() * 1000)
    return f"{prefix}_{symbol}_{ts_ms}"


class FuturesExecutor:
    def __init__(self, exchange: BaseExchange, config: Any):
        self._exchange = exchange
        self._config = config
        self._leverage_cache: dict[str, int] = {}

    async def _get_last_price(self, symbol: str) -> float:
        # Panggil endpoint public / ticker bila di-implement, mock for now
        return 50000.0

    async def _ensure_leverage(self, symbol: str, leverage: int) -> None:
        """Set bypass filter agar hemat API call rate limit jika tidak berubah"""
        current = self._leverage_cache.get(symbol, 0)
        if current == leverage:
            return

        ok = await self._exchange.set_leverage(symbol, leverage)
        if ok:
            self._leverage_cache[symbol] = leverage
            log.info(f"Leverage successfully configured to {leverage}x for {symbol}")
        else:
            raise LeverageSetError(f"Gagal set leverage {leverage}x untuk {symbol}")

    async def place_order(self, order: OrderRequest) -> OrderResponse:
        order.is_futures = True

        # 1. Pastikan quantity round
        order.quantity = self._exchange.round_quantity(order.quantity, order.symbol)

        # 2. Cek dan set Leverage
        await self._ensure_leverage(order.symbol, order.leverage)

        # 3. Margin check (USDT free / leverage) > Notional required margin
        balance = await self._exchange.get_balance("USDT")
        last_price = await self._get_last_price(order.symbol)

        notional = order.quantity * (order.price or last_price)
        required_margin = notional / order.leverage

        if balance < required_margin * 1.002:  # 0.2% commission allowance buffer
            raise InsufficientMarginError(
                f"Balance {balance:.2f} USDT < margin yang dibutuhkan "
                f"{required_margin:.2f} [{order.leverage}x Lev]"
            )

        return await self._exchange.place_order(order)

    async def close_position(
        self, symbol: str, qty: float, current_side: str, urgency: str = "normal"
    ) -> OrderResponse:
        """Penutupan posisi wajib diset reduce_only=True. current_side='BUY' (long) or 'SELL' (short)"""
        close_side = "SELL" if current_side.upper() == "BUY" else "BUY"

        order = OrderRequest(
            symbol=symbol,
            side=close_side,
            order_type="MARKET",
            quantity=self._exchange.round_quantity(qty, symbol),
            client_order_id=generate_order_id("close_futures", symbol),
            time_in_force="IOC" if urgency == "urgent" else "GTC",
            price=0.0,
            is_futures=True,
            reduce_only=True,  # Sangat mutlak dibutuhkan Binance Futures
            leverage=self._leverage_cache.get(symbol, 1),
        )
        return await self._exchange.place_order(order)
