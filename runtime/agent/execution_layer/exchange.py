"""
Definisi Kontrak untuk Layer Eksekusi (Stateless HTTP/API wrappers)
Berisi dataclass standar untuk transaksi HTTP, respon API, 
dan Class Template interaksi ke Bursa.
"""

import math
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass
class OrderRequest:
    symbol: str
    side: str  # 'BUY' | 'SELL'
    order_type: str  # 'MARKET' | 'LIMIT' | 'STOP_MARKET'
    quantity: float  # Harus sudah sesuai limit lot_filter / step_size
    price: float  # 0.0 jika order market
    stop_price: float = 0.0
    client_order_id: str = ""
    is_futures: bool = False
    reduce_only: bool = False  # True jika close out/mengurangi margin
    time_in_force: str = "GTC"  # GTC | IOC | FOK
    leverage: int = 1


@dataclass
class OrderResponse:
    exchange_order_id: str
    client_order_id: str
    status: str  # NEW | FILLED | PARTIALLY_FILLED | CANCELLED | REJECTED
    filled_qty: float
    avg_price: float
    commission: float
    commission_asset: str
    timestamp: datetime
    raw_response: dict

    @property
    def is_filled(self) -> bool:
        return self.status in ("FILLED", "PARTIALLY_FILLED")

    @property
    def is_rejected(self) -> bool:
        return self.status == "REJECTED"


class BaseExchange(ABC):
    """
    Template standar jika ingin menghubungkan lebih banyak aset ke Bybit, OKX, atau Kraken dll
    Semua konektor harus menaati struktur di bawah ini.
    """

    @abstractmethod
    async def place_order(self, order: OrderRequest) -> OrderResponse:
        ...

    @abstractmethod
    async def cancel_order(self, symbol: str, client_order_id: str) -> bool:
        ...

    @abstractmethod
    async def get_order_status(self, symbol: str, client_order_id: str) -> OrderResponse:
        ...

    @abstractmethod
    async def get_balance(self, asset: str) -> float:
        ...

    @abstractmethod
    async def get_position(self, symbol: str) -> Any | None:
        ...

    @abstractmethod
    async def set_leverage(self, symbol: str, leverage: int) -> bool:
        ...

    @abstractmethod
    async def get_open_orders(self, symbol: str | None = None) -> list[OrderResponse]:
        ...

    @abstractmethod
    def get_lot_filter(self, symbol: str) -> Any:
        ...

    @abstractmethod
    def get_exchange_name(self) -> str:
        ...

    # ── Shared utility (tidak abstract) ─────────────────────
    def round_quantity(self, qty: float, symbol: str) -> float:
        """
        Pembulatan standar matematika per lot_filter bursa agar terhindar dari margin truncation error.
        Mencoba floor terlebih dahulu, agar tak kelebihan saldo saat memotong 0.33333333333...
        """
        lf = self.get_lot_filter(symbol)
        step = getattr(lf, "step_size", 0.0001)
        precision = max(0, round(-math.log10(step)))
        # Menggunakan format floating point standard
        return round(math.floor(qty / step) * step, precision)

    def round_price(self, price: float, symbol: str) -> float:
        lf = self.get_lot_filter(symbol)
        tick = getattr(lf, "tick_size", 0.01)
        precision = max(0, round(-math.log10(tick)))
        return round(round(price / tick) * tick, precision)
