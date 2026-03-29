"""
Layer Data: Market
Klien REST asinkron untuk polling data historis (Candle, Ticker) dan detail bursa.
Memiliki sistem Caching In-Memory terpusat dengan Auto-TTL.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from runtime.agent.core.config_schema import AgentConfig  # type: ignore
from runtime.agent.data_layer.rate_limiter import BinanceRateLimiter

logger = logging.getLogger(__name__)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class Candle:
    symbol: str
    timeframe: str
    timestamp: datetime  # UTC, awal candle
    open: float
    high: float
    low: float
    close: float
    volume: float
    is_closed: bool = False
    source: str = "rest"


@dataclass
class Ticker:
    symbol: str
    bid: float
    ask: float
    last: float
    mid: float
    spread: float
    spread_pct: float
    volume_24h: float
    change_24h: float
    timestamp: datetime = field(default_factory=utcnow)


@dataclass
class Balance:
    asset: str
    free: float
    locked: float
    total: float
    usd_value: float = 0.0
    timestamp: datetime = field(default_factory=utcnow)


@dataclass
class FundingRate:
    symbol: str
    rate: float
    next_time: datetime
    timestamp: datetime = field(default_factory=utcnow)


@dataclass
class LotFilter:
    symbol: str
    min_qty: float
    max_qty: float
    step_size: float
    min_notional: float
    tick_size: float


@dataclass
class PriceFilter:
    min_price: float
    max_price: float
    tick_size: float


@dataclass
class ExchangeInfo:
    symbol: str
    status: str
    lot_filter: LotFilter
    price_filter: PriceFilter
    is_spot: bool
    is_futures: bool


class MarketAPI:
    """Implementasi klien Binance Async HTTP yang difilter oleh RateLimiter."""

    def __init__(self, config: AgentConfig, rate_limiter: BinanceRateLimiter) -> None:
        self.config = config
        self.rate_limiter = rate_limiter

        # In-Memory Cache [tipe_data][symbol] = (data, expiration_timestamp)
        self._cache: dict[str, dict[str, tuple[Any, float]]] = {
            "ticker": {},
            "balance": {},
            "position": {},
            "exchange_info": {},
        }

    def _get_from_cache(self, category: str, key: str) -> Any:
        entry = self._cache[category].get(key)
        if entry is None:
            return None
        data, exp = entry
        if exp < utcnow().timestamp():
            del self._cache[category][key]
            return None
        return data

    def _set_cache(self, category: str, key: str, data: Any, ttl_seconds: float) -> None:
        exp = utcnow().timestamp() + ttl_seconds
        self._cache[category][key] = (data, exp)

    async def _async_get(
        self, endpoint: str, params: dict | None = None, weight: int | None = None
    ) -> Any:
        """Helper untuk melakukan pemanggilan HTTP Async."""
        # TODO: Implement aiohttp.ClientSession
        await self.rate_limiter.acquire(endpoint, weight=weight)
        # return await self._http_session.get(...)
        # Untuk saat ini (MOCK) mengembalikan data simulasi
        pass

    async def get_candles(self, symbol: str, tf: str, limit: int = 200) -> list[Candle]:
        """Ambil list klines dengan rate limit 2."""
        endpoint = "/api/v3/klines"
        await self.rate_limiter.acquire(endpoint)

        # MOCK IMPLEMENTATION
        now = utcnow()
        out = []
        for i in range(limit):
            out.append(
                Candle(
                    symbol=symbol,
                    timeframe=tf,
                    timestamp=now,
                    open=50000.0,
                    high=50010.0,
                    low=49990.0,
                    close=50005.0,
                    volume=1.5,
                    is_closed=True,
                    source="rest",
                )
            )
        return out

    async def get_ticker(self, symbol: str) -> Ticker:
        """GET ticker (5s Cache)."""
        cached = self._get_from_cache("ticker", symbol)
        if cached:
            return cached

        endpoint = "/api/v3/ticker/bookTicker"
        await self.rate_limiter.acquire(endpoint)

        # MOCK
        t = Ticker(
            symbol=symbol,
            bid=50000.0,
            ask=50010.0,
            last=50005.0,
            mid=50005.0,
            spread=10.0,
            spread_pct=(10.0 / 50005.0) * 100,
            volume_24h=1200.0,
            change_24h=1.5,
        )
        self._set_cache("ticker", symbol, t, 5.0)
        return t

    async def get_balance(self, asset: str = "USDT") -> Balance:
        """GET balance (10s Cache)."""
        cached = self._get_from_cache("balance", asset)
        if cached:
            return cached

        endpoint = "/api/v3/account"
        await self.rate_limiter.acquire(endpoint)

        # MOCK
        b = Balance(asset=asset, free=1000.0, locked=0.0, total=1000.0, usd_value=1000.0)
        self._set_cache("balance", asset, b, 10.0)
        return b

    async def get_exchange_info(self, symbol: str) -> ExchangeInfo:
        """GET exchange info (1H Cache)."""
        cached = self._get_from_cache("exchange_info", symbol)
        if cached:
            return cached

        endpoint = "/api/v3/exchangeInfo"
        await self.rate_limiter.acquire(endpoint)

        # MOCK
        lf = LotFilter(symbol, 0.001, 1000.0, 0.001, 10.0, 0.1)
        pf = PriceFilter(0.1, 1000000.0, 0.1)
        info = ExchangeInfo(symbol, "TRADING", lf, pf, True, False)

        self._set_cache("exchange_info", symbol, info, 3600.0)
        return info
