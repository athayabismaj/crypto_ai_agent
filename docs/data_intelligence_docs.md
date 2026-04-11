# crypto_ai_agent — Data Layer & Intelligence Layer Documentation

*market · orderbook · websocket · validator · anomaly_detector · rate_limiter*
*regime · volatility · market_state · latency_guard*

**Version 1.0** | Reference: agent_core_docs.md · runtime_layer_docs.md

---

## 1. Overview — Two Layers, One Purpose

The Data Layer and Intelligence Layer work in tandem. The Data Layer collects and validates raw data from the exchange. The Intelligence Layer transforms that data into market understanding consumable by the Strategy Layer.

| Aspect | Data Layer | Intelligence Layer |
|---|---|---|
| Input | Exchange API, WebSocket stream | Validated candles, ticker, orderbook |
| Output | Validated Candle, Ticker, Orderbook | MarketState — ready-to-use aggregate |
| Can fail? | Yes — connection drop, corrupt data | More tolerant — uses previous state |
| I/O? | Yes — network, WebSocket, REST | No — pure in-memory computation |
| Thread-safe? | No — single async task per stream | Yes — can be called from anywhere |
| Called by | main_loop, scheduler | main_loop (every tick) |

---

### 1.1 Full Data Flow

```
Exchange (REST / WebSocket)
    │
    ▼
data_layer/websocket_client.py   ← raw candle stream
data_layer/market.py             ← REST polling (fallback + enrichment)
data_layer/orderbook.py          ← depth snapshot & stream
    │
    ▼
data_layer/validator.py          ← check format, range, timestamp
data_layer/anomaly_detector.py   ← detect spike, stale, crossed book
data_layer/rate_limiter.py       ← throttle before every request
    │
    ▼  (Candle, Ticker, Orderbook — cleaned)
    │
intelligence_layer/regime.py         ← trending / sideways / volatile
intelligence_layer/volatility.py     ← ATR, realized vol, percentile
intelligence_layer/market_state.py   ← aggregate into MarketState
intelligence_layer/latency_guard.py  ← protect during high latency
    │
    ▼  (MarketState)
    │
strategy_layer/   ← consume MarketState
```

---

## PART A — Data Layer

---

## 2. market.py — REST Data Feed

Accesses market data via REST API. Used for: initial data load at startup, fallback when WebSocket drops, polling data not available via stream (funding rate, open interest, account balance).

### 2.1 Output Dataclasses

```python
@dataclass
class Candle:
    symbol:     str
    timeframe:  str
    timestamp:  datetime    # UTC, candle open time
    open:       float
    high:       float
    low:        float
    close:      float
    volume:     float
    is_closed:  bool        # False if candle is still in progress
    source:     str         # 'websocket' | 'rest'

@dataclass
class Ticker:
    symbol:      str
    bid:         float
    ask:         float
    last:        float
    mid:         float      # (bid + ask) / 2
    spread:      float      # ask - bid
    spread_pct:  float      # spread / mid * 100
    volume_24h:  float
    change_24h:  float      # 24h pct change
    timestamp:   datetime

@dataclass
class Balance:
    asset:     str
    free:      float        # available for trading
    locked:    float        # locked in open orders
    total:     float        # free + locked
    usd_value: float        # estimated USD value
    timestamp: datetime

@dataclass
class FundingRate:
    symbol:    str
    rate:      float        # e.g. 0.0001 = 0.01%
    next_time: datetime     # when the next funding occurs
    timestamp: datetime
```

---

### 2.2 Public Interface

| Function | Parameters | Return | Cache TTL | Binance Endpoint |
|---|---|---|---|---|
| `get_candles()` | symbol, tf, limit=200 | list[Candle] | 1 tf | /api/v3/klines |
| `get_ticker()` | symbol: str | Ticker | 5s | /api/v3/ticker/bookTicker |
| `get_balance()` | asset: str='USDT' | Balance | 10s | /api/v3/account |
| `get_open_orders()` | symbol: str=None | list[Order] | No cache | /api/v3/openOrders |
| `get_position()` | symbol: str | Position\|None | 5s | /fapi/v2/positionRisk |
| `get_funding_rate()` | symbol: str | FundingRate | 30s | /fapi/v1/premiumIndex |
| `get_exchange_info()` | symbol: str=None | ExchangeInfo | 1 hour | /api/v3/exchangeInfo |
| `get_server_time()` | — | datetime | No cache | /api/v3/time |

---

### 2.3 ExchangeInfo & LotFilter

```python
@dataclass
class LotFilter:
    symbol:       str
    min_qty:      float    # minimum order quantity
    max_qty:      float    # maximum order quantity
    step_size:    float    # valid quantity increment
    min_notional: float    # minimum value in USDT
    tick_size:    float    # price precision (for limit orders)

@dataclass
class ExchangeInfo:
    symbol:       str
    status:       str      # 'TRADING' | 'BREAK' | 'HALT'
    lot_filter:   LotFilter
    price_filter: PriceFilter
    is_spot:      bool
    is_futures:   bool

# Usage example:
# info     = await market.get_exchange_info('BTCUSDT')
# qty      = round_qty(raw_qty, info.lot_filter.step_size)  # via helpers.py
# notional = qty * price
# assert notional >= info.lot_filter.min_notional
```

---

### 2.4 Caching Policy

| Data | Reason for Cache | Invalidation |
|---|---|---|
| Ticker (5s) | Needed every tick — REST polling is expensive | Auto TTL; WebSocket update also invalidates |
| Balance (10s) | Does not change except on trades | Auto TTL; trade close also invalidates |
| Position (5s) | Real-time update via user stream WebSocket | WebSocket positionUpdate event invalidates |
| ExchangeInfo (1 hour) | Rarely changes — only on new listings | Manual invalidation via gateway endpoint |
| Open Orders (no cache) | Must be real-time — status can change anytime | — |

> **CACHE RULE:** Cache is stored in memory (dict with timestamp). Does not use Redis or external cache — avoids additional dependencies and latency. On cache miss, hits the REST API directly.

---

## 3. orderbook.py — Depth Data

Manages orderbook depth: periodic snapshots via REST and real-time updates via WebSocket. Used by the intelligence layer for spread analysis and by the execution layer for slippage estimation.

### 3.1 Dataclasses

```python
@dataclass
class OrderbookLevel:
    price:    float
    quantity: float

@dataclass
class Orderbook:
    symbol:         str
    bids:           list[OrderbookLevel]    # sorted DESC (highest price first)
    asks:           list[OrderbookLevel]    # sorted ASC (lowest price first)
    timestamp:      datetime
    last_update_id: int                     # Binance update sequence ID

    @property
    def best_bid(self) -> float:
        return self.bids[0].price if self.bids else 0.0

    @property
    def best_ask(self) -> float:
        return self.asks[0].price if self.asks else 0.0

    @property
    def mid_price(self) -> float:
        return (self.best_bid + self.best_ask) / 2

    @property
    def spread_pct(self) -> float:
        if self.mid_price == 0: return 0.0
        return (self.best_ask - self.best_bid) / self.mid_price * 100

    def get_bid_depth(self, pct: float = 0.01) -> float:
        """Total bid volume within pct% range of best bid."""

    def get_ask_depth(self, pct: float = 0.01) -> float:
        """Total ask volume within pct% range of best ask."""

    def estimate_fill_price(self, qty: float, side: str) -> float:
        """Walk through bids/asks to estimate average fill price."""
```

---

### 3.2 Public Interface

| Function | Parameters | Return | Description |
|---|---|---|---|
| `get_snapshot()` | symbol: str, depth: int=20 | Orderbook | REST call — for initial load |
| `get_latest()` | symbol: str | Orderbook | Return from in-memory store (WebSocket updated) |
| `subscribe()` | symbol: str, depth: int=20 | asyncio.Task | Start WebSocket stream, update in-memory |
| `is_fresh()` | symbol: str, max_age_s: int=5 | bool | Check if orderbook is still fresh |
| `get_imbalance()` | symbol: str, depth: int=5 | float -1 to 1 | Bid vs ask volume imbalance |

---

### 3.3 Orderbook Imbalance

```python
def get_imbalance(self, depth: int = 5) -> float:
    """
    Measures buying vs selling pressure at the top N levels.

    Formula:
        bid_vol   = sum(bids[0:depth].quantity)
        ask_vol   = sum(asks[0:depth].quantity)
        imbalance = (bid_vol - ask_vol) / (bid_vol + ask_vol)

    Interpretation:
        +1.0 = all volume on bid side (strong buying pressure)
         0.0 = balanced
        -1.0 = all volume on ask side (strong selling pressure)

    Thresholds for intelligence_layer:
        imbalance > +0.3 → additional bullish signal
        imbalance < -0.3 → additional bearish signal
    """
```

---

### 3.4 WebSocket Update Handling

```python
# Binance WebSocket diff depth stream:
# 1. Take initial snapshot via REST (depth=1000 for accuracy)
# 2. Buffer all incoming WebSocket events
# 3. Drop events with lastUpdateId <= snapshot.lastUpdateId
# 4. Apply events where U <= snapshot.lastUpdateId + 1 <= u
# 5. Update local orderbook:
#    - Quantity = 0 → remove level
#    - Quantity > 0 → update/add level
# 6. Re-sort: bids DESC, asks ASC
#
# If there is a gap in sequence ID → reset by taking a new snapshot
# This is called 'orderbook recovery' and must be silent (no user alert)
```

---

## 4. websocket_client.py — Real-time Stream

The single entry point for all real-time data. Manages multiple WebSocket connections, auto-reconnect, and data distribution to subscribers.

### 4.1 Managed Streams

| Stream | Binance URI | Data | Callback Destination |
|---|---|---|---|
| Kline/Candle | `wss://stream.../kline_{tf}` | OHLCV per candle | market.py internal buffer |
| Book Ticker | `wss://stream.../bookTicker` | Best bid/ask real-time | ticker cache |
| Diff Depth | `wss://stream.../depth@100ms` | Orderbook incremental update | orderbook.py store |
| User Data Stream | `wss://stream.../userData` | Order fills, balance update, position update | sync layer callbacks |

---

### 4.2 Public Interface

```python
class WebSocketClient:

    async def connect(self) -> None:
        """Create all required connections per config symbols."""

    async def disconnect(self) -> None:
        """Close all connections gracefully."""

    def subscribe_candle(
        self,
        symbol:   str,
        tf:       str,
        callback: Callable[[Candle], Awaitable[None]],
    ) -> None:
        """Register callback for candle stream."""

    def subscribe_ticker(
        self,
        symbol:   str,
        callback: Callable[[Ticker], Awaitable[None]],
    ) -> None:

    def subscribe_orderbook(
        self,
        symbol:   str,
        callback: Callable[[Orderbook], Awaitable[None]],
    ) -> None:

    def subscribe_user_stream(
        self,
        on_order_update:   Callable,
        on_balance_update: Callable,
        on_position_update:Callable,
    ) -> None:

    def is_connected(self, symbol: str = None) -> bool:
        """Check connection status. None = check all connections."""

    def get_connection_stats(self) -> ConnectionStats:
        """Return latency, message count, uptime per stream."""
```

---

### 4.3 Reconnect Logic — State Machine

| State | Entry Condition | Action | Transitions To |
|---|---|---|---|
| **CONNECTED** | Connection successful | Process messages normally | DISCONNECTED |
| **DISCONNECTED** | Connection drop / timeout | Stop processing, start reconnect timer | RECONNECTING |
| **RECONNECTING** | Reconnect timer elapsed | Try reconnect with exponential backoff | CONNECTED / FAILED |
| **FAILED** | Max retry (10x) exhausted | Critical alert, publish SYSTEM_ERROR to event_bus | RECONNECTING (after 5 min) |
| **STALE** | No messages > MAX_STALE_S (default 30s) | Force disconnect → DISCONNECTED | DISCONNECTED |

---

### 4.4 Listen Key Management (User Stream)

```python
# Binance User Data Stream uses a 'listen key' that expires every 60 minutes.
# The system must refresh it periodically.

class ListenKeyManager:

    REFRESH_INTERVAL = timedelta(minutes=30)    # refresh before expiry

    async def get_or_create(self) -> str:
        """Create a new listen key via POST /api/v3/userDataStream."""

    async def refresh(self, listen_key: str) -> bool:
        """Extend listen key via PUT /api/v3/userDataStream."""

    async def delete(self, listen_key: str) -> bool:
        """Delete listen key on shutdown."""

    async def start_auto_refresh(self) -> asyncio.Task:
        """
        Background task that refreshes the listen key every REFRESH_INTERVAL.

        If refresh fails 3 consecutive times:
        → Create a new listen key
        → Reconnect WebSocket with the new key
        → Send warning alert (not critical)
        """
```

---

### 4.5 Config Keys — WebSocket

| Key | Default | Valid Range | Description |
|---|---|---|---|
| `WS_MAX_RECONNECT` | 10 | 3–50 | Max retries before stream is declared FAILED |
| `WS_RECONNECT_BASE_S` | 1 | 0.5–5 | Base delay for exponential backoff (seconds) |
| `WS_RECONNECT_MAX_S` | 60 | 30–300 | Maximum backoff delay |
| `WS_STALE_TIMEOUT_S` | 30 | 10–120 | Seconds without a message → considered stale |
| `WS_PING_INTERVAL_S` | 20 | 10–60 | Ping interval for keep-alive |
| `WS_MAX_QUEUE_SIZE` | 500 | 100–2000 | Buffer queue size per stream |

---

## 5. validator.py — Incoming Data Validation

The first gate before data enters the system. The validator ensures every Candle, Ticker, and Orderbook processed meets minimum integrity requirements.

### 5.1 Candle Validation Rules

| Rule | Check | Action on Failure | Log Level |
|---|---|---|---|
| OHLC Logic | high >= max(open, close) AND low <= min(open, close) | Reject candle | ERROR |
| Timestamp order | candle.timestamp > prev_candle.timestamp | Reject candle | WARNING |
| Timestamp gap | gap <= expected_tf_seconds × MAX_GAP_MULTIPLIER | Log warning, still use | WARNING |
| Positive prices | open > 0 AND high > 0 AND low > 0 AND close > 0 | Reject candle | ERROR |
| Non-negative volume | volume >= 0 | Reject candle | ERROR |
| Candle closed | is_closed = True before strategy processing | Buffer, wait for confirmation | DEBUG |
| Symbol match | candle.symbol == expected_symbol | Reject candle | ERROR |
| Timeframe match | candle.timeframe == expected_tf | Reject candle | ERROR |

---

### 5.2 Ticker Validation Rules

| Rule | Check | Action on Failure |
|---|---|---|
| Positive Bid/Ask | bid > 0 AND ask > 0 | Reject ticker, use previous cache |
| Bid < Ask | bid < ask (normal book) | Reject — crossed book, CRITICAL alert |
| Reasonable spread | spread_pct < MAX_SPREAD_PCT (default 2.0%) | Log warning, mark ticker as unreliable |
| Freshness | timestamp >= utcnow() - MAX_TICKER_AGE (default 10s) | Reject, trigger REST refresh |

---

### 5.3 Public Interface

```python
class DataValidator:

    def validate_candle(self, candle: Candle) -> ValidationResult:
        """
        Returns ValidationResult:
            valid:    bool
            errors:   list[str]    — description of failed rules
            warnings: list[str]    — non-ideal conditions but still usable
        """

    def validate_ticker(self, ticker: Ticker) -> ValidationResult:
        ...

    def validate_orderbook(self, ob: Orderbook) -> ValidationResult:
        ...

    def get_validation_stats(self) -> ValidationStats:
        """
        Return rolling statistics for the last 1000 validations:
        - reject_rate_pct per data type
        - most_common_errors
        - avg_warnings_per_candle
        Used by monitoring/health_check.py
        """

@dataclass
class ValidationResult:
    valid:     bool
    errors:    list[str]
    warnings:  list[str]
    data_type: str    # 'candle' | 'ticker' | 'orderbook'
```

---

### 5.4 Config Keys — Validator

| Key | Default | Description |
|---|---|---|
| `MAX_GAP_MULTIPLIER` | 3 | Max gap between candles = 3× timeframe before warning |
| `MAX_SPREAD_PCT` | 2.0 | Spread > 2% is considered abnormal for a liquid pair |
| `MAX_TICKER_AGE_S` | 10 | Ticker considered stale after 10 seconds |
| `MAX_OB_AGE_S` | 5 | Orderbook considered stale after 5 seconds |
| `STRICT_MODE` | False | True = reject on WARNING, False = only reject on ERROR |

---

## 6. anomaly_detector.py — Abnormal Condition Detection

Detects abnormal market conditions that could cause losses if trading continues. Unlike the validator which checks data integrity — the anomaly detector checks market conditions themselves.

### 6.1 Anomaly List & Thresholds

| Anomaly ID | Condition | Default Threshold | System Action | Severity |
|---|---|---|---|---|
| `PRICE_SPIKE` | abs(log_return 1 candle) too large | abs > 5% | Skip candle, alert | HIGH |
| `VOLUME_ZERO` | Volume = 0 on liquid pair (BTCUSDT) | vol == 0 | Skip candle, alert | HIGH |
| `STALE_DATA` | Timestamp not updating | > 2× tf | Trigger WS reconnect | CRITICAL |
| `CROSSED_BOOK` | bid >= ask in orderbook | bid >= ask | Halt trading, CRITICAL alert | CRITICAL |
| `WIDE_SPREAD` | Spread extremely wide | > 1% | Disallow market orders | MEDIUM |
| `LOW_LIQUIDITY` | Depth < threshold at best 5 levels | < MIN_DEPTH | Reduce position size by 50% | MEDIUM |
| `RAPID_MOVE` | 3 consecutive candles moving same direction > 3% | 3× >3% | Apply HIGH_VOLATILITY regime | MEDIUM |
| `FUNDING_EXTREME` | Funding rate > N% per 8 hours (futures) | > 0.1% | Alert, consider exiting | LOW |

---

### 6.2 Public Interface

```python
@dataclass
class AnomalyReport:
    anomaly_id:  str
    severity:    str      # LOW | MEDIUM | HIGH | CRITICAL
    symbol:      str
    message:     str
    value:       float    # value that triggered the anomaly
    threshold:   float    # threshold that was violated
    timestamp:   datetime
    recommended: str      # recommended action

class AnomalyDetector:

    def check_candle(
        self, candle: Candle, prev_candles: list[Candle]
    ) -> list[AnomalyReport]:
        """Check candle-based anomalies. Returns empty list if safe."""

    def check_ticker(self, ticker: Ticker) -> list[AnomalyReport]:
        """Check ticker-based anomalies (spread, freshness)."""

    def check_orderbook(self, ob: Orderbook) -> list[AnomalyReport]:
        """Check crossed book, thin book, wide spread."""

    def is_safe_to_trade(
        self, symbol: str
    ) -> tuple[bool, list[AnomalyReport]]:
        """
        Aggregate all active anomalies.
        Returns (True, []) if safe.
        Returns (False, [reports]) if any HIGH or CRITICAL anomaly exists.
        MEDIUM anomalies do not block trading but appear in the report.
        """

    def get_active_anomalies(
        self, symbol: str = None
    ) -> list[AnomalyReport]:
        """Anomalies still active (not yet resolved)."""

    def resolve(self, anomaly_id: str, symbol: str) -> None:
        """Mark an anomaly as resolved (condition returned to normal)."""
```

---

### 6.3 Config Keys — Anomaly Detector

| Key | Default | Description |
|---|---|---|
| `PRICE_SPIKE_THRESHOLD` | 0.05 | 5% — \|log_return\| above this = spike |
| `WIDE_SPREAD_PCT` | 1.0 | 1% — spread above this = wide spread |
| `MIN_BID_DEPTH_USD` | 10000 | Minimum $10k at 5 bid levels for liquid |
| `RAPID_MOVE_CANDLES` | 3 | Consecutive candle count for rapid move detection |
| `RAPID_MOVE_THRESHOLD` | 0.03 | 3% per candle for rapid move detection |
| `ANOMALY_PERSIST_S` | 300 | Anomaly considered active for 300 seconds after trigger |
| `FUNDING_EXTREME_PCT` | 0.001 | 0.1% per 8 hours = extreme funding rate |

---

## 7. rate_limiter.py — Global API Rate Limiter

Prevents Binance IP bans by centrally tracking weight consumption. All requests to the exchange **MUST** pass through `rate_limiter.acquire()` before execution.

### 7.1 Binance Rate Limit Architecture

```python
# Binance has 3 independent limit types:

# 1. REQUEST WEIGHT (rolling 1 minute)
#    Each endpoint has a different 'weight'
#    Limit: 1200 weight per minute for normal accounts
#    Response header: X-MBX-USED-WEIGHT-1M

# 2. ORDER COUNT (per 10 seconds)
#    Limit: 50 orders per 10 seconds
#    Response header: X-MBX-ORDER-COUNT-10S

# 3. ORDER COUNT (per day)
#    Limit: 160,000 orders per day
#    Response header: X-MBX-ORDER-COUNT-1D

# If limit exceeded:
#    HTTP 429 → agent must stop requests until window resets
#    HTTP 418 → IP banned (if requests continued after 429)

# Safe strategy: keep usage below 80% of limit
#    Weight target: < 960 per minute
#    Order target:  < 40 per 10 seconds
```

---

### 7.2 Weight Table — Main Endpoints

| Endpoint | Method | Weight | Notes |
|---|---|---|---|
| GET /api/v3/ticker/bookTicker | GET | 2 | Single symbol. All symbols = 2×count |
| GET /api/v3/klines | GET | 2 | Per request, regardless of limit |
| GET /api/v3/account | GET | 20 | Relatively heavy — cache 10 seconds |
| GET /api/v3/openOrders | GET | 6 | Single symbol. All symbols = 40 |
| POST /api/v3/order | POST | 1 | Create order |
| DELETE /api/v3/order | DEL | 1 | Cancel order |
| GET /api/v3/order | GET | 4 | Query order status |
| GET /fapi/v2/account | GET | 5 | Futures account info |
| GET /fapi/v2/positionRisk | GET | 5 | Futures positions |
| GET /api/v3/exchangeInfo | GET | 20 | Cache 1 hour — called infrequently |

---

### 7.3 Public Interface

```python
class BinanceRateLimiter:

    async def acquire(
        self,
        endpoint:  str,
        weight:    int  = None,     # None = auto-lookup from table
        is_order:  bool = False,    # True = also check order count limit
    ) -> None:
        """
        Wait until a 'slot' is available.
        If current_weight + weight > WEIGHT_LIMIT × SAFE_RATIO:
            await asyncio.sleep(time_until_window_reset)
        """

    def update_from_headers(self, headers: dict) -> None:
        """
        Called after every exchange response.
        Parse X-MBX-USED-WEIGHT-1M header to sync actual weight.
        This is more accurate than local tracking.
        """

    def get_current_usage(self) -> RateLimitStatus:
        """Return current weight, order count, and estimated time to reset."""

    def is_near_limit(self, threshold: float = 0.8) -> bool:
        """Return True if usage > threshold × limit."""

@dataclass
class RateLimitStatus:
    weight_used:      int
    weight_limit:     int
    weight_pct:       float
    orders_10s:       int
    orders_1d:        int
    seconds_to_reset: float
    is_near_limit:    bool
```

---

### 7.4 Config Keys — Rate Limiter

| Key | Default | Description |
|---|---|---|
| `WEIGHT_LIMIT` | 1200 | Binance limit per minute. Do not change unless on VIP account. |
| `WEIGHT_SAFE_RATIO` | 0.80 | Target maximum usage at 80% of limit. |
| `ORDER_LIMIT_10S` | 50 | Binance order limit per 10 seconds. |
| `ORDER_SAFE_RATIO` | 0.80 | Target 80% of order limit. |
| `SYNC_FROM_HEADERS` | True | Always true — exchange headers are more accurate than local tracking. |
| `WARN_AT_PCT` | 0.70 | Log warning when usage reaches 70%. |

---

## PART B — Intelligence Layer

> **PRINCIPLE:** The Intelligence Layer is pure computation — no I/O, no network calls, no DB reads or writes. All input comes from parameters, all output is a return value. This makes it easy to test and deterministic.

---

## 8. regime.py — Market Regime Classification

Classifies market conditions into a regime. Regime is the most important metadata for strategies — it determines whether trend following or mean reversion is more appropriate.

### 8.1 MarketRegime Enum

```python
from enum import Enum

class MarketRegime(Enum):
    STRONG_TREND_UP   = 'strong_trend_up'
    WEAK_TREND_UP     = 'weak_trend_up'
    SIDEWAYS          = 'sideways'
    WEAK_TREND_DOWN   = 'weak_trend_down'
    STRONG_TREND_DOWN = 'strong_trend_down'
    HIGH_VOLATILITY   = 'high_volatility'    # overrides all other regimes
    UNDEFINED         = 'undefined'          # insufficient data (< MIN_HISTORY)
```

---

### 8.2 Classification Algorithm — Two Stages

**Stage 1: Base Regime via ADX + EMA**

| Condition | Regime | ADX | Price vs EMA50 |
|---|---|---|---|
| ADX > 30 AND close > EMA50 | STRONG_TREND_UP | > 30 | Above EMA50 |
| ADX 20–30 AND close > EMA50 | WEAK_TREND_UP | 20–30 | Above EMA50 |
| ADX < 20 | SIDEWAYS | < 20 | Not relevant |
| ADX 20–30 AND close < EMA50 | WEAK_TREND_DOWN | 20–30 | Below EMA50 |
| ADX > 30 AND close < EMA50 | STRONG_TREND_DOWN | > 30 | Below EMA50 |

**Stage 2: Volatility Override**

```python
# After base regime is determined, check volatility:
if atr_percentile > HIGH_VOL_PERCENTILE:    # default 85%
    regime = MarketRegime.HIGH_VOLATILITY
    # Overrides ALL other regimes during extreme volatility
    # Reason: at extreme volatility, ML model is unreliable
    #         risk management takes priority over signals
```

---

### 8.3 Public Interface

```python
@dataclass
class RegimeResult:
    regime:           MarketRegime
    confidence:       float         # 0.0 – 1.0
    adx:              float
    adx_trend:        str           # 'rising' | 'falling' | 'flat'
    ema50_distance:   float         # (close - ema50) / ema50 × 100 (pct)
    is_stable:        bool          # True if regime unchanged for last N candles
    lookback_candles: int

class RegimeClassifier:

    def classify(self, df: pd.DataFrame) -> RegimeResult:
        """
        Input:  DataFrame with columns: high, low, close, volume
                Minimum MIN_HISTORY_CANDLES rows (default: 200)
        Output: RegimeResult
        """

    def classify_multi_tf(
        self,
        df_h1: pd.DataFrame,
        df_h4: pd.DataFrame,
    ) -> RegimeResult:
        """
        Classification using two timeframes.
        H4 as the primary regime (macro trend).
        H1 as confirmation (micro trend).
        If H4 and H1 conflict → SIDEWAYS (regime conflict).
        """

    def is_regime_change(
        self,
        prev: MarketRegime,
        curr: MarketRegime,
    ) -> bool:
        """Detect if the new regime differs significantly from the previous one."""
```

---

### 8.4 Confidence Calculation

```python
# Confidence is computed based on:
def _calculate_confidence(
    adx:          float,
    ema_distance: float,
    adx_trend:    str,
    stability:    bool,
) -> float:
    score = 0.0

    # ADX strength (0.0 – 0.4)
    if adx > 40:   score += 0.40
    elif adx > 30: score += 0.30
    elif adx > 20: score += 0.20
    else:          score += 0.05

    # EMA distance (0.0 – 0.3)
    score += min(abs(ema_distance) / 5.0, 0.30)

    # ADX trend (0.0 – 0.2)
    if adx_trend == 'rising': score += 0.20
    elif adx_trend == 'flat': score += 0.10

    # Stability bonus (0.0 – 0.1)
    if stability: score += 0.10

    return min(score, 1.0)
```

---

### 8.5 Config Keys — Regime

| Key | Default | Description |
|---|---|---|
| `ADX_PERIOD` | 14 | ADX period. 14 is the industry standard. |
| `EMA_TREND_PERIOD` | 50 | EMA used for trend direction. |
| `ADX_STRONG_THRESHOLD` | 30 | ADX above this = strong trend. |
| `ADX_WEAK_THRESHOLD` | 20 | ADX above this = weak trend. |
| `HIGH_VOL_PERCENTILE` | 85 | ATR percentile above this → HIGH_VOLATILITY override. |
| `STABILITY_CANDLES` | 5 | Regime considered stable if same for N candles. |
| `MIN_HISTORY_CANDLES` | 200 | Minimum data for valid classification. Below this → UNDEFINED. |

---

## 9. volatility.py — Volatility Measurement

Computes various volatility metrics used by: regime classifier (ATR percentile for HIGH_VOL override), position sizer (ATR for stop distance), and exit layer (ATR-based trailing stop).

### 9.1 Public Interface

```python
@dataclass
class VolatilityMetrics:
    symbol:          str
    timeframe:       str
    timestamp:       datetime

    # ATR
    atr:             float    # Average True Range absolute value
    atr_pct:         float    # ATR / close × 100
    atr_percentile:  float    # current ATR position vs 252-candle history (0–100)

    # Realized volatility
    realized_vol_20: float    # annualized realized vol over 20 candles
    realized_vol_5:  float    # annualized realized vol over 5 candles

    # Volatility regime
    vol_regime:      str      # 'low' | 'normal' | 'high' | 'extreme'

    # Bollinger Bands
    bb_width:        float    # (upper - lower) / middle
    bb_percentile:   float    # BB width percentile vs 100-candle history

class VolatilityCalculator:

    def calculate(self, df: pd.DataFrame) -> VolatilityMetrics:
        """Compute all metrics from OHLCV DataFrame."""

    def get_atr(self, df: pd.DataFrame, period: int = 14) -> float:
        """ATR absolute value."""

    def get_atr_pct(self, df: pd.DataFrame, period: int = 14) -> float:
        """ATR as a percentage of price."""

    def get_atr_percentile(
        self,
        df:       pd.DataFrame,
        period:   int = 14,
        lookback: int = 252,
    ) -> float:
        """
        Current ATR position vs historical distribution.
        50 = ATR at historical median.
        85+ = High ATR (triggers HIGH_VOLATILITY regime).
        """

    def get_realized_vol(
        self,
        df:        pd.DataFrame,
        window:    int  = 20,
        annualize: bool = True,
    ) -> float:
        """
        Realized volatility from log returns.
        annualize = True: × sqrt(252 × candles_per_day)
        """
```

---

### 9.2 Volatility Regime Classification

| Regime | ATR Percentile | Default System Action |
|---|---|---|
| **low** | < 25 | Normal trading allowed. Position size can be larger (low vol = SL closer = larger size). |
| **normal** | 25–75 | Ideal conditions. Default parameters apply. |
| **high** | 75–90 | Position size reduced by 30%. Trailing stop wider. Alert sent. |
| **extreme** | > 90 | HIGH_VOLATILITY regime activated. Position size reduced by 50%. Market orders disallowed. |

---

### 9.3 Realized Volatility Formula

```python
# Annualized Realized Volatility
# Used for: position sizing, risk reporting

def realized_vol_annualized(
    log_returns: pd.Series, window: int, tf: str
) -> float:
    # Step 1: rolling std of log returns
    rolling_std = log_returns.rolling(window).std()

    # Step 2: annualize
    # Candles per day based on timeframe:
    candles_per_day = {'1m': 1440, '5m': 288, '15m': 96,
                       '1h': 24, '4h': 6, '1d': 1}
    factor = sqrt(252 * candles_per_day[tf])

    return rolling_std.iloc[-1] * factor

# Interpretation example:
# realized_vol = 0.60 → expected move of 60% per year
#                     or ~3.8% per day (0.60 / sqrt(252))
```

---

### 9.4 Config Keys — Volatility

| Key | Default | Description |
|---|---|---|
| `ATR_PERIOD` | 14 | Standard ATR period. Change only with strong reason. |
| `ATR_LOOKBACK` | 252 | Lookback for ATR percentile. 252 ≈ 1 year of 1H data. |
| `REALIZED_VOL_WINDOW` | 20 | Window for realized volatility. |
| `BB_PERIOD` | 20 | Bollinger Bands period. |
| `BB_STD_DEV` | 2.0 | Std dev multiplier for BB. |
| `HIGH_VOL_PERCENTILE` | 75 | ATR percentile above this → vol_regime = 'high'. |
| `EXTREME_VOL_PERCENTILE` | 90 | ATR percentile above this → vol_regime = 'extreme'. |

---

## 10. market_state.py — MarketState Aggregation

Collects all intelligence layer outputs into a single MarketState object ready to be consumed by the strategy layer. One update function is called every tick.

### 10.1 MarketState Dataclass — Contract with Strategy Layer

```python
@dataclass
class MarketState:
    # ── Identity ────────────────────────────────────────────
    symbol:              str
    timeframe:           str
    timestamp:           datetime

    # ── Price & Market ───────────────────────────────────────
    last_price:          float
    bid:                 float
    ask:                 float
    mid:                 float
    spread_pct:          float
    volume_24h:          float
    orderbook_imbalance: float        # -1.0 to +1.0

    # ── Candle Data ──────────────────────────────────────────
    latest_candle:       Candle
    candles_df:          pd.DataFrame  # 200 historical candles for strategy

    # ── Intelligence ─────────────────────────────────────────
    regime:              MarketRegime
    regime_confidence:   float
    regime_stable:       bool
    vol_metrics:         VolatilityMetrics

    # Shortcut properties for easy access in strategies
    @property
    def atr(self) -> float:
        return self.vol_metrics.atr

    @property
    def atr_percentile(self) -> float:
        return self.vol_metrics.atr_percentile

    @property
    def vol_regime(self) -> str:
        return self.vol_metrics.vol_regime

    # ── Features (for ML model) ──────────────────────────────
    features:            pd.Series     # feature_names from metadata.json

    # ── Portfolio Context ────────────────────────────────────
    open_positions:      dict[str, Position]    # symbol → Position
    available_equity:    float
    equity:              float
    daily_pnl:           float

    # ── Anomaly & Safety ─────────────────────────────────────
    active_anomalies:    list[AnomalyReport]
    is_safe_to_trade:    bool

    # ── Metadata ─────────────────────────────────────────────
    data_quality:        str       # 'good' | 'degraded' | 'bad'
    latency_ms:          float     # time to build state
```

---

### 10.2 MarketStateBuilder — Interface

```python
class MarketStateBuilder:

    def __init__(
        self,
        regime_clf:    RegimeClassifier,
        vol_calc:      VolatilityCalculator,
        anomaly_det:   AnomalyDetector,
        feature_eng:   FeatureEngineering,
        latency_guard: LatencyGuard,
    ): ...

    async def build(
        self,
        candle:     Candle,
        ticker:     Ticker,
        orderbook:  Orderbook,
        candles_df: pd.DataFrame,
        portfolio:  PortfolioState,
    ) -> MarketState:
        """
        Called every tick by main_loop.
        Build sequence:
        1. Validate input (all must be present and fresh)
        2. Compute volatility metrics
        3. Classify regime
        4. Check anomalies
        5. Compute features (calls runtime feature_engineering version)
        6. Check latency guard
        7. Assemble MarketState
        """

    def get_last_state(self, symbol: str) -> MarketState | None:
        """Return the last successfully built state."""

    def is_state_fresh(self, symbol: str, max_age_s: int = 5) -> bool:
        """Check if state is still fresh. If not, use previous state."""
```

---

### 10.3 Data Quality Assessment

| Quality Level | Condition | Strategy Action |
|---|---|---|
| **good** | All data fresh, no active anomalies | Normal trading |
| **degraded** | Some data stale OR MEDIUM anomaly active | Trade with position size reduced by 50% |
| **bad** | Critical data stale OR HIGH/CRITICAL anomaly | is_safe_to_trade = False → strategy does not generate signal |

---

## 11. latency_guard.py — Execution Protection

Prevents order execution during high latency conditions that could cause fills far from expected prices. Different from rate_limiter — latency_guard measures network conditions, not API quota.

### 11.1 What Is Measured

| Metric | How Measured | Alert Threshold | Block Threshold |
|---|---|---|---|
| WS message latency | Exchange timestamp vs received timestamp | > 500ms | > 2000ms |
| REST round-trip time | Time between request and response | > 1000ms | > 3000ms |
| Order-to-fill latency | Time between order submission and fill confirmation | > 2000ms | > 5000ms |
| Tick-to-signal latency | Time from candle arrival to signal generation | > 500ms | > 1500ms |

---

### 11.2 Public Interface

```python
@dataclass
class LatencyStatus:
    ws_latency_ms:        float
    rest_latency_ms:      float
    order_latency_ms:     float
    tick_latency_ms:      float
    overall_status:       str     # 'ok' | 'degraded' | 'high' | 'critical'
    should_block_trading: bool
    warnings:             list[str]

class LatencyGuard:

    def record_ws_latency(
        self, exchange_ts: datetime, received_ts: datetime
    ) -> None:
        """Called every time a WebSocket message is received."""

    def record_rest_latency(
        self, duration_ms: float, endpoint: str
    ) -> None:
        """Called after every REST request completes."""

    def record_order_latency(
        self, submit_ts: datetime, fill_ts: datetime
    ) -> None:
        """Called after an order is filled."""

    def get_status(self) -> LatencyStatus:
        """Return current latency status."""

    def should_block_order(self) -> tuple[bool, str]:
        """
        Return (True, reason) if order should be blocked.
        Called by main_loop before submitting an order.
        """

    def get_rolling_stats(self, window: int = 20) -> dict:
        """P50, P95, P99 latency for the last 20 measurements."""
```

---

### 11.3 Config Keys — Latency Guard

| Key | Default | Description |
|---|---|---|
| `WS_LATENCY_WARN_MS` | 500 | WS latency above this → warning, reduce signal confidence. |
| `WS_LATENCY_BLOCK_MS` | 2000 | WS latency above this → block new orders. |
| `REST_LATENCY_WARN_MS` | 1000 | REST round-trip above this → warning. |
| `REST_LATENCY_BLOCK_MS` | 3000 | REST round-trip above this → block orders. |
| `LATENCY_WINDOW` | 20 | Number of measurements for rolling average. |
| `BLOCK_DURATION_S` | 60 | After block, wait 60 seconds before retrying. |

---

## 12. Integration & Dependency Map

### 12.1 File Dependencies

| File | Imports From | Output Consumed By |
|---|---|---|
| `market.py` | security/ (key_mgr), rate_limiter, utils/helpers | orderbook.py, websocket_client, market_state |
| `orderbook.py` | market.py (REST snapshot), websocket_client | market_state.py, execution_layer (slippage est.) |
| `websocket_client.py` | security/ (key_mgr), market.py (listen key) | market.py (candle), orderbook.py (depth), sync/ |
| `validator.py` | — (pure function) | main_loop (first gate every tick) |
| `anomaly_detector.py` | validator.py (ValidationResult) | market_state.py (is_safe_to_trade) |
| `rate_limiter.py` | — (pure function) | market.py, execution_layer (all REST calls) |
| `regime.py` | volatility.py (atr_percentile) | market_state.py |
| `volatility.py` | — (pure function) | regime.py, market_state.py, exit_layer (trailing) |
| `market_state.py` | regime, volatility, anomaly, feature_engineering | strategy_layer (consumed every tick) |
| `latency_guard.py` | utils/time_utils | market_state.py, main_loop (gate before order) |

---

### 12.2 Feature Engineering at Runtime

`market_state.py` calls the runtime version of feature engineering that is identical to the research version. This is the most critical component for model consistency.

> **CRITICAL CONTRACT:** The `FeatureConfig` used at runtime MUST be exactly the same as the `FeatureConfig` used during training in the research layer. If even one feature differs, the model will produce invalid predictions without an explicit error.

```python
# How runtime calls feature engineering:
from research.pipeline.feature_engineering import FeatureEngineering
# ↑ Import directly from research/ to ensure consistency
# This means: research/ and runtime/ must be in the same Python package

class RuntimeFeatureEngine:

    def __init__(self, metadata: dict):
        self.config        = FeatureConfig(**metadata['feature_config'])
        self.feature_names = metadata['feature_names']
        self._engine       = FeatureEngineering(self.config)

    def compute(self, candles_df: pd.DataFrame) -> pd.Series:
        """
        Input:  200+ candle DataFrame
        Output: pd.Series with index = feature_names
        Must be identical to what the model saw during training.
        """
        features_df = self._engine.build_features(candles_df, self.config)
        latest      = features_df.iloc[-1]

        # Ensure column order and names are correct
        assert list(latest.index) == self.feature_names, 'Feature mismatch!'
        return latest
```

---

### 12.3 Implementation Checklist

| No | Item | Verification | Done |
|---|---|---|---|
| 1 | `rate_limiter.acquire()` called before EVERY REST request | `grep -n 'await rate_limiter' execution + market + sync files` | ☐ |
| 2 | `validator.validate_candle()` called at the start of every tick | Code review main_loop — step 1 must exist | ☐ |
| 3 | `anomaly_detector.is_safe_to_trade()` blocks signal if False | Unit test: inject CROSSED_BOOK → signal not generated | ☐ |
| 4 | WebSocket reconnect runs without interrupting main_loop | Integration test: kill WS connection, confirm loop continues | ☐ |
| 5 | Orderbook update sequence ID tracked, gap → new snapshot | Test with simulated gap in update ID | ☐ |
| 6 | Listen key refreshed every 30 minutes | Mock time, confirm refresh called before 60 minutes | ☐ |
| 7 | MarketState.features identical to training features | Test: compare RuntimeFeatureEngine vs research FeatureEngine output | ☐ |
| 8 | `regime.classify()` returns UNDEFINED if data < MIN_HISTORY_CANDLES | Unit test with 50-row DataFrame | ☐ |
| 9 | `latency_guard.should_block_order()` checked before submission | Code review main_loop step 8 | ☐ |
| 10 | All HIGH/CRITICAL anomalies published to event_bus (SYSTEM_ERROR) | Test: trigger anomaly, check event_bus receives event | ☐ |
| 11 | Cache invalidation runs on WebSocket update | Test: WS order fill → balance cache cleared | ☐ |
| 12 | No I/O inside intelligence_layer (pure computation) | `grep -rn 'await\|async\|requests\|sqlite' intelligence_layer/` | ☐ |

---

> **This document is the implementation contract for the Data Layer & Intelligence Layer.**
> Any changes to output dataclasses, config key defaults, or validation rules
> **MUST** be updated before merging to main.
>
> *Related references: agent_core_docs.md • runtime_layer_docs.md • research_layer_docs.md*
