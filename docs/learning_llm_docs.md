# crypto_ai_agent — Learning Layer & LLM Layer Documentation

*reflection · adaptation · drift_detection · experience_processor · strategy_killer*
*llm_client · llm_budget · llm_rate_limiter · trade_analyzer · strategy_feedback · llm_filter*

**Version 1.0** | Reference: agent_core_docs.md · strategy_portfolio_docs.md

---

## 1. Overview — Two Adaptive Layers

The Learning Layer enables the system to learn from its own experience. The LLM Layer adds AI-based analysis as an advisor — not a decision maker. Both are optional: the system runs fully without either of them.

> **CORE PRINCIPLE:** The LLM **NEVER** executes orders. The LLM only produces a `confidence_multiplier` (0.0–1.5) that is multiplied by the original signal confidence. If the LLM fails, the system uses the default multiplier of 1.0 and continues normally.

| Aspect | Learning Layer | LLM Layer |
|---|---|---|
| Purpose | Automatic adaptation from trading history | Contextual analysis via natural language |
| Input | experience.db, performance.db | MarketState, Signal, recent trade history |
| Output | Updated parameters, disabled strategies | confidence_multiplier (float 0.0–1.5) |
| Frequency | Daily (batch) | Per signal (if budget available) |
| Critical path? | No | No |
| Can fail? | Yes, skip — does not affect execution | Yes, fallback multiplier=1.0 — trading continues |

---

### 1.1 Data Flow

```
experience.db  →  experience_processor  →  performance.db
                          │
               ┌──────────┴────────────┐
               ▼                       ▼
         reflection.py         drift_detection.py
               │                       │
               ▼                       ▼
         adaptation.py          retrain trigger
                           strategy_killer.py

(optional, per signal):
Signal + MarketState  →  llm_filter.py  →  confidence_multiplier
                               │
                     llm_budget.py (cost gate)
                     llm_rate_limiter.py (throttle)
                     llm_client.py (Anthropic API)
```

---

## PART A — Learning Layer

---

## 2. experience_processor.py

Processes closed trades from `experience.db` into structured statistics. Runs daily at UTC 01:00 after reconciliation.

### 2.1 StrategyStats Dataclass

```python
@dataclass
class StrategyStats:
    strategy_id:             str
    period:                  str       # '7d' | '30d' | '90d' | 'all'
    computed_at:             datetime
    total_trades:            int
    winning_trades:          int
    win_rate:                float
    profit_factor:           float     # gross_profit / gross_loss
    total_pnl_usd:           float
    avg_win_usd:             float
    avg_loss_usd:            float
    avg_risk_reward:         float
    max_consecutive_wins:    int
    max_consecutive_losses:  int
    max_drawdown_pct:        float
    sharpe_ratio:            float
    sortino_ratio:           float
    avg_hold_candles:        float
    best_regime:             str       # regime with highest win rate
    worst_regime:            str       # regime with lowest win rate
    high_conf_win_rate:      float     # win rate for confidence > 0.75
    low_conf_win_rate:       float     # win rate for confidence <= 0.75
```

---

### 2.2 Public Interface

```python
class ExperienceProcessor:

    async def process_daily(self) -> dict[str, StrategyStats]:
        '''Read experience.db, compute stats for all periods, save to performance.db.'''

    def get_stats(
        self, strategy_id: str, period: str = '30d'
    ) -> StrategyStats | None:
        '''Retrieve stats from performance.db cache.'''

    def get_regime_breakdown(
        self, strategy_id: str, period: str = '30d'
    ) -> dict:
        '''Win rate & avg PnL per regime. Input for reflection.py.'''

    def get_confidence_bins(
        self, strategy_id: str
    ) -> list['ConfidenceBin']:
        '''
        Divide trade history into confidence bins:
        [0.0-0.5), [0.5-0.6), [0.6-0.7), [0.7-0.8), [0.8-1.0]
        Return win_rate & avg_pnl per bin.
        Used for calibrating MIN_SIGNAL_CONFIDENCE.
        '''
```

---

### 2.3 Confidence Bin — Example Output & Action

```python
# Example output of get_confidence_bins() for SpotStrategyV1:
#
# Bin        | Trades | Win Rate | Avg PnL
# [0.0-0.5)  |     12 |   33.3%  |  -$45
# [0.5-0.6)  |     28 |   46.4%  |  -$12
# [0.6-0.7)  |     47 |   55.3%  |  +$28  ← optimal threshold is here
# [0.7-0.8)  |     31 |   71.0%  |  +$67
# [0.8-1.0]  |     15 |   80.0%  |  +$89
#
# Insight: bins < 0.65 are consistently negative.
# Action adaptation.py: raise min_signal_confidence to 0.65
```

---

## 3. reflection.py — Qualitative Analysis

Analyzes closed trades to identify patterns of success and failure. The output of reflection feeds into adaptation.

### 3.1 TradeReflection Dataclass

```python
@dataclass
class TradeReflection:
    trade_id:         str
    verdict:          str         # 'good' | 'acceptable' | 'bad' | 'terrible'
    pnl_r:            float       # PnL in risk units (1R = entry to SL)
    positives:        list[str]
    negatives:        list[str]
    process_followed: bool        # did SL, sizing, and entry follow the rules?
    lessons:          list[str]
    regime_at_entry:  str
    confidence:       float
    hold_candles:     int
    exit_reason:      str
```

---

### 3.2 Trade Scoring Logic

| Condition | Verdict | Explanation |
|---|---|---|
| PnL >= +2R | **good** | Ideal trade — target exceeded |
| PnL >= 0 OR loss < -0.5R | **acceptable** | Acceptable — process was likely correct |
| PnL from -0.5R to -1.0R | **bad** | Needs review — something can be improved |
| PnL < -1.0R | **terrible** | Process not followed — SL failed or sizing was wrong |

---

### 3.3 Detected Patterns (Batch Reflection)

| Pattern | Detection Method | Recommended Action |
|---|---|---|
| Repeated losses in SIDEWAYS regime | exit_reason=SL & regime_at_entry=sideways > 3x | Add regime filter for sideways |
| Low confidence always loses | bin [0.0-0.6) win_rate < 35% | Raise min_signal_confidence |
| Hold too short, premature exit | avg_hold < 3 candles & exit_reason=TRAIL | Increase trail_atr_mult |
| Profit eroded by tight trailing | avg exit_reason=TRAIL with low PnL | Increase trail_atr_mult |
| Frequent entry at end of trend | regime flip occurs within 5 candles of entry | Add regime_stable filter |

---

## 4. drift_detection.py — Model Degradation Detection

Detects whether the ML model is starting to lose its predictive capability. The most critical early warning system in the learning layer — if ignored, the system will keep trading with an irrelevant model.

### 4.1 Drift Types & Thresholds

| Drift Type | Metric | Detection Method | Alert Threshold | Retrain Threshold |
|---|---|---|---|---|
| Feature drift | PSI per feature | Live distribution vs training distribution | 0.10–0.25 | 0.25+ |
| Prediction drift | KL divergence | Live prediction distribution vs baseline | 0.30 | 0.70 |
| IC degradation | Rolling IC 20 days | Spearman(pred, actual_return) | < 0.04 | < 0.02 |
| Win rate decline | Pct drop 30 days | Live win rate vs training baseline | -10% | -20% |
| Regime shift | Cosine distance | 30-day regime distribution vs 90-day | 0.20 | 0.40 |

---

### 4.2 DriftReport Dataclass

```python
@dataclass
class DriftReport:
    strategy_id:        str
    checked_at:         datetime
    feature_drifts:     list['FeatureDriftResult']   # PSI per feature
    max_psi:            float
    drifted_features:   list[str]                    # features with PSI > threshold
    kl_divergence:      float
    rolling_ic:         float
    rolling_win_rate:   float
    baseline_ic:        float
    baseline_win_rate:  float
    needs_retrain:      bool
    needs_alert:        bool
    severity:           str    # 'none' | 'minor' | 'moderate' | 'major'
    recommended_action: str
```

---

### 4.3 Public Interface

```python
class DriftDetector:

    def check(
        self,
        live_features:    pd.DataFrame,   # features from last 30 days
        live_predictions: pd.Series,      # predictions from last 30 days
        live_trades:      list,           # closed trades from last 30 days
    ) -> DriftReport:
        '''Called daily at UTC 06:00. Runs all checks and returns report.'''

    def compute_psi(
        self, expected: pd.Series, actual: pd.Series, bins: int = 10
    ) -> float:
        '''
        Population Stability Index.
        PSI = sum((actual_pct - expected_pct) * ln(actual_pct / expected_pct))
        < 0.10 = stable | 0.10-0.25 = minor | > 0.25 = major
        '''

    def compute_rolling_ic(
        self, predictions: pd.Series, actual_returns: pd.Series, window: int = 20
    ) -> float:
        '''Spearman correlation over the last rolling window.'''

    def get_retrain_recommendation(self, report: DriftReport) -> str:
        if report.needs_retrain:
            return 'RETRAIN — run automation/retrain.py'
        if report.needs_alert:
            return 'MONITOR — watch for the next 3 days'
        return 'OK'
```

---

## 5. adaptation.py — Automatic Parameter Update

Adjusts strategy parameters based on analysis results. Only modifies parameters within defined bounds — does not change model architecture or features.

> **ADAPTATION LIMITS:** Adaptation may only change parameters within the MIN–MAX range defined in config. Changes beyond this still require manual review and retraining.

### 5.1 Adaptable Parameters

| Parameter | Min | Max | Change Trigger | Direction |
|---|---|---|---|---|
| `min_signal_confidence` | 0.40 | 0.90 | win_rate < 45% for 30 days | Up by 0.05 |
| `sl_atr_multiplier` | 1.0 | 3.5 | avg loss > 1.2R over 20 trades | Up by 0.2 |
| `default_tp_ratio` | 1.2 | 5.0 | profit_factor < 1.2 for 30 days | Up by 0.2 |
| `signal_cooldown` | 1 | 10 | 3 consecutive losses | Up by 1 |
| `trail_atr_mult` | 1.0 | 4.0 | TRAIL exit with low profit | Up by 0.2 |
| `risk_per_trade_pct` | 0.005 | 0.03 | sharpe < 0.5 for 30 days | Down by 0.002 |

---

### 5.2 Public Interface

```python
class AdaptationEngine:

    def evaluate(
        self, stats: StrategyStats, config: AgentConfig
    ) -> 'AdaptationResult':
        '''Evaluate & propose changes. Does NOT apply directly.'''

    def apply(
        self,
        result:  'AdaptationResult',
        config:  AgentConfig,
        dry_run: bool = False,
    ) -> bool:
        '''
        Save config_backup BEFORE applying.
        dry_run=True → log only, do not change.
        '''

    def rollback(
        self, result: 'AdaptationResult', config: AgentConfig
    ) -> bool:
        '''Restore to config_backup from result.'''

    def get_adaptation_history(
        self, strategy_id: str, last_n: int = 10
    ) -> list['AdaptationResult']:
        '''Adaptation history from DB for audit.'''
```

---

## 6. strategy_killer.py — Auto-Disable Strategy

Automatically disables underperforming strategies. Existing open positions continue to be managed until they are closed normally.

### 6.1 Kill Criteria

| Criteria | Threshold | Period | Logic |
|---|---|---|---|
| Consecutive losses | > 7 in a row | Real-time | ANY one condition met |
| Sharpe ratio | < -0.5 | 30 days | ANY one condition met |
| Win rate | < 35% | 30 days | ANY one condition met |
| Max drawdown | > 15% | 30 days | ANY one condition met |
| Profit factor | < 0.7 | 30 days | ANY one condition met |

---

### 6.2 Disable Flow

```python
async def _kill(self, strategy_id: str, reasons: list[str]) -> None:
    # Safe disable sequence:

    # 1. Stop accepting new signals from this strategy
    self._registry.disable(strategy_id, '; '.join(reasons))

    # 2. Existing positions are STILL managed (not force-closed)

    # 3. Record to disabled_strategies.json
    await self._save_disabled_record(strategy_id, reasons)

    # 4. Send alert
    await self._alerts.send(Alert(
        severity  = AlertSeverity.WARNING,
        title     = f'Strategy Disabled: {strategy_id}',
        message   = str(reasons),
        component = 'strategy_killer',
    ))

    # 5. Schedule re-evaluation after REVIEW_AFTER_DAYS days
    await self.schedule_review(strategy_id, days=self.config.review_after_days)
```

---

### 6.3 Strategy Killer Config Keys

| Key | Default | Description |
|---|---|---|
| `KILL_SHARPE_THRESHOLD` | -0.5 | Sharpe below this → disable |
| `KILL_WIN_RATE_THRESHOLD` | 0.35 | Win rate below 35% → disable |
| `KILL_DD_THRESHOLD` | 0.15 | 30-day drawdown > 15% → disable |
| `KILL_CONSEC_LOSSES` | 7 | 7 consecutive losses → disable |
| `KILL_PF_THRESHOLD` | 0.7 | Profit factor < 0.7 → disable |
| `MIN_TRADES_FOR_EVAL` | 20 | Strategies with < 20 trades are not evaluated |
| `REVIEW_AFTER_DAYS` | 7 | Automatic re-evaluation after 7 days inactive |

---

## PART B — LLM Layer

---

## 7. llm_client.py — Anthropic API Connection

### 7.1 LLMRequest & LLMResponse

```python
@dataclass
class LLMRequest:
    system_prompt: str
    user_message:  str
    model:         str   = 'claude-haiku-4-5-20251001'
    max_tokens:    int   = 512
    temperature:   float = 0.1    # low for consistency
    timeout_s:     int   = 10     # strict timeout — do not block main loop

@dataclass
class LLMResponse:
    content:       str
    input_tokens:  int
    output_tokens: int
    model:         str
    latency_ms:    float
    cost_usd:      float
    success:       bool
    error:         str = ''
```

---

### 7.2 Interface & Error Handling

```python
class LLMClient:

    PRICING = {
        'claude-haiku-4-5-20251001': {'input': 0.25,  'output': 1.25},
        'claude-sonnet-4-6':         {'input': 3.00,  'output': 15.00},
        'claude-opus-4-6':           {'input': 15.00, 'output': 75.00},
    }

    async def call(self, request: LLMRequest) -> LLMResponse:
        try:
            start = time.time()
            resp  = await asyncio.wait_for(
                self._api_call(request), timeout=request.timeout_s
            )
            return LLMResponse(
                content       = resp.content[0].text,
                input_tokens  = resp.usage.input_tokens,
                output_tokens = resp.usage.output_tokens,
                model         = request.model,
                latency_ms    = (time.time() - start) * 1000,
                cost_usd      = self._compute_cost(resp.usage, request.model),
                success       = True,
            )
        except asyncio.TimeoutError:
            return LLMResponse(content='', input_tokens=0, output_tokens=0,
                               model=request.model, latency_ms=10000, cost_usd=0,
                               success=False, error='Timeout 10s')
        except Exception as e:
            return LLMResponse(content='', input_tokens=0, output_tokens=0,
                               model=request.model, latency_ms=0, cost_usd=0,
                               success=False, error=str(e))
```

---

## 8. llm_budget.py & llm_rate_limiter.py

### 8.1 llm_budget.py — Cost Control

```python
class LLMBudget:

    async def can_call(
        self,
        estimated_tokens: int = 500,
        model: str = 'claude-haiku-4-5-20251001',
    ) -> bool:
        '''Return False if spent + estimated_cost > daily_limit.'''
        pricing  = LLMClient.PRICING[model]
        est_cost = (estimated_tokens * pricing['input'] +
                    estimated_tokens * pricing['output']) / 1_000_000
        return (self._spent_today + est_cost) <= self._daily_limit

    async def record_usage(self, response: LLMResponse) -> None:
        self._spent_today += response.cost_usd
        self._calls_today += 1
        await self._persist()   # survive restart

    def reset_daily(self) -> None:
        '''Called by scheduler at UTC 00:00.'''
        self._spent_today = 0.0
        self._calls_today = 0
```

---

### 8.2 Budget Allocation — Recommendations

| Model | Input /1M tok | Output /1M tok | Recommendation | Budget $1/day |
|---|---|---|---|---|
| `claude-haiku-4-5-20251001` | $0.25 | $1.25 | Default for per-signal filtering | ~650 calls |
| `claude-sonnet-4-6` | $3.00 | $15.00 | Deep analysis, limit to 5–10x/day | ~50 calls |
| `claude-opus-4-6` | $15.00 | $75.00 | Weekly review only | ~10 calls |

---

### 8.3 llm_rate_limiter.py — Token Bucket

```python
class LLMRateLimiter:
    '''Token bucket algorithm. Non-blocking — returns False if limit is reached.'''

    def __init__(
        self,
        calls_per_minute:  int = 5,
        tokens_per_minute: int = 50_000,
    ):
        self._call_bucket  = TokenBucket(calls_per_minute)
        self._token_bucket = TokenBucket(tokens_per_minute)

    def acquire(self, estimated_tokens: int = 500) -> bool:
        '''Non-blocking. Returns False if rate limit is reached.'''
        return (self._call_bucket.consume(1) and
                self._token_bucket.consume(estimated_tokens))
```

---

## 9. llm_filter.py — Confidence Scoring

The main component of the LLM layer. Produces a `confidence_multiplier` that is applied to the original signal confidence before it is sent to the risk layer.

### 9.1 LLMScore Dataclass

```python
@dataclass
class LLMScore:
    confidence_multiplier: float    # 0.0–1.5
    proceed:               bool     # True if multiplier >= 0.5
    reasoning:             str
    concerns:              list[str]
    model_used:            str
    cost_usd:              float
    latency_ms:            float
    fallback_used:         bool     # True if LLM failed, using default 1.0

    @property
    def is_blocking(self) -> bool:
        return self.confidence_multiplier < 0.3
```

---

### 9.2 System Prompt Template

```python
SYSTEM_PROMPT = '''
You are an experienced cryptocurrency trading analyst.
Evaluate the trading signal and respond ONLY in JSON format:
{
  "confidence_multiplier": <float 0.0-1.5>,
  "proceed": <bool>,
  "reasoning": "<one sentence>",
  "concerns": ["<concern 1>", "<concern 2>"]
}

confidence_multiplier guide:
  1.5 = very strong signal, ideal conditions
  1.0 = normal signal, no concerns
  0.7 = minor concerns
  0.3 = serious concerns, consider skipping
  0.0 = do not trade
'''
```

---

### 9.3 score_signal() — Full Flow

```python
async def score_signal(
    self, signal: Signal, state: MarketState, last_trades: list
) -> LLMScore:
    # Step 1: Budget check
    if not await self._budget.can_call():
        return self._fallback('Daily budget exhausted')

    # Step 2: Rate limit check
    if not self._rate_limiter.acquire():
        return self._fallback('Rate limit reached')

    # Step 3: Build & call
    request  = LLMRequest(
        system_prompt = SYSTEM_PROMPT,
        user_message  = self._build_message(signal, state, last_trades),
        model         = self._config.llm_model,
        max_tokens    = 256,
        temperature   = 0.1,
    )
    response = await self._client.call(request)

    if not response.success:
        return self._fallback(f'LLM error: {response.error}')

    # Step 4: Parse JSON & clamp
    try:
        data       = json.loads(response.content)
        multiplier = max(0.0, min(1.5, float(data['confidence_multiplier'])))
    except (json.JSONDecodeError, KeyError, ValueError):
        return self._fallback('Invalid JSON from LLM')

    # Step 5: Record usage
    await self._budget.record_usage(response)

    return LLMScore(
        confidence_multiplier = multiplier,
        proceed    = data.get('proceed', multiplier >= 0.5),
        reasoning  = data.get('reasoning', ''),
        concerns   = data.get('concerns', []),
        model_used = response.model,
        cost_usd   = response.cost_usd,
        latency_ms = response.latency_ms,
        fallback_used = False,
    )

def _fallback(self, reason: str) -> LLMScore:
    log.warning('LLM fallback', reason=reason)
    return LLMScore(
        confidence_multiplier = 1.0,   # default: do not alter signal
        proceed=True, reasoning=f'Fallback: {reason}',
        concerns=[], model_used='fallback',
        cost_usd=0.0, latency_ms=0.0, fallback_used=True,
    )
```

---

## 10. trade_analyzer.py & strategy_feedback.py

### 10.1 trade_analyzer.py

In-depth analysis of significant trades (|pnl| > 2× risk) and unusual market conditions. Uses `claude-sonnet` for higher-quality analysis.

```python
class TradeAnalyzer:

    async def analyze_closed_trade(
        self, trade: 'ClosedTrade'
    ) -> 'TradeAnalysis' | None:
        '''Only for trades where |pnl| > 2x risk_amount.'''
        if abs(trade.pnl_usd) < trade.risk_amount_usd * 2:
            return None

        # Prompt requests 3 insights: timing, SL placement, lesson
        # Format: {"timing": str, "sl_assessment": str, "lesson": str}
        ...

    async def analyze_unusual_market(
        self, state: MarketState, anomalies: list[str]
    ) -> str:
        '''Called when anomaly_detector detects a MEDIUM anomaly.'''
        ...
```

---

### 10.2 strategy_feedback.py — Weekly Feedback

```python
class StrategyFeedback:
    '''Sends weekly performance summary to LLM and requests improvement suggestions.'''

    async def weekly_feedback(
        self,
        strategy_id: str,
        stats:       StrategyStats,
        reflections: list[TradeReflection],
    ) -> 'FeedbackReport':
        '''
        Called by weekly scheduler every Sunday at UTC 03:00.
        Uses claude-sonnet (deeper than haiku).
        Prompt includes: 30-day statistics + recurring reflection patterns.
        Requests 3 specific recommendations in JSON format.
        '''

@dataclass
class FeedbackReport:
    strategy_id:     str
    week_ending:     datetime
    recommendations: list[str]   # 3 recommendations from LLM
    model_used:      str
    cost_usd:        float
```

---

## 11. Integration — How the Main Loop Uses Both Layers

### 11.1 LLM Filter in Main Loop

```python
# After risk_layer approves a signal, before trade_layer.open():

async def apply_llm_filter(
    signal: Signal,
    state:  MarketState,
    llm:    LLMFilter,
    config: AgentConfig,
) -> Signal | None:

    if not config.llm_enabled:
        return signal    # disabled → pass through without modification

    last_trades = trade_store.get_closed_trades(
        limit=5, strategy_id=signal.strategy_id)

    score = await llm_filter.score_signal(signal, state, last_trades)

    # Update final_confidence with multiplier
    signal.final_confidence = min(
        signal.final_confidence * score.confidence_multiplier, 1.0)

    signal.metadata['llm_score'] = {
        'multiplier': score.confidence_multiplier,
        'reasoning':  score.reasoning,
        'fallback':   score.fallback_used,
    }

    # If confidence after multiplier is below threshold → skip
    if signal.final_confidence < config.min_signal_confidence:
        return None

    return signal
```

---

### 11.2 Learning Layer Schedule

| Component | Time | Output |
|---|---|---|
| `experience_processor.process_daily` | UTC 01:00 | StrategyStats to performance.db |
| `reflection.reflect_batch` | UTC 01:15 | BatchReflection — recurring patterns |
| `adaptation.evaluate + apply` | UTC 01:30 | Config update (if needed) |
| `drift_detection.check` | UTC 06:00 | DriftReport, trigger retrain if needed |
| `strategy_killer.evaluate_all` | UTC 07:00 | Disable underperforming strategies |
| `strategy_feedback.weekly_feedback` | Sunday 03:00 | FeedbackReport with LLM recommendations |
| `trade_analyzer.analyze_closed_trade` | On-demand (after large trade) | TradeAnalysis insights |
| `llm_filter.score_signal` | Per signal (if budget available) | confidence_multiplier |

---

### 11.3 Dependency Map

| File | Imports From | Consumed By |
|---|---|---|
| `experience_processor.py` | experience.db, performance.db | reflection, adaptation, strategy_killer |
| `reflection.py` | experience_processor | adaptation, strategy_feedback |
| `adaptation.py` | reflection, config | AgentConfig (direct update) |
| `drift_detection.py` | models/metadata.json, live features | automation/retrain.py, alerts |
| `strategy_killer.py` | experience_processor, registry | registry (disable), alerts |
| `llm_client.py` | Anthropic API | llm_filter, trade_analyzer, strategy_feedback |
| `llm_budget.py` | DB (budget persist) | llm_filter, trade_analyzer, strategy_feedback |
| `llm_rate_limiter.py` | — (in-memory token bucket) | llm_filter |
| `llm_filter.py` | llm_client, llm_budget, llm_rate_limiter | main_loop apply_llm_filter() |
| `trade_analyzer.py` | llm_client, llm_budget | main_loop (after large trade close) |
| `strategy_feedback.py` | llm_client, llm_budget, experience_processor | scheduler weekly |

---

## 12. Implementation Checklist

### 12.1 Learning Layer Checklist

| No | Item | Verification | Done |
|---|---|---|---|
| 1 | `experience_processor` only reads experience.db — does not write to state.db | `grep -n 'state.db' experience_processor.py` → empty | ☐ |
| 2 | `adaptation.apply()` saves config_backup before applying changes | Test rollback: apply → rollback → config restored | ☐ |
| 3 | Adaptation parameters stay within defined MIN/MAX bounds | Test: inject very poor stats → parameter clamped at limit | ☐ |
| 4 | `drift_detection` uses baseline from models/metadata.json | Unit test: mock metadata, confirm PSI computed from training baseline | ☐ |
| 5 | `strategy_killer` does not close existing positions on disable | Test: kill strategy with 2 open positions → positions remain open | ☐ |
| 6 | Strategies with < MIN_TRADES_FOR_EVAL trades are not evaluated | Test: strategy with 10 trades → not evaluated | ☐ |
| 7 | All learning tasks run via scheduler, not inside main tick loop | `grep 'experience_processor\|reflection\|adaptation' main.py` → empty | ☐ |
| 8 | Adaptation history is saved to DB for audit and rollback | Check performance.db has `adaptation_history` table | ☐ |

---

### 12.2 LLM Layer Checklist

| No | Item | Verification | Done |
|---|---|---|---|
| 9 | `score_signal()` does not block main loop — 10-second timeout | Test: simulate slow LLM → fallback after 10 seconds, signal continues | ☐ |
| 10 | LLM fallback (multiplier=1.0) when LLM fails — trading does not stop | Test: disable network → fallback, signal still processed | ☐ |
| 11 | `confidence_multiplier` is clamped to [0.0, 1.5] after JSON parse | Test: inject JSON with multiplier=5.0 → clamped to 1.5 | ☐ |
| 12 | `llm_budget.can_call()` is called BEFORE `llm_client.call()` | Code review llm_filter.py — budget check is on the first line | ☐ |
| 13 | `llm_budget.reset_daily()` is registered in scheduler at UTC 00:00 | Code review scheduler.py — llm_budget_reset task is registered | ☐ |
| 14 | LLM cannot trigger orders directly | `grep -rn 'place_order\|executor' llm_layer/` → empty | ☐ |
| 15 | JSON response is parsed with try-except — fallback if invalid | Test: inject invalid JSON → fallback=True, no crash | ☐ |

---

> **This document is the implementation contract for the Learning Layer & LLM Layer.**
> The LLM acts only as an advisor. The system must run fully without the LLM. Fallback is always multiplier=1.0.
>
> *References: agent_core_docs.md · strategy_portfolio_docs.md · exit_monitoring_sync_docs.md*
>
> **This is the final document in the crypto_ai_agent documentation series.**
