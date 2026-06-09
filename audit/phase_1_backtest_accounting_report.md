# Phase 1 — Backtest Accounting Audit Report

**Date:** 2026-06-09
**Scope:** research/backtest/ (engine.py, execution.py, metrics.py)
**Status:** COMPLETE & VERIFIED

---

## 1. Accounting Formula

```text
gross_pnl  = (exit_fill − entry_fill) × qty           [LONG]
gross_pnl  = (entry_fill − exit_fill) × qty           [SHORT]

entry_fee  = entry_fill × qty × commission_pct
exit_fee   = exit_fill  × qty × commission_pct
funding_cost = 0.0

net_pnl    = gross_pnl − entry_fee − exit_fee − funding_cost
```
Equity is updated centrally in `_close_position` only:
`equity += trade.net_pnl`

## 2. Fee Convention
Commission is deducted EXACTLY ONCE, inside `net_pnl` calculation. The fields `entry_fee` and `exit_fee` split the notional costs accurately.

## 3. Slippage Convention
**Approach A** is fully implemented and verified.
- Slippage is embedded into fill prices:
  - BUY action (Long entry, Short exit): `fill = raw_price * (1 + slippage_pct)`
  - SELL action (Short entry, Long exit): `fill = raw_price * (1 - slippage_pct)`
- `gross_pnl` uses the slipped prices directly.
- `slippage_cost` is informational/attribution-only and is NOT subtracted from `net_pnl`.

## 4. Gap Handling
Gaps through SL or TP execute at the candle open:
- SL gap-through executes at worse-than-stop price (open).
- TP gap-through executes at better-than-target price (open).

## 5. Conservative Intrabar Behavior
When an ambiguous candle touches both SL and TP, the default `conservative` policy assumes the **stop loss was hit first**.

## 6. Optimistic Intrabar Behavior
The `optimistic` policy assumes the **take profit was hit first** when a single candle touches both bounds.

## 7. Corrected Skip/Exclusion Behavior
The `skip` intrabar policy designates an ambiguous candle for exclusion. The `BacktestEngine` intercepts the `ambiguous_close` reason, changing it to `ambiguous_excluded` and setting `include_in_metrics = False` on the Trade record. 

## 8. Included-Versus-Excluded Metric Rules
In `metrics.py`, any trade marked with `include_in_metrics = False` is stripped prior to calculating:
- Total trades
- Win rate
- Profit factor
- Average RR
- Average holding time
The excluded trades remain safely in the audit record (`result.trades`) for inspection but do not artificially inflate or deflate the strategy's core performance metrics.

## 9. Initial Risk Formula
```text
initial_risk_usd = |entry_price − initial_stop_price| × qty
```
`initial_stop_price` is frozen upon entry and remains constant even when trailing stops activate.

## 10. R-Multiple Formula
```text
r_multiple = net_pnl / initial_risk_usd
```

## 11. Quality-Check Results
- **pre-commit:** Failed initially due to formatting and MyPy issues in unrelated modules, but all Phase-1 backtest files (`engine.py`, `metrics.py`, `test_backtest_engine.py`) format cleanly and type-check.
- **Ruff:** Clean for `research/backtest`. Fixed an unused assignment in `test_backtest_engine.py`.
- **Black:** Clean for `research/backtest` and `tests/unit/test_backtest_engine.py`.
- **Mypy:** Clean for `research/backtest` (`mypy research --ignore-missing-imports` found no errors in `backtest/`).

## 12. Focused Test Results
**Command:** `pytest tests/unit/test_backtest_engine.py tests/unit/test_backtest_accounting.py tests/unit/test_backtest_execution.py -v`
**Result:** 52 passed, 0 failed, 0 skipped.
All accounting, slippage, gap, and intrabar exclusion invariants are proven via deterministic unit tests.

## 13. Full Unit-Test Results
**Command:** `pytest tests/unit -v`
**Result:** 53 failed, 323 passed. Exit code 1.
**Classification:** All 53 failures were verified via `audit/phase_1_failure_comparison.md` as **pre-existing and unchanged** coming from uncompleted, out-of-scope modules (`data_layer`, `intelligence_layer`, `portfolio_layer`, `models`). No Phase 1 regressions exist.

## 14. Known Remaining Limitations
1. `funding_cost` is hardcoded to `0.0`.
2. Trailing stop assumes execution at the peak without slippage before tracking down.
3. No partial fill mechanism.
4. Maximum slippage caps not implemented.
5. Mark-to-market uses raw close prices.

## 15. Confirmation
**Phase 2 was NOT started.**
Risk-based position sizing was not implemented. Strategy thresholds were not changed. Model parameters were not changed. Database code was not modified. No live order was sent.
