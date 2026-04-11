# crypto_ai_agent — Final Project Structure

*Production-Grade | Binance Spot + Futures | Paper → Shadow → Live Mode*

| 🔥 CRITICAL | ✅ NEW | ⚠️ IMPORTANT | — STANDARD |
|---|---|---|---|

---

## 01 Research Layer

*All offline experiments. Does not touch real money. Output: model .pkl + backtest report.*

---

### data/

| Path | File | Function | Status |
|---|---|---|---|
| `data/raw/` | **\*.parquet** | Raw OHLCV data from exchange | — |
| `data/processed/` | **\*.parquet** | Cleaned data after processing | — |
| `data/features/` | **\*.parquet** | Feature-engineered data ready for training | — |
| `data/orderbook_samples/` | **\*.json** | Depth data for realistic fill simulation | 🔥 CRITICAL |

---

### pipeline/

| Path | File | Function | Status |
|---|---|---|---|
| `pipeline/` | **fetch_data.py** | Fetch OHLCV + funding rate data from exchange | — |
| `pipeline/` | **clean_data.py** | Remove outliers, fill gaps, normalize timestamps | — |
| `pipeline/` | **resample_data.py** | Convert timeframes (1m → 5m → 1h) | — |
| `pipeline/` | **feature_engineering.py** | Compute technical indicators + ML features | — |

---

### validation/

| Path | File | Function | Status |
|---|---|---|---|
| `validation/` | **data_quality.py** | Check missing values, outliers, duplicates | — |
| `validation/` | **leakage_check.py** | Detect data leaking from the future (look-ahead) | 🔥 CRITICAL |
| `validation/` | **consistency_check.py** | Validate consistency across timeframes | — |

---

### modeling/

| Path | File | Function | Status |
|---|---|---|---|
| `modeling/` | **train.py** | Train ML model (LightGBM / XGBoost / NN) | — |
| `modeling/` | **walk_forward.py** | Walk-forward validation — prevent overfitting | 🔥 CRITICAL |
| `modeling/` | **evaluate.py** | Evaluate: accuracy, precision, recall, AUC | — |
| `modeling/` | **model_registry.py** | Model versioning + metadata tracking | — |
| `modeling/` | **feature_importance.py** | Analyze feature contribution per model | — |

---

### backtest/

| Path | File | Function | Status |
|---|---|---|---|
| `backtest/` | **engine.py** | Main backtest engine — event-driven | — |
| `backtest/` | **simulator.py** | Single-asset trade simulation | — |
| `backtest/` | **portfolio_simulator.py** | Multi-asset simulation with rebalancing | 🔥 CRITICAL |
| `backtest/` | **orderbook_simulator.py** | Realistic fill simulation via depth snapshot | 🔥 CRITICAL |
| `backtest/` | **liquidity_model.py** | Slippage + market impact model | 🔥 CRITICAL |
| `backtest/` | **execution_emulator.py** | Emulate delay, retry, partial fill | 🔥 CRITICAL |
| `backtest/` | **metrics.py** | ROI, max drawdown, Sharpe, Sortino, win rate | — |
| `backtest/` | **report.py** | Generate HTML + PDF backtest report | — |

---

### optimization/ & experiments/

| Path | File | Function | Status |
|---|---|---|---|
| `optimization/` | **tuner.py** | Runner for all optimization methods | — |
| `optimization/` | **grid_search.py** | Grid search for strategy parameters | — |
| `optimization/` | **bayesian_opt.py** | Bayesian optimization (more efficient) | — |
| `experiments/` | **\*.md / \*.ipynb** | Notes & experiment notebooks | — |

---

## 02 Runtime Layer

*Live system. Every file here touches real money when paper_mode=False.*

---

### Root runtime/

| Path | File | Function | Status |
|---|---|---|---|
| `runtime/` | **.env** | API keys, secrets — DO NOT commit to git | 🔥 CRITICAL |
| `runtime/` | **docker-compose.yml** | Container orchestration: agent, monitoring, DB | — |
| `runtime/` | **requirements.txt** | Python runtime dependencies (not research) | — |

---

### migration/ ✅ NEW

| Path | File | Function | Status |
|---|---|---|---|
| `migration/` | **migrate.py** | Database migration runner with rollback support | ✅ NEW |
| `migration/versions/` | **001_init.py** | Initial schema version (state.db, experience.db, etc.) | ✅ NEW |
| `migration/versions/` | **002_\*.py** | Incremental migrations during system upgrades | ✅ NEW |

---

### security/

| Path | File | Function | Status |
|---|---|---|---|
| `security/` | **key_manager.py** | Load & protect API keys from .env + vault | 🔥 CRITICAL |
| `security/` | **encryptor.py** | Encrypt sensitive data in database | 🔥 CRITICAL |
| `security/` | **access_control.py** | Control access to gateway endpoints | — |

---

### gateway/

| Path | File | Function | Status |
|---|---|---|---|
| `gateway/` | **api.py** | FastAPI server — external entry point | — |
| `gateway/` | **routes.py** | Endpoint routing: status, signal, control | — |
| `gateway/` | **auth.py** | JWT authentication + API key validation | — |
| `gateway/` | **middleware.py** | Rate limiting, logging, error handling | — |

---

## 03 Agent — Core

### core/ ← Heart of the System

| Path | File | Function | Status |
|---|---|---|---|
| `core/` | **main.py** | Agent main loop — master ticker for all layers | 🔥 CRITICAL |
| `core/` | **config.py** | Load configuration from .env + YAML file | — |
| `core/` | **config_schema.py** | Validate config via Pydantic at startup | ✅ NEW |
| `core/` | **modes.py** | Mode enum: LIVE / PAPER / SHADOW | ⚠️ IMPORTANT |
| `core/` | **scheduler.py** | Task scheduling: per-tick, daily, weekly | — |
| `core/` | **event_bus.py** | Async pub/sub between layers (asyncio) | — |
| `core/` | **safe_mode.py** | Emergency stop — halt all trading activity | 🔥 CRITICAL |

---

### memory/ ← State Persistence

| Path | File | Function | Status |
|---|---|---|---|
| `memory/` | **state.db** | Active positions + open order status | 🔥 CRITICAL |
| `memory/` | **experience.db** | History of all closed trades | — |
| `memory/` | **performance.db** | Performance statistics per strategy | — |
| `memory/` | **order_registry.db** | Idempotency store — prevent double orders | 🔥 CRITICAL |
| `memory/` | **execution_cache.db** | Retry tracking cache per order | 🔥 CRITICAL |
| `memory/cache/` | **\*.json** | Fast in-memory cache (short TTL) | — |

---

### logs/

| Path | File | Function | Status |
|---|---|---|---|
| `logs/` | **trades_spot.json** | Log all spot trades (JSON structured) | — |
| `logs/` | **trades_futures.json** | Log all futures trades | — |
| `logs/` | **decisions_spot.json** | Log spot strategy decisions (with reasoning) | — |
| `logs/` | **decisions_futures.json** | Log futures strategy decisions | — |
| `logs/` | **execution.log** | Log all order execution to exchange | — |
| `logs/` | **idempotency.log** | Log duplicate order checks | 🔥 CRITICAL |
| `logs/` | **errors.log** | Error log with stack traces | — |

---

## 04 Data & Intelligence Layer

### data_layer/

| Path | File | Function | Status |
|---|---|---|---|
| `data_layer/` | **market.py** | Fetch OHLCV, ticker, 24h stats | — |
| `data_layer/` | **orderbook.py** | Snapshot & stream orderbook depth | — |
| `data_layer/` | **websocket_client.py** | Binance WebSocket handler (auto-reconnect) | — |
| `data_layer/` | **validator.py** | Validate market data before processing | — |
| `data_layer/` | **anomaly_detector.py** | Detect price spikes / corrupt data | ⚠️ IMPORTANT |
| `data_layer/` | **rate_limiter.py** | Global exchange API rate limiter (weight tracking) | ✅ NEW |

---

### intelligence_layer/

| Path | File | Function | Status |
|---|---|---|---|
| `intelligence_layer/` | **regime.py** | Market classification: trending / sideways / volatile | — |
| `intelligence_layer/` | **volatility.py** | Compute ATR, realized vol, vol percentile | — |
| `intelligence_layer/` | **market_state.py** | Aggregate market conditions for strategies | — |
| `intelligence_layer/` | **latency_guard.py** | Protect execution during high latency | ⚠️ IMPORTANT |

---

## 05 Strategy & Portfolio Layer

### strategy_layer/

| Path | File | Function | Status |
|---|---|---|---|
| `strategy_layer/` | **base_strategy.py** | Abstract base class for all strategies | — |
| `strategy_layer/` | **spot_strategy.py** | Spot trading strategy implementation | — |
| `strategy_layer/` | **futures_strategy.py** | Futures strategy + hedge implementation | — |
| `strategy_layer/` | **strategy_utils.py** | Helpers: signal scoring, filter, validator | — |

---

### portfolio_layer/ ← Global Risk Control

| Path | File | Function | Status |
|---|---|---|---|
| `portfolio_layer/` | **allocator.py** | Capital allocation across strategies + symbols | 🔥 CRITICAL |
| `portfolio_layer/` | **capital_manager.py** | Monitor & control total equity | 🔥 CRITICAL |
| `portfolio_layer/` | **correlation.py** | Prevent over-exposure to correlated assets | ⚠️ IMPORTANT |
| `portfolio_layer/` | **risk_budget.py** | Distribute risk budget per strategy | ⚠️ IMPORTANT |

---

## 06 Risk Layer

*All orders MUST pass through RiskManager before being sent to exchange.*

| Path | File | Function | Status |
|---|---|---|---|
| `risk_layer/` | **risk_manager.py** | Orchestrator: single gate for all risk validation | 🔥 CRITICAL |
| `risk_layer/` | **position_size.py** | Kelly / Fixed Fractional / ATR-scaled sizing | 🔥 CRITICAL |
| `risk_layer/` | **stoploss.py** | Validate & set stop loss before entry | 🔥 CRITICAL |
| `risk_layer/` | **exposure_control.py** | Max exposure per symbol + total portfolio | ⚠️ IMPORTANT |
| `risk_layer/` | **leverage_control.py** | Leverage limit per symbol (futures) | ⚠️ IMPORTANT |
| `risk_layer/` | **circuit_breaker.py** | Auto-halt: daily loss / drawdown / loss streak | 🔥 CRITICAL |
| `risk_layer/` | **pre_trade_check.py** | Sanity check data + market status before order | — |

*Risk flow: PreTradeCheck → CircuitBreaker → LeverageControl → ExposureControl → PositionSizer → StopLoss → APPROVE/BLOCK*

---

## 07 Trade & Execution Layer

### trade_layer/

| Path | File | Function | Status |
|---|---|---|---|
| `trade_layer/` | **trade.py** | Trade data class with client_order_id | 🔥 CRITICAL |
| `trade_layer/` | **manager.py** | Orchestration: open, update, close trade | — |
| `trade_layer/` | **store.py** | Persist trades to SQLite (state.db) | — |
| `trade_layer/` | **audit_trail.py** | Immutable log of every trade change | ⚠️ IMPORTANT |
| `trade_layer/` | **trade_validator.py** | Validate trade before sending to execution | — |
| `trade_layer/` | **idempotency.py** | Anti double-order via order_registry.db | 🔥 CRITICAL |
| `trade_layer/` | **recovery.py** | State recovery after crash / restart | 🔥 CRITICAL |

---

### execution_layer/ ✅ Multi-Exchange

| Path | File | Function | Status |
|---|---|---|---|
| `execution_layer/` | **exchange.py** | Abstract base class for all exchange connectors | ✅ NEW |
| `execution_layer/exchanges/` | **binance.py** | Binance Spot + Futures implementation | ✅ NEW |
| `execution_layer/exchanges/` | **bybit.py** | Bybit implementation | ✅ NEW |
| `execution_layer/exchanges/` | **okx.py** | OKX implementation | ✅ NEW |
| `execution_layer/` | **spot_executor.py** | Spot order executor | — |
| `execution_layer/` | **futures_executor.py** | Futures order executor + margin | — |
| `execution_layer/` | **order_splitter.py** | Split large orders (TWAP / VWAP) | ⚠️ IMPORTANT |
| `execution_layer/` | **smart_router.py** | Route orders across exchanges (best price) | ✅ NEW |
| `execution_layer/` | **latency_tracker.py** | Monitor & record latency per exchange | — |
| `execution_layer/` | **execution_monitor.py** | Monitor order status after submission | — |

---

## 08 Exit, Monitoring & Sync Layer

### exit_layer/

| Path | File | Function | Status |
|---|---|---|---|
| `exit_layer/` | **trailing.py** | Trailing stop: ATR-based & percentage-based | — |
| `exit_layer/` | **take_profit.py** | TP: single target & partial TP scaling | — |
| `exit_layer/` | **break_even.py** | Move SL to entry when profit reaches threshold | — |
| `exit_layer/` | **exit_manager.py** | Coordinate all exit mechanisms | 🔥 CRITICAL |

---

### monitoring/

| Path | File | Function | Status |
|---|---|---|---|
| `monitoring/` | **heartbeat.py** | Periodic ping — detect agent hang/dead | 🔥 CRITICAL |
| `monitoring/` | **connectivity.py** | Check connection to exchange & internet | — |
| `monitoring/` | **health_check.py** | Status of all layers + DB + memory | ⚠️ IMPORTANT |
| `monitoring/` | **alerts.py** | Send alerts to Telegram/Discord on anomaly | — |

---

### sync/ ✅ + position_sync

| Path | File | Function | Status |
|---|---|---|---|
| `sync/` | **balance_sync.py** | Sync balance from exchange to internal | — |
| `sync/` | **order_sync.py** | Sync status of open orders | — |
| `sync/` | **position_sync.py** | Reconcile internal positions vs actual exchange | ✅ NEW |
| `sync/` | **reconciliation.py** | Full audit: PnL, balance, positions — daily | ⚠️ IMPORTANT |

---

## 09 Learning & LLM Layer

### learning_layer/

| Path | File | Function | Status |
|---|---|---|---|
| `learning_layer/` | **reflection.py** | Analyze historical trades — extract patterns | — |
| `learning_layer/` | **adaptation.py** | Adjust strategy parameters based on performance | — |
| `learning_layer/` | **drift_detection.py** | Detect model drift (PSI, KL divergence) | 🔥 CRITICAL |
| `learning_layer/` | **experience_processor.py** | Process & save experience to experience.db | — |
| `learning_layer/` | **strategy_killer.py** | Auto-disable underperforming strategies | 🔥 CRITICAL |

---

### llm_layer/ ✅ + cost & rate control

| Path | File | Function | Status |
|---|---|---|---|
| `llm_layer/` | **llm_client.py** | Client to Anthropic / OpenAI API | — |
| `llm_layer/` | **llm_budget.py** | Cost control: limit USD per day / month | ✅ NEW |
| `llm_layer/` | **llm_rate_limiter.py** | Rate limit LLM calls (tokens/min) | ✅ NEW |
| `llm_layer/` | **trade_analyzer.py** | Request LLM analysis of trade scenarios | — |
| `llm_layer/` | **strategy_feedback.py** | Feedback loop: performance → LLM prompt | — |
| `llm_layer/` | **llm_filter.py** | Scoring only — LLM does not execute orders | 🔥 CRITICAL |

---

## 10 Notification, Utils & Models

### notification/

| Path | File | Function | Status |
|---|---|---|---|
| `notification/` | **telegram.py** | Send notifications to Telegram Bot | — |
| `notification/` | **discord.py** | Send notifications to Discord webhook | — |
| `notification/` | **notifier.py** | Abstraction: route to the correct channel | — |

---

### utils/ ✅ + structured logger

| Path | File | Function | Status |
|---|---|---|---|
| `utils/` | **logger.py** | Standard Python logger with formatter | — |
| `utils/` | **structured_logger.py** | JSON structured logging (Prometheus-compatible) | ✅ NEW |
| `utils/` | **time_utils.py** | UTC helper, candle timing, timestamp converter | — |
| `utils/` | **helpers.py** | General helpers: retry, round_price, safe_div | — |

---

### models/

| Path | File | Function | Status |
|---|---|---|---|
| `models/` | **spot_model.pkl** | ML model for spot trading signals | — |
| `models/` | **futures_model.pkl** | ML model for futures trading signals | — |
| `models/` | **metadata.json** | Model info: version, train date, features, thresholds | ⚠️ IMPORTANT |

---

## 11 Tests Layer

### unit/

| Path | File | Function | Status |
|---|---|---|---|
| `tests/unit/` | **test_risk.py** | Unit tests for Risk Layer — 20+ test cases | 🔥 CRITICAL |
| `tests/unit/` | **test_strategy.py** | Unit tests for spot & futures strategy logic | — |
| `tests/unit/` | **test_idempotency.py** | Anti double-order tests — edge cases | 🔥 CRITICAL |
| `tests/unit/` | **test_execution.py** | Test executor + lot filter + slippage | — |

---

### integration/ & mocks/

| Path | File | Function | Status |
|---|---|---|---|
| `tests/integration/` | **test_full_flow.py** | End-to-end: signal → risk → trade → exit | ⚠️ IMPORTANT |
| `tests/integration/` | **test_exchange_flow.py** | Test execution flow with mock exchange | ⚠️ IMPORTANT |
| `tests/mocks/` | **mock_exchange.py** | Simulate Binance API responses | — |
| `tests/mocks/` | **mock_data.py** | Market data fixtures for testing | — |

---

## 12 Automation, Observability & Docs

### automation/

| Path | File | Function | Status |
|---|---|---|---|
| `automation/` | **retrain.py** | Auto-retrain model when drift is detected | — |
| `automation/` | **deploy.py** | Deploy new model to production | — |
| `automation/` | **scheduler.py** | Cron-like scheduler for automated tasks | — |
| `automation/` | **health_restart.py** | Auto-restart agent if heartbeat dies | 🔥 CRITICAL |

---

### observability/

| Path | File | Function | Status |
|---|---|---|---|
| `observability/` | **prometheus_metrics.py** | Expose metrics: PnL, latency, win rate | ⚠️ IMPORTANT |
| `observability/` | **grafana_dashboard.json** | Dashboard: equity curve, drawdown, volume | ⚠️ IMPORTANT |
| `observability/` | **alert_rules.yml** | Alerts: DD > 5%, latency > 2s, CB triggered | 🔥 CRITICAL |

---

### audit/

| Path | File | Function | Status |
|---|---|---|---|
| `audit/` | **trade_history.db** | Permanent database of all trades (immutable) | 🔥 CRITICAL |
| `audit/` | **tax_report.py** | Generate PnL report for tax purposes | — |
| `audit/` | **pnl_tracker.py** | Real-time PnL tracking per strategy & symbol | — |

---

### docs/ ✅ + rollback_guide

| Path | File | Function | Status |
|---|---|---|---|
| `docs/` | **architecture.md** | Architecture diagram + layer explanation | — |
| `docs/` | **agent_flow.md** | Flow diagram: signal → order → close | — |
| `docs/` | **risk_management.md** | Complete Risk Layer documentation + config | — |
| `docs/` | **deployment_guide.md** | Step-by-step deploy: paper → shadow → live | ⚠️ IMPORTANT |
| `docs/` | **rollback_guide.md** | Emergency procedures: shutdown, rollback, recovery | ✅ NEW |

---

## 13 Gap Analysis — Before vs After

*8 critical gaps found during initial review — all resolved in the final structure.*

| Gap Found | Solution in Final Structure | Status |
|---|---|---|
| No config validation at startup | `core/config_schema.py` (Pydantic) | **Resolved** |
| LLM layer without rate limit & cost control | `llm_budget.py` + `llm_rate_limiter.py` | **Resolved** |
| tests/ empty — no critical tests | `test_risk.py`, `test_idempotency.py`, `mocks/` | **Resolved** |
| Multi-exchange with no clear abstraction | `exchanges/` base class + binance/bybit/okx | **Resolved** |
| No position reconciliation vs exchange | `sync/position_sync.py` | **Resolved** |
| No centralized rate limiter for exchange API | `data_layer/rate_limiter.py` | **Resolved** |
| No DB schema versioning / migration | `runtime/migration/` + `versions/` | **Resolved** |
| No rollback / emergency procedure in docs | `docs/rollback_guide.md` | **Resolved** |

---

## 14 Risk Layer — Quick Reference

*Quick guide to the Risk Layer components for which a skeleton has been generated.*

| Component | Responsibility | Paper Mode Behavior |
|---|---|---|
| **RiskManager** | Single gate — all orders must pass through | Block is logged, not rejected |
| **PreTradeCheck** | Validate format, symbol, market status | Same as live |
| **CircuitBreaker** | Halt on daily loss / drawdown / loss streak | State machine still runs |
| **PositionSizer** | Kelly / Fixed Fractional / ATR-scaled sizing | Sizing still computed |
| **ExposureControl** | Max per-symbol and total portfolio exposure | Block is logged, not rejected |
| **LeverageControl** | Leverage limit per symbol (futures) | Validated, not rejected |
| **StopLossManager** | Validate SL distance & require it before entry | SL optional, warning only |

**Important config keys:**

- `paper_mode` = True/False — must be False before going live
- `risk_per_trade_pct` = 0.01 — 1% equity per trade
- `max_daily_loss_pct` = 0.05 — 5% → circuit breaker halt
- `max_drawdown_pct` = 0.10 — 10% from peak → halt
- `max_open_positions` = 5 — maximum concurrent positions
- `global_max_leverage` = 5 — leverage limit for all symbols
- `sizing_method` = `fixed_fractional` | `kelly` | `volatility_scaled`

---

## 15 Recommended Deployment Flow

| Phase | Mode | Activities | Minimum Duration |
|---|---|---|---|
| **1** | **Paper Trading** | `paper_mode=True`. All logic runs, no real orders. Validate risk + sizing. | *Min. 2 weeks* |
| **2** | **Shadow Mode** | Run in parallel with live — orders sent to exchange but not filled (testnet or special flag). | *Min. 1 week* |
| **3** | **Live — Small Capital** | `paper_mode=False`. Minimal capital (10–20%). Monitor circuit breaker & equity daily. | *Min. 1 month* |
| **4** | **Live — Full** | Scale up capital gradually. Enable multi-exchange routing. Monitor Grafana dashboard. | *Ongoing* |

*This structure is the foundation. The detailed implementation of each layer can be generated one at a time.*
