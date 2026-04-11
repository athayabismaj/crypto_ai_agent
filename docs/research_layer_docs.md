# crypto_ai_agent — Research Layer Documentation

*Complete implementation guide — interface contracts, config, data flow, design decisions*

**Version 1.0** | Reference: crypto_ai_agent final structure

---

## 1. Overview — Research Layer

The Research Layer is an offline experimentation zone that is completely separate from the live system. No code here can execute real orders. The outputs of this layer are:

- Trained ML models (`.pkl`) ready to deploy to `runtime/agent/models/`
- Complete backtest reports: ROI, drawdown, Sharpe, win rate
- Optimal config from the walk-forward & optimization process
- Clean datasets & validated features

> **HARD RULE:** No imports from `runtime/` inside `research/`. The dependency must be one-directional: research produces artifacts → runtime consumes artifacts.

---

### 1.1 Folder Structure

```
research/
├── data/
│   ├── raw/                  ← output of fetch_data.py (immutable, do not edit)
│   ├── processed/            ← output of clean_data.py
│   ├── features/             ← output of feature_engineering.py
│   └── orderbook_samples/    ← depth snapshots for realistic fill simulation
├── pipeline/
│   ├── fetch_data.py
│   ├── clean_data.py
│   ├── resample_data.py
│   └── feature_engineering.py
├── validation/
│   ├── data_quality.py
│   ├── leakage_check.py
│   └── consistency_check.py
├── modeling/
│   ├── train.py
│   ├── walk_forward.py
│   ├── evaluate.py
│   ├── model_registry.py
│   └── feature_importance.py
├── backtest/
│   ├── engine.py
│   ├── simulator.py
│   ├── portfolio_simulator.py
│   ├── orderbook_simulator.py
│   ├── liquidity_model.py
│   ├── execution_emulator.py
│   ├── metrics.py
│   └── report.py
├── optimization/
│   ├── tuner.py
│   ├── grid_search.py
│   └── bayesian_opt.py
└── experiments/              ← notebooks & experiment notes
```

---

### 1.2 Data Flow Between Sub-layers

| Stage | Input | Process | Output | Required Validation |
|---|---|---|---|---|
| 1. Fetch | Exchange API / CSV | fetch_data.py | raw/*.parquet | data_quality.py |
| 2. Clean | raw/*.parquet | clean_data.py | processed/*.parquet | consistency_check.py |
| 3. Resample | processed/*.parquet | resample_data.py | processed/*_Xm.parquet | consistency_check.py |
| 4. Feature | processed/*.parquet | feature_engineering.py | features/*.parquet | leakage_check.py |
| 5. Train | features/*.parquet | train.py + walk_forward.py | models/*.pkl + metadata | evaluate.py |
| 6. Backtest | features/ + orderbook_samples/ | engine.py + simulator.py | results/*.json + report | metrics.py (threshold check) |
| 7. Optimize | results/*.json | tuner.py / bayesian_opt.py | best_params.json | re-run walk_forward |
| 8. Deploy | models/*.pkl + best_params.json | deploy.py (automation/) | runtime/agent/models/*.pkl | metadata.json update |

---

## 2. Pipeline — Data Acquisition & Preprocessing

### 2.1 fetch_data.py

Fetches OHLCV data, funding rates, and open interest from the exchange. Data is stored in Parquet format partitioned per symbol per timeframe.

**Public Interface**

| Function | Parameters | Return | Exception |
|---|---|---|---|
| `fetch_ohlcv()` | symbol: str, tf: str, start: datetime, end: datetime, exchange: str='binance' | pd.DataFrame [open,high,low,close,volume,timestamp] | FetchError, RateLimitError |
| `fetch_funding_rate()` | symbol: str, start: datetime, end: datetime | pd.DataFrame [timestamp, rate, next_time] | FetchError |
| `fetch_orderbook_snap()` | symbol: str, depth: int=20, n_samples: int=500 | list[dict] — saves to orderbook_samples/ | FetchError |
| `save_raw()` | df: pd.DataFrame, symbol: str, tf: str | Path — location of the .parquet file | IOError |

**Config Keys**

| Key | Default | Valid Range | Description |
|---|---|---|---|
| `EXCHANGE` | binance | binance \| bybit \| okx | Source exchange for data |
| `SYMBOLS` | ['BTCUSDT'] | list of str | Symbols to fetch |
| `TIMEFRAMES` | ['1h','4h'] | 1m\|5m\|15m\|1h\|4h\|1d | Timeframes to retrieve |
| `START_DATE` | 2020-01-01 | ISO datetime | Start of the fetch period |
| `RATE_LIMIT_SLEEP` | 0.2 | 0.1–2.0 (seconds) | Delay between requests (avoid bans) |
| `MAX_RETRY` | 3 | 1–10 | Retries on request failure |
| `RAW_DIR` | data/raw/ | str path | Directory for raw data storage |

**Design Decisions**

- Parquet format instead of CSV — 5–10x smaller, faster loading, schema enforced.
- Raw files are **NEVER** edited after saving. If corrections are needed, save a new version.
- Naming convention: `BTCUSDT_1h_20200101_20241231.parquet` — deterministic, easy to glob.
- Rate limit sleep is configured, not hardcoded — each exchange has different limits.

---

### 2.2 clean_data.py

Cleans raw data: handles missing candles, price outliers, duplicate timestamps, and normalizes timezone to UTC.

**Public Interface**

| Function | Parameters | Return | Notes |
|---|---|---|---|
| `clean_ohlcv()` | df: pd.DataFrame, symbol: str, tf: str | Clean pd.DataFrame | Forward-fill up to 3 consecutive empty candles |
| `remove_outliers()` | df, z_thresh: float=4.0 | pd.DataFrame | Z-score on log-return, not on absolute price |
| `fix_timestamps()` | df, tz: str='UTC' | pd.DataFrame | Convert to UTC, remove duplicates |
| `validate_ohlc_logic()` | df: pd.DataFrame | bool, list[str] errors | High >= max(Open,Close), Low <= min(Open,Close) |

**Config Keys**

| Key | Default | Description |
|---|---|---|
| `MAX_FFILL_CANDLES` | 3 | Max consecutive empty candles to forward-fill. Beyond this → row is deleted. |
| `OUTLIER_Z_THRESH` | 4.0 | Z-score threshold on log-return. Z > threshold → outlier. Default 4 ≈ 0.003% of data. |
| `MIN_CANDLE_VOLUME` | 0.0 | Volume 0 is allowed (can occur on illiquid pairs). Set > 0 to filter out. |
| `PROCESSED_DIR` | data/processed/ | Output directory. |

**Cleaning Rules That Must Be Consistent**

> **IMPORTANT:** Cleaning rules MUST be identical between training and runtime. If runtime applies different normalization, the model will receive a distribution it does not recognize.

- Log-return is computed here as an outlier cross-check, but is **NOT** stored — `feature_engineering.py` recomputes it.
- Duplicate timestamps: keep the first (not the last) — consistent with exchange behavior.
- Gaps larger than `MAX_FFILL_CANDLES` are filled with NaN and then the row is deleted, not interpolated.

---

### 2.3 resample_data.py

Converts data timeframes: 1m → 5m, 1h → 4h, etc. Uses correct OHLC resampling (not a simple downsample).

**Public Interface**

| Function | Parameters | Return |
|---|---|---|
| `resample_ohlcv()` | df: pd.DataFrame, source_tf: str, target_tf: str | pd.DataFrame at target timeframe |
| `align_multi_tf()` | dfs: dict[str, pd.DataFrame] | dict[str, pd.DataFrame] — all aligned to the same index |

**OHLC Resampling Rules**

```python
Open   = first(open)    # first candle in the window
High   = max(high)      # highest in the window
Low    = min(low)       # lowest in the window
Close  = last(close)    # last candle in the window
Volume = sum(volume)    # total volume

# Timestamp label: START of window, not end
# Example: 4H candle at 08:00 = data from 08:00–11:59
```

---

### 2.4 feature_engineering.py

Computes all features required by the ML model. Output is a DataFrame with feature columns + target label (return N candles forward).

**Feature Categories**

| Category | Features | Formula / Library | Notes |
|---|---|---|---|
| Price action | log_return, log_return_N | np.log(close/close.shift(N)) | N = 1, 5, 20 |
| Momentum | RSI, MOM, ROC | ta-lib / pandas-ta | Period: 7, 14, 21 |
| Trend | EMA_fast, EMA_slow, MACD | ta-lib | EMA 9/21/50/200 |
| Volatility | ATR, BB_width, realized_vol | ta-lib / rolling std log_return | ATR period 14 |
| Volume | volume_ratio, OBV, VWAP | volume/volume.rolling(20).mean() | VWAP per day |
| Regime | adx, trend_strength | ADX ta-lib period 14 | ADX > 25 = trending |
| Funding | funding_rate, funding_cum8h | merge from fetch_funding_rate() | Futures only |
| Target | target_return_Nh | log_return.shift(-N) | N = 1, 4, 8 candles |

**Public Interface**

| Function | Parameters | Return | Exception |
|---|---|---|---|
| `build_features()` | df: pd.DataFrame, config: FeatureConfig | pd.DataFrame with all features | InsufficientDataError |
| `add_target()` | df, horizon: int, col: str='close' | pd.DataFrame + target column | — |
| `validate_no_leakage()` | df: pd.DataFrame | bool | DataLeakageError |
| `get_feature_names()` | config: FeatureConfig | list[str] | — |

**FeatureConfig — Dataclass**

```python
@dataclass
class FeatureConfig:
    rsi_periods:      list[int] = (7, 14, 21)
    ema_periods:      list[int] = (9, 21, 50, 200)
    atr_period:       int       = 14
    bb_period:        int       = 20
    volume_ma_period: int       = 20
    target_horizons:  list[int] = (1, 4, 8)    # candles forward
    include_funding:  bool      = False          # True for futures
    drop_na:          bool      = True
```

> **CRITICAL — Anti Leakage:** The target label (`target_return_Nh`) MUST use `shift(-N)` and must be validated AFTER all features are computed. Never use future data inside features.

---

## 3. Validation — Data Quality Assurance

### 3.1 data_quality.py

The first validation layer: check completeness, value ranges, and basic statistics before data is processed further.

**Public Interface**

| Function | Parameters | Return |
|---|---|---|
| `run_quality_report()` | df: pd.DataFrame, symbol: str, tf: str | QualityReport dataclass (see below) |
| `check_missing()` | df: pd.DataFrame | dict[col, missing_pct] — missing pct per column |
| `check_price_range()` | df: pd.DataFrame, symbol: str | bool, list[str] anomalies |
| `check_candle_gaps()` | df: pd.DataFrame, tf: str | list[datetime] — gap timestamps found |

**QualityReport Dataclass**

```python
@dataclass
class QualityReport:
    symbol:            str
    timeframe:         str
    total_rows:        int
    missing_pct:       dict[str, float]   # per column
    gap_count:         int                # number of candle gaps
    outlier_count:     int
    ohlc_logic_errors: int                # High < Low, etc.
    passed:            bool               # True if all thresholds are met
    warnings:          list[str]
```

**Thresholds Required for passed=True**

| Check | Threshold | Action if Failed |
|---|---|---|
| Missing candle | < 1% of total candles | Log warning, continue. If > 5%, raise DataQualityError |
| OHLC logic error | = 0 | Raise DataQualityError — data is corrupt |
| Gap > MAX_FFILL | < 10 gaps per 1000 candles | Log warning |
| Volume = 0 | < 0.5% of candles | Log warning — may be an illiquid pair |

---

### 3.2 leakage_check.py

A critical validation to ensure no future information leaks into the features. Data leakage is the primary cause of overly optimistic backtests.

**Public Interface**

| Function | Parameters | Return |
|---|---|---|
| `check_temporal_leakage()` | df: pd.DataFrame, feature_cols: list, target_col: str | bool — True = safe (no leakage) |
| `check_lookahead_bias()` | df, window: int | dict[col, correlation_with_future] |
| `check_index_alignment()` | features: pd.DataFrame, target: pd.Series | bool |

**How Temporal Leakage Check Works**

1. Compute the correlation of every feature column with the future target (shift -1, -2, -5).
2. Correlation > 0.8 with the future → strong indication of leakage.
3. Rolling window forward correlation: correlation of feature[t] with close[t+N] must not be consistently high.
4. For features that use `.shift()`: ensure the shift is positive (not negative) for historical data.

> **RULE:** `leakage_check.py` MUST be run after `feature_engineering.py` and BEFORE `train.py`. The pipeline must not continue if `check_temporal_leakage()` returns False.

---

### 3.3 consistency_check.py

Validates data consistency across timeframes and across symbols. Ensures 1H data can be derived from 15m data, and that BTCUSDT spot is consistent with BTCUSDT futures.

**Public Interface**

| Function | Parameters | Return |
|---|---|---|
| `check_tf_consistency()` | df_low: pd.DataFrame, df_high: pd.DataFrame, src_tf: str, tgt_tf: str | bool, list[str] discrepancies |
| `check_spot_futures()` | df_spot: pd.DataFrame, df_futures: pd.DataFrame | bool, max_basis_pct: float |
| `check_feature_drift()` | df_old: pd.DataFrame, df_new: pd.DataFrame | dict[col, psi_score] — Population Stability Index |

**PSI Thresholds for Feature Drift**

| PSI Score | Interpretation | Action |
|---|---|---|
| < 0.1 | No drift | Safe, continue training |
| 0.1–0.2 | Minor drift | Log warning, monitor |
| > 0.2 | Significant drift | Stop training, investigate data source |

---

## 4. Modeling — Training & Evaluation

### 4.1 train.py

Trains the ML model from validated features. Supports LightGBM (default), XGBoost, and other sklearn estimators through a uniform interface.

**Public Interface**

| Function | Parameters | Return | Side Effect |
|---|---|---|---|
| `train_model()` | df: pd.DataFrame, config: TrainConfig | TrainedModel dataclass | Saves .pkl to models/ |
| `load_model()` | path: str \| Path | TrainedModel | — |
| `predict()` | model: TrainedModel, X: pd.DataFrame | np.ndarray — probabilities / return | — |
| `get_feature_list()` | model: TrainedModel | list[str] | — |

**TrainConfig — Dataclass**

```python
@dataclass
class TrainConfig:
    model_type:     str   = 'lightgbm'    # lightgbm | xgboost | random_forest
    target_col:     str   = 'target_return_4h'
    task:           str   = 'regression'  # regression | classification
    test_size:      float = 0.2
    n_splits:       int   = 5             # for walk_forward
    early_stopping: int   = 50
    verbose:        int   = 100

    # LightGBM params (override if needed)
    lgbm_params: dict = field(default_factory=lambda: {
        'n_estimators':    1000,
        'learning_rate':   0.05,
        'num_leaves':      31,
        'subsample':       0.8,
        'colsample_bytree':0.8,
        'reg_alpha':       0.1,
        'reg_lambda':      0.1,
    })
```

**TrainedModel — Dataclass (output artifact)**

```python
@dataclass
class TrainedModel:
    model:             Any           # estimator object
    feature_names:     list[str]     # REQUIRED — runtime needs this
    target_col:        str
    model_type:        str
    train_date_range:  tuple[str, str]
    metrics:           dict          # val_score, ic, etc.
    config:            TrainConfig
    version:           str           # semver: '1.0.0'
```

> **CRITICAL:** `feature_names` inside `TrainedModel` is the contract between Research and Runtime. Runtime MUST prepare features with exactly the same order and names. If they differ, the model will produce garbage predictions without any explicit error.

---

### 4.2 walk_forward.py

Validates the model using walk-forward (expanding window or rolling window). This is the most realistic validation method for time series data because it never uses future data for training.

**Walk-Forward Scheme**

```
Expanding Window (default):
  Fold 1: Train [Jan–Jun]  →  Val [Jul]
  Fold 2: Train [Jan–Jul]  →  Val [Aug]
  Fold 3: Train [Jan–Aug]  →  Val [Sep]
  ...

Rolling Window (set rolling=True):
  Fold 1: Train [Jan–Jun]  →  Val [Jul]
  Fold 2: Train [Feb–Jul]  →  Val [Aug]  (Jan is dropped)
  ...

Required gap between train end and val start:
  → At least equal to the target horizon
  → If target = 4H, gap = 4 candles = 16 hours
```

**Public Interface**

| Function | Parameters | Return |
|---|---|---|
| `walk_forward_cv()` | df, config: WalkForwardConfig | list[FoldResult] — metrics per fold |
| `aggregate_results()` | results: list[FoldResult] | WalkForwardSummary — mean, std, stability |
| `plot_equity_curve()` | results: list[FoldResult], save_path: str | None — saves image |

**WalkForwardConfig**

```python
@dataclass
class WalkForwardConfig:
    n_splits:       int  = 5
    gap_periods:    int  = 4       # candle gap between train & val
    rolling:        bool = False   # False = expanding window
    min_train_size: int  = 1000    # minimum candles for training
```

---

### 4.3 evaluate.py

Computes all model evaluation metrics. Has minimum thresholds that must be met before the model is allowed to proceed to backtest.

**Metrics Computed**

| Metric | Formula | Minimum Threshold | Notes |
|---|---|---|---|
| IC (Information Coef.) | spearman(pred, actual_return) | > 0.05 | IC < 0.03 = model is not predictive |
| ICIR | IC.mean() / IC.std() | > 0.5 | IC stability across periods |
| Directional Accuracy | sign(pred) == sign(actual) | > 52% | Above 50% means there is an edge |
| Sharpe (signal) | mean(ret*signal)/std(ret*signal) | ≥ 0.8 | Before transaction costs |
| Max DD (signal) | max drawdown of equity curve | < 30% | Signal drawdown, not trade drawdown |

> **PIPELINE RULE:** If any threshold is not met, `evaluate.py` MUST raise `ModelNotReadyError` and the pipeline stops. The model must not be deployed to backtest, let alone to runtime.

---

### 4.4 model_registry.py

Versioning and tracking of all models ever trained. Every model has complete metadata so it can be rolled back at any time.

**metadata.json — Format**

```json
{
  "model_id":       "spot_lgbm_v2_3_0",
  "version":        "2.3.0",
  "created_at":     "2024-11-15T08:30:00Z",
  "model_type":     "lightgbm",
  "target_col":     "target_return_4h",
  "symbol":         "BTCUSDT",
  "timeframe":      "1h",
  "train_period":   ["2020-01-01", "2024-10-31"],
  "feature_names":  ["rsi_14", "ema_ratio_9_21", "atr_14", ...],
  "feature_count":  47,
  "metrics": {
    "ic_mean": 0.078, "ic_std": 0.031, "icir": 1.85,
    "dir_accuracy": 0.543, "sharpe_signal": 1.12
  },
  "status":         "production",
  "replaces":       "spot_lgbm_v2_2_1"
}
```

**Model Status Lifecycle**

| Status | Meaning | Transitions To |
|---|---|---|
| **candidate** | Just finished training, not yet validated | validated (after evaluate.py passes) |
| **validated** | Passed all evaluate.py thresholds | production (after backtest passes) |
| **production** | Actively used in runtime | deprecated (when replaced by a new version) |
| **deprecated** | Replaced by a new version, kept for rollback | archived (after 90 days) |
| **failed** | Did not pass thresholds, must not be deployed | — (reference only) |

---

## 5. Backtest — Trading Simulation

The backtest layer is the most critical step for assessing whether the model and strategy are ready to deploy. The accuracy of the simulation determines whether live performance will be consistent with backtested results.

> **PRINCIPLE:** A good backtest is better off showing conservative rather than optimistic results. Every assumption that makes results look better should be questioned.

### 5.1 engine.py — Backtest Engine

An event-driven backtest engine. Processes candles one by one in chronological order to avoid lookahead bias.

**Public Interface**

| Function / Class | Parameters | Return |
|---|---|---|
| **BacktestEngine** | config: BacktestConfig | — |
| `.run()` | df: pd.DataFrame, strategy: BaseStrategy | BacktestResult |
| `.run_portfolio()` | dfs: dict[str, pd.DataFrame], strategy: ... | PortfolioResult |

**BacktestConfig**

```python
@dataclass
class BacktestConfig:
    initial_capital:  float = 10_000.0    # USDT
    commission_pct:   float = 0.001       # 0.1% taker fee Binance
    slippage_model:   str   = 'liquidity' # fixed | percentage | liquidity
    slippage_pct:     float = 0.0005      # 0.05% if model='percentage'
    use_orderbook:    bool  = True        # use orderbook_simulator
    execution_delay:  int   = 1           # candle delay before fill
    max_position_pct: float = 0.1         # max 10% per position
    allow_short:      bool  = False       # spot: False, futures: True
    funding_rate:     bool  = False       # True for futures
```

**Candle Execution Sequence (MUST BE FOLLOWED)**

1. Receive new candle (OHLCV).
2. Update existing positions: check SL, TP, trailing stop using candle high/low.
3. Call strategy: `strategy.on_candle(candle, portfolio_state)` → Signal.
4. Process signal through risk check (position sizing, exposure).
5. Simulate order execution on the **NEXT** candle (`execution_delay=1`).
6. Record trade & update equity.

> **LOOKAHEAD PROTECTION:** A signal on candle T must not be filled at candle T's price. The minimum is to fill at the open of candle T+1. This mimics real live trading conditions.

---

### 5.2 orderbook_simulator.py & liquidity_model.py

These two components work together to simulate order fills realistically, based on orderbook depth data obtained from `fetch_orderbook_snap()`.

**Interface — orderbook_simulator.py**

| Function | Parameters | Return |
|---|---|---|
| `simulate_fill()` | order: Order, orderbook: Orderbook | FillResult (avg_price, filled_qty, slippage) |
| `load_orderbook()` | symbol: str, timestamp: datetime | Orderbook — nearest snapshot from data |
| `estimate_market_impact()` | qty: float, orderbook: Orderbook, side: str | float — estimated impact in percent |

**FillResult Dataclass**

```python
@dataclass
class FillResult:
    avg_price:     float    # average fill price
    filled_qty:    float    # quantity successfully filled
    slippage_pct:  float    # (avg_price - mid_price) / mid_price
    market_impact: float    # estimated price impact
    partial:       bool     # True if not fully filled
    unfilled_qty:  float    # quantity not filled
```

**Slippage Models — liquidity_model.py**

| Model | Formula | When to Use |
|---|---|---|
| `fixed` | slippage = slippage_pct × price | Quick testing, not realistic |
| `percentage` | slippage = order_value / ADV × impact_factor | When no orderbook data is available |
| `liquidity` | Walk through orderbook bids/asks until qty is met | Default — most realistic |

---

### 5.3 execution_emulator.py

Emulates execution uncertainty: network delays, partial fills, order rejections, and retry logic.

**Emulated Scenarios**

| Scenario | Default Probability | Impact | Config Key |
|---|---|---|---|
| Execution delay 1 candle | 100% | Fill at open of next candle | `execution_delay` |
| Partial fill | 5% | Only 70–99% of qty is filled | `partial_fill_prob` |
| Order rejection | 0.5% | Order not entered, requires retry | `rejection_prob` |
| Price gap / slippage | 100% | Price differs from expected | `slippage_model` |
| High volatility spread | ATR-based | Spread widens when ATR is high | `vol_spread_multiplier` |

---

### 5.4 metrics.py

Computes all performance metrics from backtest results. All metrics must be computed consistently using a single source of truth.

**All Metrics Computed**

| Metric | Formula | Minimum Threshold for Deploy |
|---|---|---|
| Total ROI | ((final_equity - initial) / initial) × 100 | Positive after costs |
| CAGR | (final/initial)^(365/days) - 1 | > 20% per year |
| Max Drawdown | max(peak - trough) / peak | < 20% |
| Sharpe Ratio | annualized(mean_return) / annualized(std_return) | ≥ 1.0 |
| Sortino Ratio | annualized(mean_return) / annualized(downside_std) | ≥ 1.5 |
| Calmar Ratio | CAGR / max_drawdown | ≥ 1.0 |
| Win Rate | winning_trades / total_trades | > 45% |
| Profit Factor | gross_profit / gross_loss | > 1.3 |
| Avg R:R | avg_win / avg_loss | > 1.2 |
| Total Trades | count | > 30 (statistically valid) |

> **IMPORTANT:** Sharpe Ratio must be computed using daily returns (not trade returns) and compared against a risk-free rate of 0 (since we hold USDT, not bonds).

---

## 6. Optimization — Parameter Tuning

The optimization layer searches for the best parameter combination for a strategy. Out-of-sample validation is mandatory to avoid overfitting.

> **HARD WARNING:** Never optimize on the entire dataset and then test on the same data. Always set aside the out-of-sample period BEFORE optimization begins. This period must not be viewed until the final evaluation.

### 6.1 Correct Optimization Protocol

```
Split data BEFORE optimization:
  Total data:      Jan 2020 – Dec 2024  (5 years)
  In-sample:       Jan 2020 – Dec 2023  (4 years) ← used for optimization
  Out-of-sample:   Jan 2024 – Dec 2024  (1 year)  ← MUST NOT BE VIEWED

Workflow:
  1. Optimize parameters using in-sample walk-forward
  2. Select best_params based on in-sample Sharpe
  3. Run backtest ONCE on out-of-sample
  4. Compare IS vs OOS — degradation > 40% = overfitting
  5. If passes, proceed to deploy. If not, return to step 1
```

---

### 6.2 tuner.py — Runner

**Public Interface**

| Function | Parameters | Return |
|---|---|---|
| `run_optimization()` | param_space: dict, config: OptConfig, method: str | OptResult — best_params, all_trials |
| `load_best_params()` | symbol: str, strategy: str | dict — best params from file |
| `save_best_params()` | params: dict, symbol: str, strategy: str | Path |

---

### 6.3 bayesian_opt.py vs grid_search.py

| Method | When to Use | Advantage | Disadvantage |
|---|---|---|---|
| **grid_search** | Few parameters (< 3), small range | Exhaustive, easy to debug | Exponential complexity |
| **bayesian_opt** | Many parameters (> 3), large range | Efficient, converges faster | Requires more setup |

**OptConfig**

```python
@dataclass
class OptConfig:
    n_trials:        int = 100        # for bayesian
    n_jobs:          int = -1         # parallel (-1 = all cores)
    objective:       str = 'sharpe'   # sharpe | sortino | calmar | profit_factor
    min_trades:      int = 30         # skip trials with trade count < N
    timeout_seconds: int = 3600       # max optimization time
    sampler:         str = 'tpe'      # tpe | random | cma-es
```

---

## 7. Dependencies & Integration with Runtime

### 7.1 File Dependencies

| File | Depends On | Output Consumed By |
|---|---|---|
| `fetch_data.py` | Exchange API (ccxt) | clean_data.py |
| `clean_data.py` | fetch_data.py output | resample_data.py, feature_engineering.py |
| `resample_data.py` | clean_data.py output | feature_engineering.py |
| `feature_engineering.py` | clean/resample output | train.py, engine.py, leakage_check.py |
| `leakage_check.py` | feature_engineering.py output | train.py (gate) |
| `train.py` | features/*.parquet + leakage_check passed | evaluate.py, model_registry.py |
| `walk_forward.py` | features/*.parquet + TrainConfig | evaluate.py |
| `evaluate.py` | TrainedModel + WalkForwardSummary | engine.py (gate), model_registry.py |
| `engine.py` | features/ + orderbook_samples/ + TrainedModel | metrics.py, report.py |
| `metrics.py` | BacktestResult | report.py, tuner.py |
| `model_registry.py` | TrainedModel + metadata.json | automation/deploy.py |

---

### 7.2 Interface Contract with Runtime

These are the interfaces that must maintain compatibility. Changes here require coordination with runtime.

| Artifact | Format | Required Fields | Consumed By |
|---|---|---|---|
| `*.pkl` | joblib/pickle | model object + predict(X) method + feature_names | runtime/agent/models/ |
| `metadata.json` | JSON | feature_names (ordered list), version, threshold | runtime core/config.py |
| `best_params.json` | JSON | strategy params matching strategy_layer/ | runtime strategy_layer/ |
| `FeatureConfig` | Python dataclass | Exactly as used in feature_engineering.py | runtime data_layer/ |

> **VERSIONING RULE:** Every time `feature_names` changes (add/remove/reorder), the model version MUST do a major version bump (1.x.x → 2.0.0). Runtime must not use the new model without updating its feature pipeline.

---

### 7.3 Python Dependencies

| Library | Min Version | Used In | Notes |
|---|---|---|---|
| pandas | 2.0+ | All files | Use pyarrow backend for Parquet |
| numpy | 1.24+ | All files | — |
| lightgbm | 4.0+ | train.py | Primary model |
| scikit-learn | 1.3+ | train.py, evaluate.py, walk_forward | Metrics, pipeline |
| ta-lib | 0.4+ | feature_engineering.py | Requires C library install first |
| pandas-ta | 0.3+ | feature_engineering.py | Alternative to ta-lib, pure Python |
| ccxt | 4.0+ | fetch_data.py | Unified exchange API |
| optuna | 3.0+ | bayesian_opt.py | Bayesian optimization |
| joblib | 1.3+ | train.py, model_registry.py | Model serialization |
| pyarrow | 12.0+ | All Parquet read/write | Parquet backend for pandas |

---

## 8. Checklist Before Deploying Model to Runtime

All of the following checklist items MUST be satisfied before the model is moved to `runtime/agent/models/`. Check every point.

| No | Checklist Item | Verification Method | Responsible |
|---|---|---|---|
| 1 | `data_quality.py` passed=True for all training data | `run_quality_report()` — check the `passed` field | Data Engineer |
| 2 | `leakage_check.py` returned True | `check_temporal_leakage()` does not raise | Data Engineer |
| 3 | Walk-forward IC > 0.05 and ICIR > 0.5 | `WalkForwardSummary.ic_mean` & `icir` | ML Engineer |
| 4 | `evaluate.py` passed all thresholds | No `ModelNotReadyError` raised | ML Engineer |
| 5 | Backtest OOS Sharpe ≥ 1.0 and Max DD < 20% | `metrics.py` report for OOS period | Quant |
| 6 | IS vs OOS Sharpe degradation < 40% | `sharpe_oos >= sharpe_is * 0.6` | Quant |
| 7 | `feature_names` in metadata.json matches runtime pipeline | `python -c 'import json; check_features()'` | ML Engineer |
| 8 | `metadata.json` contains all required fields | JSON schema validation | ML Engineer |
| 9 | `model_registry.py` status = 'validated' | Load from registry, check status | ML Engineer |
| 10 | Backtest has at least 30 trades in OOS period | `BacktestResult.total_trades >= 30` | Quant |
| 11 | No runtime dependencies inside research/ | `grep -r 'from runtime' research/` | Dev |
| 12 | Paper trading run for at least 1 week before live | Paper mode log shows normal signals | Ops |

---

## 9. Experiments — Recording Convention

Every experiment must be documented to avoid repeating the same work. The minimum required format:

```
experiments/
├── 2024_11_exp001_rsi_ema_baseline/
│   ├── README.md       ← hypothesis, results, conclusion
│   ├── notebook.ipynb  ← exploration code
│   ├── results.json    ← final metrics
│   └── config.yaml     ← config used
├── 2024_11_exp002_add_funding_feature/
│   └── ...
```

**Experiment README.md Template**

```markdown
# Exp-001: RSI + EMA Baseline

## Hypothesis
A model with RSI(14) and EMA crossover features is sufficient
to achieve IC > 0.05 on BTCUSDT 1H.

## Config
- Symbol: BTCUSDT, TF: 1H, Period: 2021-01-01 – 2023-12-31
- Model: LightGBM default params
- Target: target_return_4h

## Results
- IC mean: 0.062  ICIR: 1.21  Dir Acc: 53.1%
- Backtest OOS Sharpe: 0.94  Max DD: 18.2%

## Conclusion
Hypothesis PARTIALLY met. IC passes but Sharpe does not.
Next: try adding volatility features (exp-002).

## Decision
NOT deploying. Moving to exp-002.
```

---

> **This document is the implementation contract for the Research Layer.**
> Any changes to public interfaces or config key defaults
> **MUST** be updated here before merging to the main branch.
