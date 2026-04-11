# crypto_ai_agent — Exit Layer · Monitoring Layer · Sync Layer Documentation

*trailing · take_profit · break_even · exit_manager*
*heartbeat · connectivity · health_check · alerts*
*balance_sync · order_sync · position_sync · reconciliation*

**Version 1.0** | Reference: trade_execution_docs.md · agent_core_docs.md

---

## 1. Overview — Three Complementary Layers

These three layers work behind the scenes to ensure the system always remains healthy, synchronized, and exits positions correctly. They do not make entry decisions — they only manage active positions and system health.

| Layer | Responsibility | Called Every | Primary Output |
|---|---|---|---|
| **Exit** | Determine when and how to exit positions | Every tick (per active position) | ExitDecision |
| **Monitoring** | Detect system problems before they become crises | 30 seconds (background) | HealthStatus, Alert |
| **Sync** | Ensure internal state = exchange state (truth) | 30–60 seconds (scheduled) | SyncReport, DiscrepancyAlert |

---

### 1.1 Layer Interaction

```python
# main_loop (every tick):
for position in trade_mgr.get_open_trades():
    exit_decision = exit_mgr.evaluate(position, market_state)
    if exit_decision.action != 'HOLD':
        await trade_mgr.close(position, exit_decision)
        circuit_breaker.record_loss/win()

# scheduler (background):
every 30s  → monitoring.health_check.run()
every 30s  → sync.order_sync.run()
every 60s  → sync.balance_sync.run()
every 60s  → sync.position_sync.run()
every 30s  → monitoring.heartbeat.pulse()
daily      → sync.reconciliation.run_daily()
```

---

## PART A — Exit Layer

---

## 2. exit_manager.py — Exit Coordinator

Called every tick for every active position. Evaluates all exit conditions in sequence and returns a single final decision.

### 2.1 ExitDecision & ExitAction

```python
class ExitAction(str, Enum):
    HOLD          = 'hold'          # do nothing
    EXIT_SL       = 'exit_sl'       # stop loss hit
    EXIT_TP       = 'exit_tp'       # take profit hit
    EXIT_TRAIL    = 'exit_trail'    # trailing stop hit
    EXIT_SIGNAL   = 'exit_signal'   # strategy requests exit
    EXIT_TIMEOUT  = 'exit_timeout'  # position held too long
    EXIT_FORCED   = 'exit_forced'   # circuit breaker / safe mode
    UPDATE_SL     = 'update_sl'     # shift SL (breakeven / trailing)
    PARTIAL_CLOSE = 'partial_close' # close partial position

@dataclass
class ExitDecision:
    action:     ExitAction
    exit_price: float      # 0.0 = use market price
    new_sl:     float      # only for UPDATE_SL
    close_qty:  float      # only for PARTIAL_CLOSE (0 = close all)
    reason:     str        # text for logging & audit
    urgency:    str        # 'normal' | 'urgent'
    source:     str        # 'sl' | 'tp' | 'trailing' | 'strategy' | 'timeout'
    confidence: float      # 0.0–1.0 how confident to exit

    @property
    def is_exit(self) -> bool:
        return self.action not in (ExitAction.HOLD, ExitAction.UPDATE_SL)

    @property
    def is_urgent(self) -> bool:
        return self.urgency == 'urgent'
```

---

### 2.2 Evaluation Sequence — 7 Steps

| Priority | Check | Condition | Action | Urgency |
|---|---|---|---|---|
| 1 (highest) | **Hard SL** | candle low/high breaches sl_price | EXIT_SL | urgent |
| 2 | **Hard TP** | candle low/high breaches tp_price | EXIT_TP | normal |
| 3 | **Break-even** | profit >= BREAKEVEN_TRIGGER_R × risk | UPDATE_SL | normal |
| 4 | **Trailing SL** | price moves favorably, update trail level | UPDATE_SL / EXIT_TRAIL | normal |
| 5 | **Partial TP** | profit >= PARTIAL_TP_TRIGGER, not yet partially closed | PARTIAL_CLOSE | normal |
| 6 | **Strat Exit** | strategy.should_exit() returns ExitSignal | EXIT_SIGNAL | normal |
| 7 (lowest) | **Timeout** | hold_candles >= MAX_HOLD_CANDLES | EXIT_TIMEOUT | normal |

> **IMPORTANT:** Evaluation stops at the first condition met. If SL is hit, there is no need to check TP or trailing. This prevents conflicting decisions.

---

### 2.3 Public Interface

```python
class ExitManager:

    def __init__(
        self,
        config:      AgentConfig,
        trailing:    'TrailingStopManager',
        take_profit: 'TakeProfitManager',
        break_even:  'BreakEvenManager',
        strategy:    'BaseStrategy',
    ): ...

    def evaluate(
        self,
        position: 'Trade',
        state:    'MarketState',
    ) -> ExitDecision:
        """
        Evaluate one position. Pure function — no I/O.
        Called from main_loop every tick for every open trade.
        """
        high  = state.latest_candle.high
        low   = state.latest_candle.low
        close = state.latest_candle.close

        # 1. Hard SL
        sl_hit = self._check_sl(position, high, low)
        if sl_hit: return sl_hit

        # 2. Hard TP
        tp_hit = self._check_tp(position, high, low)
        if tp_hit: return tp_hit

        # 3. Break-even
        be_update = self.break_even.check(position, close)
        if be_update: return be_update

        # 4. Trailing SL
        trail = self.trailing.update(position, high, low, close, state.atr)
        if trail: return trail

        # 5. Partial TP
        partial = self._check_partial_tp(position, close)
        if partial: return partial

        # 6. Strategy exit
        strat_exit = self.strategy.should_exit(position, state)
        if strat_exit:
            return ExitDecision(
                action     = ExitAction.EXIT_SIGNAL,
                exit_price = 0.0, new_sl=0.0, close_qty=0.0,
                reason     = strat_exit.reason,
                urgency    = strat_exit.urgency,
                source     = 'strategy',
                confidence = strat_exit.confidence,
            )

        # 7. Timeout
        timeout = self._check_timeout(position, state)
        if timeout: return timeout

        return ExitDecision(
            action=ExitAction.HOLD, exit_price=0.0, new_sl=0.0,
            close_qty=0.0, reason='', urgency='normal',
            source='hold', confidence=0.0,
        )

    def register_open_positions(
        self, trades: list['Trade']
    ) -> None:
        """
        Initialize trailing state for all positions at startup.
        Called by main.py T-08.
        """
```

---

### 2.4 SL Check — Using Candle High/Low

```python
def _check_sl(
    self,
    position: 'Trade',
    high:     float,
    low:      float,
) -> ExitDecision | None:
    """
    Uses candle high/low, not just close price.
    This is more accurate because SL can be hit mid-candle.
    BUY position:  SL is hit if low  <= sl_price
    SELL position: SL is hit if high >= sl_price
    """
    if position.side == 'BUY' and low <= position.sl_price:
        return ExitDecision(
            action     = ExitAction.EXIT_SL,
            exit_price = position.sl_price,   # worst case: SL price
            new_sl     = 0.0,
            close_qty  = 0.0,
            reason     = f'SL hit: low {low:.2f} <= sl {position.sl_price:.2f}',
            urgency    = 'urgent',
            source     = 'sl',
            confidence = 1.0,
        )
    if position.side == 'SELL' and high >= position.sl_price:
        return ExitDecision(
            action     = ExitAction.EXIT_SL,
            exit_price = position.sl_price,
            new_sl     = 0.0, close_qty=0.0,
            reason     = f'SL hit: high {high:.2f} >= sl {position.sl_price:.2f}',
            urgency    = 'urgent',
            source     = 'sl',
            confidence = 1.0,
        )
    return None
```

---

## 3. trailing.py — Trailing Stop

Automatically shifts the SL following favorable price movement, locking in profit while allowing room for normal volatility.

### 3.1 Three Trailing Methods

| Method | Formula | Advantage | Config Key |
|---|---|---|---|
| **ATR-based** | trail = highest_high − (N × ATR) for BUY | Adapts to volatility | TRAIL_ATR_MULT |
| **Percentage** | trail = highest_high × (1 − pct) for BUY | Simple, predictable | TRAIL_PCT |
| **Chandelier** | trail = highest_high_N − (M × ATR) | Better for long trends | TRAIL_CHANDELIER_N |

---

### 3.2 TrailingState — Per Position

```python
@dataclass
class TrailingState:
    trade_id:      str
    method:        str      # 'atr' | 'percentage' | 'chandelier'
    activated:     bool     # True after profit >= activation threshold
    current_trail: float    # current trailing SL level
    highest_high:  float    # for BUY: highest high since entry
    lowest_low:    float    # for SELL: lowest low since entry
    last_updated:  datetime
```

---

### 3.3 Public Interface

```python
class TrailingStopManager:

    def __init__(self, config: AgentConfig):
        self._states:     dict[str, TrailingState] = {}
        self.method       = config.trailing_method
        self.atr_mult     = config.trail_atr_mult
        self.trail_pct    = config.trail_pct
        self.activation_r = config.trail_activation_r

    def register(self, trade: 'Trade') -> None:
        """Initialize TrailingState when a position is opened."""
        self._states[trade.trade_id] = TrailingState(
            trade_id      = trade.trade_id,
            method        = self.method,
            activated     = False,
            current_trail = trade.sl_price,    # starts from initial SL
            highest_high  = trade.avg_fill_price,
            lowest_low    = trade.avg_fill_price,
            last_updated  = utcnow(),
        )

    def update(
        self,
        trade: 'Trade',
        high:  float,
        low:   float,
        close: float,
        atr:   float,
    ) -> ExitDecision | None:
        """
        Update trailing state and return ExitDecision if:
        - Trail level is updated → UPDATE_SL
        - Price breaches trail → EXIT_TRAIL
        - Trailing not yet active → None
        """
        state = self._states.get(trade.trade_id)
        if not state: return None

        # Check trail activation
        if not state.activated:
            profit = self._calc_profit_r(trade, close)
            if profit >= self.activation_r:
                state.activated = True
                log.info('Trailing activated', trade_id=trade.trade_id,
                         profit_r=profit)
            else:
                return None

        if trade.side == 'BUY':
            return self._update_buy_trail(trade, state, high, low, atr)
        else:
            return self._update_sell_trail(trade, state, high, low, atr)

    def deregister(self, trade_id: str) -> None:
        """Remove state when position is closed."""
        self._states.pop(trade_id, None)

    def get_state(self, trade_id: str) -> TrailingState | None:
        return self._states.get(trade_id)
```

---

### 3.4 ATR-based Trail — Implementation

```python
def _update_buy_trail(
    self,
    trade: 'Trade',
    state: TrailingState,
    high:  float,
    low:   float,
    atr:   float,
) -> ExitDecision | None:
    # Update highest high
    if high > state.highest_high:
        state.highest_high = high

    # Compute new trail level
    if self.method == 'atr':
        new_trail = state.highest_high - (atr * self.atr_mult)
    elif self.method == 'percentage':
        new_trail = state.highest_high * (1 - self.trail_pct)
    else:  # chandelier
        new_trail = state.highest_high - (atr * self.atr_mult)

    # Trail can only move up (ratchet effect)
    if new_trail > state.current_trail:
        old_trail           = state.current_trail
        state.current_trail = new_trail
        state.last_updated  = utcnow()
        return ExitDecision(
            action     = ExitAction.UPDATE_SL,
            exit_price = 0.0,
            new_sl     = new_trail,
            close_qty  = 0.0,
            reason     = f'Trailing SL raised: {old_trail:.2f} → {new_trail:.2f}',
            urgency    = 'normal',
            source     = 'trailing',
            confidence = 1.0,
        )

    # Check if price breaches trail level
    if low <= state.current_trail:
        return ExitDecision(
            action     = ExitAction.EXIT_TRAIL,
            exit_price = state.current_trail,
            new_sl=0.0, close_qty=0.0,
            reason     = f'Trail SL hit: low {low:.2f} <= trail {state.current_trail:.2f}',
            urgency    = 'urgent',
            source     = 'trailing',
            confidence = 1.0,
        )

    return None
```

---

### 3.5 Trailing Config Keys

| Key | Default | Valid Range | Description |
|---|---|---|---|
| `TRAILING_METHOD` | atr | atr\|percentage\|chandelier | Trailing method used |
| `TRAIL_ATR_MULT` | 2.0 | 1.0–5.0 | Trail distance = ATR × multiplier |
| `TRAIL_PCT` | 0.02 | 0.005–0.10 | Trail distance 2% from high/low for percentage method |
| `TRAIL_CHANDELIER_N` | 22 | 10–50 | Lookback for highest high / lowest low (chandelier) |
| `TRAIL_ACTIVATION_R` | 1.0 | 0.5–3.0 | Trailing activates after profit = N × risk (1R = 1:1) |

---

## 4. take_profit.py & break_even.py

### 4.1 take_profit.py — TP Management

Manages two take profit modes: single TP (close everything at once) and partial TP (close a portion, trail the remainder).

```python
class TakeProfitManager:

    @dataclass
    class TPState:
        trade_id:      str
        tp1_hit:       bool     # already partially closed?
        tp1_price:     float    # first TP price
        tp2_price:     float    # second TP (or 0 if single TP)
        partial_ratio: float    # fraction closed at TP1

    def check(
        self,
        trade: 'Trade',
        high:  float,
        low:   float,
    ) -> ExitDecision | None:
        """
        Single TP:  if tp_price > 0 and price reaches it → EXIT_TP
        Partial TP: if not yet partially closed, close PARTIAL_TP_RATIO of position;
                    remaining position continues trailing
        """
        if trade.tp_price <= 0:
            return None    # no fixed TP

        # BUY: TP if high >= tp_price
        # SELL: TP if low  <= tp_price
        tp_hit = (
            (trade.side == 'BUY'  and high >= trade.tp_price) or
            (trade.side == 'SELL' and low  <= trade.tp_price)
        )
        if not tp_hit:
            return None

        state = self._states.get(trade.trade_id)

        # Partial TP: close portion, continue trailing
        if self.config.partial_tp_enabled and state and not state.tp1_hit:
            state.tp1_hit = True
            return ExitDecision(
                action    = ExitAction.PARTIAL_CLOSE,
                exit_price= trade.tp_price,
                new_sl    = trade.avg_fill_price,    # shift SL to entry (breakeven)
                close_qty = trade.filled_qty * self.config.partial_tp_ratio,
                reason    = f'Partial TP: {self.config.partial_tp_ratio:.0%} closed',
                urgency   = 'normal',
                source    = 'tp',
                confidence= 1.0,
            )

        # Full TP
        return ExitDecision(
            action    = ExitAction.EXIT_TP,
            exit_price= trade.tp_price,
            new_sl=0.0, close_qty=0.0,
            reason    = f'TP hit: {trade.tp_price:.2f}',
            urgency   = 'normal',
            source    = 'tp',
            confidence= 1.0,
        )
```

---

### 4.2 break_even.py — Shift SL to Entry

```python
class BreakEvenManager:
    """
    Shifts SL to entry price when profit reaches the threshold.
    Ensures the position cannot lose after the threshold is reached.
    """

    def check(
        self,
        trade: 'Trade',
        close: float,
    ) -> ExitDecision | None:
        # Already at breakeven? No need to check again.
        if self._is_at_breakeven(trade): return None

        # Calculate profit in risk units (R)
        risk = abs(trade.avg_fill_price - trade.sl_price)
        if risk <= 0: return None

        if trade.side == 'BUY':
            profit   = close - trade.avg_fill_price
            be_price = trade.avg_fill_price + self.config.breakeven_buffer_pct * close
        else:
            profit   = trade.avg_fill_price - close
            be_price = trade.avg_fill_price - self.config.breakeven_buffer_pct * close

        profit_r = profit / risk

        if profit_r >= self.config.breakeven_trigger_r:
            # Do not move SL backwards
            if trade.side == 'BUY'  and be_price <= trade.sl_price: return None
            if trade.side == 'SELL' and be_price >= trade.sl_price: return None

            return ExitDecision(
                action     = ExitAction.UPDATE_SL,
                exit_price = 0.0,
                new_sl     = be_price,
                close_qty  = 0.0,
                reason     = f'Break-even: profit {profit_r:.1f}R >= {self.config.breakeven_trigger_r}R',
                urgency    = 'normal',
                source     = 'break_even',
                confidence = 1.0,
            )
        return None

    def _is_at_breakeven(self, trade: 'Trade') -> bool:
        """SL is already above entry (BUY) or below entry (SELL)."""
        if trade.side == 'BUY':  return trade.sl_price >= trade.avg_fill_price
        if trade.side == 'SELL': return trade.sl_price <= trade.avg_fill_price
        return False
```

---

### 4.3 Take Profit & Break Even Config Keys

| Key | Default | Description |
|---|---|---|
| `PARTIAL_TP_ENABLED` | True | True = close a portion at TP, trail the remainder |
| `PARTIAL_TP_RATIO` | 0.50 | 50% of position closed when first TP is hit |
| `BREAKEVEN_TRIGGER_R` | 1.0 | Activate breakeven when profit = 1R (1:1 risk) |
| `BREAKEVEN_BUFFER_PCT` | 0.001 | 0.1% buffer above entry for breakeven SL |
| `MAX_HOLD_CANDLES` | 48 | Force exit if position is open > N candles without progress |

---

## PART B — Monitoring Layer

---

## 5. heartbeat.py — Agent Death Detection

A periodic pulse that proves the agent is still alive. `health_restart.py` in `automation/` monitors this heartbeat from outside the process and restarts the agent if there is no sign of life.

### 5.1 How It Works

```python
class Heartbeat:
    PULSE_INTERVAL_S = 30     # write to DB every 30 seconds
    STALE_THRESHOLD_S = 90    # STALE if no pulse for 90 seconds
    DEAD_THRESHOLD_S  = 180   # DEAD if no pulse for 3 minutes

    async def start(self) -> None:
        """Background loop that runs for the lifetime of the agent."""
        while True:
            await self._pulse()
            await asyncio.sleep(self.PULSE_INTERVAL_S)

    async def _pulse(self) -> None:
        """
        Write timestamp to heartbeat.db.
        Update Prometheus gauge: agent_alive = 1.
        Check that all asyncio tasks are still running.
        """
        now = utcnow()
        self._db.execute(
            'INSERT OR REPLACE INTO heartbeat (id, ts, tasks_ok) VALUES (1, ?, ?)',
            (now.isoformat(), self._check_tasks())
        )
        metrics.gauge('agent_alive').set(1)
        metrics.gauge('agent_last_pulse_ts').set(now.timestamp())

    def get_status(self) -> 'HeartbeatStatus':
        """Read by health_check.py and health_restart.py."""
        last = self._db.fetchone('SELECT ts FROM heartbeat WHERE id = 1')
        if not last: return HeartbeatStatus.DEAD

        elapsed = (utcnow() - datetime.fromisoformat(last[0])).total_seconds()
        if elapsed > self.DEAD_THRESHOLD_S:  return HeartbeatStatus.DEAD
        if elapsed > self.STALE_THRESHOLD_S: return HeartbeatStatus.STALE
        return HeartbeatStatus.ALIVE

class HeartbeatStatus(str, Enum):
    ALIVE = 'alive'
    STALE = 'stale'    # pulse exists but is old
    DEAD  = 'dead'     # no pulse → trigger restart
```

---

### 5.2 health_restart.py — External Response

```python
# automation/health_restart.py
# Runs as a SEPARATE PROCESS from the agent.
# Checks heartbeat every 60 seconds.

async def monitor_and_restart():
    while True:
        await asyncio.sleep(60)
        status = read_heartbeat_from_db(DB_PATH)

        if status == HeartbeatStatus.DEAD:
            log.critical('Agent DEAD — restarting')
            send_alert('Agent not responding, auto-restarting...')
            await restart_agent()   # docker restart / systemctl restart

        elif status == HeartbeatStatus.STALE:
            log.warning('Agent STALE — monitoring')
            send_alert('Agent pulse is slow — investigation required')

async def restart_agent():
    # Use subprocess to restart via Docker or systemd
    import subprocess
    subprocess.run(['docker', 'restart', 'crypto_ai_agent'], check=True)
    await asyncio.sleep(30)    # allow startup time
```

---

## 6. health_check.py — Component Health

Checks each system component individually. Unlike heartbeat which only proves the agent is alive — health_check provides detail on which specific components are problematic.

### 6.1 All Checks Performed

| Component | How Checked | DEGRADED Threshold | UNHEALTHY Threshold |
|---|---|---|---|
| WebSocket stream | Last message age | > 30 seconds without message | > 60 seconds |
| Exchange REST ping | GET /api/v3/ping latency | > 1000ms | > 3000ms |
| Database state.db | SELECT 1 latency | > 100ms | > 500ms |
| Memory (RSS) | process.memory_info().rss | > 300 MB | > 500 MB |
| Disk space | shutil.disk_usage(data_dir).free | < 2 GB free | < 500 MB free |
| Open orders count | store.count_open() | > max_positions × 1.5 | > max_positions × 2 |
| Circuit breaker | circuit_breaker.state | WARNED | HALTED |
| Rate limit usage | rate_limiter.is_near_limit() | > 70% weight | > 90% weight |

---

### 6.2 HealthReport Dataclass

```python
class ComponentStatus(str, Enum):
    HEALTHY   = 'healthy'
    DEGRADED  = 'degraded'    # still running but not optimal
    UNHEALTHY = 'unhealthy'   # requires intervention

@dataclass
class ComponentHealth:
    name:       str
    status:     ComponentStatus
    value:      float | str    # the measured value
    message:    str
    latency_ms: float = 0.0

@dataclass
class HealthReport:
    overall:    ComponentStatus     # worst status among all components
    components: list[ComponentHealth]
    timestamp:  datetime

    @property
    def is_healthy(self) -> bool:
        return self.overall == ComponentStatus.HEALTHY

    @property
    def unhealthy_components(self) -> list[str]:
        return [c.name for c in self.components
                if c.status == ComponentStatus.UNHEALTHY]
```

---

### 6.3 Public Interface

```python
class HealthCheck:

    async def run(self) -> HealthReport:
        """
        Run all checks in parallel (asyncio.gather).
        Timeout per check: 5 seconds.
        Overall status = worst status across all components.
        """
        checks = await asyncio.gather(
            self._check_websocket(),
            self._check_exchange_ping(),
            self._check_database(),
            self._check_memory(),
            self._check_disk(),
            self._check_open_orders(),
            self._check_circuit_breaker(),
            self._check_rate_limit(),
            return_exceptions=True,
        )
        components = []
        for check in checks:
            if isinstance(check, Exception):
                components.append(ComponentHealth(
                    name='unknown', status=ComponentStatus.UNHEALTHY,
                    value='error', message=str(check)))
            else:
                components.append(check)

        statuses = [c.status for c in components]
        if ComponentStatus.UNHEALTHY in statuses:
            overall = ComponentStatus.UNHEALTHY
        elif ComponentStatus.DEGRADED in statuses:
            overall = ComponentStatus.DEGRADED
        else:
            overall = ComponentStatus.HEALTHY

        return HealthReport(overall=overall,
                            components=components,
                            timestamp=utcnow())

    def get_last_report(self) -> HealthReport | None:
        """Return the last cached report (without re-running checks)."""
```

---

## 7. connectivity.py & alerts.py

### 7.1 connectivity.py — Network Check

```python
class ConnectivityChecker:
    ENDPOINTS = {
        'binance_api':  'https://api.binance.com/api/v3/ping',
        'binance_fapi': 'https://fapi.binance.com/fapi/v1/ping',
        'internet':     'https://httpbin.org/get',
    }

    async def check_all(self) -> dict[str, ConnStatus]:
        """
        Return dict of status per endpoint.
        ConnStatus: UP | SLOW | DOWN
        SLOW = response > 2000ms
        DOWN = timeout or error
        """

    async def is_exchange_reachable(self, exchange: str = 'binance') -> bool:
        """Quick check — used by health_check."""

    async def get_server_time_drift(self) -> float:
        """
        Compare local time with exchange server time.
        Drift > 1000ms can cause orders to be rejected (-1021).
        Returns drift in milliseconds.
        """
```

---

### 7.2 alerts.py — Alert Delivery

A thin layer that ensures every alert has a standard format and is routed to the correct channel based on severity.

```python
class AlertSeverity(str, Enum):
    INFO     = 'info'
    WARNING  = 'warning'
    CRITICAL = 'critical'    # wake up the operator

@dataclass
class Alert:
    severity:  AlertSeverity
    title:     str
    message:   str
    component: str           # component that sent the alert
    data:      dict = None   # additional data (equity, trade_id, etc.)
    timestamp: datetime = None

class AlertManager:

    async def send(self, alert: Alert) -> None:
        """
        Route based on severity:
        INFO     → Telegram only
        WARNING  → Telegram + Discord
        CRITICAL → Telegram + Discord + log to errors.log
        """

    async def send_circuit_breaker(
        self, state: 'CircuitState', reasons: list[str], equity: float
    ) -> None:
        """Template for circuit breaker alert."""

    async def send_trade_opened(self, trade: 'Trade') -> None:
        """Template for new trade notification."""

    async def send_daily_summary(self, stats: dict) -> None:
        """Daily summary: PnL, win rate, trade count, equity."""
```

---

### 7.3 Alert Message Format — Templates

| Alert Type | Required Content | Example Format |
|---|---|---|
| Trade Opened | Symbol, side, qty, entry, SL, TP, confidence | BUY BTCUSDT \| 0.001 @ 50000 \| SL: 49000 \| TP: 52000 |
| Trade Closed (win) | Symbol, PnL USD, PnL%, hold candles, exit reason | CLOSED BTCUSDT +$150 (+3.00%) \| 12 candles \| TP |
| Trade Closed (loss) | Symbol, PnL USD, PnL%, hold candles, exit reason | CLOSED BTCUSDT -$100 (-2.00%) \| 8 candles \| SL |
| Circuit Breaker | State, reason, daily PnL, equity, halt duration | HALT \| Daily loss -5.2% \| Equity: $9,480 \| 60 min |
| System Error | Component, error message, stack trace (shortened) | ERROR execution_layer \| TimeoutError \| retrying... |
| Daily Summary | Total trades, win rate, PnL, equity, open positions | Day +$234 \| 4 trades \| 75% WR \| Eq: $10,234 |

---

## PART C — Sync Layer

The Sync Layer is the mechanism that keeps the agent's internal state consistently aligned with the actual state on the exchange. The exchange is the source of truth — internal state must follow the exchange, not the other way around.

> **PRINCIPLE:** If there is a conflict between internal state and exchange state, ALWAYS update internal state to match the exchange. Never send corrections to the exchange based on internal assumptions.

---

## 8. balance_sync.py — Balance Synchronization

### 8.1 How It Works

```python
class BalanceSync:
    MAX_DRIFT_PCT   = 0.01   # 1% difference is still within tolerance
    ALERT_DRIFT_PCT = 0.05   # 5% → critical alert

    async def run(self) -> 'BalanceSyncReport':
        """
        Called by scheduler every 60 seconds.
        1. Fetch actual balance from exchange REST API
        2. Compare with internal tracking in capital_manager
        3. Update capital_manager with actual value
        4. Compute drift — if > threshold → alert
        """
        actual   = await self._exchange.get_balance('USDT')
        internal = self._capital.get_status().current_equity

        drift_pct = abs(actual - internal) / max(internal, 1.0)

        if drift_pct > self.ALERT_DRIFT_PCT:
            await self._alerts.send(Alert(
                severity  = AlertSeverity.CRITICAL,
                title     = 'Significant balance drift',
                message   = f'Internal: {internal:.2f}, Exchange: {actual:.2f}, Drift: {drift_pct:.1%}',
                component = 'balance_sync',
            ))

        # Update internal with actual value (exchange is truth)
        self._capital.update_from_exchange(actual)

        return BalanceSyncReport(
            internal_before = internal,
            exchange_actual = actual,
            drift_pct       = drift_pct,
            updated         = True,
            timestamp       = utcnow(),
        )
```

---

### 8.2 BalanceSyncReport

```python
@dataclass
class BalanceSyncReport:
    internal_before: float    # internal equity before sync
    exchange_actual: float    # actual balance from exchange
    drift_pct:       float    # percentage difference
    updated:         bool     # True if internal was updated
    alert_sent:      bool     # True if drift > ALERT_DRIFT_PCT
    timestamp:       datetime
```

---

## 9. order_sync.py — Order Status Synchronization

Ensures that the status of all open orders in the internal DB matches the status on the exchange. Detects orders that were filled, cancelled, or rejected without a WebSocket notification.

### 9.1 How It Works

```python
class OrderSync:

    async def run(self) -> 'OrderSyncReport':
        """
        Called by scheduler every 30 seconds.
        Focused on trades with status SUBMITTED or PARTIAL.
        """
        pending_trades = self._store.get_all_active()
        submitted      = [t for t in pending_trades
                          if t.status in (TradeStatus.SUBMITTED, TradeStatus.PARTIAL)]
        updates   = []
        conflicts = []

        for trade in submitted:
            try:
                ex_resp = await self._exchange.get_order_status(
                    trade.symbol, trade.client_order_id
                )
            except OrderNotFoundError:
                conflicts.append(f'Order not found: {trade.client_order_id}')
                continue

            # Reconcile
            if ex_resp.status == 'FILLED' and trade.status != TradeStatus.OPEN:
                self._manager.confirm(trade, ex_resp)
                updates.append(f'SYNCED FILLED: {trade.trade_id}')

            elif ex_resp.status == 'CANCELLED':
                self._store.update_status(trade.trade_id, TradeStatus.CANCELLED)
                updates.append(f'SYNCED CANCELLED: {trade.trade_id}')

            elif ex_resp.status == 'PARTIALLY_FILLED':
                self._store.update_status(
                    trade.trade_id, TradeStatus.PARTIAL,
                    filled_qty=ex_resp.filled_qty,
                    avg_fill_price=ex_resp.avg_price,
                )
                updates.append(f'SYNCED PARTIAL: {trade.trade_id}')

        return OrderSyncReport(
            checked   = len(submitted),
            updated   = len(updates),
            conflicts = conflicts,
            timestamp = utcnow(),
        )
```

---

## 10. position_sync.py — Position Reconciliation

The most critical component in the sync layer. Compares actual positions on the exchange against positions the agent believes are open. A ghost position (exists on exchange but not internally) is a dangerous condition.

### 10.1 How It Works

```python
class PositionSync:

    async def run(self) -> 'PositionSyncReport':
        """
        Called by scheduler every 60 seconds.
        For futures: can fetch real positions from the exchange.
        For spot: derives from order history (no position API for spot).
        """
        internal_positions = {
            t.symbol: t
            for t in self._store.get_open_trades()
        }

        # Fetch positions from exchange (futures only)
        if self._config.market_type == 'futures':
            exchange_positions = await self._get_futures_positions()
        else:
            exchange_positions = await self._derive_spot_positions()

        discrepancies = []

        # Check ghost positions: exist on exchange, not internally
        for symbol, ex_pos in exchange_positions.items():
            if symbol not in internal_positions:
                discrepancies.append(Discrepancy(
                    type     = 'GHOST_POSITION',
                    symbol   = symbol,
                    internal = None,
                    exchange = ex_pos,
                    severity = 'CRITICAL',
                ))

        # Check zombie positions: exist internally, not on exchange
        for symbol, int_pos in internal_positions.items():
            if symbol not in exchange_positions:
                discrepancies.append(Discrepancy(
                    type     = 'ZOMBIE_POSITION',
                    symbol   = symbol,
                    internal = int_pos,
                    exchange = None,
                    severity = 'HIGH',
                ))

        # Check qty mismatch
        for symbol in (set(internal_positions) & set(exchange_positions)):
            int_qty = internal_positions[symbol].filled_qty
            ex_qty  = exchange_positions[symbol].qty
            if abs(int_qty - ex_qty) / max(int_qty, 1e-8) > 0.01:   # 1% tolerance
                discrepancies.append(Discrepancy(
                    type     = 'QTY_MISMATCH',
                    symbol   = symbol,
                    internal = int_pos,
                    exchange = ex_pos,
                    severity = 'MEDIUM',
                ))

        if discrepancies:
            await self._handle_discrepancies(discrepancies)

        return PositionSyncReport(
            internal_count = len(internal_positions),
            exchange_count = len(exchange_positions),
            discrepancies  = discrepancies,
            timestamp      = utcnow(),
        )
```

---

### 10.2 Discrepancy Types & Handling

| Type | Condition | Severity | Automatic Action | Manual Action |
|---|---|---|---|---|
| **GHOST_POSITION** | Exists on exchange, not internally | CRITICAL | Alert + enter cautious mode | Investigate: manually close on exchange if needed |
| **ZOMBIE_POSITION** | Exists internally, not on exchange | HIGH | Alert + mark trade as CLOSED | Check if it was closed without notification |
| **QTY_MISMATCH** | Qty differs > 1% | MEDIUM | Alert + update internal to exchange qty | Investigate unrecorded partial fill |
| **SIDE_MISMATCH** | BUY/SELL side differs | CRITICAL | Emergency stop | Serious bug — requires code review |

---

## 11. reconciliation.py — Full Daily Audit

A full audit that runs once per day (UTC 00:30). Checks comprehensive consistency between all internal databases and the exchange, then generates a report saved to disk.

### 11.1 Reconciliation Scope

| What Is Checked | Internal Source | Exchange Source | Action If Different |
|---|---|---|---|
| USDT balance | capital_manager.current_equity | GET /api/v3/account | Update internal, alert if > 1% |
| Number of open positions | store.get_open_trades() | GET open positions | Handle ghost/zombie position |
| Today's PnL | performance.db daily_pnl | Computed from closed trades | Alert if difference > 0.1% |
| Total commission paid | experience.db sum(commission) | Exchange trade history | Log for reconciliation report |
| Stuck orders | order_registry status=pending | GET open orders | Cancel if pending > 1 day |

---

### 11.2 ReconciliationReport

```python
@dataclass
class ReconciliationReport:
    date:                    str      # reconciliation date (UTC)

    # Balance
    balance_internal:        float
    balance_exchange:        float
    balance_drift_pct:       float

    # Positions
    open_internal:           int
    open_exchange:           int
    position_discrepancies:  list['Discrepancy']

    # PnL
    daily_pnl_internal:      float
    daily_pnl_computed:      float
    pnl_drift:               float

    # Commission
    total_commission:        float
    total_trades_today:      int
    win_rate_today:          float

    # Status
    all_ok:                  bool
    issues:                  list[str]
    warnings:                list[str]
    duration_s:              float
    timestamp:               datetime
```

---

### 11.3 Report Storage & Distribution

```python
async def run_daily(self) -> ReconciliationReport:
    """
    Called by scheduler at UTC 00:30 (30 minutes after daily reset).
    Report is saved to three places:
    1. audit/trade_history.db  — for long-term querying
    2. logs/reconciliation_{date}.json  — for easy-to-read archiving
    3. Sent via Telegram if there are issues
    """
    report = await self._run_all_checks()

    # Save to audit DB
    self._audit_db.insert_reconciliation(report)

    # Save as JSON
    path = f'logs/reconciliation_{report.date}.json'
    with open(path, 'w') as f:
        json.dump(dataclasses.asdict(report), f, default=str, indent=2)

    # Alert if issues found
    if not report.all_ok:
        await self._alerts.send(Alert(
            severity  = AlertSeverity.WARNING,
            title     = f'Reconciliation {report.date}: issues found',
            message   = '\n'.join(report.issues),
            component = 'reconciliation',
        ))

    return report
```

---

## 12. notification/ — Telegram & Discord

### 12.1 notifier.py — Centralized Routing

```python
class Notifier:
    """
    Abstraction that routes notifications to the correct channel.
    Other layers do not need to know whether Telegram or Discord is being used.
    """

    def __init__(
        self,
        telegram: 'TelegramNotifier',
        discord:  'DiscordNotifier',
        config:   AgentConfig,
    ): ...

    async def notify(
        self,
        event_type: str,
        data:       dict,
    ) -> None:
        """
        Routing based on event_type and config:
        notify_trade_open    → Telegram
        notify_trade_close   → Telegram
        notify_circuit_break → Telegram + Discord
        notify_daily_summary → Telegram
        CRITICAL severity    → Telegram + Discord
        """
```

---

### 12.2 telegram.py — Message Formatting

```python
class TelegramNotifier:
    MAX_MESSAGE_LEN = 4096    # Telegram limit
    RETRY_ON_FAIL   = 3

    async def send(self, message: str, parse_mode: str = 'HTML') -> bool:
        """
        Send message to Telegram.
        If failed: retry 3x with 5s delay.
        Returns True if successful.
        """

    # Template for each event:
    def format_trade_opened(self, trade: 'Trade') -> str:
        emoji = '🟢' if trade.side == 'BUY' else '🔴'
        return (
            f'{emoji} <b>Trade Opened</b>\n'
            f'Symbol: <code>{trade.symbol}</code>\n'
            f'Side: {trade.side} | Qty: {trade.filled_qty}\n'
            f'Entry: {trade.avg_fill_price:,.2f} USDT\n'
            f'SL: {trade.sl_price:,.2f} | TP: {trade.tp_price:,.2f}\n'
            f'Risk: {trade.risk_amount_usd:.2f} USDT\n'
            f'Mode: [{trade.mode.upper()}]'
        )

    def format_trade_closed(self, trade: 'Trade') -> str:
        pnl_emoji = '✅' if trade.pnl_usd >= 0 else '❌'
        return (
            f'{pnl_emoji} <b>Trade Closed</b>\n'
            f'Symbol: <code>{trade.symbol}</code>\n'
            f'PnL: <b>{trade.pnl_usd:+.2f} USDT ({trade.pnl_pct:+.2f}%)</b>\n'
            f'Exit: {trade.exit_reason} @ {trade.exit_price:,.2f}\n'
            f'Equity: {self._equity:.2f} USDT'
        )
```

---

## 13. Integration — Full Dependency Map

### 13.1 File Dependencies

| File | Imports From | Called By | Frequency |
|---|---|---|---|
| `exit_manager.py` | trailing, take_profit, break_even, strategy | main_loop per trade per tick | Every tick |
| `trailing.py` | — (pure state) | exit_manager.py | Every tick |
| `take_profit.py` | — (pure state) | exit_manager.py | Every tick |
| `break_even.py` | — (pure state) | exit_manager.py | Every tick |
| `heartbeat.py` | DB (heartbeat.db), metrics | main.py startup | Every 30 seconds |
| `health_check.py` | exchange, store, circuit_breaker, rate_limiter | scheduler every 30 seconds | Every 30 seconds |
| `connectivity.py` | aiohttp | health_check.py | Per health_check run |
| `alerts.py` | telegram.py, discord.py | monitoring, sync, main | On-demand |
| `balance_sync.py` | exchange.get_balance, capital_manager | scheduler every 60 seconds | Every 60 seconds |
| `order_sync.py` | exchange.get_order_status, store | scheduler every 30 seconds | Every 30 seconds |
| `position_sync.py` | exchange.get_position, store | scheduler every 60 seconds | Every 60 seconds |
| `reconciliation.py` | balance_sync, order_sync, position_sync, audit_db | scheduler daily UTC 00:30 | Daily |

---

## 14. Implementation Checklist

### 14.1 Exit Layer Checklist

| No | Item | Verification | Done |
|---|---|---|---|
| 1 | SL check uses candle high/low, not just close price | Unit test: inject candle with low < SL → EXIT_SL | ☐ |
| 2 | Trailing SL only moves up — never down (ratchet) | Test: price rises then falls → trail does not follow down | ☐ |
| 3 | Trailing does not activate before TRAIL_ACTIVATION_R is reached | Test: profit 0.5R, activation=1.0R → trailing not yet active | ☐ |
| 4 | Partial TP: SL shifted to entry after partial close | Test: TP1 hit → SL_UPDATED to avg_fill_price | ☐ |
| 5 | Breakeven does not move SL backwards (only forward) | Test: SL already above entry → breakeven does not change SL | ☐ |
| 6 | Timeout exit runs after MAX_HOLD_CANDLES is exceeded | Test: mock hold_candles > 48 → EXIT_TIMEOUT | ☐ |
| 7 | `evaluate()` is a pure function — no I/O | `grep 'await\|open(\|sqlite' exit_layer/*.py` → empty | ☐ |
| 8 | `register_open_positions()` is called at startup for recovery | Code review main.py T-08 → exit_mgr.register(open_trades) | ☐ |

---

### 14.2 Monitoring Layer Checklist

| No | Item | Verification | Done |
|---|---|---|---|
| 9 | Heartbeat pulse runs every 30 seconds without interruption | Log heartbeat.db timestamp — interval is consistent | ☐ |
| 10 | `health_restart.py` runs as a separate process | `ps aux | grep health_restart` → two processes found | ☐ |
| 11 | `health_check.run()` runs in parallel using asyncio.gather | Benchmark: all 8 checks complete in < 5 seconds total | ☐ |
| 12 | CRITICAL alerts are always sent before sys.exit() | Test safe_mode.emergency_stop → alert is called first | ☐ |
| 13 | Server time drift is checked at startup — alert if > 1000ms | Test: mock server time +2000ms → alert sent | ☐ |

---

### 14.3 Sync Layer Checklist

| No | Item | Verification | Done |
|---|---|---|---|
| 14 | `balance_sync` always updates internal from exchange (not vice versa) | Code review: capital.update_from_exchange(actual), not the other way | ☐ |
| 15 | GHOST_POSITION is detected and triggers CRITICAL alert | Test: create position on exchange without internal record → ghost detected | ☐ |
| 16 | ZOMBIE_POSITION is marked CLOSED internally | Test: close trade without notification → zombie detected | ☐ |
| 17 | `reconciliation.run_daily()` produces JSON report to disk | Check `logs/reconciliation_{date}.json` exists after run | ☐ |
| 18 | `order_sync` does not retry orders that are already CANCELLED | Test: inject cancelled order → not re-submitted | ☐ |
| 19 | All sync tasks are scheduled by the scheduler, not called directly | Code review main.py — sync called from scheduler.is_due | ☐ |

---

> **This document is the implementation contract for the Exit Layer, Monitoring Layer & Sync Layer.**
> Any changes to the ExitDecision dataclass, HealthReport format, or Discrepancy types
> **MUST** be updated here before merging to main.
>
> *References: trade_execution_docs.md · risk_layer_docs.md · agent_core_docs.md*
