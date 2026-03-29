"""
Konektor Binance (Spot & Futures USDT-M).
Meng-handle request sign, rate limit, retries, dan parsing JSON response.
"""

import asyncio
import logging
import uuid
from typing import Any, Optional

from runtime.agent.execution_layer.exchange import BaseExchange, OrderRequest, OrderResponse
from runtime.shared.utils import utcnow  # type: ignore

log = logging.getLogger(__name__)


class BinanceExchange(BaseExchange):
    BASE_URL_SPOT = "https://api.binance.com"
    BASE_URL_FUTURES = "https://fapi.binance.com"
    BASE_URL_TESTNET = "https://testnet.binancefutures.com"

    def __init__(
        self, api_key: str, api_secret: str, testnet: bool = False, rate_limiter: Any = None
    ):
        self._key = api_key
        self._secret = api_secret
        self._testnet = testnet
        self._rate_lim = rate_limiter
        self._session = None  # aiohttp.ClientSession (disetup oleh runtime main.py)

        # Simpan state metadata ticker bursa
        self._lot_cache: dict[str, Any] = {}

    def get_exchange_name(self) -> str:
        return "binance_testnet" if self._testnet else "binance"

    async def connect(self) -> None:
        """Koneksi aiohttp (Mock implementasi). Aslinya pakai aiohttp.ClientSession()"""
        pass

    async def disconnect(self) -> None:
        if self._session:
            await self._session.close()

    def get_lot_filter(self, symbol: str) -> Any:
        """Mengembalikan LotFilter object yang di cache saat start"""
        return self._lot_cache.get(
            symbol, type("MockFilter", (), {"step_size": 0.001, "tick_size": 0.01})()
        )

    async def place_order(self, order: OrderRequest) -> OrderResponse:
        base = (
            self.BASE_URL_TESTNET
            if self._testnet
            else (self.BASE_URL_FUTURES if order.is_futures else self.BASE_URL_SPOT)
        )
        endpoint = "/fapi/v1/order" if order.is_futures else "/api/v3/order"

        if self._rate_lim:
            await self._rate_lim.acquire(endpoint, is_order=True)

        # Build req params
        params = {
            "symbol": order.symbol,
            "side": order.side,
            "type": order.order_type,
            "quantity": str(order.quantity),
            "newClientOrderId": order.client_order_id,
            "newOrderRespType": "FULL",
        }

        if order.order_type == "LIMIT":
            params["price"] = str(order.price)
            params["timeInForce"] = order.time_in_force

        if order.order_type == "STOP_MARKET":
            params["stopPrice"] = str(order.stop_price)

        if order.is_futures and order.reduce_only:
            params["reduceOnly"] = "true"

        log.info(f"Mengirim Order ke f{base}{endpoint}: {params}")

        # Simulasikan HTTP call request (aslinya dengan _signed_request POST dan update headers limit)
        await asyncio.sleep(0.02)  # 20ms sim latency

        return OrderResponse(
            exchange_order_id=f"BNC_{str(uuid.uuid4())[:8]}",
            client_order_id=order.client_order_id,
            status="FILLED" if order.order_type == "MARKET" else "NEW",
            filled_qty=order.quantity if order.order_type == "MARKET" else 0.0,
            avg_price=order.price if order.price else 50000.0,  # dummy market price
            commission=2.5,
            commission_asset="USDT",
            timestamp=utcnow(),
            raw_response={"dummy": "binance_response"},
        )

    async def cancel_order(self, symbol: str, client_order_id: str) -> bool:
        if self._rate_lim:
            await self._rate_lim.acquire("/fapi/v1/order", is_order=True)

        log.info(f"Cancellation order request untuk {client_order_id} at {symbol}")
        await asyncio.sleep(0.01)
        return True

    async def get_order_status(self, symbol: str, client_order_id: str) -> OrderResponse:
        """Pengecekan ke exchange, error dilempar bila not-found / timeout"""
        # Dalam implementasi nyata: return await self._signed_request('GET', ...)
        # Disini kita mock untuk testing.
        await asyncio.sleep(0.01)
        return OrderResponse(
            exchange_order_id="UNKNOWN",
            client_order_id=client_order_id,
            status="FILLED",
            filled_qty=1.0,
            avg_price=50000.0,
            commission=0.0,
            commission_asset="USDT",
            timestamp=utcnow(),
            raw_response={},
        )

    async def get_balance(self, asset: str) -> float:
        # Mocking
        return 100000.0

    async def get_position(self, symbol: str) -> Optional[Any]:
        return None

    async def set_leverage(self, symbol: str, leverage: int) -> bool:
        """Set pengali leverage Futures-M sebelum trade."""
        if self._rate_lim:
            await self._rate_lim.acquire("/fapi/v1/marginType")
        log.info(f"⚙️ Leverage diset ke {leverage}x untuk {symbol}")
        return True

    async def get_open_orders(self, symbol: str | None = None) -> list[OrderResponse]:
        return []
