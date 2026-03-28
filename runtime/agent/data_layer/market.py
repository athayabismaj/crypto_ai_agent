"""
MarketDataFeed — REST data feed dengan in-memory caching.

Menyediakan data market via REST polling. Digunakan untuk:
- Initial data load saat startup
- Fallback saat WebSocket drop
- Data yang tidak tersedia via stream (funding, balance)

I/O di-stub via _request() — actual HTTP wiring deferred ke integration.
"""

import logging
from datetime import UTC, datetime

from runtime.agent.models import (  # type: ignore
    Balance,
    Candle,
    ExchangeInfo,
    FundingRate,
    LotFilter,
    Ticker,
)

logger = logging.getLogger(__name__)


class MarketDataFeed:
    """
    REST data feed dengan in-memory TTL cache.

    Constructor menerima rate_limiter (optional) yang akan di-acquire
    sebelum setiap request ke exchange.
    """

    def __init__(
        self,
        rate_limiter: object | None = None,
        default_symbol: str = "BTCUSDT",
    ) -> None:
        self._rate_limiter = rate_limiter
        self._default_symbol = default_symbol

        # Cache: {key: (expire_timestamp, data)}
        self._cache: dict[str, tuple[float, object]] = {}

    # ── Public API ─────────────────────────────────────────────

    async def get_candles(
        self,
        symbol: str = "",
        timeframe: str = "1h",
        limit: int = 200,
    ) -> list[Candle]:
        """
        Get candle OHLCV via REST.
        Cache TTL = 1 timeframe.
        """
        symbol = symbol or self._default_symbol
        cache_key = f"candles:{symbol}:{timeframe}"
        tf_s = self._tf_to_seconds(timeframe)

        cached = self._get_cache(cache_key)
        if cached is not None:
            return cached  # type: ignore[return-value]

        # Acquire rate limit
        await self._acquire("/api/v3/klines")

        # REST call (stub — override _request untuk koneksi live)
        raw = await self._request(
            "GET",
            "/api/v3/klines",
            {"symbol": symbol, "interval": timeframe, "limit": limit},
        )

        candles = self._parse_candles(raw, symbol, timeframe)
        self._set_cache(cache_key, candles, ttl_s=tf_s)
        return candles

    async def get_ticker(self, symbol: str = "") -> Ticker:
        """Get best bid/ask ticker. Cache TTL = 5s."""
        symbol = symbol or self._default_symbol
        cache_key = f"ticker:{symbol}"

        cached = self._get_cache(cache_key)
        if cached is not None:
            return cached  # type: ignore[return-value]

        await self._acquire("/api/v3/ticker/bookTicker")
        raw = await self._request("GET", "/api/v3/ticker/bookTicker", {"symbol": symbol})

        ticker = self._parse_ticker(raw, symbol)
        self._set_cache(cache_key, ticker, ttl_s=5.0)
        return ticker

    async def get_balance(self, asset: str = "USDT") -> Balance:
        """Get saldo aset. Cache TTL = 10s."""
        cache_key = f"balance:{asset}"

        cached = self._get_cache(cache_key)
        if cached is not None:
            return cached  # type: ignore[return-value]

        await self._acquire("/api/v3/account")
        raw = await self._request("GET", "/api/v3/account", {})

        balance = self._parse_balance(raw, asset)
        self._set_cache(cache_key, balance, ttl_s=10.0)
        return balance

    async def get_funding_rate(self, symbol: str = "") -> FundingRate:
        """Get funding rate futures. Cache TTL = 30s."""
        symbol = symbol or self._default_symbol
        cache_key = f"funding:{symbol}"

        cached = self._get_cache(cache_key)
        if cached is not None:
            return cached  # type: ignore[return-value]

        await self._acquire("/fapi/v1/premiumIndex")
        raw = await self._request("GET", "/fapi/v1/premiumIndex", {"symbol": symbol})

        fr = self._parse_funding_rate(raw, symbol)
        self._set_cache(cache_key, fr, ttl_s=30.0)
        return fr

    async def get_exchange_info(self, symbol: str = "") -> ExchangeInfo:
        """Get exchange info + lot filter. Cache TTL = 1 jam."""
        symbol = symbol or self._default_symbol
        cache_key = f"exchange_info:{symbol}"

        cached = self._get_cache(cache_key)
        if cached is not None:
            return cached  # type: ignore[return-value]

        await self._acquire("/api/v3/exchangeInfo")
        raw = await self._request("GET", "/api/v3/exchangeInfo", {"symbol": symbol})

        info = self._parse_exchange_info(raw, symbol)
        self._set_cache(cache_key, info, ttl_s=3600.0)
        return info

    async def get_server_time(self) -> datetime:
        """Get Binance server time. No cache."""
        await self._acquire("/api/v3/time")
        raw = await self._request("GET", "/api/v3/time", {})
        ts_ms = raw.get("serverTime", 0) if isinstance(raw, dict) else 0
        return datetime.fromtimestamp(ts_ms / 1000, tz=UTC)

    def invalidate_cache(self, key: str | None = None) -> None:
        """Hapus cache. None = hapus semua."""
        if key is None:
            self._cache.clear()
            logger.info("Cache cleared (all)")
        elif key in self._cache:
            del self._cache[key]  # type: ignore[arg-type]
            logger.info("Cache invalidated: %s", key)

    # ── Cache internal ─────────────────────────────────────────

    def _get_cache(self, key: str) -> object | None:
        """Get from cache if not expired."""
        if key not in self._cache:
            return None
        expire_ts, data = self._cache[key]
        if datetime.now(UTC).timestamp() >= expire_ts:
            del self._cache[key]  # type: ignore[arg-type]
            return None
        return data

    def _set_cache(self, key: str, data: object, ttl_s: float) -> None:
        """Set cache with TTL in seconds."""
        expire_ts = datetime.now(UTC).timestamp() + ttl_s
        self._cache[key] = (expire_ts, data)

    # ── Rate limiter integration ───────────────────────────────

    async def _acquire(self, endpoint: str) -> None:
        """Acquire rate limit slot if limiter is configured."""
        if self._rate_limiter is not None:
            acquire_fn = getattr(self._rate_limiter, "acquire", None)
            if acquire_fn:
                await acquire_fn(endpoint=endpoint)

    # ── REST stub — override untuk koneksi live ────────────────

    async def _request(
        self,
        method: str,
        endpoint: str,
        params: dict,
    ) -> dict | list:
        """
        HTTP request stub.

        Override di subclass atau inject mock untuk testing.
        Return format raw Binance JSON.
        """
        logger.debug("REST stub: %s %s %s", method, endpoint, params)
        return {}

    # ── Parsers ────────────────────────────────────────────────

    def _parse_candles(self, raw: dict | list, symbol: str, timeframe: str) -> list[Candle]:
        """Parse raw Binance klines ke list[Candle]."""
        if not isinstance(raw, list):
            return []

        candles: list[Candle] = []
        for item in raw:
            if not isinstance(item, list) or len(item) < 12:
                continue
            candles.append(
                Candle(
                    symbol=symbol,
                    timeframe=timeframe,
                    timestamp=datetime.fromtimestamp(item[0] / 1000, tz=UTC),
                    open=float(item[1]),
                    high=float(item[2]),
                    low=float(item[3]),
                    close=float(item[4]),
                    volume=float(item[5]),
                    is_closed=True,
                    source="rest",
                )
            )
        return candles

    def _parse_ticker(self, raw: dict | list, symbol: str) -> Ticker:
        """Parse raw Binance bookTicker ke Ticker."""
        if not isinstance(raw, dict):
            raw = {}
        bid = float(raw.get("bidPrice", 0))
        ask = float(raw.get("askPrice", 0))
        mid = (bid + ask) / 2 if (bid + ask) > 0 else 0.0
        spread = ask - bid
        spread_pct = (spread / mid * 100) if mid > 0 else 0.0
        return Ticker(
            symbol=symbol,
            bid=bid,
            ask=ask,
            last=mid,
            mid=mid,
            spread=spread,
            spread_pct=spread_pct,
        )

    def _parse_balance(self, raw: dict | list, asset: str) -> Balance:
        """Parse raw account response ke Balance."""
        if isinstance(raw, dict):
            balances = raw.get("balances", [])
            for b in balances:
                if isinstance(b, dict) and b.get("asset") == asset:
                    free = float(b.get("free", 0))
                    locked = float(b.get("locked", 0))
                    return Balance(
                        asset=asset,
                        free=free,
                        locked=locked,
                        total=free + locked,
                    )
        return Balance(asset=asset, free=0.0, locked=0.0, total=0.0)

    def _parse_funding_rate(self, raw: dict | list, symbol: str) -> FundingRate:
        """Parse raw premiumIndex ke FundingRate."""
        if not isinstance(raw, dict):
            raw = {}
        rate = float(raw.get("lastFundingRate", 0))
        next_ts = int(raw.get("nextFundingTime", 0))
        next_time = datetime.fromtimestamp(next_ts / 1000, tz=UTC) if next_ts else datetime.now(UTC)
        return FundingRate(
            symbol=symbol,
            rate=rate,
            next_time=next_time,
        )

    def _parse_exchange_info(self, raw: dict | list, symbol: str) -> ExchangeInfo:
        """Parse raw exchangeInfo ke ExchangeInfo."""
        if isinstance(raw, dict):
            for sym_info in raw.get("symbols", []):
                if isinstance(sym_info, dict) and sym_info.get("symbol") == symbol:
                    filters = {
                        f.get("filterType"): f
                        for f in sym_info.get("filters", [])
                        if isinstance(f, dict)
                    }
                    lot = filters.get("LOT_SIZE", {})
                    price = filters.get("PRICE_FILTER", {})
                    notional = filters.get("NOTIONAL", filters.get("MIN_NOTIONAL", {}))

                    return ExchangeInfo(
                        symbol=symbol,
                        status=sym_info.get("status", "UNKNOWN"),
                        lot_filter=LotFilter(
                            symbol=symbol,
                            min_qty=float(lot.get("minQty", 0)),
                            max_qty=float(lot.get("maxQty", 0)),
                            step_size=float(lot.get("stepSize", 0)),
                            min_notional=float(notional.get("minNotional", 0)),
                            tick_size=float(price.get("tickSize", 0)),
                        ),
                        is_spot=sym_info.get("isSpotTradingAllowed", True),
                    )

        # Fallback
        return ExchangeInfo(
            symbol=symbol,
            status="UNKNOWN",
            lot_filter=LotFilter(
                symbol=symbol,
                min_qty=0,
                max_qty=0,
                step_size=0,
                min_notional=0,
                tick_size=0,
            ),
        )

    @staticmethod
    def _tf_to_seconds(tf: str) -> float:
        """Convert timeframe string ke detik."""
        multipliers = {"m": 60, "h": 3600, "d": 86400, "w": 604800}
        if not tf:
            return 60
        unit = tf[-1]
        try:
            value = int(tf[:-1])  # type: ignore[index]
        except ValueError:
            return 60
        return float(value * multipliers.get(unit, 60))
