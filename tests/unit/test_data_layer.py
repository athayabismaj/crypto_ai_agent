"""Unit tests untuk runtime.agent.data_layer."""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest  # type: ignore

from runtime.agent.data_layer import (  # type: ignore
    AnomalyDetector,
    BinanceRateLimiter,
    DataValidator,
    MarketAPI,
    OrderbookStore,
    WebSocketClient,
)
from runtime.agent.models import (  # type: ignore
    Candle,
    Orderbook,
    OrderbookLevel,
    Ticker,
)

# ================================================================
#  Helper — buat test data
# ================================================================


def _make_candle(
    close: float = 50000.0,
    open_: float = 49800.0,
    high: float = 50200.0,
    low: float = 49700.0,
    volume: float = 100.0,
    symbol: str = "BTCUSDT",
    tf: str = "1h",
    ts: datetime | None = None,
) -> Candle:
    return Candle(
        symbol=symbol,
        timeframe=tf,
        timestamp=ts or datetime.now(UTC),
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=volume,
        is_closed=True,
    )


def _make_ticker(
    bid: float = 50000.0,
    ask: float = 50010.0,
    symbol: str = "BTCUSDT",
    ts: datetime | None = None,
) -> Ticker:
    mid = (bid + ask) / 2
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
        timestamp=ts or datetime.now(UTC),
    )


def _make_orderbook(
    bids: list[tuple[float, float]] | None = None,
    asks: list[tuple[float, float]] | None = None,
    symbol: str = "BTCUSDT",
    ts: datetime | None = None,
) -> Orderbook:
    bids = bids or [(50000.0, 1.0), (49990.0, 2.0)]
    asks = asks or [(50010.0, 1.5), (50020.0, 3.0)]
    return Orderbook(
        symbol=symbol,
        bids=[OrderbookLevel(price=p, quantity=q) for p, q in bids],
        asks=[OrderbookLevel(price=p, quantity=q) for p, q in asks],
        timestamp=ts or datetime.now(UTC),
    )


# ================================================================
#  BinanceRateLimiter Tests
# ================================================================


class TestRateLimiter:
    def test_acquire_tracks_weight(self):
        rl = BinanceRateLimiter(weight_limit=1200, safe_ratio=0.80)
        asyncio.run(rl.acquire(endpoint="/api/v3/klines", weight=2))
        status = rl.get_current_usage()
        assert status.weight_used == 2

    def test_is_near_limit(self):
        rl = BinanceRateLimiter(weight_limit=100, safe_ratio=0.80)
        rl._weight_used = 85
        assert rl.is_near_limit(threshold=0.80) is True

    def test_not_near_limit(self):
        rl = BinanceRateLimiter(weight_limit=100)
        rl._weight_used = 50
        assert rl.is_near_limit(threshold=0.80) is False

    def test_update_from_headers(self):
        rl = BinanceRateLimiter()
        rl.update_from_headers(
            {
                "X-MBX-USED-WEIGHT-1M": "500",
                "X-MBX-ORDER-COUNT-10S": "10",
                "X-MBX-ORDER-COUNT-1D": "1000",
            }
        )
        assert rl._weight_used == 500
        assert rl._orders_10s == 10
        assert rl._orders_1d == 1000

    def test_status_output(self):
        rl = BinanceRateLimiter(weight_limit=1200)
        rl._weight_used = 600
        status = rl.get_current_usage()
        assert status.weight_pct == 50.0
        assert status.weight_limit == 1200


# ================================================================
#  DataValidator Tests
# ================================================================


class TestValidator:
    def test_valid_candle(self):
        v = DataValidator()
        c = _make_candle()
        result = v.validate_candle(c)
        assert result.valid is True
        assert result.errors == []

    def test_invalid_ohlc_logic(self):
        v = DataValidator()
        # high < close → invalid
        c = _make_candle(close=51000.0, high=50200.0)
        result = v.validate_candle(c)
        assert result.valid is False
        assert any("OHLC_LOGIC" in e for e in result.errors)

    def test_negative_price(self):
        v = DataValidator()
        c = _make_candle(close=-1.0)
        result = v.validate_candle(c)
        assert result.valid is False

    def test_valid_ticker(self):
        v = DataValidator()
        t = _make_ticker()
        result = v.validate_ticker(t)
        assert result.valid is True

    def test_crossed_book_ticker(self):
        v = DataValidator()
        t = _make_ticker(bid=50010.0, ask=50000.0)  # crossed
        result = v.validate_ticker(t)
        assert result.valid is False
        assert any("CROSSED_BOOK" in e for e in result.errors)

    def test_valid_orderbook(self):
        v = DataValidator()
        ob = _make_orderbook()
        result = v.validate_orderbook(ob)
        assert result.valid is True

    def test_empty_orderbook(self):
        v = DataValidator()
        ob = Orderbook(
            symbol="BTCUSDT",
            bids=[],
            asks=[],
            timestamp=datetime.now(UTC),
        )
        result = v.validate_orderbook(ob)
        assert result.valid is False

    def test_validation_stats(self):
        v = DataValidator()
        v.validate_candle(_make_candle())
        v.validate_candle(_make_candle(close=-1.0))
        stats = v.get_validation_stats()
        assert stats["candle_total"] == 2.0
        assert stats["candle_reject_rate_pct"] == 50.0

    def test_timestamp_gap_warning(self):
        v = DataValidator(max_gap_multiplier=2)
        now = datetime.now(UTC)
        c1 = _make_candle(ts=now - timedelta(hours=5))
        c2 = _make_candle(ts=now)
        result = v.validate_candle(c2, prev_candle=c1)
        # Gap = 5h vs max 2×1h = 2h → warning
        assert any("TIMESTAMP_GAP" in w for w in result.warnings)


# ================================================================
#  AnomalyDetector Tests
# ================================================================


class TestAnomalyDetector:
    def test_price_spike(self):
        ad = AnomalyDetector(price_spike_threshold=0.03)
        prev = _make_candle(close=50000.0)
        # 10% spike
        current = _make_candle(close=55000.0)
        reports = ad.check_candle(current, [prev])
        assert len(reports) > 0
        assert reports[0].anomaly_id == "PRICE_SPIKE"

    def test_no_spike(self):
        ad = AnomalyDetector(price_spike_threshold=0.05)
        prev = _make_candle(close=50000.0)
        current = _make_candle(close=50100.0)  # tiny move
        reports = ad.check_candle(current, [prev])
        assert len(reports) == 0

    def test_volume_zero(self):
        ad = AnomalyDetector()
        c = _make_candle(volume=0)
        reports = ad.check_candle(c)
        assert any(r.anomaly_id == "VOLUME_ZERO" for r in reports)

    def test_crossed_book_ticker(self):
        ad = AnomalyDetector()
        t = _make_ticker(bid=50010.0, ask=50000.0)
        reports = ad.check_ticker(t)
        assert any(r.anomaly_id == "CROSSED_BOOK" for r in reports)

    def test_wide_spread(self):
        ad = AnomalyDetector(wide_spread_pct=0.5)
        t = _make_ticker(bid=50000.0, ask=50500.0)  # 1% spread
        reports = ad.check_ticker(t)
        assert any(r.anomaly_id == "WIDE_SPREAD" for r in reports)

    def test_is_safe_to_trade_clean(self):
        ad = AnomalyDetector()
        safe, reports = ad.is_safe_to_trade("BTCUSDT")
        assert safe is True
        assert reports == []

    def test_is_safe_to_trade_blocked(self):
        ad = AnomalyDetector()
        t = _make_ticker(bid=50010.0, ask=50000.0)
        ad.check_ticker(t)  # adds CROSSED_BOOK (CRITICAL)
        safe, reports = ad.is_safe_to_trade("BTCUSDT")
        assert safe is False

    def test_resolve_anomaly(self):
        ad = AnomalyDetector()
        c = _make_candle(volume=0)
        ad.check_candle(c)
        assert len(ad.get_active_anomalies("BTCUSDT")) > 0
        ad.resolve("VOLUME_ZERO", "BTCUSDT")
        assert len(ad.get_active_anomalies("BTCUSDT")) == 0


# ================================================================
#  MarketAPI Tests
# ================================================================


class TestMarketAPI:
    def test_cache_hit(self):
        mdf = MarketAPI()
        mdf._set_cache("test", [1, 2, 3], ttl_s=60.0)
        assert mdf._get_cache("test") == [1, 2, 3]

    def test_cache_miss(self):
        mdf = MarketAPI()
        assert mdf._get_cache("nonexistent") is None

    def test_cache_expired(self):
        mdf = MarketAPI()
        # Set with TTL in the past
        mdf._cache["test"] = (0.0, "old_data")
        assert mdf._get_cache("test") is None

    def test_invalidate_all(self):
        mdf = MarketAPI()
        mdf._set_cache("a", 1, ttl_s=60)
        mdf._set_cache("b", 2, ttl_s=60)
        mdf.invalidate_cache()
        assert len(mdf._cache) == 0

    def test_invalidate_key(self):
        mdf = MarketAPI()
        mdf._set_cache("a", 1, ttl_s=60)
        mdf._set_cache("b", 2, ttl_s=60)
        mdf.invalidate_cache("a")
        assert mdf._get_cache("a") is None
        assert mdf._get_cache("b") == 2

    def test_parse_candles(self):
        mdf = MarketAPI()
        raw = [
            [
                1672531200000,
                "50000",
                "50500",
                "49800",
                "50200",
                "100",
                1672534800000,
                "5000000",
                500,
                "50",
                "2500000",
                "0",
            ],
        ]
        candles = mdf._parse_candles(raw, "BTCUSDT", "1h")
        assert len(candles) == 1
        assert candles[0].close == 50200.0
        assert candles[0].symbol == "BTCUSDT"

    def test_parse_ticker(self):
        mdf = MarketAPI()
        raw = {"bidPrice": "50000.0", "askPrice": "50010.0"}
        ticker = mdf._parse_ticker(raw, "BTCUSDT")
        assert ticker.bid == 50000.0
        assert ticker.ask == 50010.0

    def test_parse_balance(self):
        mdf = MarketAPI()
        raw = {
            "balances": [
                {"asset": "USDT", "free": "1000.0", "locked": "200.0"},
                {"asset": "BTC", "free": "0.5", "locked": "0.0"},
            ]
        }
        bal = mdf._parse_balance(raw, "USDT")
        assert bal.free == 1000.0
        assert bal.total == 1200.0


# ================================================================
#  OrderbookManager Tests
# ================================================================


class TestOrderbookManager:
    def test_set_and_get_snapshot(self):
        om = OrderbookManager()
        ob = _make_orderbook()
        om.set_snapshot(ob)
        got = om.get_snapshot("BTCUSDT")
        assert got is not None
        assert got.symbol == "BTCUSDT"

    def test_imbalance(self):
        om = OrderbookManager()
        # All volume on bid side
        ob = _make_orderbook(
            bids=[(50000.0, 10.0)],
            asks=[(50010.0, 0.1)],
        )
        om.set_snapshot(ob)
        imb = om.get_imbalance("BTCUSDT", depth=5)
        assert imb > 0.8  # strong bid imbalance

    def test_imbalance_balanced(self):
        om = OrderbookManager()
        ob = _make_orderbook(
            bids=[(50000.0, 5.0)],
            asks=[(50010.0, 5.0)],
        )
        om.set_snapshot(ob)
        imb = om.get_imbalance("BTCUSDT")
        assert imb == pytest.approx(0.0)

    def test_estimate_fill_price_buy(self):
        om = OrderbookManager()
        ob = _make_orderbook(
            asks=[
                (50010.0, 1.0),  # fill 1 BTC at 50010
                (50020.0, 1.0),  # fill 1 BTC at 50020
            ],
        )
        om.set_snapshot(ob)
        avg_price = om.estimate_fill_price("BTCUSDT", 1.5, "BUY")
        # 1.0 * 50010 + 0.5 * 50020 = 50010 + 25010 = 75020 / 1.5
        expected = (1.0 * 50010 + 0.5 * 50020) / 1.5
        assert avg_price == pytest.approx(expected)

    def test_update_incremental(self):
        om = OrderbookManager()
        ob = _make_orderbook(
            bids=[(50000.0, 1.0)],
            asks=[(50010.0, 1.0)],
        )
        om.set_snapshot(ob)

        # Update: change bid qty, add new ask
        om.update(
            "BTCUSDT",
            bid_updates=[(50000.0, 2.0)],  # qty → 2
            ask_updates=[(50015.0, 0.5)],  # new level
            last_update_id=100,
        )
        updated = om.get_snapshot("BTCUSDT")
        assert updated is not None
        assert updated.bids[0].quantity == 2.0
        assert len(updated.asks) == 2

    def test_update_remove_level(self):
        om = OrderbookManager()
        ob = _make_orderbook(
            bids=[(50000.0, 1.0), (49990.0, 2.0)],
            asks=[(50010.0, 1.0)],
        )
        om.set_snapshot(ob)

        # Remove a bid level (qty=0)
        om.update(
            "BTCUSDT",
            bid_updates=[(49990.0, 0)],
            ask_updates=[],
        )
        updated = om.get_snapshot("BTCUSDT")
        assert updated is not None
        assert len(updated.bids) == 1

    def test_is_fresh(self):
        om = OrderbookManager()
        ob = _make_orderbook(ts=datetime.now(UTC))
        om.set_snapshot(ob)
        assert om.is_fresh("BTCUSDT", max_age_s=5.0) is True

    def test_no_snapshot(self):
        om = OrderbookManager()
        assert om.get_snapshot("ETHUSDT") is None
        assert om.is_fresh("ETHUSDT") is False
        assert om.get_imbalance("ETHUSDT") == 0.0

    def test_bid_ask_depth(self):
        om = OrderbookManager()
        ob = _make_orderbook(
            bids=[(50000.0, 1.0), (49900.0, 2.0), (49500.0, 5.0)],
            asks=[(50010.0, 1.0), (50100.0, 2.0), (50500.0, 5.0)],
        )
        om.set_snapshot(ob)
        # 1% of 50000 = 500, so threshold = 49500
        # All 3 bids within range
        bid_depth = om.get_bid_depth("BTCUSDT", pct=0.01)
        assert bid_depth == 8.0  # 1 + 2 + 5


# ================================================================
#  WebSocketClient Tests
# ================================================================


class TestWebSocketClient:
    def test_subscribe_candle(self):
        ws = WebSocketClient()

        async def dummy(c: Candle) -> None:
            pass

        ws.subscribe_candle("BTCUSDT", "1h", dummy)
        assert "btcusdt@kline_1h" in ws._streams
        assert "btcusdt@kline_1h" in ws._candle_callbacks

    def test_subscribe_ticker(self):
        ws = WebSocketClient()

        async def dummy(t: Ticker) -> None:
            pass

        ws.subscribe_ticker("BTCUSDT", dummy)
        assert "btcusdt@bookTicker" in ws._streams

    def test_connect_disconnect(self):
        ws = WebSocketClient()

        async def dummy(c: Candle) -> None:
            pass

        ws.subscribe_candle("BTCUSDT", "1h", dummy)

        async def run() -> None:
            await ws.connect()
            assert ws.is_connected("BTCUSDT") is True
            await ws.disconnect()
            assert ws._running is False

        asyncio.run(run())

    def test_connection_stats(self):
        ws = WebSocketClient()

        async def dummy(c: Candle) -> None:
            pass

        ws.subscribe_candle("BTCUSDT", "1h", dummy)

        async def run() -> None:
            await ws.connect()
            stats = ws.get_connection_stats()
            assert "btcusdt@kline_1h" in stats
            assert stats["btcusdt@kline_1h"]["state"] == "connected"
            await ws.disconnect()

        asyncio.run(run())

    def test_record_message(self):
        ws = WebSocketClient()
        ws._ensure_stream("test_stream")
        ws._streams["test_stream"].state = WsState.CONNECTED
        ws._record_message("test_stream")
        assert ws._streams["test_stream"].message_count == 1
        assert ws._streams["test_stream"].last_message_at is not None


from runtime.agent.data_layer.websocket_client import WsState  # type: ignore  # noqa: E402
