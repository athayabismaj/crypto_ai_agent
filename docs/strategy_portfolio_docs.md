# crypto_ai_agent — Strategy Layer & Portfolio Layer Documentation

*base_strategy · spot_strategy · futures_strategy · strategy_utils*
*allocator · capital_manager · correlation · risk_budget*

**Version 1.0** | Reference: data_intelligence_docs.md · agent_core_docs.md

---

## 1. Overview — Two Layers, One Purpose

The Strategy Layer generates trading signals based on market conditions. The Portfolio Layer ensures those signals do not damage the overall capital structure. Both operate before the Risk Layer executes the final decision.

| Aspect | Strategy Layer | Portfolio Layer |
|---|---|---|
| Core question | Is there an opportunity in this market right now? | Are we still able to take this opportunity? |
| Input | MarketState (from intelligence layer) | PortfolioState, active allocations, correlations |
| Output | Signal or None | Approved quota or BLOCKED |
| Knows about money? | No — knows market only | Yes — knows all positions and capital |
| Stateful? | Minimal — only cooldown per symbol | Yes — tracks all active allocations |
| I/O? | No — pure computation | No — pure computation |
| Called from | main_loop after intelligence update | risk_layer before position sizing |

---

### 1.1 Position in the Trading Flow

```
MarketState  (from intelligence_layer)
     │
     ▼
strategy_layer/
├── base_strategy.py      ← abstract contract
├── spot_strategy.py      ← spot signals
├── futures_strategy.py   ← futures signals + hedge
└── strategy_utils.py     ← shared helpers
     │
     ▼  Signal
     │
portfolio_layer/           ← capital allocation validation
├── allocator.py           ← quota per strategy & symbol
├── capital_manager.py     ← global equity tracking
├── correlation.py         ← exposure limits between correlated assets
└── risk_budget.py         ← risk distribution per strategy
     │
     ▼  Signal + approved_quota
     │
risk_layer/                ← position sizing + circuit breaker
```

---

## PART A — Strategy Layer

## 2. base_strategy.py — Abstract Contract

Defines the contract that all strategies must fulfil. Every new strategy MUST extend `BaseStrategy` and implement all abstract methods.

> **HARD RULE:** No network access, no DB read/write, no side effects inside strategy methods. `generate_signal()` and `should_exit()` must be pure functions that can be called 1000 times without issues.

### 2.1 Abstract Base Class

```python
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional

class BaseStrategy(ABC):

    def __init__(self, config: AgentConfig, model: TrainedModel):
        self.config       = config
        self.model        = model
        self.strategy_id  = self.__class__.__name__
        self._last_signal: dict[str, datetime] = {}   # cooldown tracker
        self._signal_count: dict[str, int]     = {}   # burst limiter

    # ── MUST be implemented ──────────────────────────────────────────

    @abstractmethod
    def generate_signal(
        self,
        state: MarketState
    ) -> Optional['Signal']:
        """
        Return a Signal if there is an opportunity, None if not.
        Called EVERY TICK by main_loop.
        MAY:      read state, call model.predict(), compute thresholds
        MUST NOT: access exchange, write to DB, send notifications, any I/O
        """

    @abstractmethod
    def should_exit(
        self,
        position: 'Position',
        state:    MarketState
    ) -> Optional['ExitSignal']:
        """
        Evaluate whether an existing position should be closed based on
        current market conditions (outside of SL/TP managed by exit_layer).
        Examples: exit due to regime change, or model prediction flip.
        """

    @abstractmethod
    def get_signal_metadata(self, state: MarketState) -> dict:
        """
        Return additional metadata for logging & LLM review.
        Minimum: {'reasoning': str, 'model_output': float}
        """

    # ── CAN be overridden (defaults provided) ───────────────────────

    def is_allowed_to_trade(
        self,
        state: MarketState
    ) -> tuple[bool, str]:
        """
        Global filter applied before generate_signal is called.
        Default checks (can be overridden):
        1. Regime filter  — block if HIGH_VOLATILITY
        2. Spread filter  — block if spread > MAX_SPREAD
        3. Cooldown filter — block if too soon after last signal
        4. Safety filter  — block if is_safe_to_trade = False
        """
        # Check 1: Safety
        if not state.is_safe_to_trade:
            return False, 'Market not safe to trade (anomaly detected)'

        # Check 2: Regime filter
        if self.config.regime_filter:
            if state.regime == MarketRegime.HIGH_VOLATILITY:
                return False, f'Blocked: regime is HIGH_VOLATILITY'

        # Check 3: Spread filter
        if state.spread_pct > self.config.max_spread_pct:
            return False, f'Spread {state.spread_pct:.3f}% > max {self.config.max_spread_pct:.3f}%'

        # Check 4: Cooldown
        last = self._last_signal.get(state.symbol)
        if last:
            elapsed  = (utcnow() - last).total_seconds()
            required = self.config.signal_cooldown * tf_to_seconds(self.config.timeframe)
            if elapsed < required:
                return False, f'Cooldown: {elapsed:.0f}s < {required:.0f}s required'

        return True, ''

    def get_confidence_multiplier(self, state: MarketState) -> float:
        """
        Confidence modifier based on additional conditions.
        Default: 1.0 (no modification).
        Override to: scale up during strong regime, scale down near resistance.
        """
        return 1.0
```

---

### 2.2 Signal Dataclass — Output Contract

```python
@dataclass
class Signal:
    # ── Identity ─────────────────────────────────────────────────────
    symbol:           str
    side:             str       # 'BUY' | 'SELL'
    strategy_id:      str
    timestamp:        datetime

    # ── Order parameters ─────────────────────────────────────────────
    signal_type:      str       # 'market' | 'limit' | 'conditional'
    suggested_price:  float     # 0.0 = use market price
    suggested_sl:     float     # REQUIRED — risk_layer needs this
    suggested_tp:     float = 0.0  # optional (0.0 = no fixed TP)

    # ── Confidence ───────────────────────────────────────────────────
    confidence:       float     # 0.0 – 1.0 (raw from model)
    final_confidence: float = 0.0  # after multiplier & LLM filter

    # ── Context ──────────────────────────────────────────────────────
    reasoning:        str   = ''      # explanatory text for logging
    model_output:     float = 0.0     # raw model output (return or prob)
    regime:           str   = ''      # regime when signal was generated
    vol_regime:       str   = ''
    metadata:         dict  = field(default_factory=dict)

    def __post_init__(self):
        if self.final_confidence == 0.0:
            self.final_confidence = self.confidence
        if self.suggested_sl <= 0:
            raise ValueError('suggested_sl MUST be > 0')

@dataclass
class ExitSignal:
    position_id:  str
    reason:       str    # 'regime_change' | 'model_flip' | 'time_exit' | 'manual'
    urgency:      str    # 'normal' | 'urgent'
    exit_price:   float  # 0.0 = market price
    confidence:   float  # how certain to exit (0.0–1.0)
    metadata:     dict = field(default_factory=dict)
```

---

## 3. spot_strategy.py — Spot Trading Strategy

Concrete implementation for spot trading BTCUSDT (and other pairs). Uses an ML model to predict return direction, combined with technical and regime filters.

### 3.1 Signal Generation Logic

```python
class SpotStrategy(BaseStrategy):

    def generate_signal(self, state: MarketState) -> Optional[Signal]:

        # ── Step 1: Pre-filter ───────────────────────────────────────
        allowed, reason = self.is_allowed_to_trade(state)
        if not allowed:
            return None

        # ── Step 2: Model prediction ─────────────────────────────────
        pred = self.model.predict(state.features.values.reshape(1, -1))[0]
        # pred = predicted return N candles forward (float)

        # ── Step 3: Entry threshold ──────────────────────────────────
        entry_threshold = self._get_dynamic_threshold(state)
        if abs(pred) < entry_threshold:
            return None   # prediction too small, not worth it

        side = 'BUY' if pred > 0 else 'SELL'

        # ── Step 4: Spot-specific filter ─────────────────────────────
        # Spot cannot short — only BUY is allowed
        if side == 'SELL' and not self.config.allow_spot_short:
            return None

        # If already holding a position on this symbol, skip
        if state.symbol in state.open_positions:
            return None

        # ── Step 5: Confidence calculation ───────────────────────────
        confidence = self._calc_confidence(pred, state)
        multiplier = self.get_confidence_multiplier(state)
        final_conf = min(confidence * multiplier, 1.0)

        if final_conf < self.config.min_signal_confidence:
            return None

        # ── Step 6: SL / TP calculation ──────────────────────────────
        sl_price = self._calc_sl(state.last_price, side, state.atr)
        tp_price = self._calc_tp(state.last_price, sl_price, side)

        # ── Step 7: Update cooldown & return signal ───────────────────
        self._last_signal[state.symbol] = utcnow()

        return Signal(
            symbol           = state.symbol,
            side             = side,
            strategy_id      = self.strategy_id,
            timestamp        = utcnow(),
            signal_type      = 'market',
            suggested_price  = 0.0,
            suggested_sl     = sl_price,
            suggested_tp     = tp_price,
            confidence       = confidence,
            final_confidence = final_conf,
            reasoning        = self._build_reasoning(pred, state),
            model_output     = pred,
            regime           = state.regime.value,
            vol_regime       = state.vol_regime,
        )
```

---

### 3.2 Dynamic Entry Threshold

```python
def _get_dynamic_threshold(self, state: MarketState) -> float:
    """
    Entry threshold adjusted to market conditions.
    The more volatile / stronger the regime → the higher the threshold.
    """
    base = self.config.model_threshold    # default: 0.003 (0.3%)

    # Scale based on volatility regime
    vol_multiplier = {
        'low':     0.8,   # low volatility → lower threshold
        'normal':  1.0,
        'high':    1.3,   # high volatility → requires stronger signal
        'extreme': 2.0,
    }.get(state.vol_regime, 1.0)

    # Scale based on spread
    spread_cost = state.spread_pct / 100    # convert to decimal

    # Threshold = base × vol_multiplier + spread cost
    return base * vol_multiplier + spread_cost
```

---

### 3.3 SL & TP Calculation

```python
def _calc_sl(self, price: float, side: str, atr: float) -> float:
    """
    ATR-based SL to adapt to volatility.
    SL distance = atr × SL_ATR_MULTIPLIER
    """
    distance = atr * self.config.sl_atr_multiplier    # default: 1.5
    distance = max(distance, price * self.config.default_sl_pct)   # floor: 2%

    if side == 'BUY':
        return round_price(price - distance, tick_size)
    else:
        return round_price(price + distance, tick_size)


def _calc_tp(self, price: float, sl: float, side: str) -> float:
    """
    TP based on risk-reward ratio from config.
    Risk   = |price - sl|
    Reward = risk × TP_RR_RATIO
    """
    risk   = abs(price - sl)
    reward = risk * self.config.default_tp_ratio    # default: 2.0 (1:2 RR)

    if side == 'BUY':
        return round_price(price + reward, tick_size)
    else:
        return round_price(price - reward, tick_size)
```

---

### 3.4 should_exit — Strategy-Based Exit Logic

```python
def should_exit(
    self,
    position: Position,
    state:    MarketState
) -> Optional[ExitSignal]:
    """
    Strategy-based exit conditions (outside SL/TP from exit_layer):
    """
    # Condition 1: Regime flip opposite to the position
    if position.side == 'BUY':
        adverse_regimes = [MarketRegime.STRONG_TREND_DOWN, MarketRegime.WEAK_TREND_DOWN]
    else:
        adverse_regimes = [MarketRegime.STRONG_TREND_UP, MarketRegime.WEAK_TREND_UP]

    if state.regime in adverse_regimes and state.regime_stable:
        return ExitSignal(
            position_id = position.trade_id,
            reason      = 'regime_change',
            urgency     = 'normal',
            exit_price  = 0.0,
            confidence  = state.regime_confidence,
            metadata    = {'regime': state.regime.value}
        )

    # Condition 2: Model flip — prediction shifts direction significantly
    pred = self.model.predict(state.features.values.reshape(1, -1))[0]

    if position.side == 'BUY' and pred < -self.config.model_flip_threshold:
        return ExitSignal(
            position_id = position.trade_id,
            reason      = 'model_flip',
            urgency     = 'normal',
            exit_price  = 0.0,
            confidence  = abs(pred) / self.config.model_flip_threshold,
        )

    return None
```

---

### 3.5 Spot Strategy Config Keys

| Key | Default | Valid Range | Description |
|---|---|---|---|
| `MODEL_THRESHOLD` | 0.003 | 0.001–0.05 | Minimum predicted return for entry |
| `SL_ATR_MULTIPLIER` | 1.5 | 1.0–3.0 | SL distance = ATR × multiplier |
| `DEFAULT_TP_RATIO` | 2.0 | 1.0–5.0 | Risk:Reward ratio for TP |
| `MIN_SIGNAL_CONFIDENCE` | 0.60 | 0.30–0.95 | Minimum confidence to pass to risk layer |
| `SIGNAL_COOLDOWN` | 3 | 1–20 | Minimum candles between two signals on the same symbol |
| `REGIME_FILTER` | True | bool | True = block signals during HIGH_VOLATILITY |
| `MAX_SPREAD_PCT` | 0.5 | 0.1–2.0 | Maximum spread to allow a signal (%) |
| `ALLOW_SPOT_SHORT` | False | bool | False = BUY only for spot |
| `MODEL_FLIP_THRESHOLD` | 0.005 | 0.002–0.02 | Model flip exit if prediction opposes by > threshold |

---

## 4. futures_strategy.py — Futures Strategy

Extends `SpotStrategy` with short selling capability and leverage management. Also includes hedging logic to protect existing spot positions.

### 4.1 Differences from SpotStrategy

| Aspect | SpotStrategy | FuturesStrategy |
|---|---|---|
| Short selling | No (ALLOW_SPOT_SHORT defaults to False) | Yes — BUY = long, SELL = short |
| Leverage | Always 1x | Configurable (max 10x, default 3x) |
| Funding rate | Not relevant | Factored into entry decision |
| Margin type | None | Isolated (default) or Cross |
| Hedge mode | None | Can open LONG + SHORT simultaneously |
| Spot basis | None | Monitors spot-futures basis for arb signals |
| Max hold candles | 48 (default) | 24 (stricter due to funding cost) |

---

### 4.2 Funding Rate Impact

```python
def _is_funding_cost_acceptable(
    self,
    side:         str,
    funding_rate: float,
    pred_return:  float
) -> bool:
    """
    Check whether the funding rate does not erode the predicted return.

    Funding cost per trade:
    - If LONG and funding_rate > 0: we PAY funding → positive cost
    - If SHORT and funding_rate > 0: we RECEIVE funding → negative cost (benefit)
    - Reversed if funding_rate < 0

    Formula:
    expected_funding_cost = abs(funding_rate) × hold_candles / (8h_in_candles)
    → If expected_cost > pred_return × MAX_FUNDING_COST_RATIO → reject signal
    """
    hold_estimate  = self.config.avg_hold_candles    # estimated hold
    funding_per_8h = abs(funding_rate)               # cost per 8 hours
    tf_per_8h      = 8 * 3600 / tf_to_seconds(self.config.timeframe)
    funding_cost   = funding_per_8h * (hold_estimate / tf_per_8h)

    # If LONG and positive funding: we pay
    paying_funding = (side == 'BUY' and funding_rate > 0) or \
                     (side == 'SELL' and funding_rate < 0)

    if paying_funding:
        max_acceptable = abs(pred_return) * self.config.max_funding_cost_ratio
        return funding_cost <= max_acceptable

    return True   # receiving funding = bonus, always ok
```

---

### 4.3 Hedge Logic

```python
def generate_hedge_signal(
    self,
    spot_position: Position,
    state:         MarketState,
) -> Optional[Signal]:
    """
    Open a SHORT futures position to hedge an existing LONG spot position.

    Called when:
    1. There is a significant LONG spot position
    2. The model predicts a short-term decline
    3. We do not want to sell spot (long-term holding)

    Hedge size = spot_qty × HEDGE_RATIO (default 0.5 = 50% hedge)
    """
    pred = self.model.predict(state.features.values.reshape(1, -1))[0]

    # Only hedge if prediction is strongly negative
    if pred > -self.config.hedge_threshold:
        return None

    hedge_qty = spot_position.filled_qty * self.config.hedge_ratio
    sl_price  = self._calc_sl(state.last_price, 'SELL', state.atr)

    return Signal(
        symbol          = state.symbol,
        side            = 'SELL',
        strategy_id     = f'{self.strategy_id}_hedge',
        signal_type     = 'market',
        suggested_price = 0.0,
        suggested_sl    = sl_price,
        confidence      = abs(pred) / self.config.hedge_threshold,
        reasoning       = f'Hedge spot long: pred={pred:.4f}',
        metadata        = {'is_hedge': True, 'hedge_ratio': self.config.hedge_ratio}
    )
```

---

### 4.4 Futures Strategy Config Keys

| Key | Default | Description |
|---|---|---|
| `DEFAULT_LEVERAGE` | 3 | Default leverage for all futures positions |
| `MAX_LEVERAGE` | 10 | Upper leverage limit (overrides global_max_leverage if smaller) |
| `MARGIN_TYPE` | isolated | isolated \| cross — isolated is safer |
| `MAX_HOLD_CANDLES` | 24 | Stricter than spot due to funding cost |
| `MAX_FUNDING_COST_RATIO` | 0.30 | Funding cost max 30% of predicted return |
| `HEDGE_ENABLED` | False | True = enable spot position hedge logic |
| `HEDGE_RATIO` | 0.5 | 50% of spot position is hedged |
| `HEDGE_THRESHOLD` | 0.005 | Prediction must be < -0.5% to trigger hedge |

---

## 5. strategy_utils.py — Shared Helpers

A collection of utility functions used by all strategies. Has no state — all functions are pure.

### 5.1 Signal Scoring & Filtering

```python
def score_signal(
    pred:       float,
    regime:     MarketRegime,
    vol_regime: str,
    spread_pct: float,
    imbalance:  float,
) -> float:
    """
    Composite score 0.0–1.0 from multiple factors.
    Used for final_confidence before the MIN_SIGNAL_CONFIDENCE check.
    """
    score = 0.0

    # Model prediction strength (40% weight)
    pred_score = min(abs(pred) / 0.01, 1.0)   # normalize: 1% pred = full score
    score += pred_score * 0.40

    # Regime alignment (30% weight)
    regime_score = {
        MarketRegime.STRONG_TREND_UP:   0.9,
        MarketRegime.WEAK_TREND_UP:     0.6,
        MarketRegime.SIDEWAYS:          0.3,
        MarketRegime.WEAK_TREND_DOWN:   0.6,
        MarketRegime.STRONG_TREND_DOWN: 0.9,
        MarketRegime.HIGH_VOLATILITY:   0.0,   # never trade in this regime
        MarketRegime.UNDEFINED:         0.0,
    }.get(regime, 0.0)
    score += regime_score * 0.30

    # Volatility (20% weight) — low vol is better for precision
    vol_score = {'low': 1.0, 'normal': 0.7, 'high': 0.4, 'extreme': 0.0}.get(vol_regime, 0.5)
    score += vol_score * 0.20

    # Orderbook imbalance (10% weight)
    # Only if imbalance direction aligns with prediction
    if (pred > 0 and imbalance > 0) or (pred < 0 and imbalance < 0):
        score += min(abs(imbalance), 1.0) * 0.10

    return round(score, 4)
```

---

### 5.2 Other Utility Functions

| Function | Parameters | Return | Description |
|---|---|---|---|
| `calc_rr_ratio()` | entry, sl, tp | float | Actual Risk:Reward from prices |
| `is_near_resistance()` | price, df, margin=0.02 | bool | Check if price is near a key resistance level |
| `is_near_support()` | price, df, margin=0.02 | bool | Check if price is near a key support level |
| `tf_to_seconds()` | timeframe: str | int | '1h' → 3600, '4h' → 14400 |
| `align_sl_to_structure()` | price, side, df, atr | float | Shift SL to nearest swing low/high |
| `get_trend_strength()` | df: pd.DataFrame | float 0–1 | Trend strength based on ADX + EMA slope |
| `build_reasoning()` | pred, state, metadata | str | Generate reasoning text for logging |

---

## PART B — Portfolio Layer

The Portfolio Layer is the capital control layer that sits between the Strategy Layer and the Risk Layer. It does not make market decisions — it only ensures that no strategy uses more than its capital allocation.

> **CORE PRINCIPLE:** The Portfolio Layer is the 'portfolio manager' that looks at the entire portfolio, not a single trade. A perfect signal from a strategy can still be rejected if the portfolio is already over-exposed.

---

## 6. allocator.py — Capital Allocation

Controls how much capital can be used by each strategy + symbol combination. This is the first portfolio layer component consulted before position sizing.

### 6.1 Allocation Model

```python
@dataclass
class AllocationBudget:
    strategy_id:    str
    symbol:         str
    max_usdt:       float    # maximum quota in USDT
    used_usdt:      float    # amount already used
    available_usdt: float    # max_usdt - used_usdt
    pct_of_equity:  float    # available / total_equity × 100

@dataclass
class AllocationMatrix:
    """Snapshot of all active allocations."""
    total_equity:    float
    free_equity:     float
    reserved_equity: float                      # MIN_CASH_RESERVE
    allocations:     dict[str, AllocationBudget]  # key = f'{strategy_id}:{symbol}'
    total_used_pct:  float
    timestamp:       datetime
```

---

### 6.2 Public Interface

```python
class PortfolioAllocator:

    def get_budget(
        self,
        strategy_id: str,
        symbol:      str,
        equity:      float,
        positions:   dict,
    ) -> AllocationBudget:
        """
        Return how much USDT is available for this strategy+symbol combination.
        Called by risk_layer before position sizing.
        """

    def record_opened(
        self,
        strategy_id: str,
        symbol:      str,
        notional:    float,
    ) -> None:
        """Update internal tracking when a position is opened."""

    def record_closed(
        self,
        strategy_id: str,
        symbol:      str,
        notional:    float,
    ) -> None:
        """Release allocation when a position is closed."""

    def get_matrix(self, equity: float, positions: dict) -> AllocationMatrix:
        """Snapshot of the full allocation situation. For monitoring & reporting."""

    def can_open(
        self,
        strategy_id: str,
        symbol:      str,
        notional:    float,
        equity:      float,
        positions:   dict,
    ) -> tuple[bool, str]:
        """
        Check all limits at once:
        1. Per-strategy limit
        2. Per-symbol limit
        3. Cash reserve
        4. Correlation budget (delegated to correlation.py)
        Return (True, '') or (False, reason)
        """
```

---

### 6.3 Allocation Rules — All Limits

| Limit | Formula | Config Key | Default | On Failure |
|---|---|---|---|---|
| Per strategy | notional <= equity × MAX_STRATEGY_PCT | MAX_STRATEGY_ALLOCATION_PCT | 0.40 | Block signal |
| Per symbol | notional <= equity × MAX_SYMBOL_PCT | MAX_SYMBOL_ALLOCATION_PCT | 0.20 | Block signal |
| Cash reserve | free_equity >= equity × MIN_CASH_RESERVE | MIN_CASH_RESERVE_PCT | 0.10 | Block signal |
| Max positions | count(open) < MAX_OPEN_POSITIONS | MAX_OPEN_POSITIONS | 5 | Block signal |
| Correlated pair | combined <= equity × MAX_CORRELATED_PCT | MAX_CORRELATED_PCT | 0.35 | Block signal |

---

### 6.4 Allocation Calculation Example

```python
# Example: Equity = $10,000
# Config: MAX_STRATEGY=40%, MAX_SYMBOL=20%, RESERVE=10%

# Current situation:
# - SpotStrategy:BTCUSDT = $1,500 active
# - SpotStrategy:ETHUSDT = $1,000 active
# Total SpotStrategy used = $2,500 (25% of equity)

# Query: how much budget is available for SpotStrategy:SOLUSDT?

# Step 1: Strategy budget = equity × 40% = $4,000
#         Used = $2,500 → Available = $1,500

# Step 2: Symbol budget = equity × 20% = $2,000
#         SOLUSDT not yet open → Available = $2,000

# Step 3: Cash reserve check
#         Total used = $2,500, reserve = $1,000
#         Free = $10,000 - $2,500 - $1,000 = $6,500

# Step 4: Correlation check
#         SOLUSDT not strongly correlated with BTC/ETH → pass

# Result: min($1,500, $2,000) = $1,500 available for SOLUSDT
```

---

## 7. capital_manager.py — Global Capital Management

Tracks equity in real-time and detects dangerous conditions before the Risk Layer is triggered. The Capital Manager is the 'accounting system' of the agent.

### 7.1 CapitalStatus Dataclass

```python
@dataclass
class CapitalStatus:
    # ── Equity snapshot ──────────────────────────────────────────────
    current_equity:     float
    peak_equity:        float
    initial_equity:     float

    # ── PnL ──────────────────────────────────────────────────────────
    daily_pnl:          float
    daily_pnl_pct:      float
    total_pnl:          float
    total_pnl_pct:      float

    # ── Drawdown ─────────────────────────────────────────────────────
    current_drawdown:   float    # from peak (pct)
    max_drawdown_today: float
    max_drawdown_ever:  float

    # ── Exposure ─────────────────────────────────────────────────────
    open_notional:      float    # total value of open positions
    exposure_pct:       float    # open_notional / equity × 100
    unrealized_pnl:     float    # PnL of positions still open

    # ── Status flags ─────────────────────────────────────────────────
    safe_to_trade:      bool
    warnings:           list[str]
    timestamp:          datetime
```

---

### 7.2 Public Interface

```python
class CapitalManager:

    def update(
        self,
        realized_equity: float,    # from balance_sync.py
        open_positions:  dict,     # for computing unrealized
        current_prices:  dict,     # symbol → last_price
    ) -> CapitalStatus:
        """
        Called every tick (via scheduler sync interval).
        Updates all metrics and returns status.
        If dangerous conditions exist, adds to warnings.
        If critical, sets safe_to_trade = False.
        """

    def get_status(self) -> CapitalStatus:
        """Return the last status (cached, does not recompute)."""

    def record_trade_open(
        self,
        notional: float,
        risk_usd: float,
    ) -> None:
        """Update exposure when a position is opened."""

    def record_trade_close(
        self,
        pnl_usd:    float,
        commission: float,
    ) -> None:
        """Update PnL and equity when a position is closed."""

    def get_compound_equity(self) -> float:
        """
        Equity for paper mode (simulated compounding).
        Live mode: use realized_equity from balance_sync.
        """
```

---

### 7.3 Warning & Critical Thresholds

| Condition | Warning Threshold | Critical (safe_to_trade=False) | Config Key |
|---|---|---|---|
| Daily PnL loss | < -3% of equity | < -5% of equity | WARN/MAX_DAILY_LOSS_PCT |
| Drawdown from peak | < -7% from peak | < -10% from peak | WARN/MAX_DRAWDOWN_PCT |
| Total exposure | > 70% equity | > 85% equity | WARN/MAX_EXPOSURE_PCT |
| Unrealized loss | < -3% of equity | < -6% of equity | WARN/MAX_UNREALIZED_LOSS |

---

### 7.4 Equity Compounding (Paper Mode)

```python
# Paper mode: equity never changes from the exchange balance.
# Capital manager simulates compounding internally.

class PaperEquityTracker:

    def __init__(self, initial: float):
        self._equity = initial
        self._peak   = initial

    def apply_trade(
        self,
        pnl_usd:    float,
        commission: float
    ) -> float:
        net_pnl       = pnl_usd - commission
        self._equity += net_pnl
        self._peak    = max(self._peak, self._equity)
        return self._equity

    @property
    def equity(self) -> float: return self._equity

    @property
    def peak(self) -> float: return self._peak

    @property
    def drawdown(self) -> float:
        return (self._peak - self._equity) / self._peak
```

---

## 8. correlation.py — Correlation Control

Prevents over-concentration in assets that move together. If BTCUSDT and ETHUSDT are both in LONG positions, losses can double when the market falls simultaneously.

### 8.1 Correlation Map — Static

```python
# Pairs treated as 'one bucket'
# Update based on empirical 90-day rolling observations

CORRELATION_GROUPS: list[dict] = [
    {
        'id':              'btc_eth',
        'symbols':         ['BTCUSDT', 'ETHUSDT'],
        'corr':            0.85,
        'max_combined_pct': 0.35,    # max 35% combined equity
    },
    {
        'id':              'eth_alts',
        'symbols':         ['ETHUSDT', 'BNBUSDT', 'SOLUSDT'],
        'corr':            0.75,
        'max_combined_pct': 0.40,
    },
    {
        'id':              'large_caps',
        'symbols':         ['BTCUSDT', 'ETHUSDT', 'BNBUSDT', 'SOLUSDT'],
        'corr':            0.65,
        'max_combined_pct': 0.60,
    },
]

# Config: CORRELATION_GROUPS can be overridden via config YAML
# for periodic updates without redeploying code.
```

---

### 8.2 Public Interface

```python
class CorrelationController:

    def check(
        self,
        new_symbol:   str,
        new_side:     str,
        new_notional: float,
        positions:    dict,
        equity:       float,
    ) -> tuple[bool, str]:
        """
        Check if opening a new position would violate correlation limits.
        Return (True, '') = safe
        Return (False, reason) = rejected
        """
        for group in self._groups:
            if new_symbol not in group['symbols']:
                continue

            # Compute current combined exposure in this group
            current_combined = sum(
                pos['notional']
                for sym, pos in positions.items()
                if sym in group['symbols']
            )

            if (current_combined + new_notional) / equity > group['max_combined_pct']:
                return False, (
                    f"Correlation group '{group['id']}' limit: "
                    f"{group['max_combined_pct']:.0%} equity. "
                    f"Current={current_combined/equity:.1%}, "
                    f"Would be={(current_combined+new_notional)/equity:.1%}"
                )

        return True, ''

    def get_group_exposure(
        self,
        group_id:  str,
        positions: dict,
        equity:    float,
    ) -> float:
        """Return % equity exposed in a specific correlation group."""

    def update_groups(self, new_groups: list[dict]) -> None:
        """Update correlation groups from config YAML (no restart needed)."""
```

---

### 8.3 Dynamic Correlation (Optional)

```python
# For advanced implementation: compute rolling correlation from historical data
# Enable with DYNAMIC_CORRELATION = True in config

def compute_rolling_correlation(
    returns_df: pd.DataFrame,   # columns = symbols, rows = candles
    window:     int = 90,
) -> pd.DataFrame:
    """
    Return a 90-candle rolling correlation matrix.
    If actual correlation > threshold → tighten exposure limits.
    If actual correlation < threshold → loosen limits.
    """
    return returns_df.rolling(window).corr().iloc[-len(returns_df.columns):]

# Note: dynamic correlation is more accurate but:
# 1. Requires historical candle data for all symbols
# 2. More computationally expensive
# 3. Correlation can shift suddenly during crises (all correlations → 1)
# For initial implementation: use pre-calibrated static correlation groups.
```

---

## 9. risk_budget.py — Risk Distribution

Controls how much risk (in USD) may be taken per strategy, per day, and in total. Unlike the allocator which controls notional — `risk_budget` controls the maximum loss amount.

### 9.1 Risk Budget Dataclass

```python
@dataclass
class RiskBudgetStatus:
    strategy_id:      str

    # Daily
    daily_risk_budget: float    # max loss per day for this strategy
    daily_risk_used:   float    # realized + unrealized loss today
    daily_risk_pct:    float    # used / budget × 100
    daily_remaining:   float    # budget - used

    # Total
    total_risk_budget: float    # accumulated from initial equity
    total_risk_used:   float

    # Status
    is_exhausted:      bool     # True if remaining <= 0
    is_warning:        bool     # True if remaining < 20% of budget
    timestamp:         datetime
```

---

### 9.2 Public Interface

```python
class RiskBudgetManager:

    def get_status(
        self,
        strategy_id: str,
        equity:      float,
    ) -> RiskBudgetStatus:
        """Return the risk budget status for a given strategy."""

    def can_take_risk(
        self,
        strategy_id: str,
        risk_amount: float,    # USD to be risked on this trade
        equity:      float,
    ) -> tuple[bool, str]:
        """
        Check if there is budget remaining to take this amount of risk.
        Called by risk_layer/position_size.py after sizing.
        """

    def record_risk_taken(
        self,
        strategy_id: str,
        risk_amount: float,
    ) -> None:
        """Record risk taken when a position is opened."""

    def record_risk_realized(
        self,
        strategy_id: str,
        actual_pnl:  float,    # positive = win, negative = loss
    ) -> None:
        """Update when a position is closed — free budget if win, consume if loss."""

    def reset_daily(self) -> None:
        """Called by scheduler at UTC 00:00."""

    def get_portfolio_risk_summary(self, equity: float) -> dict:
        """Summary across all strategies for monitoring."""
```

---

### 9.3 Budget Allocation Formula

```python
# Risk budget is computed from equity at the start of the day

def compute_daily_budget(
    strategy_id:     str,
    equity:          float,
    config:          AgentConfig,
    strategy_weight: float = 1.0,    # relative weight between strategies
) -> float:
    """
    Daily risk budget per strategy:
    1. Total daily budget = equity × max_daily_loss_pct
       Example: $10,000 × 5% = $500/day

    2. Per strategy = total_budget × strategy_weight / sum(all_weights)
       If 2 strategies with equal weight: each gets $250/day

    3. Per trade = risk_per_trade_pct × equity
       Example: 1% × $10,000 = $100/trade

    4. Max trades per day per strategy:
       = daily_budget / risk_per_trade
       = $250 / $100 = 2.5 → max 2 trades/day
    """
    total_budget = equity * config.max_daily_loss_pct
    return total_budget * strategy_weight
```

---

### 9.4 Risk Budget Config Keys

| Key | Default | Description |
|---|---|---|
| `STRATEGY_WEIGHTS` | {'default': 1.0} | Dict of relative weights between strategies. Does not have to sum to 1.0. |
| `BUDGET_WARN_THRESHOLD` | 0.20 | Warning if remaining budget < 20% of daily budget. |
| `CARRY_OVER_WINS` | False | True = today's profits can add to tomorrow's budget (compound risk). |
| `RESET_ON_DAILY` | True | True = budget resets every UTC 00:00. |

---

## 10. Integration — Signal Flow from Strategy to Risk

### 10.1 Full Sequence Per Signal

```python
# Called from main_loop every time a new Signal arrives:

async def process_signal(
    signal:    Signal,
    state:     MarketState,
    portfolio: PortfolioAllocator,
    capital:   CapitalManager,
    corr:      CorrelationController,
    risk_bgt:  RiskBudgetManager,
    risk_mgr:  RiskManager,
) -> ProcessResult:

    # ── LAYER 1: Portfolio checks ─────────────────────────────────
    # 1a. Capital check
    cap_status = capital.get_status()
    if not cap_status.safe_to_trade:
        return ProcessResult.BLOCKED_CAPITAL

    # 1b. Allocation check
    ok, reason = portfolio.can_open(
        signal.strategy_id, signal.symbol,
        estimated_notional, state.equity, state.open_positions
    )
    if not ok:
        return ProcessResult.BLOCKED_ALLOCATION

    # 1c. Correlation check
    ok, reason = corr.check(
        signal.symbol, signal.side,
        estimated_notional, state.open_positions, state.equity
    )
    if not ok:
        return ProcessResult.BLOCKED_CORRELATION

    # ── LAYER 2: Risk checks (risk_layer) ─────────────────────────
    risk_result = risk_mgr.evaluate(
        TradeRequest.from_signal(signal),
        portfolio_state
    )
    if not risk_result.is_approved:
        return ProcessResult.BLOCKED_RISK

    # ── LAYER 3: Risk budget final check ──────────────────────────
    ok, reason = risk_bgt.can_take_risk(
        signal.strategy_id,
        risk_result.risk_amount_usd,
        state.equity
    )
    if not ok:
        return ProcessResult.BLOCKED_BUDGET

    # ── APPROVED: proceed to trade_layer ──────────────────────────
    return ProcessResult.APPROVED
```

---

### 10.2 File Dependency Map

| File | Imports From | Output Consumed By |
|---|---|---|
| `base_strategy.py` | intelligence_layer (MarketState, MarketRegime), models (TrainedModel) | spot_strategy, futures_strategy |
| `spot_strategy.py` | base_strategy, strategy_utils | main_loop (via strategy registry) |
| `futures_strategy.py` | base_strategy, spot_strategy, strategy_utils | main_loop (via strategy registry) |
| `strategy_utils.py` | intelligence_layer (MarketRegime), utils/helpers | spot_strategy, futures_strategy |
| `allocator.py` | correlation.py | main_loop process_signal(), risk_layer |
| `capital_manager.py` | sync/balance_sync (equity update) | main_loop, risk_layer (equity snapshot) |
| `correlation.py` | — (pure computation) | allocator.py |
| `risk_budget.py` | capital_manager.py (equity) | main_loop process_signal() final check |

---

## 11. Strategy Registry — Multi-Strategy Management

As the system grows, there may be multiple strategies running simultaneously. The Strategy Registry manages the lifecycle of all active strategies.

### 11.1 StrategyRegistry Interface

```python
class StrategyRegistry:

    def register(
        self,
        strategy: BaseStrategy,
        symbols:  list[str],
        weight:   float = 1.0,
    ) -> None:
        """Register a strategy and the symbols it handles."""

    def disable(self, strategy_id: str, reason: str) -> None:
        """
        Deactivate a strategy. Called by strategy_killer.py.
        Existing open positions continue to be managed until closed.
        """

    def enable(self, strategy_id: str) -> None:
        """Re-activate after review."""

    def get_active_strategies(
        self,
        symbol: str = None,
    ) -> list[BaseStrategy]:
        """Return all active strategies, filtered by symbol if provided."""

    def get_signals(
        self,
        state: MarketState,
    ) -> list[Signal]:
        """
        Call generate_signal() for all active strategies.
        Return list of signals (empty if no opportunities).
        Already filtered to MAX_SIGNALS_PER_TICK.
        """

    def get_status(self) -> dict:
        """Status of all strategies: active, disabled, performance."""
```

---

### 11.2 Signal Priority with Multiple Strategies

| Condition | Handling |
|---|---|
| Two strategies signal on the same symbol | Take the one with higher confidence. If equal, take the earlier one. |
| BUY and SELL signals on the same symbol | Block both — conflicting signals. Log as CONFLICT. |
| Number of signals > MAX_SIGNALS_PER_TICK | Sort by confidence DESC, take the top N. N = MAX_SIGNALS_PER_TICK. |
| One strategy signals on multiple symbols | Process all, but portfolio layer will block if allocation is exhausted. |

---

## 12. Implementation Checklist — Strategy & Portfolio Layer

### 12.1 Strategy Layer Checklist

| No | Item | Verification | Done |
|---|---|---|---|
| 1 | `BaseStrategy.generate_signal()` has no I/O or side effects | `grep -n 'await\|open(\|sqlite\|requests' strategy_layer/` | ☐ |
| 2 | `Signal.suggested_sl` always > 0 — enforced in `__post_init__` | Unit test: create Signal with sl=0 → ValueError | ☐ |
| 3 | `is_allowed_to_trade()` runs before `generate_signal()` | Code review SpotStrategy.generate_signal — step 1 | ☐ |
| 4 | Cooldown per symbol works correctly after a signal is sent | Test: generate signal 2x within 3 candles → both should be None | ☐ |
| 5 | `ALLOW_SPOT_SHORT=False` actually blocks SELL signals in spot | Unit test: SpotStrategy with side=SELL → None | ☐ |
| 6 | Futures strategy checks funding rate before entry | Test: inject high funding rate → signal is blocked | ☐ |
| 7 | `score_signal()` returns values 0.0–1.0 for all valid inputs | Property test with random inputs | ☐ |
| 8 | `should_exit()` returns None if there is no strong reason to exit | Test: inject SIDEWAYS regime on BUY position → return None | ☐ |

---

### 12.2 Portfolio Layer Checklist

| No | Item | Verification | Done |
|---|---|---|---|
| 9 | `allocator.can_open()` rejects if cash reserve is breached | Test: open positions up to 90% equity → rejected (reserve=10%) | ☐ |
| 10 | `correlation.check()` rejects when BTCUSDT+ETHUSDT exceeds 35% | Test: open BTC 20% + ETH 20% → try ETH again → rejected | ☐ |
| 11 | `capital_manager.safe_to_trade=False` when daily loss > threshold | Test: inject 6% loss → safe_to_trade=False | ☐ |
| 12 | `risk_budget.reset_daily()` called exactly at UTC 00:00 by scheduler | Test with mock datetime, check daily_risk_used=0 after reset | ☐ |
| 13 | `record_trade_open/close` are paired — no allocation leak | Test: open 5 positions, close all → free_equity returns to start | ☐ |
| 14 | `StrategyRegistry.disable()` does not close existing positions | Test: disable strategy → existing positions remain open | ☐ |
| 15 | Conflicting signals (BUY+SELL same symbol) are both blocked | Test: inject two strategies with opposing signals on BTCUSDT | ☐ |

---

> **This document is the implementation contract for the Strategy Layer & Portfolio Layer.**
> Any changes to the Signal dataclass, config key defaults, or allocation rules
> **MUST** be updated before merging to the main branch.
>
> *Related references: data_intelligence_docs.md · agent_core_docs.md · risk_layer skeleton code*
