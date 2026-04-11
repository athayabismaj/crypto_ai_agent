# crypto_ai_agent — Risk Layer Documentation

*risk_manager · position_size · stoploss · exposure_control*
*leverage_control · circuit_breaker · pre_trade_check*

**Version 1.0** | Reference: strategy_portfolio_docs.md · agent_core_docs.md

---

## 1. Overview — Risk Layer

The Risk Layer is the single gate before an order is sent to the exchange. Every trade request MUST pass through `RiskManager` without exception. There is no bypass path.

> **CORE PRINCIPLE:** The Risk Layer does not make trading decisions. It only asks: *"Is this trade safe to execute right now?"* If not, it rejects — regardless of how good the signal is.

### 1.1 Design Philosophy

- Conservative by default — when in doubt, reject.
- Paper mode records all blocks but does not stop the simulation.
- Live mode: a block means the order is genuinely not sent.
- Every block decision must be auditable with a clear reason.
- No hidden magic numbers — all thresholds are in config.

---

### 1.2 Evaluation Flow — 7 Layers

| Layer | Component | Question | On Failure |
|---|---|---|---|
| L1 | pre_trade_check.py | Is data valid? Is market normal? Is symbol correct? | BLOCKED — data error |
| L2 | circuit_breaker.py | Is trading still allowed? (daily loss / drawdown)? | BLOCKED — circuit breaker |
| L3 | leverage_control.py | Is leverage within allowed limits? | BLOCKED — leverage too high |
| L4 | exposure_control.py | Is the portfolio not over-exposed? | BLOCKED — exposure limit |
| L5 | position_size.py | How much qty is safe? (Kelly / Fixed / ATR) | qty=0 → BLOCKED |
| L6 | stoploss.py | Is SL valid and within reasonable range? | WARNED (live) / pass (paper) |
| L7 | risk_manager.py | All passed? Assemble RiskResult. | APPROVED with final qty |

---

### 1.3 RiskResult — Final Output

```python
from enum import Enum
from dataclasses import dataclass, field

class RiskVerdict(str, Enum):
    APPROVED = 'approved'
    WARNED   = 'warned'    # may proceed but with notes
    BLOCKED  = 'blocked'   # order must not be sent

@dataclass
class RiskResult:
    verdict:           RiskVerdict
    approved_quantity: float      # final qty after sizing (0 if BLOCKED)
    risk_amount_usd:   float      # estimated max loss in USD
    sl_price:          float      # validated / calculated SL
    tp_price:          float      # validated TP (0 = none)
    reasons:           list[str]  # block reasons (empty if APPROVED)
    warnings:          list[str]  # warnings (can still proceed)
    sizing_method:     str        # method used
    timestamp:         datetime

    @property
    def is_approved(self) -> bool:
        return self.verdict != RiskVerdict.BLOCKED

    @property
    def has_warnings(self) -> bool:
        return len(self.warnings) > 0
```

---

## 2. risk_manager.py — Main Orchestrator

The single entry point for all risk evaluation. Delegates to the appropriate components and assembles the final `RiskResult`.

### 2.1 TradeRequest — Input

```python
@dataclass
class TradeRequest:
    symbol:            str
    side:              str       # 'BUY' | 'SELL'
    quantity:          float     # requested qty (may be scaled down)
    price:             float     # 0.0 = market order
    suggested_sl:      float     # from Signal.suggested_sl
    suggested_tp:      float = 0.0
    leverage:          int   = 1  # 1 for spot
    is_futures:        bool  = False
    strategy_id:       str   = ''
    client_order_id:   str   = ''  # filled before sending to idempotency
    signal_confidence: float = 0.0

    @classmethod
    def from_signal(
        cls,
        signal:   'Signal',
        quantity: float,
    ) -> 'TradeRequest':
        return cls(
            symbol            = signal.symbol,
            side              = signal.side,
            quantity          = quantity,
            price             = signal.suggested_price,
            suggested_sl      = signal.suggested_sl,
            suggested_tp      = signal.suggested_tp,
            strategy_id       = signal.strategy_id,
            signal_confidence = signal.final_confidence,
        )
```

---

### 2.2 Public Interface

```python
class RiskManager:

    def __init__(self, config: AgentConfig):
        self.paper_mode       = config.mode != 'live'
        self.pre_trade_check  = PreTradeCheck(config)
        self.circuit_breaker  = CircuitBreaker(config)
        self.leverage_control = LeverageControl(config)
        self.exposure_control = ExposureControl(config)
        self.position_sizer   = PositionSizer(config)
        self.stoploss_manager = StopLossManager(config)
        self._daily_pnl       = 0.0
        self._peak_equity     = config.initial_equity
        self._current_equity  = config.initial_equity

    def evaluate(
        self,
        request:         TradeRequest,
        portfolio_state: dict,
    ) -> RiskResult:
        """
        Full evaluation of one trade request.
        The only function that needs to be called from outside.
        """

    def update_equity(self, current_equity: float) -> None:
        """
        Called after every trade close or periodic sync.
        Updates internal state for circuit breaker & drawdown check.
        """

    def reset_daily_stats(self) -> None:
        """Called by scheduler at UTC 00:00. Resets daily_pnl counter."""

    def get_risk_summary(self) -> dict:
        """Snapshot of current risk state for monitoring."""
```

---

### 2.3 evaluate() — Full Implementation

```python
def evaluate(self, request: TradeRequest, portfolio_state: dict) -> RiskResult:
    reasons:  list[str] = []
    warnings: list[str] = []

    # ── L1: Pre-trade check ──────────────────────────────────────────
    ok, msg = self.pre_trade_check.validate(request, portfolio_state)
    if not ok:
        return self._make_result(RiskVerdict.BLOCKED, 0, [msg], [], request)

    # ── L2: Circuit breaker ──────────────────────────────────────────
    cb_state = self.circuit_breaker.check(
        self._daily_pnl,
        self._current_equity,
        self._peak_equity
    )
    if cb_state == CircuitState.HALTED:
        return self._make_result(RiskVerdict.BLOCKED, 0,
            ['Circuit breaker HALTED'], [], request)
    if cb_state == CircuitState.WARNED:
        warnings.append('Circuit breaker WARNED — approaching loss limit')

    # ── L3: Leverage check (futures only) ────────────────────────────
    if request.is_futures:
        lev_ok, lev_msg = self.leverage_control.validate(
            request.leverage, request.symbol)
        if not lev_ok:
            return self._make_result(RiskVerdict.BLOCKED, 0, [lev_msg], [], request)

    # ── L4: Exposure check ───────────────────────────────────────────
    exp_ok, exp_msg = self.exposure_control.validate(request, portfolio_state)
    if not exp_ok:
        return self._make_result(RiskVerdict.BLOCKED, 0, [exp_msg], [], request)

    # ── L5: Position sizing ──────────────────────────────────────────
    sized_qty, size_warnings, risk_usd = self.position_sizer.calculate(
        request, self._current_equity, portfolio_state)
    warnings.extend(size_warnings)

    if sized_qty <= 0:
        return self._make_result(RiskVerdict.BLOCKED, 0,
            ['PositionSizer: qty=0 (notional below minimum)'], warnings, request)

    # ── L6: Stop loss validation ─────────────────────────────────────
    sl_ok, sl_msg, final_sl = self.stoploss_manager.validate_and_compute(
        request, portfolio_state)
    if not sl_ok and not self.paper_mode:
        return self._make_result(RiskVerdict.BLOCKED, 0, [sl_msg], warnings, request)
    if not sl_ok:
        warnings.append(sl_msg)

    # ── L7: Assemble RiskResult ──────────────────────────────────────
    verdict = RiskVerdict.WARNED if warnings else RiskVerdict.APPROVED

    result = RiskResult(
        verdict           = verdict,
        approved_quantity = sized_qty,
        risk_amount_usd   = risk_usd,
        sl_price          = final_sl,
        tp_price          = request.suggested_tp,
        reasons           = reasons,
        warnings          = warnings,
        sizing_method     = self.position_sizer.last_method,
        timestamp         = utcnow(),
    )

    # Paper mode: BLOCKED is still recorded but verdict is changed to WARNED
    if self.paper_mode and verdict == RiskVerdict.BLOCKED:
        result.verdict           = RiskVerdict.WARNED
        result.approved_quantity = sized_qty or request.quantity
        result.warnings          = result.reasons + warnings
        result.reasons           = []

    self._log_result(request, result)
    return result
```

---

## 3. pre_trade_check.py — Data & Market Validation

The very first gate. If data is invalid or the market is in an abnormal state, there is no need to proceed to deeper risk checks.

### 3.1 All Validations Performed

| Check | Valid Condition | Action if Failed | Error Code |
|---|---|---|---|
| Symbol format | len > 0 AND len ≤ 20 characters | BLOCKED | INVALID_SYMBOL |
| Side | 'BUY' or 'SELL' (uppercase) | BLOCKED | INVALID_SIDE |
| Quantity | qty >= MIN_QTY (1e-8) | BLOCKED | INVALID_QTY |
| Price | price >= 0 (0 = market ok if allowed) | BLOCKED | INVALID_PRICE |
| Market order flag | allow_market_order = True if price = 0 | BLOCKED | MARKET_ORDER_DISABLED |
| Client order ID | valid format (alphanumeric, 1–36 chars) | BLOCKED | INVALID_ORDER_ID |
| Exchange status | portfolio_state['exchange_status'] = normal | BLOCKED | EXCHANGE_MAINTENANCE |
| Market halted | portfolio_state['market_halted'] = False | BLOCKED | MARKET_HALTED |
| Price available | last_price_{symbol} exists in portfolio_state | BLOCKED | PRICE_UNAVAILABLE |
| Reasonable spread | spread_pct <= MAX_SPREAD_PCT | WARNED | WIDE_SPREAD |

---

### 3.2 Public Interface

```python
class PreTradeCheck:

    def validate(
        self,
        request:         TradeRequest,
        portfolio_state: dict,
    ) -> tuple[bool, str]:
        """
        Return (True, '') if all checks pass.
        Return (False, error_message) on the first check that fails.
        Fast-fail: stops at the first failing check.
        """

    def validate_all(
        self,
        request:         TradeRequest,
        portfolio_state: dict,
    ) -> list[tuple[str, bool, str]]:
        """
        Run ALL checks without fast-fail.
        Return list of (check_name, passed, message).
        Useful for debugging and testing.
        """
```

---

### 3.3 Client Order ID — Format & Validation

```python
import re

# Valid format:
# {strategy_id}_{symbol}_{timestamp_ms}
# Example: spot_strategy_v1_BTCUSDT_1731658200000

ORDER_ID_PATTERN = re.compile(r'^[a-zA-Z0-9_\-]{1,36}$')

def validate_client_order_id(cid: str) -> tuple[bool, str]:
    if not cid:
        return False, 'client_order_id is empty'
    if len(cid) > 36:
        return False, f'client_order_id too long: {len(cid)} > 36'
    if not ORDER_ID_PATTERN.match(cid):
        return False, f'client_order_id contains invalid characters: {cid}'
    return True, ''

# Helper to generate a valid client_order_id:
def generate_order_id(strategy_id: str, symbol: str) -> str:
    ts  = int(utcnow().timestamp() * 1000)
    raw = f'{strategy_id}_{symbol}_{ts}'
    # Truncate and sanitize if needed
    return raw[:36].replace(' ', '_')
```

---

### 3.4 Pre-Trade Check Config Keys

| Key | Default | Description |
|---|---|---|
| `ALLOW_MARKET_ORDER` | True | False = limit orders only. Safer but may miss entry. |
| `REQUIRE_CLIENT_ORDER_ID` | True | False for testing only. Live MUST be True for idempotency. |
| `MAX_SPREAD_PCT` | 1.0 | Spread above this → WARNED (not BLOCKED, but recorded). |
| `MIN_QTY` | 1e-8 | Absolute minimum quantity (before exchange lot filter). |

---

## 4. circuit_breaker.py — Automatic Trading Stop

A state machine that halts trading when dangerous conditions are detected. This is the most important self-preservation mechanism in the system.

### 4.1 State Machine

```python
class CircuitState(str, Enum):
    NORMAL  = 'normal'
    WARNED  = 'warned'   # approaching limit — trading still allowed
    HALTED  = 'halted'   # trading STOPPED until conditions improve

# Valid transitions:
# NORMAL → WARNED → HALTED → NORMAL (after auto-resume or manual reset)
# NORMAL → HALTED (if conditions are immediately critical with no warning)
# HALTED → NORMAL (only via auto-resume timeout or manual_resume())
```

---

### 4.2 Trigger Conditions

| Trigger | WARNED Threshold | HALTED Threshold | Auto-resume? | Config Key |
|---|---|---|---|---|
| Daily loss from equity | < -3% | < -5% | Yes (daily reset) | WARN/MAX_DAILY_LOSS_PCT |
| Drawdown from peak | < -7% | < -10% | Yes (auto timer) | WARN/MAX_DRAWDOWN_PCT |
| Consecutive losses | >= 4 | >= 5 | Yes (daily reset) | MAX_CONSECUTIVE_LOSSES |
| Manual halt (via gateway) | — | Immediately | No (manual) | — |

---

### 4.3 Full Public Interface

```python
class CircuitBreaker:

    def __init__(self, config: AgentConfig):
        self.max_daily_loss_pct     = config.max_daily_loss_pct      # 0.05
        self.max_drawdown_pct       = config.max_drawdown_pct        # 0.10
        self.max_consecutive_losses = config.max_consecutive_loss    # 5
        self.halt_duration_minutes  = config.halt_duration_minutes   # 60
        self.warn_daily_loss_pct    = config.warn_daily_loss_pct     # 0.03
        self._state                 = CircuitState.NORMAL
        self._halted_at             = None
        self._consecutive_losses    = 0
        self._manual_halt           = False
        self._halt_reasons          = []

    def check(
        self,
        daily_pnl:      float,
        current_equity: float,
        peak_equity:    float,
    ) -> CircuitState:
        """
        Evaluate all conditions and return the current state.
        Called every tick by RiskManager.evaluate().
        Side effect: may change self._state.
        """

    def record_loss(self) -> None:
        """Called after every losing trade."""

    def record_win(self) -> None:
        """Reset consecutive loss counter after a win."""

    def manual_halt(self, reason: str) -> None:
        """Permanent halt — can only be resumed via manual_resume()."""

    def manual_resume(self) -> None:
        """Resume from manual halt. Requires operator confirmation."""

    def reset_daily(self) -> None:
        """Reset daily counters. Called by scheduler at UTC 00:00."""

    @property
    def state(self) -> CircuitState:
        return self._state

    def status(self) -> dict:
        """State snapshot for monitoring/healthcheck."""
        return {
            'state':              self._state,
            'consecutive_losses': self._consecutive_losses,
            'halted_at':          self._halted_at,
            'manual_halt':        self._manual_halt,
            'halt_reasons':       self._halt_reasons,
        }
```

---

### 4.4 Auto-Resume Logic

```python
def _auto_resume_due(self) -> bool:
    """
    Auto-resume only applies to non-manual halts.
    Resumes after HALT_DURATION_MINUTES minutes.
    """
    if self._manual_halt:
        return False    # manual halt does not auto-resume
    if not self._halted_at:
        return False

    elapsed = datetime.utcnow() - self._halted_at
    return elapsed >= timedelta(minutes=self.halt_duration_minutes)

# Logic inside check():
# if self._state == CircuitState.HALTED and self._auto_resume_due():
#     self._resume()   # return to NORMAL
#     return CircuitState.NORMAL

# Daily reset (scheduler UTC 00:00):
# - Reset consecutive_losses → 0
# - If HALTED due to daily_loss: resume (not manual halt)
# - Drawdown is NOT reset — drawdown from peak is still tracked
```

---

### 4.5 Circuit Breaker Config Keys

| Key | Default | Valid Range | Description |
|---|---|---|---|
| `MAX_DAILY_LOSS_PCT` | 0.05 | 0.01–0.15 | 5% equity loss in one day → HALT |
| `WARN_DAILY_LOSS_PCT` | 0.03 | 0.01–0.10 | 3% loss → WARNED (still allowed to trade) |
| `MAX_DRAWDOWN_PCT` | 0.10 | 0.05–0.30 | 10% drawdown from peak → HALT |
| `MAX_CONSECUTIVE_LOSSES` | 5 | 3–15 | 5 consecutive losses → HALT |
| `HALT_DURATION_MINUTES` | 60 | 15–480 | Auto-resume after 60 minutes for non-manual halt |

---

## 5. position_size.py — Position Size Calculation

Determines the appropriate quantity for each trade. Three methods are available; all outputs are automatically clamped to the Binance lot filter.

### 5.1 Three Sizing Methods

| Method | Philosophy | When to Use | Advantage | Disadvantage |
|---|---|---|---|---|
| **fixed_fractional** | Risk X% of equity per trade | Default — all conditions | Simple, predictable | Does not adjust for volatility |
| **kelly** | Optimal based on win rate & RR | After 50+ trade history | Mathematically optimal | Overfits to short history |
| **volatility_scaled** | Smaller when volatile, larger when calm | Varying market conditions | Adaptive | Requires accurate ATR |

---

### 5.2 Fixed Fractional — Detail

```python
def _fixed_fractional(
    self,
    request: TradeRequest,
    equity:  float,
    price:   float,
) -> tuple[float, list[str], float]:
    """
    Return: (quantity, warnings, risk_usd)

    Formula:
    1. risk_amount = equity × risk_per_trade_pct
       Example: $10,000 × 1% = $100
    2. stop_distance = |entry_price - sl_price|
       Example: |50,000 - 49,000| = $1,000
    3. quantity = risk_amount / stop_distance
       Example: $100 / $1,000 = 0.1 BTC
    4. notional = quantity × price
       Example: 0.1 × 50,000 = $5,000
    """
    risk_amount   = equity * self.risk_per_trade
    stop_distance = abs(price - request.suggested_sl)

    if stop_distance <= 0:
        # Fallback: use default SL pct
        stop_distance = price * self.config.default_sl_pct

    quantity = risk_amount / stop_distance
    risk_usd = min(quantity * stop_distance, risk_amount)

    return quantity, [], risk_usd
```

---

### 5.3 Kelly Criterion — Detail

```python
def _kelly(
    self,
    request:         TradeRequest,
    equity:          float,
    price:           float,
    portfolio_state: dict,
) -> tuple[float, list[str], float]:
    """
    Kelly Criterion: f* = (b×p - q) / b
    b = avg_win / avg_loss  (risk-reward ratio)
    p = win rate
    q = 1 - p = loss rate

    Half-Kelly is used for safety (× kelly_fraction = 0.5)
    Full Kelly is often too aggressive for trading.
    """
    warnings = []

    stats = portfolio_state.get('strategy_stats', {}).get(
        request.strategy_id, {})

    if not stats:
        warnings.append(
            f'No stats for {request.strategy_id}, using defaults')
        win_rate = 0.50
        avg_rr   = 1.50
    else:
        win_rate = stats.get('win_rate', 0.50)
        avg_rr   = stats.get('avg_risk_reward', 1.50)

    # Validate: win_rate & rr must be reasonable
    if win_rate <= 0 or win_rate >= 1:
        win_rate = 0.50
        warnings.append('win_rate is invalid, reset to 0.50')

    b = avg_rr
    p = win_rate
    q = 1.0 - p

    kelly_pct = max(0.0, (b * p - q) / b) * self.kelly_fraction

    # Clamp: do not exceed 3× fixed fractional
    max_kelly = self.risk_per_trade * 3
    kelly_pct = min(kelly_pct, max_kelly)

    risk_amount = equity * kelly_pct
    stop_dist   = abs(price - request.suggested_sl) or price * 0.02
    quantity    = risk_amount / stop_dist

    return quantity, warnings, risk_amount
```

---

### 5.4 Volatility Scaled — Detail

```python
def _volatility_scaled(
    self,
    request:         TradeRequest,
    equity:          float,
    price:           float,
    portfolio_state: dict,
) -> tuple[float, list[str], float]:
    """
    ATR-based sizing: the more volatile, the smaller the position.
    Stop distance = ATR × atr_multiplier (default 2.0)
    """
    warnings = []
    atr_key  = f'atr_{request.symbol}'

    if atr_key not in portfolio_state:
        warnings.append(f'ATR not found for {request.symbol}, estimating 1.5% of price')
        atr = price * 0.015
    else:
        atr = portfolio_state[atr_key]

    stop_distance = atr * self.atr_multiplier
    risk_amount   = equity * self.risk_per_trade
    quantity      = risk_amount / stop_distance

    return quantity, warnings, risk_amount
```

---

### 5.5 Binance Lot Filter — Must Be Applied

```python
# All sizing methods must pass through the lot filter before returning

BINANCE_LOT_DEFAULTS = {
    'spot': {
        'min_qty':      0.00001,
        'step_size':    0.00001,
        'min_notional': 10.0,
    },
    'futures': {
        'min_qty':      0.001,
        'step_size':    0.001,
        'min_notional': 5.0,
        'max_notional': 1_000_000.0,
    }
}

def _apply_lot_filter(self, qty: float, is_futures: bool) -> float:
    """
    Floor quantity to step_size (NOT round — to avoid over-ordering).
    Uses math.floor, not round.

    Example:
        qty = 0.123456, step_size = 0.001
        floor(0.123456 / 0.001) × 0.001 = 0.123
    """
    import math
    market    = 'futures' if is_futures else 'spot'
    step      = BINANCE_LOT_DEFAULTS[market]['step_size']
    # Precision from step_size
    precision = max(0, round(-math.log10(step)))
    floored   = math.floor(qty / step) * step
    return round(floored, precision)

# After lot filter, check minimum notional:
# notional = qty × price
# if notional < MIN_NOTIONAL: return 0 → BLOCKED
```

---

### 5.6 Position Sizer Config Keys

| Key | Default | Valid Range | Description |
|---|---|---|---|
| `SIZING_METHOD` | fixed_fractional | fixed_fractional\|kelly\|volatility_scaled | Default method |
| `RISK_PER_TRADE_PCT` | 0.01 | 0.001–0.05 | Percentage of equity risked per trade |
| `MAX_POSITION_PCT` | 0.10 | 0.01–0.30 | Max notional per position as % of equity |
| `KELLY_FRACTION` | 0.5 | 0.1–1.0 | Half-Kelly. Values ≤ 0.5 are more conservative. |
| `ATR_MULTIPLIER` | 2.0 | 1.0–5.0 | Stop distance = ATR × multiplier (volatility_scaled) |
| `MIN_NOTIONAL_USD` | 10.0 | 5.0–100.0 | Minimum notional in USDT (Binance minimum) |

---

## 6. stoploss.py — Stop Loss Validation & Calculation

Ensures every trade has a valid stop loss before entry. SL is the most fundamental risk management component — without SL, losses can be unlimited.

### 6.1 SL Validation — Full Rules

| Rule | Check | Action if Failed | Config Key |
|---|---|---|---|
| SL must exist | suggested_sl > 0 | BLOCKED (live) / WARNED (paper) | REQUIRE_SL |
| Minimum distance | \|price - sl\| / price >= MIN_SL_DISTANCE_PCT | BLOCKED — SL too close | MIN_SL_DISTANCE_PCT |
| Maximum distance | \|price - sl\| / price <= MAX_SL_DISTANCE_PCT | WARNED — risk per trade is very large | MAX_SL_DISTANCE_PCT |
| Correct SL direction | BUY: sl < price, SELL: sl > price | BLOCKED — SL on wrong side | — |
| SL not zero | sl > 0 for BUY | BLOCKED — invalid price | — |

---

### 6.2 Public Interface

```python
class StopLossManager:

    def validate_and_compute(
        self,
        request:         TradeRequest,
        portfolio_state: dict,
    ) -> tuple[bool, str, float]:
        """
        Return: (ok, message, final_sl_price)
        If ok=True: final_sl is the validated SL.
        If ok=False:
          - Live mode: BLOCKED
          - Paper mode: final_sl = calculated default SL
        """

    def compute_default_sl(
        self,
        price: float,
        side:  str,
        atr:   float = 0.0,
    ) -> float:
        """
        Compute default SL if not set.
        Priority:
        1. If ATR is available: SL = price ± (ATR × SL_ATR_MULT)
        2. Fallback:            SL = price × (1 ± DEFAULT_SL_PCT)
        """

    def is_sl_tight_enough(
        self,
        price: float,
        sl:    float,
        atr:   float,
    ) -> tuple[bool, str]:
        """
        Check if SL is not too far away (risk too large per trade).
        SL is considered too far if:
        |price - sl| > ATR × MAX_SL_ATR_MULT
        """
```

---

### 6.3 Stop Loss Config Keys

| Key | Default | Valid Range | Description |
|---|---|---|---|
| `REQUIRE_SL` | True | bool | True in live mode. False for testing only. |
| `DEFAULT_SL_PCT` | 0.02 | 0.005–0.10 | Default SL 2% from price if no ATR. |
| `MIN_SL_DISTANCE_PCT` | 0.003 | 0.001–0.01 | SL minimum 0.3% from price. Below this is too susceptible to noise. |
| `MAX_SL_DISTANCE_PCT` | 0.15 | 0.05–0.30 | SL maximum 15% from price. Above this the risk is too large. |
| `SL_ATR_MULT` | 1.5 | 1.0–3.0 | Default SL = ATR × multiplier (if ATR available). |
| `MAX_SL_ATR_MULT` | 4.0 | 2.0–8.0 | SL is considered too far if > ATR × max_mult. |

---

## 7. exposure_control.py — Portfolio Exposure Control

Prevents the portfolio from being over-leveraged or over-concentrated. Runs after `portfolio_layer/allocator.py` but with a different perspective — allocator looks at USDT, exposure_control looks at percentage of equity.

### 7.1 All Checks Performed

| Check | Formula | Config Key | Default |
|---|---|---|---|
| Max open positions | count(open_positions) < MAX_OPEN_POSITIONS | MAX_OPEN_POSITIONS | 5 |
| Max per symbol | notional_symbol / equity <= MAX_PER_SYMBOL_PCT | MAX_EXPOSURE_PER_SYMBOL_PCT | 0.20 |
| Max total exposure | Σ(notional) / equity <= MAX_TOTAL_PCT | MAX_TOTAL_EXPOSURE_PCT | 0.80 |
| Correlated exposure | Σ(notional corr group) / equity <= MAX_CORRELATED_PCT | MAX_CORRELATED_EXPOSURE_PCT | 0.35 |

---

### 7.2 Public Interface

```python
class ExposureControl:

    def validate(
        self,
        request:         TradeRequest,
        portfolio_state: dict,
    ) -> tuple[bool, str]:
        """
        Return (True, '') if exposure is within limits.
        Return (False, reason) if a limit is exceeded.
        """
        equity      = portfolio_state.get('equity', 1.0)
        positions   = portfolio_state.get('open_positions', {})
        price       = request.price or portfolio_state.get(
            f'last_price_{request.symbol}', 0.0)
        new_notional = request.quantity * price

        # Check 1: max open positions
        if len(positions) >= self.max_positions:
            if request.symbol not in positions:   # not adding to existing position
                return False, f'Max positions ({self.max_positions}) already reached'

        # Check 2: max per symbol
        existing = positions.get(request.symbol, {}).get('notional', 0.0)
        if (existing + new_notional) / equity > self.max_per_symbol:
            return False, (
                f'{request.symbol} exposure {(existing+new_notional)/equity:.1%} '
                f'> max {self.max_per_symbol:.1%}'
            )

        # Check 3: max total
        total_current = sum(p.get('notional', 0) for p in positions.values())
        if (total_current + new_notional) / equity > self.max_total:
            return False, (
                f'Total exposure {(total_current+new_notional)/equity:.1%} '
                f'> max {self.max_total:.1%}'
            )

        # Check 4: correlated exposure
        corr_result = self._check_correlation(
            request.symbol, new_notional, positions, equity)
        if corr_result:
            return False, corr_result

        return True, ''
```

---

### 7.3 Correlation Pairs — Defaults

```python
# Pairs considered highly correlated
# Update via config if new pairs are added

CORRELATED_PAIRS: list[set[str]] = [
    {'BTCUSDT', 'ETHUSDT'},
    {'ETHUSDT', 'BNBUSDT'},
    {'BTCUSDT', 'ETHUSDT', 'BNBUSDT', 'SOLUSDT'},
]

def _check_correlation(
    self,
    symbol:       str,
    new_notional: float,
    positions:    dict,
    equity:       float,
) -> str:
    """Return error string if violated, empty string if safe."""
    for pair in CORRELATED_PAIRS:
        if symbol not in pair:
            continue

        partner_notional = sum(
            positions.get(s, {}).get('notional', 0)
            for s in pair if s != symbol
        )
        existing = positions.get(symbol, {}).get('notional', 0)
        total    = partner_notional + existing + new_notional

        if total / equity > self.max_correlated:
            return (
                f"Correlated exposure ({'+'.join(pair)}) "
                f'{total/equity:.1%} > max {self.max_correlated:.1%}'
            )
    return ''
```

---

## 8. leverage_control.py — Futures Leverage Control

Specific to futures trading. Prevents excessive leverage usage that could drastically amplify losses.

### 8.1 Leverage Limits per Symbol

| Symbol | Max Leverage (Agent) | Max Leverage (Binance) | Notes |
|---|---|---|---|
| BTCUSDT | 10x | 125x | Agent limits to 10x — safe for trend following strategies |
| ETHUSDT | 10x | 100x | Same as BTC |
| BNBUSDT | 5x | 75x | More volatile — tighter limit |
| SOLUSDT | 5x | 50x | Altcoin — tighter limit |
| DEFAULT | 3x | Varies | For unlisted symbols — conservative |

---

### 8.2 Public Interface

```python
class LeverageControl:

    def validate(
        self,
        leverage: int,
        symbol:   str,
    ) -> tuple[bool, str]:
        """
        Return (True, '') if leverage is valid.
        Return (False, reason) if limit is exceeded.
        """
        if leverage <= 0:
            return False, f'Invalid leverage: {leverage}'

        symbol_max   = SYMBOL_MAX_LEVERAGE.get(symbol, SYMBOL_MAX_LEVERAGE['DEFAULT'])
        effective_max = min(self.global_max, symbol_max)

        if leverage > effective_max:
            return False, (
                f'Leverage {leverage}x exceeds limit {effective_max}x '
                f'for {symbol} (global={self.global_max}, symbol={symbol_max})'
            )

        if leverage > 3:
            # Warning for high leverage even if still within limits
            log.warning('High leverage', leverage=leverage, symbol=symbol)

        return True, ''

    def get_max_leverage(self, symbol: str) -> int:
        """Return the maximum allowed leverage for this symbol."""
        symbol_max = SYMBOL_MAX_LEVERAGE.get(symbol, SYMBOL_MAX_LEVERAGE['DEFAULT'])
        return min(self.global_max, symbol_max)

    def get_effective_leverage(
        self,
        notional: float,
        margin:   float,
    ) -> float:
        """Calculate actual leverage from notional and margin used."""
        if margin <= 0: return 0.0
        return notional / margin
```

---

### 8.3 Leverage Control Config Keys

| Key | Default | Description |
|---|---|---|
| `GLOBAL_MAX_LEVERAGE` | 5 | Upper limit for ALL symbols. Overrides symbol-specific if smaller. |
| `ALLOW_CROSS_MARGIN` | False | False = isolated margin only. Cross margin carries higher risk. |
| `SYMBOL_MAX_LEVERAGE` | see table | Dict per symbol, can be overridden via config YAML. |

---

## 9. Integration — Full portfolio_state Format

`portfolio_state` is the dict sent to `risk_manager.evaluate()`. This is the contract between `main_loop` and `risk_layer`. All risk layer components read from this dict.

### 9.1 Full portfolio_state Format

```python
# Dict sent to risk_manager.evaluate()
# All fields are required except those marked 'optional'

portfolio_state: dict = {
    # ── REQUIRED ──────────────────────────────────────────────────────
    'equity':          10_000.0,
    'open_positions': {
        'BTCUSDT': {
            'trade_id':      'uuid-...',
            'side':          'BUY',
            'qty':            0.01,
            'notional':       500.0,     # qty × entry_price
            'entry_price':    50_000.0,
            'unrealized_pnl': 50.0,
        }
    },
    'exchange_status': 'normal',        # 'normal' | 'maintenance'
    'market_halted':   False,

    # ── PRICE (required for every symbol being traded) ─────────────────
    'last_price_BTCUSDT': 50_000.0,
    'last_price_ETHUSDT':  3_000.0,

    # ── FOR VOLATILITY SCALED SIZING (optional) ────────────────────────
    'atr_BTCUSDT':          800.0,
    'atr_ETHUSDT':           60.0,

    # ── FOR KELLY SIZING (optional) ────────────────────────────────────
    'strategy_stats': {
        'spot_strategy_v1': {
            'win_rate':         0.55,
            'avg_risk_reward':  1.80,
            'total_trades':     47,
        }
    },

    # ── FOR SPREAD CHECK (optional, from ticker) ───────────────────────
    'spread_pct_BTCUSDT':  0.012,
}
```

---

### 9.2 Who Fills portfolio_state

| Field | Filled By | Updated Every |
|---|---|---|
| `equity` | capital_manager.py (via sync/balance_sync) | Every periodic sync (60 seconds) |
| `open_positions` | trade_layer/store.py + sync/position_sync | Every trade open/close + periodic sync |
| `exchange_status` | data_layer/market.py (from exchange info) | Every health check (30 seconds) |
| `last_price_*` | data_layer/market.py (from ticker cache) | Every tick (from WebSocket) |
| `atr_*` | intelligence_layer/volatility.py | Every tick |
| `strategy_stats` | learning_layer/performance.db | Daily (after daily_eval) |
| `spread_pct_*` | data_layer/market.py (from ticker) | Every tick |

---

### 9.3 File Dependency Map

| File | Imports From | Called By |
|---|---|---|
| `risk_manager.py` | pre_trade_check, circuit_breaker, leverage_control, exposure_control, position_size, stoploss | main_loop process_signal() |
| `pre_trade_check.py` | utils/helpers (validate_order_id) | risk_manager.evaluate() |
| `circuit_breaker.py` | — (pure state machine) | risk_manager.evaluate(), exit_layer (record_win/loss) |
| `position_size.py` | utils/helpers (round_qty, round_price) | risk_manager.evaluate() |
| `stoploss.py` | utils/helpers (round_price) | risk_manager.evaluate() |
| `exposure_control.py` | — (pure computation) | risk_manager.evaluate() |
| `leverage_control.py` | — (pure computation) | risk_manager.evaluate() |

---

## 10. Test Scenarios — Critical Test Cases

The Risk Layer must have 100% branch coverage. The following test scenarios are mandatory as they are the most common sources of hidden bugs.

### 10.1 Happy Path Scenarios

| Scenario | Setup | Expected Result |
|---|---|---|
| Normal trade approved | Equity $10k, no positions, valid signal, paper mode | verdict=APPROVED, qty>0 |
| WARNED but continues | ATR not available → default is used | verdict=WARNED, qty>0, warning contains ATR message |
| Scale-down quantity | Requested qty 0.1 BTC but exceeds max_position_pct | verdict=APPROVED, qty < 0.1 (clamped), warning contains reason |
| Paper mode: block still runs | Circuit breaker HALTED, paper_mode=True | verdict=WARNED (not BLOCKED), qty>0 |

---

### 10.2 Error & Block Scenarios

| Scenario | Setup | Expected Result |
|---|---|---|
| Empty symbol | request.symbol = '' | verdict=BLOCKED, reason='Symbol is invalid' |
| Zero quantity | request.quantity = 0.0 | verdict=BLOCKED, reason='Quantity too small' |
| SL on wrong side (BUY+SL>entry) | side=BUY, sl=51000, price=50000 | verdict=BLOCKED, reason='SL on wrong side' |
| Circuit breaker HALTED | daily_pnl = -6% of equity, live mode | verdict=BLOCKED, reason='Circuit breaker HALTED' |
| Max positions reached | 5 open positions, trying to open a new symbol | verdict=BLOCKED, reason='Max positions' |
| Per-symbol exposure exceeded | BTCUSDT already at 20% equity, trying to add more | verdict=BLOCKED, reason='Exposure BTCUSDT' |
| Leverage too high | leverage=20, symbol=SOLUSDT (max=5) | verdict=BLOCKED, reason='Leverage 20x > max 5x' |
| Notional below minimum | very small qty → notional < MIN_NOTIONAL ($10) | verdict=BLOCKED, reason='Notional below minimum' |
| Exchange maintenance | portfolio_state['exchange_status']='maintenance' | verdict=BLOCKED, reason='Exchange maintenance' |

---

### 10.3 Edge Case Scenarios

| Scenario | Expected Behavior |
|---|---|
| equity = 0 | Pre-trade check: BLOCKED — cannot size from zero equity |
| SL = 0 (not set) | StopLoss: compute default SL, return WARNED (paper) / BLOCKED (live) |
| ATR = 0 (data unavailable) | PositionSizer: fallback to default_sl_pct, add warning |
| Kelly with win_rate = 0 | Kelly: clamp to 0, fallback to fixed_fractional |
| Two concurrent requests for the same symbol | Idempotency check in trade_layer handles this — risk_layer is not aware |
| consecutive_losses = 5, then a win | circuit_breaker.record_win() resets consecutive=0 |
| Manual halt → manual_resume() → check() called | After resume: state = NORMAL, all checks normal |

---

## 11. Risk Layer Implementation Checklist

### 11.1 Functional Checklist

| No | Item | Verification | Done |
|---|---|---|---|
| 1 | `risk_manager.evaluate()` is the single entry point — no bypass | `grep -rn 'place_order\|executor' | grep -v 'risk_manager'` → empty | ☐ |
| 2 | Paper mode: BLOCKED is changed to WARNED with qty still filled | Unit test: CB halted + paper_mode → verdict=WARNED, qty>0 | ☐ |
| 3 | All components accept portfolio_state dict with the same format | Unit test each component with a valid portfolio_state | ☐ |
| 4 | `circuit_breaker.record_loss/win()` called after every trade close | Code review exit_layer — after close, record is called | ☐ |
| 5 | `circuit_breaker.reset_daily()` called exactly at UTC 00:00 by scheduler | Mock datetime, verify daily_pnl=0 after reset | ☐ |
| 6 | `_apply_lot_filter()` uses floor, not round | Test: qty=0.1235, step=0.001 → 0.123 (not 0.124) | ☐ |
| 7 | SL on wrong side (BUY + sl > price) → BLOCKED | Unit test this case | ☐ |
| 8 | Correlated exposure check works for groups of 3+ symbols | Test: BTC+ETH+BNB all open, try opening SOL → check group | ☐ |
| 9 | Manual halt does not auto-resume (requires manual_resume()) | Inject manual_halt, wait > halt_duration → still HALTED | ☐ |
| 10 | `leverage_control` only active if is_futures=True | Test: leverage=20, is_futures=False → not blocked | ☐ |

---

### 11.2 Performance Checklist

| No | Item | Verification | Done |
|---|---|---|---|
| 11 | `evaluate()` completes in < 30ms (no I/O inside) | Benchmark: 1000 evaluations, avg < 30ms | ☐ |
| 12 | No DB queries inside `evaluate()` — all from portfolio_state | `grep -n 'sqlite\|db\|await' risk_layer/risk_manager.py` → empty | ☐ |
| 13 | No network calls inside `evaluate()` | `grep -n 'requests\|aiohttp\|await' risk_layer/*.py` → empty | ☐ |

---

### 11.3 Config Checklist

| No | Item | Verification | Done |
|---|---|---|---|
| 14 | All risk thresholds are in AgentConfig — no hardcoding | `grep -rn '0\.05\|0\.10\|0\.01' risk_layer/*.py` → from config only | ☐ |
| 15 | Config change log is filled every time a threshold is changed | Check agent_core_docs.md Section 3.4 Config Change Log | ☐ |

---

> **This document is the implementation contract for the Risk Layer.**
> **No order may be sent to the exchange without first passing through `risk_manager.evaluate()`.**
>
> *References: strategy_portfolio_docs.md · agent_core_docs.md · risk_layer skeleton code*
