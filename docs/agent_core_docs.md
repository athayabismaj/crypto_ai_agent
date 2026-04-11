# crypto_ai_agent — Agent Core Documentation

*main loop · config · modes · scheduler · event bus · safe mode · memory · logs*

**Version 1.0** | Reference: runtime_layer_docs.md

---

## 1. Overview — Agent Core

Agent Core is the heart of the entire runtime. It does **not** make trading decisions, analyze markets, or execute orders. Its responsibilities are:

- Initialize all layers in the correct and safe order
- Keep the main event loop running, stable, and fault-tolerant
- Distribute events between layers without direct coupling
- Save and restore state after crashes or restarts
- Provide a single control point for halt and emergency shutdown

> **POSITION IN THE SYSTEM:**
> Agent Core is the most critical layer. Bugs here affect the entire system.
> All changes to files in `core/` must go through code review and have 100% test coverage.

---

### 1.1 File Map & Responsibilities

| File | Responsibility | Called By | Depends On |
|---|---|---|---|
| **main.py** | Entry point — startup, main loop, shutdown | OS / Docker CMD | All layers |
| **config.py** | Load & merge config from .env + YAML | main.py | security/key_manager |
| **config_schema.py** | Validate config via Pydantic | config.py | pydantic |
| **modes.py** | Define PAPER/SHADOW/LIVE modes + behavior | main.py, all layers | — |
| **scheduler.py** | Periodic tasks: daily reset, retrain check, etc. | main.py | asyncio |
| **event_bus.py** | Async pub/sub between layers | All layers | asyncio.Queue |
| **safe_mode.py** | Emergency stop & graceful shutdown | monitoring, main.py | execution, notification |

---

## 2. main.py — Entry Point & Main Loop

### 2.1 Startup Sequence — Step by Step

The initialization order **MUST NOT** be changed. Each step depends on the previous one.

| Step | Action | On Failure | Timeout |
|---|---|---|---|
| T-01 | Setup logging (structured_logger) — must be first so all errors are recorded | Exit code 1 — no diagnostic info | — |
| T-02 | Load config.py → validate via AgentConfig (Pydantic) | Log failing fields, exit code 1 | 2s |
| T-03 | Initialize KeyManager, validate exchange permissions | Exit code 1 if can_withdraw=True or key invalid | 5s |
| T-04 | Initialize database connections (state.db, order_registry.db, etc.) | Exit code 1 — cannot proceed without persistent storage | 3s |
| T-05 | Run migration/migrate.py — ensure DB schema is up-to-date | Exit code 1 if any migration fails | 30s |
| T-06 | Initialize DataLayer — REST connection, check exchange status | Exit code 1 if exchange is in maintenance | 10s |
| T-07 | Load ML model from agent/models/ — validate feature_names vs config | Exit code 1 if model is incompatible | 5s |
| T-08 | Initialize all layers: intelligence, portfolio, risk, strategy, trade, execution, exit | Exit code 1 per failing layer | 15s |
| T-09 | Run RecoveryManager.restore() — reconcile state after crash | Enter safe_mode if unresolved discrepancies found | 60s |
| T-10 | Run SyncManager.full_reconciliation() — sync balance + positions | Alert + continue in cautious mode | 30s |
| T-11 | Start WebSocket streams (market data, user stream) | Retry 3x then exit code 1 | 20s |
| T-12 | Start Heartbeat & MonitoringSystem | Alert, continue (non-fatal) | 5s |
| T-13 | Start Scheduler (periodic tasks) | Alert, continue (non-fatal) | 2s |
| T-14 | Start main event loop — begin receiving candles | — | — |

> **TOTAL STARTUP TIME:** Target completion under 3 minutes.
> If longer than 3 minutes, log a warning. If longer than 5 minutes, auto-restart via health_restart.py.

---

### 2.2 Main Loop — Full Pseudocode

```python
async def main_loop(
    data_layer, intel_layer, portfolio, risk_mgr,
    strategy, trade_mgr, executor, exit_mgr, sync, monitoring
):
    async for candle in data_layer.stream():
        # Guard: check halt flag before anything
        if safe_mode.is_halted():
            continue

        tick_start = utcnow()

        try:
            # ── STEP 1: Validate data ─────────────────────────────
            if not data_layer.validator.is_valid(candle):
                log.warning('Invalid candle, skip', candle=candle)
                continue

            # ── STEP 2: Update intelligence ───────────────────────
            market_state = intel_layer.update(candle)

            # ── STEP 3: Manage active positions (exit checks) ─────
            for position in trade_mgr.get_open_positions():
                exit_decision = exit_mgr.evaluate(position, market_state)
                if exit_decision.action != 'HOLD':
                    await trade_mgr.close(position, exit_decision)

            # ── STEP 4: Periodic sync ─────────────────────────────
            if scheduler.is_due('sync'):
                await sync.run_sync()
                risk_mgr.update_equity(portfolio.current_equity)

            # ── STEP 5: Generate & process signal ─────────────────
            signal = strategy.generate_signal(market_state)
            if signal is None:
                continue

            # ── STEP 6: Risk evaluation ───────────────────────────
            risk_result = risk_mgr.evaluate(
                TradeRequest.from_signal(signal),
                portfolio.get_state()
            )
            if not risk_result.is_approved:
                event_bus.publish(SIGNAL_BLOCKED, signal, risk_result)
                continue

            # ── STEP 7: Idempotency check ─────────────────────────
            idem = trade_mgr.idempotency.check_or_register(signal)
            if idem == DUPLICATE:
                continue

            # ── STEP 8: Execute ───────────────────────────────────
            trade = await trade_mgr.open(signal, risk_result)
            order_resp = await executor.place_order(trade.to_order_request())
            trade_mgr.confirm(trade, order_resp)

            # ── STEP 9: Post-trade ────────────────────────────────
            portfolio.update(trade)
            event_bus.publish(TRADE_OPENED, trade)

        except CircuitBreakerHalt:
            log.error('Circuit breaker triggered, halting')
            await safe_mode.emergency_stop('Circuit breaker')
            break

        except Exception as e:
            log.error('Unhandled tick error', error=e)
            monitoring.record_error(e)
            # Continue to next candle — do not crash the loop

        finally:
            tick_duration = (utcnow() - tick_start).total_seconds()
            metrics.observe('tick_duration_seconds', tick_duration)
            if tick_duration > MAX_TICK_DURATION:
                log.warning('Slow tick', duration=tick_duration)
```

---

### 2.3 Performance Budget Per Tick

| Step | Time Budget | Action If Exceeded |
|---|---|---|
| Data validation (step 1) | < 5 ms | Log warning — do not block |
| Intelligence update (step 2) | < 50 ms | Log warning — use previous state if timeout |
| Exit checks all positions (step 3) | < 100 ms | Log warning — positions still checked |
| Signal generation + model inference (step 5) | < 200 ms | Skip signal if > 500ms |
| Risk evaluation (step 6) | < 30 ms | Must be fast — no I/O here |
| Order execution (step 8) | < 2000 ms | Alert latency_guard, retry logic active |
| Total tick (1H candle) | < 3000 ms | Alert if consistently > 3s |

> **IMPORTANT:** The main loop **MUST NOT** crash due to an exception from a single tick.
> All exceptions must be caught at the tick level, logged, and the loop continues to the next candle.
> The only things that may stop the loop are `CircuitBreakerHalt` and `SafeMode.halt`.

---

## 3. config.py & config_schema.py — Configuration

### 3.1 Configuration Hierarchy

Config values are taken from multiple sources in priority order (higher = higher priority):

1. Environment variables (from .env loaded via python-dotenv)
2. File `config/agent_config.yaml` (defaults that can be overridden)
3. Hardcoded defaults inside the AgentConfig dataclass

> **RULE:** NEVER hardcode sensitive values (API keys, passwords) in code or YAML.
> All secrets MUST come from environment variables.

---

### 3.2 AgentConfig — Complete Pydantic Model

```python
from pydantic import BaseModel, Field, validator
from typing import Literal

class AgentConfig(BaseModel):
    # ── MODE ──────────────────────────────────────────────────
    mode: Literal['paper', 'shadow', 'live'] = 'paper'
    initial_equity:       float = Field(10_000.0, gt=0)

    # ── TRADING UNIVERSE ──────────────────────────────────────
    symbols:              list[str]  = ['BTCUSDT']
    timeframe:            str        = '1h'
    exchange:             str        = 'binance'
    market_type:          str        = 'spot'   # spot | futures

    # ── RISK ──────────────────────────────────────────────────
    risk_per_trade_pct:   float = Field(0.01,  ge=0.001, le=0.05)
    max_daily_loss_pct:   float = Field(0.05,  ge=0.01,  le=0.15)
    max_drawdown_pct:     float = Field(0.10,  ge=0.05,  le=0.30)
    max_consecutive_loss: int   = Field(5,     ge=3,     le=15)
    max_open_positions:   int   = Field(5,     ge=1,     le=20)
    global_max_leverage:  int   = Field(3,     ge=1,     le=20)

    # ── SIZING ────────────────────────────────────────────────
    sizing_method:        str   = 'fixed_fractional'
    max_position_pct:     float = Field(0.10, ge=0.01, le=0.30)
    min_notional_usd:     float = Field(10.0, ge=5.0)

    # ── EXECUTION ─────────────────────────────────────────────
    execution_delay_ms:   int   = Field(100,   ge=0, le=5000)
    order_timeout_s:      int   = Field(30,    ge=5, le=300)
    max_order_retry:      int   = Field(3,     ge=1, le=10)
    slippage_tolerance:   float = Field(0.005, ge=0, le=0.02)

    # ── STRATEGY ──────────────────────────────────────────────
    strategy_id:          str   = 'spot_strategy_v1'
    min_signal_confidence:float = Field(0.60,  ge=0.3, le=1.0)
    signal_cooldown:      int   = Field(3,     ge=1,   le=20)

    # ── EXIT ──────────────────────────────────────────────────
    default_sl_pct:       float = Field(0.02,  ge=0.005, le=0.10)
    default_tp_ratio:     float = Field(2.0,   ge=1.0,   le=10.0)
    trailing_method:      str   = 'atr'   # atr | percentage | chandelier
    trail_atr_mult:       float = Field(2.0,   ge=1.0, le=5.0)
    breakeven_trigger_r:  float = Field(1.0,   ge=0.5, le=3.0)
    max_hold_candles:     int   = Field(48,    ge=10,  le=200)

    # ── SYNC ──────────────────────────────────────────────────
    balance_sync_interval_s:  int = Field(60,  ge=30,  le=300)
    order_sync_interval_s:    int = Field(30,  ge=15,  le=120)
    position_sync_interval_s: int = Field(60,  ge=30,  le=300)

    # ── LLM (optional) ────────────────────────────────────────
    llm_enabled:          bool  = False
    llm_model:            str   = 'claude-haiku-4-5-20251001'
    llm_daily_budget_usd: float = Field(1.0,  ge=0.0, le=50.0)

    # ── NOTIFICATION ──────────────────────────────────────────
    notify_trade_open:    bool  = True
    notify_trade_close:   bool  = True
    notify_daily_summary: bool  = True
    notify_circuit_break: bool  = True

    # ── VALIDATORS ────────────────────────────────────────────
    @validator('timeframe')
    def validate_tf(cls, v):
        valid = ['1m', '5m', '15m', '30m', '1h', '4h', '1d']
        if v not in valid:
            raise ValueError(f'timeframe must be one of {valid}')
        return v

    @validator('market_type')
    def validate_market(cls, v):
        if v not in ('spot', 'futures'):
            raise ValueError('market_type must be spot or futures')
        return v

    @validator('global_max_leverage')
    def leverage_spot_check(cls, v, values):
        if values.get('market_type') == 'spot' and v > 1:
            raise ValueError('Spot trading does not support leverage > 1')
        return v
```

---

### 3.3 agent_config.yaml — Template

```yaml
# config/agent_config.yaml
# Values here can be overridden by environment variables

mode: paper
initial_equity: 10000.0

symbols:
  - BTCUSDT
  - ETHUSDT

timeframe: 1h
exchange: binance
market_type: spot

# Risk
risk_per_trade_pct: 0.01
max_daily_loss_pct: 0.05
max_drawdown_pct: 0.10
max_open_positions: 5

# Sizing
sizing_method: fixed_fractional

# Exit
trailing_method: atr
trail_atr_mult: 2.0

# LLM
llm_enabled: false
```

---

### 3.4 Config Change Log — Must Be Filled

| Date | Field | Old Value | New Value | Reason | Author |
|---|---|---|---|---|---|
| YYYY-MM-DD | risk_per_trade_pct | 0.01 | 0.015 | Backtest OOS Sharpe > 1.5 | Dev |
| YYYY-MM-DD | max_open_positions | 5 | 7 | Added new symbol | Quant |

---

## 4. modes.py — Operating Modes

### 4.1 AgentMode Enum & Behavior

```python
from enum import Enum

class AgentMode(Enum):
    PAPER  = 'paper'
    SHADOW = 'shadow'
    LIVE   = 'live'

class ModeConfig:
    """Controls different behavior per mode."""

    @staticmethod
    def should_send_order(mode: AgentMode) -> bool:
        return mode in (AgentMode.SHADOW, AgentMode.LIVE)

    @staticmethod
    def use_real_money(mode: AgentMode) -> bool:
        return mode == AgentMode.LIVE

    @staticmethod
    def use_testnet(mode: AgentMode) -> bool:
        return mode == AgentMode.SHADOW

    @staticmethod
    def block_on_risk_violation(mode: AgentMode) -> bool:
        """Paper mode: log but don't block. Live mode: hard block."""
        return mode == AgentMode.LIVE

    @staticmethod
    def require_sl(mode: AgentMode) -> bool:
        """SL required in live. Optional in paper (for experimentation)."""
        return mode == AgentMode.LIVE
```

---

### 4.2 Behavior Differences Per Mode

| Behavior | PAPER | SHADOW | LIVE |
|---|---|---|---|
| Send orders to exchange | **No** | **Yes (testnet)** | **Yes (mainnet)** |
| Use real money | No | No | **Yes** |
| Hard risk block | Log only | Log only | **Hard block** |
| SL required | No | No | **Yes** |
| Fill simulation | Internal simulator | Exchange testnet | Exchange mainnet |
| Equity tracking | Simulated | Simulated | Real balance |
| Circuit breaker halt | Simulated | Active | **Active + hard stop** |
| Idempotency enforcement | Active (test) | Active | **Active (strict)** |
| Telegram notifications | Optional | Yes | Yes (all events) |

---

### 4.3 Mode Transition Protection

```python
class ModeTransitionGuard:
    """
    Validates before a mode change is allowed.
    Called from gateway/routes.py when a mode change request arrives.
    """

    def validate_transition(
        self,
        from_mode:     AgentMode,
        to_mode:       AgentMode,
        current_state: SystemState,
    ) -> TransitionResult:

        # Allowed transitions:
        ALLOWED = {
            AgentMode.PAPER:  [AgentMode.SHADOW],
            AgentMode.SHADOW: [AgentMode.LIVE, AgentMode.PAPER],
            AgentMode.LIVE:   [AgentMode.SHADOW],  # cannot go directly to PAPER
        }

        if to_mode not in ALLOWED[from_mode]:
            return TransitionResult.FORBIDDEN

        if current_state.open_positions_count > 0:
            return TransitionResult.HAS_OPEN_POSITIONS

        if current_state.pending_orders_count > 0:
            return TransitionResult.HAS_PENDING_ORDERS

        return TransitionResult.ALLOWED
```

---

## 5. scheduler.py — Periodic Tasks

### 5.1 Scheduled Task List

| Task ID | Interval | Function | On Failure |
|---|---|---|---|
| daily_reset | UTC 00:00 | risk_mgr.reset_daily_stats(), circuit_breaker.reset_daily() | Alert + retry tomorrow |
| balance_sync | 60 seconds | sync.balance_sync.run() | Log warning, continue |
| order_sync | 30 seconds | sync.order_sync.run() | Log warning, continue |
| position_sync | 60 seconds | sync.position_sync.run() | Alert + log |
| health_check | 30 seconds | monitoring.health_check.run() | Alert if component unhealthy |
| drift_check | Daily 06:00 | learning.drift_detection.check() | Alert if PSI > 0.2 |
| strategy_eval | Daily 07:00 | learning.strategy_killer.evaluate_all() | Log result, disable if needed |
| daily_reconcile | UTC 00:30 | sync.reconciliation.run_daily() | Alert if discrepancy found |
| daily_summary | UTC 23:55 | notifier.send_daily_summary() | Log error, continue |
| llm_budget_reset | UTC 00:00 | llm_budget.reset_daily() | Log warning |
| db_vacuum | Weekly Sunday | VACUUM all SQLite databases | Alert, retry next week |
| log_rotation | Daily 01:00 | Compress & archive logs > 7 days | Alert disk space |

---

### 5.2 Scheduler Interface

```python
class Scheduler:

    def __init__(self, config: AgentConfig):
        self._tasks: dict[str, ScheduledTask] = {}
        self._loop = asyncio.get_event_loop()

    def register(
        self,
        task_id:  str,
        coro:     Coroutine,
        interval: timedelta | CronSchedule,
        timeout:  int = 60,
        on_error: Callable = None,
    ) -> None:
        """Register a task with the scheduler. Called in main.py at startup."""

    def is_due(self, task_id: str) -> bool:
        """
        Check if a task is due to run.
        Used in main_loop for tasks triggered per tick.
        Example: if scheduler.is_due('balance_sync'): await sync.balance_sync.run()
        """

    async def start(self) -> None:
        """Start all background tasks. Called after all layers are ready."""

    async def stop(self) -> None:
        """Gracefully stop all tasks. Wait for running tasks to complete."""
```

---

### 5.3 CronSchedule — Format

```python
# Format: CronSchedule(hour, minute, weekday=None)
# weekday: 0=Monday, 6=Sunday, None=every day

daily_reset    = CronSchedule(hour=0,  minute=0)     # UTC 00:00 every day
daily_summary  = CronSchedule(hour=23, minute=55)    # UTC 23:55 every day
weekly_vacuum  = CronSchedule(hour=2,  minute=0, weekday=6)  # Sunday 02:00

# Regular intervals (timedelta)
balance_sync   = timedelta(seconds=60)
order_sync     = timedelta(seconds=30)
```

---

## 6. event_bus.py — Inter-Layer Communication

Event bus enables layers to communicate without importing each other directly. Publishers don't know who is listening. Subscribers don't know who is publishing.

### 6.1 Implementation

```python
import asyncio
from dataclasses import dataclass
from enum import Enum
from typing import Callable, Coroutine

class EventType(Enum):
    # Data events
    CANDLE_READY         = 'candle_ready'
    ORDERBOOK_UPDATE     = 'orderbook_update'
    TICKER_UPDATE        = 'ticker_update'
    # Intelligence events
    REGIME_CHANGED       = 'regime_changed'
    VOLATILITY_SPIKE     = 'volatility_spike'
    # Strategy events
    SIGNAL_GENERATED     = 'signal_generated'
    SIGNAL_BLOCKED       = 'signal_blocked'
    # Trade events
    TRADE_OPENED         = 'trade_opened'
    TRADE_CLOSED         = 'trade_closed'
    TRADE_UPDATED        = 'trade_updated'       # SL/TP changed
    # Risk events
    CIRCUIT_BREAKER      = 'circuit_breaker'
    RISK_VIOLATION       = 'risk_violation'
    EQUITY_UPDATE        = 'equity_update'
    # System events
    SYSTEM_ERROR         = 'system_error'
    HEALTH_STATUS        = 'health_status'
    SAFE_MODE_TRIGGERED  = 'safe_mode_triggered'

@dataclass
class Event:
    event_type: EventType
    payload:    any
    timestamp:  datetime
    source:     str        # name of the publishing layer

class EventBus:

    def __init__(self):
        self._subscribers: dict[EventType, list[Callable]] = defaultdict(list)
        self._queue: asyncio.Queue[Event] = asyncio.Queue(maxsize=1000)

    def subscribe(self, event_type: EventType, handler: Callable) -> None:
        """Register a handler for a specific event type."""
        self._subscribers[event_type].append(handler)

    def publish(self, event_type: EventType, payload: any, source: str) -> None:
        """Non-blocking publish — insert into queue."""
        event = Event(event_type, payload, utcnow(), source)
        try:
            self._queue.put_nowait(event)
        except asyncio.QueueFull:
            log.error('EventBus queue full — event dropped', event_type=event_type)

    async def dispatch_loop(self) -> None:
        """Background loop that takes events from queue and dispatches to handlers."""
        async for event in self._queue:
            for handler in self._subscribers[event.event_type]:
                try:
                    await handler(event)
                except Exception as e:
                    log.error('Handler error', handler=handler, error=e)
```

---

### 6.2 Event Routing Table

| EventType | Publisher | Subscriber(s) | Payload Type |
|---|---|---|---|
| CANDLE_READY | data_layer | intel, strategy, exit_mgr | Candle |
| REGIME_CHANGED | intel_layer | strategy, notification | MarketRegime |
| SIGNAL_GENERATED | strategy_layer | risk_layer, trade_layer, llm_layer | Signal |
| SIGNAL_BLOCKED | risk_layer | learning_layer, notification | BlockedSignal |
| TRADE_OPENED | trade_layer | exit_layer, portfolio, monitoring | Trade |
| TRADE_CLOSED | exit_layer | trade_layer, portfolio, learning | TradeResult |
| CIRCUIT_BREAKER | risk_layer | main_loop, monitoring, notification | CircuitState |
| EQUITY_UPDATE | portfolio_layer | risk_layer | float |
| SYSTEM_ERROR | All layers | monitoring, notification | ErrorReport |
| SAFE_MODE_TRIGGERED | safe_mode | main_loop, notification | SafeModeEvent |

> **RULE:** Event handlers **MUST NOT** perform blocking operations (direct I/O, heavy DB queries, HTTP requests).
> Handlers may only: update in-memory state, push to another queue, or trigger lightweight coroutines.

---

## 7. safe_mode.py — Emergency Stop

SafeMode is the emergency shutdown mechanism that ensures the system stops safely without leaving positions unmonitored.

### 7.1 Trigger Conditions

| Trigger | Source | Auto or Manual | Close Positions? |
|---|---|---|---|
| Circuit breaker HALTED | risk_layer | Auto | No (positions remain, SL still active) |
| Exchange maintenance detected | data_layer | Auto | No |
| Unresolved discrepancy in recovery | trade_layer | Auto | No |
| API key invalid / permissions changed | security | Auto | No |
| WebSocket down > 5 minutes without reconnect | data_layer | Auto | No |
| Memory usage > 90% threshold | monitoring | Auto | No |
| Manual halt via gateway endpoint | gateway/api | Manual | Optional (parameter) |
| Manual halt via Telegram command | notification | Manual | Optional (parameter) |

---

### 7.2 Emergency Stop — Implementation

```python
class SafeMode:

    _halt_flag: asyncio.Event = asyncio.Event()

    def is_halted(self) -> bool:
        return self._halt_flag.is_set()

    async def emergency_stop(
        self,
        reason:          str,
        close_positions: bool = False,
        exit_code:       int  = 2,
    ) -> None:
        """
        EMERGENCY SHUTDOWN SEQUENCE — DO NOT CHANGE THE ORDER:
        """
        # Step 1: Set halt flag — main_loop.tick() returns immediately
        self._halt_flag.set()
        log.critical('SAFE MODE TRIGGERED', reason=reason)

        # Step 2: Publish event to all subscribers
        event_bus.publish(SAFE_MODE_TRIGGERED, reason, 'safe_mode')

        # Step 3: Optional — close all positions
        if close_positions:
            await self._close_all_positions()

        # Step 4: Cancel all pending orders
        await self._cancel_all_pending_orders()

        # Step 5: Save state snapshot
        await self._save_state_snapshot(reason)

        # Step 6: Send notification
        await notifier.send_emergency_alert(
            reason          = reason,
            equity          = portfolio.current_equity,
            open_positions  = trade_mgr.count_open_positions(),
        )

        # Step 7: Stop scheduler tasks
        await scheduler.stop()

        # Step 8: Close DB connections gracefully
        await db_manager.close_all()

        # Step 9: Exit
        log.info('Safe mode complete, exiting', code=exit_code)
        sys.exit(exit_code)

    async def graceful_shutdown(self) -> None:
        """
        Normal shutdown (maintenance, update):
        1. Stop accepting new signals
        2. Wait for in-progress tick to finish (30s timeout)
        3. Save state
        4. Exit code 0
        """
```

---

### 7.3 State Snapshot — Format

```json
{
  "snapshot_id":    "20241115_083000_circuit_breaker",
  "reason":         "Circuit breaker HALTED — daily loss 5.2%",
  "timestamp":      "2024-11-15T08:30:00Z",
  "mode":           "live",
  "equity":         9480.0,
  "daily_pnl":      -520.0,
  "open_positions": [
    {"symbol": "BTCUSDT", "side": "BUY", "qty": 0.001, "entry": 50000}
  ],
  "pending_orders": [],
  "circuit_state":  "HALTED",
  "halt_reasons":   ["daily_loss_pct 5.2% > max 5.0%"]
}
```

---

## 8. memory/ — Persistent Storage

All state is stored in SQLite databases. Each database has a schema managed by `migration/`. There is no direct SQL outside the designated layer files.

### 8.1 Database Map

| File | Contents | Read By | Written By | Estimated Size |
|---|---|---|---|---|
| **state.db** | Active positions + open order status | trade_layer, exit_layer | trade_layer, sync | < 10 MB |
| **experience.db** | History of all closed trades | learning_layer, audit | trade_layer | < 100 MB / year |
| **performance.db** | Performance statistics per strategy | strategy_killer, risk | trade_layer (after close) | < 5 MB |
| **order_registry.db** | Idempotency store — all order IDs | idempotency.py | idempotency.py | < 20 MB / year |
| **execution_cache.db** | Cache for retry tracking per order | execution_layer | execution_layer | < 5 MB |

---

### 8.2 Schema — state.db

```sql
-- Main active positions table
CREATE TABLE positions (
    trade_id          TEXT PRIMARY KEY,
    client_order_id   TEXT UNIQUE NOT NULL,
    exchange_order_id TEXT,
    symbol            TEXT NOT NULL,
    side              TEXT NOT NULL,        -- BUY | SELL
    order_type        TEXT NOT NULL,
    requested_qty     REAL NOT NULL,
    filled_qty        REAL DEFAULT 0,
    avg_fill_price    REAL DEFAULT 0,
    sl_price          REAL,
    tp_price          REAL,
    risk_amount_usd   REAL,
    status            TEXT DEFAULT 'pending', -- pending|open|closed|cancelled|failed
    strategy_id       TEXT,
    mode              TEXT NOT NULL,
    created_at        TEXT NOT NULL,        -- ISO 8601 UTC
    opened_at         TEXT,
    updated_at        TEXT
);

-- Audit trail — APPEND ONLY, never deleted
CREATE TABLE audit_events (
    event_id          TEXT PRIMARY KEY,
    trade_id          TEXT NOT NULL,
    event_type        TEXT NOT NULL,
    timestamp         TEXT NOT NULL,
    actor             TEXT NOT NULL,
    details           TEXT NOT NULL,        -- JSON string
    equity_snapshot   REAL
);

-- State snapshot at safe mode
CREATE TABLE snapshots (
    snapshot_id       TEXT PRIMARY KEY,
    reason            TEXT NOT NULL,
    timestamp         TEXT NOT NULL,
    payload           TEXT NOT NULL        -- JSON string
);
```

---

### 8.3 Schema — order_registry.db

```sql
CREATE TABLE order_registry (
    client_order_id   TEXT PRIMARY KEY,
    strategy_id       TEXT NOT NULL,
    symbol            TEXT NOT NULL,
    status            TEXT NOT NULL,    -- pending|confirmed|failed|duplicate
    exchange_order_id TEXT,
    created_at        TEXT NOT NULL,
    confirmed_at      TEXT,
    failed_at         TEXT,
    failure_reason    TEXT,
    retry_count       INTEGER DEFAULT 0
);

-- Indexes for fast queries
CREATE INDEX idx_status  ON order_registry(status);
CREATE INDEX idx_created ON order_registry(created_at);

-- Auto-cleanup: delete records > 30 days that are confirmed/failed
-- Performed by scheduler.db_vacuum task
```

---

### 8.4 Schema — experience.db

```sql
CREATE TABLE closed_trades (
    trade_id          TEXT PRIMARY KEY,
    client_order_id   TEXT UNIQUE NOT NULL,
    symbol            TEXT NOT NULL,
    side              TEXT NOT NULL,
    entry_price       REAL NOT NULL,
    exit_price        REAL NOT NULL,
    qty               REAL NOT NULL,
    pnl_usd           REAL NOT NULL,
    pnl_pct           REAL NOT NULL,
    commission_usd    REAL NOT NULL,
    strategy_id       TEXT NOT NULL,
    exit_reason       TEXT NOT NULL,    -- SL|TP|TRAIL|SIGNAL|TIMEOUT|MANUAL
    hold_candles      INTEGER,
    regime_at_entry   TEXT,
    vol_regime_entry  TEXT,
    mode              TEXT NOT NULL,
    opened_at         TEXT NOT NULL,
    closed_at         TEXT NOT NULL
);

CREATE INDEX idx_strategy ON closed_trades(strategy_id, closed_at);
CREATE INDEX idx_symbol   ON closed_trades(symbol, closed_at);
```

---

### 8.5 Schema — performance.db

```sql
CREATE TABLE strategy_stats (
    strategy_id       TEXT NOT NULL,
    period            TEXT NOT NULL,    -- '7d' | '30d' | '90d' | 'all'
    updated_at        TEXT NOT NULL,
    total_trades      INTEGER,
    winning_trades    INTEGER,
    win_rate          REAL,
    avg_win_usd       REAL,
    avg_loss_usd      REAL,
    profit_factor     REAL,
    sharpe_ratio      REAL,
    max_drawdown_pct  REAL,
    avg_hold_candles  REAL,
    total_pnl_usd     REAL,
    PRIMARY KEY (strategy_id, period)
);

-- Updated by learning_layer daily (drift_check task)
```

> **DATABASE RULE:** No raw SQL outside the designated repository files.
> All DB access goes through the class repository defined per domain.
> Example: state.db is only accessed via TradeStore, not directly from main.py.

---

### 8.6 Backup Policy

| Database | Backup Frequency | Retention | Location |
|---|---|---|---|
| state.db | Before every restart + daily 00:00 | 7 days | agent/memory/backups/ |
| experience.db | Daily 00:15 | 30 days | agent/memory/backups/ |
| order_registry.db | Daily 00:20 | 30 days | agent/memory/backups/ |
| performance.db | Weekly | 90 days | agent/memory/backups/ |
| audit/ (immutable) | Weekly + cloud sync | Permanent | audit/ + cloud storage |

---

## 9. logs/ — Log Structure & Format

### 9.1 Log File Map

| File | Contents | Format | Rotation | Retention |
|---|---|---|---|---|
| trades_spot.json | All spot trades — open + close | JSON lines | Daily | 90 days |
| trades_futures.json | All futures trades | JSON lines | Daily | 90 days |
| decisions_spot.json | Every strategy decision (signal + block) | JSON lines | Daily | 30 days |
| decisions_futures.json | Futures strategy decisions | JSON lines | Daily | 30 days |
| execution.log | All exchange API interactions | JSON lines | Daily | 30 days |
| idempotency.log | All idempotency checks — PROCEED/DUPLICATE | JSON lines | Daily | 30 days |
| errors.log | All errors with stack traces | JSON lines | Daily | 90 days |

---

### 9.2 JSON Format — trades_spot.json

```json
{
  "timestamp":        "2024-11-15T08:30:00.123Z",
  "event":            "trade_opened",
  "trade_id":         "uuid-...",
  "client_order_id":  "spot_001_BTCUSDT_1731658200000",
  "symbol":           "BTCUSDT",
  "side":             "BUY",
  "order_type":       "market",
  "requested_qty":    0.001,
  "filled_qty":       0.001,
  "avg_fill_price":   50000.0,
  "sl_price":         49000.0,
  "tp_price":         52000.0,
  "risk_amount_usd":  50.0,
  "strategy_id":      "spot_strategy_v1",
  "signal_confidence":0.72,
  "regime":           "strong_trend_up",
  "mode":             "paper",
  "equity_before":    10000.0,
  "commission_usd":   0.05
}
```

On close, add these fields:

```json
{
  "event":        "trade_closed",
  "exit_price":   51500.0,
  "exit_reason":  "TP",
  "pnl_usd":      150.0,
  "pnl_pct":      3.00,
  "hold_candles": 12,
  "equity_after": 10150.0
}
```

---

### 9.3 JSON Format — decisions_spot.json

```json
{
  "timestamp":    "2024-11-15T08:30:00Z",
  "event":        "signal_generated",
  "symbol":       "BTCUSDT",
  "side":         "BUY",
  "confidence":   0.72,
  "strategy_id":  "spot_strategy_v1",
  "reasoning":    "RSI oversold + EMA bullish cross + strong regime",
  "model_output": 0.0082,
  "regime":       "strong_trend_up",
  "vol_regime":   "normal",
  "outcome":      "approved",     // approved | blocked | duplicate
  "block_reason": null,           // filled if blocked
  "risk_verdict": "approved",
  "approved_qty": 0.001
}
```

---

### 9.4 Log Level Convention

| Level | When to Use | Example |
|---|---|---|
| **DEBUG** | Internal detail for debugging. **Disabled in production.** | Tick duration, feature values, orderbook depth |
| **INFO** | Normal important events worth recording | Trade opened, sync completed, config loaded |
| **WARNING** | Abnormal but system can continue | Slow tick, rate limit warning, high spread |
| **ERROR** | Failures that need investigation but are not fatal | Order rejected, sync failed, handler error |
| **CRITICAL** | Fatal failures requiring immediate intervention | Safe mode triggered, circuit breaker, DB corrupt |

---

## 10. migration/ — Database Schema Versioning

Every database schema change (add column, change type, add table) **MUST** go through a migration. Never alter tables directly in production.

### 10.1 migrate.py — Interface

```python
class MigrationRunner:

    def __init__(self, db_path: str):
        self.db_path = db_path

    def run(self) -> MigrationReport:
        """
        1. Check current schema version (from _schema_version table)
        2. Find migration files not yet run
        3. Run in a transaction — rollback on failure
        4. Update _schema_version
        5. Return MigrationReport
        """

    def rollback(self, target_version: int) -> bool:
        """Roll back to a specific version using the down() method."""

    def status(self) -> list[MigrationStatus]:
        """List all migrations — which have and haven't been run."""
```

---

### 10.2 Migration File Format

```python
# migrations/versions/001_initial_schema.py
MIGRATION_ID = 1
DESCRIPTION  = 'Initial schema — positions, audit_events, snapshots'

def up(cursor) -> None:
    """Run when migrating forward."""
    cursor.execute('''
        CREATE TABLE positions (
            trade_id TEXT PRIMARY KEY,
            ...
        )
    ''')

def down(cursor) -> None:
    """Run when rolling back."""
    cursor.execute('DROP TABLE IF EXISTS positions')


# migrations/versions/002_add_commission_column.py
MIGRATION_ID = 2
DESCRIPTION  = 'Add commission_usd column to positions'

def up(cursor) -> None:
    cursor.execute(
        'ALTER TABLE positions ADD COLUMN commission_usd REAL DEFAULT 0'
    )

def down(cursor) -> None:
    # SQLite does not support DROP COLUMN before 3.35
    # Create new table without column, copy data, rename
    cursor.execute('CREATE TABLE positions_tmp AS ...')
    cursor.execute('DROP TABLE positions')
    cursor.execute('ALTER TABLE positions_tmp RENAME TO positions')
```

> **MANDATORY RULES:**
> - Every migration file must have both `up()` and `down()`
> - MIGRATION_ID numbers must be sequential
> - NEVER edit a migration that has already run in production — create a new one

---

## 11. models/ — ML Models at Runtime

### 11.1 Folder Structure

```
agent/models/
├── spot_model.pkl           ← active model for spot trading
├── futures_model.pkl        ← active model for futures trading
├── metadata.json            ← interface contract with research layer
└── archive/                 ← old models (for quick rollback)
    ├── spot_model_v1.2.1.pkl
    └── spot_model_v1.2.1_metadata.json
```

---

### 11.2 metadata.json — Interface Contract

```json
{
  "model_id":         "spot_lgbm_v2_3_0",
  "version":          "2.3.0",
  "created_at":       "2024-11-10T08:00:00Z",
  "deployed_at":      "2024-11-15T00:00:00Z",
  "model_type":       "lightgbm",
  "symbol":           "BTCUSDT",
  "timeframe":        "1h",
  "train_period":     ["2020-01-01", "2024-10-31"],
  "feature_names": [
    "rsi_7", "rsi_14", "rsi_21",
    "ema_ratio_9_21", "ema_ratio_21_50",
    "atr_14", "atr_percentile",
    "bb_width_20", "volume_ratio_20",
    "log_return_1", "log_return_5", "log_return_20",
    "adx_14", "funding_rate"
  ],
  "feature_count":    47,
  "target_col":       "target_return_4h",
  "prediction_type":  "regression",
  "thresholds": {
    "entry_long":     0.003,
    "entry_short":   -0.003,
    "min_confidence": 0.60
  },
  "metrics": {
    "ic_mean":        0.078,
    "icir":           1.85,
    "dir_accuracy":   0.543,
    "sharpe_signal":  1.12,
    "backtest_sharpe":1.34,
    "backtest_maxdd": 0.162
  },
  "status":           "production",
  "replaces":         "spot_lgbm_v2_2_1",
  "compatible_modes": ["paper", "shadow", "live"]
}
```

---

### 11.3 Model Validation at Load

```python
class ModelLoader:

    def load(self, model_path: str, meta_path: str) -> TrainedModel:
        model    = joblib.load(model_path)
        metadata = json.load(open(meta_path))

        # Validation 1: feature_names must match runtime FeatureConfig
        runtime_features = FeatureConfig().get_feature_names()
        if metadata['feature_names'] != runtime_features:
            raise ModelCompatibilityError(
                f'Feature mismatch: model={metadata["feature_names"][:3]}...'
                f' runtime={runtime_features[:3]}...'
            )

        # Validation 2: model must have predict() method
        if not hasattr(model, 'predict'):
            raise ModelCompatibilityError('Model does not have predict() method')

        # Validation 3: status must be "production" or "validated"
        if metadata['status'] not in ('production', 'validated'):
            raise ModelStatusError(
                f'Model status is {metadata["status"]} — cannot deploy'
            )

        return TrainedModel(model=model, metadata=metadata)
```

---

## 12. Implementation Checklist

### 12.1 core/ Checklist

| No | Item | Verification | Done |
|---|---|---|---|
| 1 | main.py follows startup sequence T-01 through T-14 | Code review + startup log shows all steps | ☐ |
| 2 | Main loop catches all exceptions at tick level — no total crash | Unit test: inject error at every step, loop continues | ☐ |
| 3 | AgentConfig Pydantic validation tested for all edge cases | pytest test_config.py — all boundary values tested | ☐ |
| 4 | All validators in AgentConfig tested (leverage spot, mode, etc.) | 100% branch coverage for validators | ☐ |
| 5 | Mode transitions validated by ModeTransitionGuard | Test: PAPER→LIVE blocked (must go through SHADOW) | ☐ |
| 6 | config_schema.py rejects config with invalid fields | pytest: inject invalid value, check ValueError message | ☐ |

---

### 12.2 scheduler/ Checklist

| No | Item | Verification | Done |
|---|---|---|---|
| 1 | All tasks registered in scheduler at startup | Startup log shows task list + intervals | ☐ |
| 2 | daily_reset confirmed to run exactly at UTC 00:00 | Test with mock datetime | ☐ |
| 3 | A failing task does not stop other tasks | Inject error in one task, check others continue | ☐ |
| 4 | Scheduler stops gracefully during graceful_shutdown() | Wait for active tasks to finish, no force kill | ☐ |

---

### 12.3 event_bus/ Checklist

| No | Item | Verification | Done |
|---|---|---|---|
| 1 | Handler error does not stop dispatch loop | Inject exception in handler, check next event processed | ☐ |
| 2 | Queue full does not crash publisher — log and skip | Fill queue to maxsize, publish one more | ☐ |
| 3 | All EventTypes have at least one subscriber | Test: publish every event type, check someone receives it | ☐ |

---

### 12.4 safe_mode/ Checklist

| No | Item | Verification | Done |
|---|---|---|---|
| 1 | emergency_stop() runs in correct order | Integration test with mock executor & notifier | ☐ |
| 2 | State snapshot saved before exit | Check snapshot file exists in memory/snapshots/ after trigger | ☐ |
| 3 | Notification sent before sys.exit() | Mock notifier.send_emergency_alert — confirmed called | ☐ |
| 4 | is_halted() checked at start of every tick | Code review main_loop | ☐ |

---

### 12.5 memory/ Checklist

| No | Item | Verification | Done |
|---|---|---|---|
| 1 | Migration runs at startup before other layer initialization | Log: 'Migration complete, version N' before layer init | ☐ |
| 2 | Each DB backed up before new migration runs | migrate.py creates backup before run | ☐ |
| 3 | No raw SQL outside designated repository files | grep -r 'execute(' --include='*.py' \| grep -v repository | ☐ |
| 4 | audit_events table is truly append-only | No UPDATE or DELETE query for this table | ☐ |
| 5 | Backup runs on schedule | Check backup files in memory/backups/ with timestamp | ☐ |

---

> **This document is the implementation contract for Agent Core.**
> Any changes to startup sequence, EventType, database schema, or log format
> **MUST** be updated here before merging to main.
>
> *Related references: research_layer_docs.md • runtime_layer_docs.md • risk_layer skeleton code*
