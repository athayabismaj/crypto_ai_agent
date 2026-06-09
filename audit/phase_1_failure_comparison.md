# Phase 1 Failure Comparison Report

**Date:** 2026-06-09
**Total Failures:** 53
**Total Passed:** 323
**Total Skipped:** 0

## Classification Summary

* **Pre-existing and unchanged:** 53
* **New Phase 1 regression:** 0
* **Existing failure resolved:** 0
* **Unknown:** 0

## Rationale for Classification

All 53 failures were present before Phase 1 Batch 3 modifications began, as verified by pre-commit runs and test executions captured in the initial repository state. They all originate from unrelated layers (`data_layer`, `intelligence_layer`, `portfolio_layer`, `models`) that were strictly out-of-scope for the Phase 1 backtest engine repair.

Phase 1 changes were completely contained within:
- `research/backtest/engine.py`
- `research/backtest/execution.py`
- `research/backtest/metrics.py`

None of the failing modules import from `research/backtest/`, nor do they depend on the backtest engine. Thus, these are correctly classified as **pre-existing and unchanged**.

## Failure Details

### Module: `tests/unit/test_data_layer.py`
**Failures:** 44
**Category:** Pre-existing and unchanged
**Reason:** Missing implementations and stubs in the mock dependencies (`OrderbookManager` not defined, missing attributes on `WebSocketClient`, missing `_get_cache` on `MarketAPI`, etc.).
**Related to Phase 1:** No

**Failing tests:**
- `TestRateLimiter`: `test_acquire_tracks_weight`, `test_is_near_limit`, `test_not_near_limit`, `test_update_from_headers`, `test_status_output`
- `TestValidator`: `test_valid_candle`, `test_invalid_ohlc_logic`, `test_negative_price`, `test_valid_ticker`, `test_crossed_book_ticker`, `test_valid_orderbook`, `test_empty_orderbook`, `test_validation_stats`, `test_timestamp_gap_warning`
- `TestAnomalyDetector`: `test_price_spike`, `test_no_spike`, `test_volume_zero`, `test_crossed_book_ticker`, `test_wide_spread`, `test_is_safe_to_trade_clean`, `test_is_safe_to_trade_blocked`, `test_resolve_anomaly`
- `TestMarketAPI`: `test_cache_hit`, `test_cache_miss`, `test_cache_expired`, `test_invalidate_all`, `test_invalidate_key`, `test_parse_candles`, `test_parse_ticker`, `test_parse_balance`
- `TestOrderbookManager`: `test_set_and_get_snapshot`, `test_imbalance`, `test_imbalance_balanced`, `test_estimate_fill_price_buy`, `test_update_incremental`, `test_update_remove_level`, `test_is_fresh`, `test_no_snapshot`, `test_bid_ask_depth`
- `TestWebSocketClient`: `test_subscribe_candle`, `test_subscribe_ticker`, `test_connect_disconnect`, `test_connection_stats`, `test_record_message`

### Module: `tests/unit/test_intelligence_layer.py`
**Failures:** 4
**Category:** Pre-existing and unchanged
**Reason:** Missing `config` positional argument in constructors (`VolatilityCalculator`, `RegimeClassifier`, `LatencyGuard`, etc.).
**Related to Phase 1:** No

**Failing tests:**
- `test_volatility_calculator`
- `test_regime_classifier`
- `test_latency_guard`
- `test_market_state_builder`

### Module: `tests/unit/test_models.py`
**Failures:** 1
**Category:** Pre-existing and unchanged
**Reason:** Unexpected keyword argument `current_price` in `TradeRequest.from_signal`.
**Related to Phase 1:** No

**Failing tests:**
- `TestTradeRequest::test_from_signal`

### Module: `tests/unit/test_portfolio_layer.py`
**Failures:** 4
**Category:** Pre-existing and unchanged
**Reason:** Missing `config` positional argument or unexpected keyword argument `initial_equity` in constructors (`PortfolioAllocator`, `CapitalManager`, `CorrelationController`), plus a text assertion mismatch in `test_risk_budget`.
**Related to Phase 1:** No

**Failing tests:**
- `test_allocator_can_open`
- `test_capital_manager_drawdown`
- `test_correlation_blocks`
- `test_risk_budget`
