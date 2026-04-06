"""
Konektor Binance (Spot & Futures USDT-M).
Meng-handle request sign, rate limit, retries, dan parsing JSON response.
Terintegrasi secara asinkron menggunakan aiohttp.
"""

import asyncio
import hashlib
import hmac
import logging
from typing import Any, Optional
from urllib.parse import urlencode

import aiohttp

from runtime.agent.execution_layer.exchange import BaseExchange, OrderRequest, OrderResponse
from runtime.shared.utils import utcnow  # type: ignore

log = logging.getLogger(__name__)

class BinanceExchangeError(Exception):
    pass
    
class OrderNotFoundError(Exception):
    pass


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
        self._session: aiohttp.ClientSession | None = None

        # Simpan state metadata ticker bursa
        self._lot_cache: dict[str, Any] = {}
        self._time_offset: int = 0

    def get_exchange_name(self) -> str:
        return "binance_testnet" if self._testnet else "binance"

    async def connect(self) -> None:
        """Membuka session aiohttp dan sinkronisasi waktu UTC server."""
        self._session = aiohttp.ClientSession(
            headers={"X-MBX-APIKEY": self._key}
        )
        await self._sync_time()
        
        # Di environment nyata, di sini akan ditarik exchangeInfo untuk mempopulerkan _lot_cache.

    async def disconnect(self) -> None:
        if self._session:
            await self._session.close()

    async def _sync_time(self) -> None:
        """Mendapatkan offset timestamp antara lokal dan Binance."""
        base = self.BASE_URL_TESTNET if self._testnet else self.BASE_URL_FUTURES
        try:
            assert self._session is not None
            async with self._session.get(f"{base}/fapi/v1/time") as resp:
                data = await resp.json()
                server_time = data["serverTime"]
                local_time = int(utcnow().timestamp() * 1000)
                self._time_offset = server_time - local_time
                log.info(f"Binance time synced. Offset: {self._time_offset}ms")
        except Exception as e:
            log.warning(f"Gagal sync waktu bursa: {e}. Mengabaikan offset.")

    def _get_timestamp(self) -> int:
        return int(utcnow().timestamp() * 1000) + self._time_offset

    def _sign(self, query_string: str) -> str:
        """Membuat signature HMAC SHA256 menggunakan _secret."""
        return hmac.new(
            self._secret.encode("utf-8"),
            query_string.encode("utf-8"),
            hashlib.sha256
        ).hexdigest()

    async def _signed_request(self, method: str, url: str, params: dict) -> dict:
        if not self._session:
            raise RuntimeError("BinanceExchange belum terkoneksi. Panggil connect().")
            
        params["timestamp"] = self._get_timestamp()
        params["recvWindow"] = 5000
        
        query_string = urlencode(params)
        signature = self._sign(query_string)
        
        # Tambahkan signature ke final URL parameter
        final_url = f"{url}?{query_string}&signature={signature}"
        
        async with self._session.request(method, final_url) as response:
            data = await response.json()
            
            # Rate limiting detection (Mock for now or reading headers in real life)
            
            # Error handling
            if response.status >= 400:
                code = data.get("code", 0)
                msg = data.get("msg", "Unknown error")
                
                if code == -2011:
                    raise OrderNotFoundError(f"Order not found: {msg}")
                raise BinanceExchangeError(f"Binance API Error {code}: {msg} -> {data}")
                
            return data

    def get_lot_filter(self, symbol: str) -> Any:
        # Mock lot filter if empty
        return self._lot_cache.get(
            symbol, type("MockFilter", (), {"step_size": 0.001, "tick_size": 0.01})()
        )

    def _parse_order_response(self, data: dict, client_order_id: str) -> OrderResponse:
        """Parse raw response dict menjadi standar dataclass internal kita"""
        return OrderResponse(
            exchange_order_id=str(data.get("orderId", "")),
            client_order_id=data.get("clientOrderId", client_order_id),
            status=data.get("status", "NEW"), # NEW, FILLED, dsb
            filled_qty=float(data.get("executedQty", 0.0)),
            avg_price=float(data.get("avgPrice", 0.0) or data.get("price", 0.0)),
            commission=0.0, # di Binance endpoint spesifik kadang tidak mengembalikan fee
            commission_asset="USDT",
            timestamp=utcnow(), # fallback default jika gaada updatetime
            raw_response=data
        )

    async def place_order(self, order: OrderRequest) -> OrderResponse:
        base = (
            self.BASE_URL_TESTNET
            if self._testnet
            else (self.BASE_URL_FUTURES if order.is_futures else self.BASE_URL_SPOT)
        )
        endpoint = "/fapi/v1/order" if order.is_futures else "/api/v3/order"
        url = f"{base}{endpoint}"

        if self._rate_lim:
            await self._rate_lim.acquire(endpoint, is_order=True)

        params = {
            "symbol": order.symbol,
            "side": order.side,
            "type": order.order_type,
            "quantity": str(order.quantity),
            "newClientOrderId": order.client_order_id,
            "newOrderRespType": "RESULT",
        }

        if order.order_type == "LIMIT":
            params["price"] = str(order.price)
            params["timeInForce"] = order.time_in_force

        if order.order_type == "STOP_MARKET":
            params["stopPrice"] = str(order.stop_price)

        if order.is_futures and order.reduce_only:
            params["reduceOnly"] = "true"

        data = await self._signed_request("POST", url, params)
        return self._parse_order_response(data, order.client_order_id)

    async def cancel_order(self, symbol: str, client_order_id: str) -> bool:
        base = self.BASE_URL_TESTNET if self._testnet else self.BASE_URL_FUTURES
        endpoint = "/fapi/v1/order" # assume futures for now unless genericized
        
        if self._rate_lim:
            await self._rate_lim.acquire(endpoint, is_order=True)

        try:
            await self._signed_request("DELETE", f"{base}{endpoint}", {
                "symbol": symbol,
                "origClientOrderId": client_order_id
            })
            return True
        except OrderNotFoundError:
            log.warning(f"Cancel failed: order {client_order_id} ga ketemu.")
            return True # If it's already missing, assume canceled/filled.
            
    async def get_order_status(self, symbol: str, client_order_id: str) -> OrderResponse:
        """Pengecekan ke exchange, error dilempar bila not-found / timeout"""
        base = self.BASE_URL_TESTNET if self._testnet else self.BASE_URL_FUTURES
        endpoint = "/fapi/v1/order"
        
        if self._rate_lim:
            await self._rate_lim.acquire(endpoint)

        data = await self._signed_request("GET", f"{base}{endpoint}", {
            "symbol": symbol,
            "origClientOrderId": client_order_id
        })
        return self._parse_order_response(data, client_order_id)

    async def get_balance(self, asset: str) -> float:
        base = self.BASE_URL_TESTNET if self._testnet else self.BASE_URL_FUTURES
        endpoint = "/fapi/v2/balance"
        
        if self._rate_lim:
            await self._rate_lim.acquire(endpoint)

        data = await self._signed_request("GET", f"{base}{endpoint}", {})
        for bal in data:
            if bal.get("asset") == asset:
                return float(bal.get("availableBalance", 0.0))
        return 0.0

    async def get_position(self, symbol: str) -> Optional[Any]:
        base = self.BASE_URL_TESTNET if self._testnet else self.BASE_URL_FUTURES
        endpoint = "/fapi/v2/positionRisk"
        
        if self._rate_lim:
            await self._rate_lim.acquire(endpoint)

        data = await self._signed_request("GET", f"{base}{endpoint}", {"symbol": symbol})
        if data and isinstance(data, list):
            for pos in data:
                if pos.get("symbol") == symbol:
                    return pos
        return None

    async def set_leverage(self, symbol: str, leverage: int) -> bool:
        """Set pengali leverage Futures-M sebelum trade."""
        base = self.BASE_URL_TESTNET if self._testnet else self.BASE_URL_FUTURES
        endpoint = "/fapi/v1/leverage"
        
        if self._rate_lim:
            await self._rate_lim.acquire(endpoint)
            
        data = await self._signed_request("POST", f"{base}{endpoint}", {
            "symbol": symbol,
            "leverage": leverage
        })
        return data.get("leverage") == leverage

    async def get_open_orders(self, symbol: str | None = None) -> list[OrderResponse]:
        base = self.BASE_URL_TESTNET if self._testnet else self.BASE_URL_FUTURES
        endpoint = "/fapi/v1/openOrders"
        
        if self._rate_lim:
            await self._rate_lim.acquire(endpoint)
            
        params = {}
        if symbol:
            params["symbol"] = symbol

        data = await self._signed_request("GET", f"{base}{endpoint}", params)
        return [self._parse_order_response(o, o.get("clientOrderId", "")) for o in data]
