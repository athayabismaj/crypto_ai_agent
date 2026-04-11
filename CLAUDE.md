# crypto_ai_agent — Universal Agent Rules
# Version: 1.0
# Read by: Claude Code, Antigravity, Codex, and other tools

## READ THIS FIRST
Before making any changes, you MUST read:
1. docs/dev_workflow_tech_stack.md  ← construction sequence
2. Relevant layer documentation inside docs/

## Project
AI trading agent for Binance Spot + Futures.
Language: Python 3.11 | Async: asyncio | Modes: Paper → Shadow → Live

## Architecture — Layer Sequence
Layers are built from the bottom up. Do not skip layers:
1.  utils/              → helpers, time_utils, logger
2.  security/           → key_manager, encryptor
3.  core/               → config, event_bus, scheduler, safe_mode, main
4.  data_layer/         → market, orderbook, websocket, validator, rate_limiter
5.  intelligence_layer/ → regime, volatility, market_state, latency_guard
6.  strategy_layer/     → base_strategy, spot_strategy, futures_strategy
7.  portfolio_layer/    → allocator, capital_manager, correlation, risk_budget
8.  risk_layer/         → risk_manager, circuit_breaker, position_size, stoploss
9.  trade_layer/        → trade, manager, store, idempotency, audit, recovery
10. execution_layer/    → exchange base, binance, spot_executor, smart_router
11. exit_layer/         → exit_manager, trailing, take_profit, break_even
12. monitoring/         → heartbeat, health_check, alerts, connectivity
13. sync/               → balance_sync, order_sync, position_sync, reconciliation
14. notification/       → telegram, discord, notifier
15. learning_layer/     → experience_processor, drift_detection, reflection, adaptation
16. llm_layer/          → llm_client, llm_budget, trade_analyzer, llm_filter

## Strict Coding Rules — DO NOT VIOLATE
1. NO direct `datetime.utcnow()`
   → MUST use `utils/time_utils.utcnow()`

2. `round_qty()` MUST use `math.floor` instead of `round()`
   → Reason: Prevent over-ordering rejections at the exchange

3. NO I/O (network, DB, file) in the following layers:
   → strategy_layer/, intelligence_layer/, exit_layer/
   → These layers must be strictly pure computation

4. Paper mode MUST NEVER send orders to the live exchange
   → Guard check: `if config.mode == 'paper': return self._simulate_fill()`

5. EVERY new file MUST have a corresponding unit test
   → `runtime/agent/X/file.py` → `tests/unit/test_X_file.py`

6. Idempotency MUST be atomic
   → Use `INSERT OR IGNORE` in SQLite operations

7. Audit trails MUST NOT be updated or deleted
   → INSERT operations only, absolutely NO UPDATE or DELETE

8. Risk layer is the SOLE gateway to execution
   → No order can be dispatched without passing through `RiskManager.evaluate()`

## Reference Documentation per Layer
Before implementing any layer, strictly read these docs:
- utils, notification, models → docs/notification_utils_models_docs.md
- core (main, config, modes)  → docs/agent_core_docs.md
- data + intelligence         → docs/data_intelligence_docs.md
- strategy + portfolio        → docs/strategy_portfolio_docs.md
- risk                        → docs/risk_layer_docs.md
- trade + execution           → docs/trade_execution_docs.md
- exit + monitoring + sync    → docs/exit_monitoring_sync_docs.md
- learning + llm              → docs/learning_llm_docs.md
- tests                       → docs/tests_layer_docs.md
- tech stack & order          → docs/dev_workflow_tech_stack.md

## Coverage Requirements
- risk_layer/         → 100% (Strictly mandatory)
- trade_layer/        → 100% (Idempotency & recovery critical)
- exit_layer/         → 100% (SL/TP must be precise)
- execution_layer/    → 95%
- strategy_layer/     → 90%
- utils/              → 95%
- all other layers    → minimum 85%

## Layer Status (update when a layer is completed)
- [x] utils/
- [x] security/
- [x] core/
- [x] models/
- [x] data_layer/
- [x] intelligence_layer/
- [x] strategy_layer/
- [x] portfolio_layer/
- [x] risk_layer/
- [x] trade_layer/
- [x] execution_layer/
- [x] exit_layer/
- [x] monitoring/
- [x] sync/
- [x] notification/
- [x] learning_layer/
- [x] llm_layer/

## How to Mark a Layer Complete
Once a layer passes all unit tests:
1. Change `[ ]` to `[x]` in the Layer Status section above.
2. Run: `git commit -m "feat(layer_name): complete implementation"`
