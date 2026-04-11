# crypto_ai_agent — Runtime Layer Documentation

*Interface contracts · Config keys · Design decisions · Data flow · Error handling*

**Binance Spot + Futures | Paper → Shadow → Live | Version 1.0**

---

## 1. Overview — Runtime Layer

The Runtime is the live system that executes trading automatically. Unlike Research, every component here has the potential to make or lose real money. Core principles:

- **Fault tolerant** — a crash must not leave open positions unmonitored.
- **Idempotent** — a restart must not produce duplicate orders.
- **Observable** — every decision must be auditable after the fact.
- **Mode-aware** — paper/shadow/live behave differently but pass through the same code path.

> **HARD RULE:** No direct connection from `research/` to `runtime/` within the same process. Artifacts (models, config, params) are moved via the filesystem through `automation/deploy.py`.

---

### 1.1 Sublayer Map & Initialization Order

| Order | Sublayer | Primary Responsibility | Depends On |
|---|---|---|---|
| 1 | **security/** | Load & protect API keys, encrypt secrets | — |
| 2 | **core/config** | Validate all config via Pydantic | security/ |
| 3 | **data_layer/** | WebSocket & REST connection to exchange | security/, core/config |
| 4 | **intelligence_layer/** | Regime & volatility classification | data_layer/ |
| 5 | **portfolio_layer/** | Capital allocation & global risk budget | core/config |
| 6 | **risk_layer/** | Initialize all risk guards | portfolio_layer/ |
| 7 | **strategy_layer/** | Load ML model & strategy parameters | risk_layer/, intelligence_layer/ |
| 8 | **trade_layer/** | Restore state from DB, check open orders | risk_layer/ |
| 9 | **execution_layer/** | Connect to exchange executor | security/, data_layer/ |
| 10 | **exit_layer/** | Register open positions for monitoring | trade_layer/ |
| 11 | **sync/** | Initial reconciliation: balance, positions, orders | execution_layer/ |
| 12 | **monitoring/** | Start heartbeat & health check | All layers |
| 13 | **core/main loop** | Start main event loop | All ready |

---

### 1.2 Operating Modes

| Mode | paper_mode | Send Orders? | Fill Orders? | Use Real Capital? | When Used |
|---|---|---|---|---|---|
| **PAPER** | True | No | Internal simulation | No | Development & early testing |
| **SHADOW** | False | Yes (testnet) | Yes (testnet) | No | Validation before live |
| **LIVE** | False | Yes (mainnet) | Yes (mainnet) | Yes | Production |

> **MODE TRANSITION:** A mode change MUST NOT occur while there are open positions. Close all positions first, ensure `state.db` is clean, then change `modes.py`.

---

## 2. security/ — Security & Secret Protection

The first layer to be initialized. No other component may access the API key directly — everything must go through `KeyManager`.

### 2.1 key_manager.py

**Public Interface**

| Function | Parameters | Return | Exception |
|---|---|---|---|
| `get_api_key()` | exchange: str, env: str='production' | str — key encrypted in memory | KeyNotFoundError |
| `get_api_secret()` | exchange: str, env: str='production' | str | KeyNotFoundError |
| `rotate_key()` | exchange: str, new_key: str, new_secret: str | bool | RotationError |
| `validate_permissions()` | exchange: str | PermissionReport | APIError |

**PermissionReport Dataclass**

```python
@dataclass
class PermissionReport:
    exchange:      str
    can_read:      bool   # read balance & positions
    can_trade:     bool   # open & close orders
    can_withdraw:  bool   # MUST be False — agent must not withdraw
    ip_restricted: bool   # True = key is only valid from specific IP
    passed:        bool   # True if can_read+can_trade AND NOT can_withdraw
```

> **CRITICAL:** The API key used by the agent MUST have `can_withdraw=False`. If `validate_permissions()` returns `can_withdraw=True`, the system MUST shut down and send an alert.

---

### 2.2 encryptor.py

| Function | Parameters | Return |
|---|---|---|
| `encrypt()` | data: str \| bytes, key: bytes | bytes — ciphertext |
| `decrypt()` | ciphertext: bytes, key: bytes | str |
| `generate_key()` | — | bytes — 32-byte Fernet key |
| `hash_order_id()` | client_order_id: str | str — SHA-256 hash for logging |

- Use Fernet (AES-128-CBC + HMAC-SHA256) from the `cryptography` library.
- The master key is stored in the environment variable `CRYPTO_AGENT_MASTER_KEY`, not in the `.env` file.
- Data in `state.db`, `experience.db`, and `order_registry.db` is encrypted before being stored.

---

### 2.3 .env — Full Template

```bash
# Exchange API (MUST be filled before deploy)
BINANCE_API_KEY=
BINANCE_API_SECRET=
BINANCE_TESTNET_KEY=
BINANCE_TESTNET_SECRET=

# Master encryption key
CRYPTO_AGENT_MASTER_KEY=   # generate via encryptor.generate_key()

# Mode
AGENT_MODE=paper            # paper | shadow | live
INITIAL_EQUITY=10000.0

# Notification
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
DISCORD_WEBHOOK_URL=

# Gateway
GATEWAY_SECRET_KEY=         # JWT secret for the API gateway
GATEWAY_PORT=8080

# Database paths
STATE_DB_PATH=agent/memory/state.db
EXPERIENCE_DB_PATH=agent/memory/experience.db
ORDER_REGISTRY_DB_PATH=agent/memory/order_registry.db

# LLM (optional)
ANTHROPIC_API_KEY=
LLM_DAILY_BUDGET_USD=1.0
```

---

## 3. core/ — Heart of the System

### 3.1 config.py & config_schema.py

`config.py` reads values from `.env` and a YAML file. `config_schema.py` validates all values using Pydantic before the system is allowed to run.

**AgentConfig — Pydantic Model**

```python
from pydantic import BaseModel, validator, Field

class AgentConfig(BaseModel):
    # Mode
    mode:                 str   = 'paper'    # paper|shadow|live
    initial_equity:       float = 10_000.0

    # Risk
    risk_per_trade_pct:   float = Field(0.01, ge=0.001, le=0.05)
    max_daily_loss_pct:   float = Field(0.05, ge=0.01,  le=0.15)
    max_drawdown_pct:     float = Field(0.10, ge=0.05,  le=0.30)
    max_open_positions:   int   = Field(5,    ge=1,     le=20)
    global_max_leverage:  int   = Field(5,    ge=1,     le=20)

    # Execution
    symbols:              list[str] = ['BTCUSDT']
    timeframe:            str   = '1h'
    execution_delay_ms:   int   = Field(100, ge=0, le=5000)

    # Sizing
    sizing_method:        str   = 'fixed_fractional'

    # LLM
    llm_enabled:          bool  = False
    llm_daily_budget_usd: float = 1.0

    @validator('mode')
    def validate_mode(cls, v):
        if v not in ('paper', 'shadow', 'live'):
            raise ValueError(f'mode must be paper|shadow|live, got: {v}')
        return v
```

> **STARTUP VALIDATION:** If `AgentConfig` fails to parse (any field is invalid), `main.py` MUST exit with code 1 and log a clear error message. The system must not run with an invalid config.

---

### 3.2 main.py — Main Loop

**Full Startup Flow**

```python
async def main():
    # 1. Load & validate config
    config = AgentConfig(**load_env())

    # 2. Initialize security
    key_mgr = KeyManager(config)
    perms   = key_mgr.validate_permissions('binance')
    assert not perms.can_withdraw, 'FATAL: API key has withdraw permission!'

    # 3. Initialize all layers (order matters)
    data_layer  = DataLayer(config, key_mgr)
    intel_layer = IntelligenceLayer(config)
    portfolio   = PortfolioLayer(config)
    risk_mgr    = RiskManager(config)
    strategy    = load_strategy(config)
    trade_mgr   = TradeManager(config, risk_mgr)
    executor    = ExecutionLayer(config, key_mgr)
    exit_mgr    = ExitManager(config)
    sync        = SyncManager(config, executor)
    monitoring  = MonitoringSystem(config)

    # 4. Recovery: restore state before starting
    await trade_mgr.recovery.restore()
    await sync.full_reconciliation()

    # 5. Start heartbeat & monitoring
    await monitoring.start()

    # 6. Main event loop
    async for candle in data_layer.stream():
        await tick(candle, ...all_layers...)
```

**tick() — Per-Candle Execution Sequence**

| Step | Action | Layer | On Failure |
|---|---|---|---|
| 1 | Validate & anomaly check incoming candle data | data_layer/validator | Skip candle, log warning |
| 2 | Update intelligence: regime, volatility, market_state | intelligence_layer/ | Use previous state |
| 3 | Update exit monitoring: check SL/TP/trailing on active positions | exit_layer/ | Alert + emergency close |
| 4 | Sync balance & positions if interval has elapsed | sync/ | Log error, don't skip risk check |
| 5 | Call strategy.generate_signal(market_state) | strategy_layer/ | Skip signal, no trade |
| 6 | Evaluate signal through risk_manager.evaluate() | risk_layer/ | Block trade, log reason |
| 7 | Create trade request & check idempotency | trade_layer/idempotency | Raise DuplicateOrderError |
| 8 | Execute order to exchange | execution_layer/ | Retry N times, then alert |
| 9 | Save trade to store & audit_trail | trade_layer/store | Log error, don't crash |
| 10 | Update portfolio state & equity | portfolio_layer/ | Alert if equity drops sharply |
| 11 | Trigger LLM advisor if enabled & budget available | llm_layer/ | Skip, non-blocking |

---

### 3.3 event_bus.py

Async pub/sub between layers using `asyncio.Queue`. No layer may directly import another layer for event communication.

**Event Types**

| Event | Publisher | Subscriber(s) | Payload |
|---|---|---|---|
| `CANDLE_READY` | data_layer | intelligence, strategy | Candle dataclass |
| `SIGNAL_GENERATED` | strategy_layer | risk_layer, trade_layer | Signal dataclass |
| `TRADE_OPENED` | trade_layer | exit_layer, portfolio, monitoring | Trade dataclass |
| `TRADE_CLOSED` | exit_layer | trade_layer, portfolio, learning | TradeResult dataclass |
| `CIRCUIT_BREAKER` | risk_layer | main_loop, monitoring, notification | CircuitState enum |
| `EQUITY_UPDATE` | portfolio_layer | risk_layer | float — new equity |
| `SYSTEM_ERROR` | All layers | monitoring, notification | ErrorReport dataclass |

---

### 3.4 safe_mode.py — Emergency Stop

```python
class SafeMode:

    async def emergency_stop(self, reason: str):
        """
        Safe emergency shutdown sequence:
        1. Set global halt flag → all tick() calls return immediately
        2. Cancel all open orders on the exchange
        3. Optional: close all positions (if close_positions=True)
        4. Save state snapshot to disk
        5. Send alert to Telegram + Discord
        6. Log reason to errors.log
        7. Exit process with code 2 (not 0)
        """

    async def graceful_shutdown(self):
        """
        Normal shutdown (e.g. maintenance):
        1. Stop accepting new signals
        2. Wait for in-progress trades to complete (timeout 30s)
        3. Save state
        4. Exit code 0
        """
```

---

## 4. data_layer/ — Market Input

### 4.1 market.py — REST Data

**Public Interface**

| Function | Parameters | Return | Cache TTL |
|---|---|---|---|
| `get_ticker()` | symbol: str | Ticker dataclass | 5 seconds |
| `get_ohlcv()` | symbol: str, tf: str, limit: int=100 | list[Candle] | 1 candle (tf) |
| `get_balance()` | asset: str='USDT' | Balance dataclass | 10 seconds |
| `get_open_orders()` | symbol: str=None | list[Order] | No cache |
| `get_position()` | symbol: str (futures only) | Position \| None | 5 seconds |

**Ticker Dataclass**

```python
@dataclass
class Ticker:
    symbol:     str
    bid:        float
    ask:        float
    last:       float
    spread_pct: float    # (ask - bid) / mid * 100
    volume_24h: float
    timestamp:  datetime
```

---

### 4.2 websocket_client.py

Manages WebSocket connections to Binance for real-time data. Must auto-reconnect and must not lose candles during reconnection.

**Public Interface**

| Function | Parameters | Return |
|---|---|---|
| `subscribe_kline()` | symbol: str, tf: str, callback: Callable | asyncio.Task |
| `subscribe_orderbook()` | symbol: str, depth: int=20, callback: Callable | asyncio.Task |
| `subscribe_user_stream()` | callback: Callable | asyncio.Task — order updates, balance |
| `disconnect()` | — | None |

**Reconnect Policy**

| Condition | Action | Max Retry | Alert? |
|---|---|---|---|
| Connection lost | Reconnect with exponential backoff (1s, 2s, 4s, 8s...) | 10x | After 3 failures |
| Message timeout (>30s) | Force reconnect | Infinite | After 60s with no data |
| Invalid message | Log & skip | — | After 10 consecutive |
| Listen key expired | Refresh listen key & reconnect | 3x | Yes |

---

### 4.3 rate_limiter.py — Global API Rate Limiter

Binance uses a weight-per-endpoint system. This rate limiter prevents bans by tracking weight consumption centrally.

```python
class BinanceRateLimiter:
    # Binance limits (per 1-minute rolling window)
    WEIGHT_LIMIT   = 1200     # request weight
    ORDER_LIMIT_10S = 50      # orders per 10 seconds
    ORDER_LIMIT_1D  = 160000  # orders per day

    # Weight per endpoint (partial list)
    WEIGHT = {
        'GET /api/v3/ticker':  2,
        'GET /api/v3/klines':  2,
        'POST /api/v3/order':  1,
        'GET /api/v3/account': 20,
        'GET /fapi/v2/account': 5,
    }

    async def acquire(self, endpoint: str, weight: int = None):
        """Wait until weight is available before the request can proceed."""
```

---

### 4.4 anomaly_detector.py

| Anomaly | Detection | Action |
|---|---|---|
| Price spike > 5% in 1 candle | abs(log_return) > 0.05 | Skip candle, log warning, wait for confirmation |
| Volume 0 on liquid pair | volume == 0 for BTCUSDT/ETHUSDT | Skip candle, alert |
| Stale data (timestamp not updating) | last_ts == prev_ts for > 2× tf | Trigger WebSocket reconnect |
| Spread > 1% (BTCUSDT) | (ask-bid)/mid > 0.01 | Block market order execution |
| Bid > Ask (crossed book) | bid >= ask | Halt trading, critical alert |

---

## 5. intelligence_layer/ — Market Understanding

### 5.1 regime.py

Classifies market conditions into regimes. Different strategies are activated based on the active regime.

**MarketRegime Enum**

```python
class MarketRegime(Enum):
    STRONG_TREND_UP   = 'strong_trend_up'
    WEAK_TREND_UP     = 'weak_trend_up'
    SIDEWAYS          = 'sideways'
    WEAK_TREND_DOWN   = 'weak_trend_down'
    STRONG_TREND_DOWN = 'strong_trend_down'
    HIGH_VOLATILITY   = 'high_volatility'   # overrides all other regimes
    UNDEFINED         = 'undefined'          # when data is insufficient
```

**Regime Classification**

| Condition | Regime | Active Strategies |
|---|---|---|
| ADX > 30 AND close > EMA50 | STRONG_TREND_UP | Trend following, long bias |
| ADX 20–30 AND close > EMA50 | WEAK_TREND_UP | Conservative trend following |
| ADX < 20 | SIDEWAYS | Mean reversion, reduce position size |
| ADX 20–30 AND close < EMA50 | WEAK_TREND_DOWN | Trend following, short bias (futures) |
| ADX > 30 AND close < EMA50 | STRONG_TREND_DOWN | Aggressive trend following, short bias |
| ATR percentile > 85% | HIGH_VOLATILITY | Reduce position size 50%, tighten SL |

**Public Interface**

| Function | Parameters | Return |
|---|---|---|
| `classify()` | df: pd.DataFrame (historical candles) | MarketRegime |
| `get_regime_score()` | df: pd.DataFrame | dict[regime, confidence_0_to_1] |
| `is_regime_stable()` | df, lookback: int=5 | bool — True if same regime for last N candles |

---

### 5.2 volatility.py

| Function | Return | Formula |
|---|---|---|
| `get_atr()` | float | ATR(14) via ta-lib |
| `get_atr_percentile()` | float 0–100 | Current ATR vs rolling 252 candles |
| `get_realized_vol()` | float — annualized | std(log_return, 20) × sqrt(252×24) for 1H |
| `get_vol_regime()` | str: low\|normal\|high\|extreme | Based on ATR percentile: <25\|25-75\|75-90\|>90 |

---

### 5.3 market_state.py — Aggregate

**MarketState Dataclass — Input for Strategies**

```python
@dataclass
class MarketState:
    symbol:            str
    timestamp:         datetime

    # Price
    last_price:        float
    bid:               float
    ask:               float
    spread_pct:        float

    # Candle
    latest_candle:     Candle
    candles_df:        pd.DataFrame    # 200 historical candles

    # Intelligence
    regime:            MarketRegime
    regime_confidence: float
    vol_regime:        str             # low|normal|high|extreme
    atr:               float
    atr_percentile:    float

    # Features (output of feature_engineering, ready for model)
    features:          pd.Series      # column names = feature_names from metadata.json

    # Portfolio context
    open_positions:    dict           # symbol → Position
    available_equity:  float
```

---

## 6. strategy_layer/ — Trading Signals

### 6.1 base_strategy.py — Abstract Base

```python
from abc import ABC, abstractmethod

class BaseStrategy(ABC):

    def __init__(self, config: AgentConfig, model: TrainedModel):
        self.config = config
        self.model  = model

    @abstractmethod
    def generate_signal(self, state: MarketState) -> Signal | None:
        """
        Return a Signal if there is an opportunity, None if not.
        MUST NOT: access exchange, write to DB, send notifications.
        MAY:       read state, call model.predict(), compute thresholds.
        """

    @abstractmethod
    def should_exit(self, position: Position, state: MarketState) -> ExitSignal | None:
        """Evaluate whether an existing position should be closed."""

    def get_signal_confidence(self, state: MarketState) -> float:
        """Override for strategies that need custom confidence scoring."""
        return 1.0
```

**Signal Dataclass**

```python
@dataclass
class Signal:
    symbol:          str
    side:            str           # BUY | SELL
    signal_type:     str           # market | limit | conditional
    confidence:      float         # 0.0 – 1.0
    suggested_price: float         # 0.0 = market price
    suggested_sl:    float         # stop loss price
    suggested_tp:    float | None  # take profit price
    strategy_id:     str           # strategy identifier
    reasoning:       str           # for logging & LLM review
    timestamp:       datetime
    metadata:        dict          # free-form additional data
```

---

### 6.2 Strategy Config Keys

| Key | Default | Description |
|---|---|---|
| `MIN_SIGNAL_CONFIDENCE` | 0.60 | Minimum confidence for a signal to be processed. Below this → ignored. |
| `SIGNAL_COOLDOWN_CANDLES` | 3 | Minimum number of candles between two signals on the same symbol. |
| `MAX_SIGNALS_PER_TICK` | 2 | Maximum signals processed per candle. Prevents order bursts. |
| `REGIME_FILTER` | True | If True: signals are ignored during HIGH_VOLATILITY regime. |
| `MODEL_THRESHOLD` | 0.0 | Model prediction threshold (return or probability) for entry. |

---

## 7. portfolio_layer/ — Global Capital Control

The portfolio layer is the highest-level capital overseer. It does not make trading decisions but ensures no strategy takes more than its allocated share.

### 7.1 allocator.py

**Public Interface**

| Function | Parameters | Return |
|---|---|---|
| `get_allocation()` | strategy_id: str, symbol: str | float — max USDT allowed |
| `update_allocation()` | strategy_id: str, used: float | None — update internal tracking |
| `rebalance()` | portfolio_state: PortfolioState | dict[strategy_id, new_allocation] |
| `get_free_equity()` | — | float — unallocated equity |

**Allocation Rules**

| Rule | Formula | Config Key |
|---|---|---|
| Max per strategy | total_equity × max_strategy_pct | MAX_STRATEGY_ALLOCATION_PCT = 0.40 |
| Max per symbol | total_equity × max_symbol_pct | MAX_SYMBOL_ALLOCATION_PCT = 0.20 |
| Cash reserve | total_equity × min_cash_reserve | MIN_CASH_RESERVE_PCT = 0.10 |
| Correlation budget | Correlated assets max combined exposure | MAX_CORRELATED_PCT = 0.35 |

---

### 7.2 capital_manager.py

```python
class CapitalManager:

    def update(self, new_equity: float) -> CapitalStatus:
        """
        Called after every trade close or periodic sync.
        Returns a CapitalStatus containing:
        - daily_pnl:       float
        - drawdown_pct:    float (from peak)
        - equity_at_risk:  float (current open exposure)
        - safe_to_trade:   bool
        - warnings:        list[str]
        """

    def compound_equity(self) -> float:
        """
        For paper mode: compute compounded equity.
        For live mode:  fetch from sync/balance_sync.py.
        """
```

---

## 8. risk_layer/ — Primary Gate Before Orders

The full Risk Layer is documented separately. This section focuses on integration with other layers.

### 8.1 Interfaces Consumed by Other Layers

| Caller | Calls | Required Fields in portfolio_state |
|---|---|---|
| trade_layer/manager.py | `risk_manager.evaluate(request, state)` | equity, open_positions, exchange_status, market_halted, last_price_SYMBOL |
| trade_layer/manager.py | `risk_manager.update_equity(equity)` | float — latest equity from balance_sync |
| core/scheduler.py | `risk_manager.reset_daily_stats()` | Called exactly at UTC 00:00 |
| exit_layer/exit_manager.py | `circuit_breaker.record_loss/win()` | Called after every trade close |

---

### 8.2 portfolio_state — Full Format

```python
# Dict sent to risk_manager.evaluate()
portfolio_state = {
    # Required
    'equity':          10_000.0,        # float — current equity
    'open_positions': {                  # dict[symbol, PositionInfo]
        'BTCUSDT': {
            'notional': 500.0,          # qty × entry_price
            'side':     'BUY',
            'qty':      0.01,
        }
    },
    'exchange_status': 'normal',        # normal | maintenance
    'market_halted':   False,

    # Price (for every symbol being traded)
    'last_price_BTCUSDT': 50_000.0,

    # Optional (for specific sizing methods)
    'atr_BTCUSDT':    800.0,            # for volatility_scaled
    'strategy_stats': {                  # for kelly sizing
        'strategy_001': {
            'win_rate':        0.55,
            'avg_risk_reward': 1.8,
        }
    },
}
```

---

## 9. trade_layer/ — Trade Orchestration

### 9.1 trade.py — Trade Dataclass

```python
@dataclass
class Trade:
    # Identity — IMMUTABLE after creation
    trade_id:          str          # UUID v4
    client_order_id:   str          # format: {strategy_id}_{symbol}_{timestamp_ms}
    exchange_order_id: str | None = None   # filled after exchange confirmation

    # Details
    symbol:            str
    side:              str          # BUY | SELL
    order_type:        str          # market | limit
    requested_qty:     float
    filled_qty:        float = 0.0
    avg_fill_price:    float = 0.0

    # Risk params
    sl_price:          float = 0.0
    tp_price:          float = 0.0
    risk_amount_usd:   float = 0.0

    # Status
    status:            str   = 'pending'   # pending|open|closed|cancelled|failed
    strategy_id:       str   = ''
    mode:              str   = 'paper'

    # Timestamps
    created_at:        datetime = field(default_factory=datetime.utcnow)
    opened_at:         datetime | None = None
    closed_at:         datetime | None = None

    # PnL (filled at close)
    pnl_usd:           float = 0.0
    pnl_pct:           float = 0.0
    commission_usd:    float = 0.0
```

---

### 9.2 idempotency.py — Anti Double Order

Prevents duplicate orders that can occur during: agent restart, network timeout, or loop bugs.

**How It Works**

1. Before sending an order, hash `client_order_id` and check in `order_registry.db`.
2. If already exists: return the status of the existing order, **do NOT** send a new one.
3. If not found: save to registry with status=`'pending'`, then proceed to send.
4. After exchange confirmation: update status to `'confirmed'`.
5. Registry entries are kept for at least 24 hours after close.

```python
class IdempotencyManager:

    def check_or_register(self, client_order_id: str) -> IdempotencyResult:
        """
        Return:
        - IdempotencyResult.PROCEED    if new order, register immediately
        - IdempotencyResult.DUPLICATE  if already exists (do not resend)
        - IdempotencyResult.PENDING    if currently being processed (wait for confirmation)
        """

    def confirm(self, client_order_id: str, exchange_order_id: str) -> None:
        """Called after the exchange confirms the order was received."""

    def mark_failed(self, client_order_id: str, reason: str) -> None:
        """Called if the order fails completely after all retries."""
```

---

### 9.3 recovery.py — State Recovery

Called at startup to ensure internal state is in sync with the exchange after a crash.

**Recovery Flow**

```python
class RecoveryManager:

    async def restore(self) -> RecoveryReport:
        """
        Startup recovery sequence:
        1. Load all trades with status='open' from state.db
        2. Query the exchange for each open order (by client_order_id)
        3. Reconcile discrepancies:
           - Order exists in DB but not on exchange → mark cancelled
           - Order exists on exchange but not in DB → add it (ghost order)
           - Status differs → update DB to exchange status (truth of record)
        4. Load open positions from the exchange
        5. Ensure every position has a SL set
        6. Return RecoveryReport (summary of what was recovered)
        """
```

> **RULE:** `recovery.py` MUST complete successfully before the main loop begins accepting signals. If recovery fails or there is a discrepancy that cannot be resolved automatically, the system MUST enter `safe_mode`.

---

### 9.4 audit_trail.py

```python
# Every event on a trade is recorded as an immutable record

@dataclass
class AuditEvent:
    event_id:        str    # UUID
    trade_id:        str
    event_type:      str    # CREATED|SUBMITTED|FILLED|SL_HIT|TP_HIT|CLOSED|CANCELLED
    timestamp:       datetime
    actor:           str    # 'strategy'|'risk'|'exit_manager'|'manual'|'recovery'
    details:         dict   # event-type-specific data
    equity_snapshot: float  # equity at the time of the event

# Audit trail MUST NOT be deleted or edited.
# If a correction is needed, add a CORRECTION event on top.
```

---

## 10. execution_layer/ — Execution to Exchange

### 10.1 exchange.py — Abstract Base

```python
from abc import ABC, abstractmethod

class BaseExchange(ABC):
    @abstractmethod
    async def place_order(self, order: OrderRequest) -> OrderResponse: ...

    @abstractmethod
    async def cancel_order(self, symbol: str, order_id: str) -> bool: ...

    @abstractmethod
    async def get_order_status(self, symbol: str, client_order_id: str) -> OrderStatus: ...

    @abstractmethod
    async def get_balance(self, asset: str) -> float: ...

    @abstractmethod
    async def get_position(self, symbol: str) -> Position | None: ...

    @abstractmethod
    async def set_leverage(self, symbol: str, leverage: int) -> bool: ...

    @abstractmethod
    def get_lot_filter(self, symbol: str) -> LotFilter: ...
```

---

### 10.2 OrderRequest & OrderResponse Dataclass

```python
@dataclass
class OrderRequest:
    symbol:          str
    side:            str           # BUY | SELL
    order_type:      str           # MARKET | LIMIT | STOP_MARKET
    quantity:        float         # already rounded to step_size
    price:           float         # 0.0 for market
    stop_price:      float         # for STOP_MARKET
    client_order_id: str           # idempotency key
    is_futures:      bool  = False
    reduce_only:     bool  = False  # for close position only
    time_in_force:   str   = 'GTC'  # GTC | IOC | FOK

@dataclass
class OrderResponse:
    exchange_order_id: str
    client_order_id:   str
    status:            str          # NEW|FILLED|PARTIALLY_FILLED|CANCELLED|REJECTED
    filled_qty:        float
    avg_price:         float
    commission:        float
    commission_asset:  str          # 'USDT' or 'BNB'
    timestamp:         datetime
    raw_response:      dict         # raw response from exchange
```

---

### 10.3 smart_router.py — Cross-Exchange Routing

| Routing Strategy | When Used | How It Works |
|---|---|---|
| Best Price | Default | Compare spread + commission across all exchanges, pick cheapest |
| Primary Exchange | If best price diff < 0.05% | Always use primary exchange (Binance) for consistency |
| Fallback | Primary down/rate limited | Automatically switch to secondary exchange |
| Split Order | Order size > 30% of exchange ADV | Split order across 2–3 exchanges to reduce market impact |

---

### 10.4 Retry Policy — Execution

| Error Type | Max Retry | Delay | Alert? | Final Action |
|---|---|---|---|---|
| Network timeout | 3x | 1s, 2s, 4s | After 3x | mark_failed() + log |
| Rate limit (429) | 5x | 5s, 10s, 20s, 40s, 80s | After 2x | Wait for window reset |
| Order rejected (400) | 1x | 0s | Yes | Check reason, likely a bug |
| Insufficient balance | 0x | — | Yes | Block signal, alert |
| Exchange maintenance | Infinite | 60s interval | Immediately | Wait for maintenance to end |

---

## 11. exit_layer/ — Position Exit Management

### 11.1 exit_manager.py — Coordinator

Called every tick for each active position. Checks all exit conditions in sequence.

```python
class ExitManager:

    async def evaluate(self, position: Position, state: MarketState) -> ExitDecision:
        """
        Check sequence (stops at first condition met):
        1. Hard SL:        price breaks sl_price → EXIT_SL
        2. Hard TP:        price breaks tp_price → EXIT_TP
        3. Break-even:     profit > threshold, shift SL to entry → UPDATE_SL
        4. Trailing stop:  update trail level → UPDATE_SL or EXIT_TRAIL
        5. Strategy exit:  strategy.should_exit() → EXIT_SIGNAL
        6. Time-based exit: position open too long without progress → EXIT_TIMEOUT
        7. None of above → HOLD
        """
```

**ExitDecision Dataclass**

```python
@dataclass
class ExitDecision:
    action:     str     # HOLD|EXIT_SL|EXIT_TP|EXIT_TRAIL|EXIT_SIGNAL|EXIT_TIMEOUT|UPDATE_SL
    exit_price: float   # exit price (0 if HOLD or UPDATE_SL)
    new_sl:     float   # new SL (only for UPDATE_SL)
    reason:     str
    urgency:    str     # normal | urgent (urgent = market order, no wait)
```

---

### 11.2 trailing.py — Trailing Stop

| Method | Formula | Config Key | When Used |
|---|---|---|---|
| **ATR-based** | trail = high - (N × ATR) | TRAIL_ATR_MULT = 2.0 | Default — adapts to volatility |
| **Percentage** | trail = high × (1 - pct) | TRAIL_PCT = 0.02 | Pairs with low volatility |
| **Chandelier** | trail = highest_high(N) - (M × ATR) | TRAIL_CHANDELIER_N = 22 | Long-term trend following |

**Exit Layer Config Keys**

| Key | Default | Description |
|---|---|---|
| `BREAKEVEN_TRIGGER_R` | 1.0 | Activate break-even after profit = 1× risk (1R) |
| `BREAKEVEN_BUFFER_PIPS` | 5 | Buffer above entry when shifting to break-even |
| `PARTIAL_TP_ENABLED` | True | Close 50% of position at TP1, trail the rest |
| `PARTIAL_TP_RATIO` | 0.5 | Proportion of position closed at first TP |
| `MAX_HOLD_CANDLES` | 48 | Force close if position open > N candles without progress |

---

## 12. monitoring/ & sync/ — System Health

### 12.1 heartbeat.py

```python
class Heartbeat:
    INTERVAL_SECONDS = 30
    DEAD_THRESHOLD   = 90    # if no heartbeat for 90s → DEAD

    async def start(self):
        """
        Every 30 seconds:
        - Write timestamp to heartbeat.db
        - Check that all coroutines are still running
        - Update Prometheus metrics (agent_alive gauge)
        """

    async def check(self) -> HeartbeatStatus:
        """
        Called from health_restart.py (automation).
        Return ALIVE | STALE | DEAD
        STALE = heartbeat exists but is old (30–90 seconds)
        DEAD  = no heartbeat for > 90 seconds → trigger restart
        """
```

---

### 12.2 health_check.py

| Component | Check | Threshold | Action if Failed |
|---|---|---|---|
| WebSocket | Last message timestamp | < 60 seconds ago | Reconnect WS |
| Exchange REST | Ping /api/v3/ping | Response < 2000ms | Alert + retry |
| Database | SELECT 1 from state.db | < 100ms | Critical alert |
| Memory usage | RSS process | < 500 MB | Alert + log |
| Disk space | Free space in data dir | < 1 GB | Critical alert |
| Open orders | Number of pending orders | < max_open_positions × 2 | Alert if stuck |

---

### 12.3 sync/ — Reconciliation Protocol

| File | Frequency | What is Synced | On Conflict |
|---|---|---|---|
| `balance_sync.py` | Every 60 seconds | Actual USDT balance from exchange | Update internal, alert if diff > 1% |
| `order_sync.py` | Every 30 seconds | Status of all open orders | Update DB to exchange status |
| `position_sync.py` | Every 60 seconds | Actual futures positions vs internal | Alert + log — requires manual investigation |
| `reconciliation.py` | Daily (UTC 00:00) | Full audit: PnL, fees, all trades | Generate report, save to audit/ |

> **TRUTH OF RECORD:** The exchange is always the source of truth. If internal state differs from the exchange, ALWAYS update internal to match the exchange — never the other way around.

---

## 13. learning_layer/ — Adaptation & Reflection

### 13.1 drift_detection.py

Detects whether the distribution of input features has shifted from the training-time distribution. This is the early warning system for model degradation.

| Metric | Formula | Alert Threshold | Frequency |
|---|---|---|---|
| PSI per feature | Population Stability Index | PSI > 0.2 | Daily |
| Rolling IC | Spearman(pred, actual) last 20 days | IC < 0.03 | Daily |
| Win rate drift | 30-day WR vs baseline | Drop > 10% | Daily |
| Prediction distribution | KL divergence pred vs training dist | KL > 0.5 | Per batch of 100 predictions |

---

### 13.2 strategy_killer.py — Auto-Disable

```python
class StrategyKiller:
    """
    Auto-disable underperforming strategies.

    Disable criteria (ANY one met):
    - Consecutive losses > MAX_CONSECUTIVE_LOSSES (default: 7)
    - 30-day Sharpe < SHARPE_KILL_THRESHOLD (default: -0.5)
    - 30-day win rate < WIN_RATE_KILL_THRESHOLD (default: 35%)
    - 30-day max drawdown > DD_KILL_THRESHOLD (default: 15%)

    When disabled:
    1. Stop accepting new signals from this strategy
    2. Existing positions continue to be managed until closed
    3. Send notification: strategy X disabled with reasons
    4. Save to disabled_strategies.json for audit
    5. Trigger re-evaluation after 7 days (or manual review)
    """
```

---

## 14. llm_layer/ — AI Advisor (Optional)

> **CORE PRINCIPLE:** The LLM acts only as an advisor/filter by weighting confidence scores. The LLM **NEVER** executes orders directly. Execution always goes through `risk_layer` and `trade_layer`.

### 14.1 llm_budget.py — Cost Control

```python
class LLMBudget:

    async def can_call(self, estimated_tokens: int) -> bool:
        """
        Check if there is budget remaining to call the LLM.
        Computes: estimated_cost = tokens × price_per_token
        Returns False if daily_spent + estimated_cost > DAILY_BUDGET_USD
        """

    async def record_usage(self, input_tokens: int, output_tokens: int,
                           model: str) -> float:
        """Record usage and return actual cost in USD."""

    def get_remaining_budget(self) -> float:
        """Return remaining budget for today in USD."""
```

**Model Pricing (Reference — update to latest rates)**

| Model | Input (per 1M tokens) | Output (per 1M tokens) | Recommended Use |
|---|---|---|---|
| claude-sonnet-4 | $3.00 | $15.00 | Default — balance between quality and cost |
| claude-haiku-4.5 | $0.25 | $1.25 | High-frequency analysis (per signal) |
| claude-opus-4 | $15.00 | $75.00 | Deep analysis only — limit to 1–2x/day |

---

### 14.2 llm_filter.py — Scoring Only

The LLM produces a `confidence_multiplier` (0.0–1.5) that is multiplied against the original signal confidence. This is not a binary go/no-go decision.

```python
class LLMFilter:

    async def score_signal(self, signal: Signal, state: MarketState) -> LLMScore:
        """
        Input to LLM:
        - Current market conditions (regime, volatility, spread)
        - Signal generated by the strategy
        - Last 10 trades for context

        Output from LLM (JSON):
        {
          'confidence_multiplier': 0.8,    // 0.0–1.5
          'reasoning':             'string',
          'concerns':              ['list of concerns'],
          'proceed':               true
        }

        Final confidence = signal.confidence × multiplier
        If final < MIN_SIGNAL_CONFIDENCE → signal is ignored
        """
```

---

## 15. notification/ & utils/ — Supporting Components

### 15.1 notifier.py — Notification Routing

| Event | Channel | Priority | Format |
|---|---|---|---|
| Trade opened | Telegram | Normal | Symbol, side, qty, price, SL, TP |
| Trade closed (profit) | Telegram | Normal | Symbol, PnL USD, PnL%, duration |
| Trade closed (loss) | Telegram + Discord | High | Symbol, PnL USD, PnL%, exit reason |
| Circuit breaker triggered | Telegram + Discord | URGENT | Reason, current equity, daily PnL |
| Strategy disabled | Telegram + Discord | High | Strategy ID, reason, performance |
| System error | Telegram + Discord | URGENT | Error message + stack trace |
| Daily summary | Telegram | Normal | Total PnL, win rate, trade count, equity |

---

### 15.2 structured_logger.py — JSON Logging

All logs are written in JSON format so they can be queried by Prometheus and displayed in Grafana.

```json
// Standard log format (each line = one JSON object)
{
  "timestamp": "2024-11-15T08:30:00.123Z",
  "level":     "INFO",
  "layer":     "trade_layer",
  "event":     "trade_opened",
  "trade_id":  "uuid-...",
  "symbol":    "BTCUSDT",
  "side":      "BUY",
  "qty":       0.001,
  "price":     50000.0,
  "mode":      "paper",
  "equity":    10234.5
}

// Required fields in every log:
// timestamp (ISO 8601 UTC), level, layer, event
// Optional fields: trade_id, symbol, strategy_id, equity
```

---

### 15.3 utils/helpers.py — Common Functions

| Function | Parameters | Return | Notes |
|---|---|---|---|
| `round_qty()` | qty: float, step: float | float | Floor to step_size — do NOT use regular round |
| `round_price()` | price: float, tick: float | float | Round to nearest tick_size |
| `safe_div()` | a: float, b: float, default: float=0 | float | Prevent ZeroDivisionError |
| `retry_async()` | coro, max_retry, backoff | Any | Decorator for retry with exponential backoff |
| `utcnow()` | — | datetime (timezone-aware UTC) | Always use this, never datetime.utcnow() |

---

## 16. Checklist — Deploy Runtime to Live Mode

| No | Checklist Item | Verification | Responsible |
|---|---|---|---|
| 1 | `.env` has all required fields filled (API key, master key, notifications) | `python -c 'from core.config import AgentConfig; AgentConfig()'` | DevOps |
| 2 | `validate_permissions()` → `can_withdraw=False` | Log at startup: 'Permissions OK' | Security |
| 3 | `AgentConfig` passes Pydantic validation without errors | No `ValueError` at startup | Dev |
| 4 | `recovery.py` completes with no unresolved discrepancies | Log: 'Recovery complete, 0 discrepancies' | Dev |
| 5 | `full_reconciliation()` shows balance matches | balance_sync delta < 0.1% | Ops |
| 6 | All heartbeat checks are green | `health_check.py` all components HEALTHY | Ops |
| 7 | Paper mode run for at least 2 weeks with positive PnL | `performance.db` shows Sharpe > 1 | Quant |
| 8 | Shadow mode run for at least 1 week — no ghost orders | `order_registry.db` has no DUPLICATE | Dev |
| 9 | Rate limiter configured for the Binance account tier | No 429 errors in paper/shadow | Dev |
| 10 | Circuit breaker thresholds adjusted to capital | `max_daily_loss_pct` matches risk appetite | Quant |
| 11 | Telegram + Discord notifications working | Send a manual test alert | Ops |
| 12 | Grafana dashboard shows all metrics | `prometheus_metrics` endpoint accessible | Ops |
| 13 | `rollback_guide.md` has been read and understood | Team acknowledgment | All |
| 14 | `state.db` backup is in place before going live | Cron backup is active | Ops |
| 15 | Initial capital is fixed — no more than the agreed budget | `INITIAL_EQUITY` in `.env` = agreed budget | Lead |

---

> **This document is the implementation contract for the Runtime Layer.**
> Any changes to public interfaces, config key defaults, or dataclass formats
> **MUST** be updated here before merging to the main branch.
>
> *Related references: research_layer_docs.md · risk_layer skeleton code*
