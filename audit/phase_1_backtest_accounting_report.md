# Phase 1 — Backtest Accounting Audit Report

**Date:** 2026-06-09
**Scope:** research/backtest/ (engine.py, execution.py)
**Status:** COMPLETE

---

## 1. Original Confirmed Bugs

### Batch 1 — Double commission deduction
- `net_pnl` included commission inside `gross_pnl`, then subtracted it again
- No separate `gross_pnl` field existed — accounting was opaque
- `equity += trade.pnl` used a value that already had commission baked in,
  then subtracted commission a second time

### Batch 2 — Incorrect slippage direction
- Short entry used `open × (1 + slippage)` — **paid more** instead of selling
  at a lower price
- No exit slippage was applied on any exit path (SL, TP, trailing, end-of-data)
- SL/TP were absolute prices from the signal candle close, not recalculated
  from the actual fill price — distorting the planned risk/reward ratio

### Batch 3 — Unrealistic fill assumptions
- No gap-through handling: SL exit used the stop price even when the candle
  opened beyond it (impossible fill in real markets)
- No TP gap-through handling: TP exit used the target price even when the
  candle opened past it (gave the backtest a worse fill than reality)
- No intrabar ambiguity handling: when the same candle touched both SL and
  TP, the engine silently assumed SL without recording the ambiguity
- No initial risk or R-multiple on any trade

---

## 2. Files Modified

| File | Batch | Change |
|------|-------|--------|
| `research/backtest/engine.py` | 1-3 | Trade/Position/Signal dataclass, engine loop, `_check_exit`, `_close_position`, `_open_position` |
| `research/backtest/execution.py` | 2 | **NEW** — `apply_adverse_slippage`, `calc_slippage_cost`, `calc_trade_levels`, `signal_to_distances` |
| `tests/unit/test_backtest_accounting.py` | 1 | 7 regression tests for PnL accounting |
| `tests/unit/test_backtest_execution.py` | 2 | 20 tests for slippage, fill levels, RR preservation |
| `tests/unit/test_backtest_engine.py` | 3 | **NEW** — 21 tests for gaps, intrabar, R-multiple, forced close |
| `audit/phase_1_backtest_accounting_report.md` | 3 | **NEW** — This report |

---

## 3. Accounting Conventions

### Net PnL formula (Approach A — slippage embedded in fill prices)
```
gross_pnl  = (exit_fill − entry_fill) × qty           [LONG]
gross_pnl  = (entry_fill − exit_fill) × qty           [SHORT]

entry_fee  = entry_fill × qty × commission_pct
exit_fee   = exit_fill  × qty × commission_pct
funding_cost = 0.0                                     [placeholder]

net_pnl    = gross_pnl − entry_fee − exit_fee − funding_cost
```

`slippage_cost` is recorded for diagnostics but is **NOT** subtracted from
`net_pnl` because it is already embedded in the fill prices.

### Equity update
```
equity += trade.net_pnl
```
This is the ONLY place equity is modified.  No other formula touches equity.

### Final equity invariant
```
final_equity == initial_equity + Σ(trade.net_pnl)
```

---

## 4. Slippage Conventions

| Action | Formula | Applies to |
|--------|---------|------------|
| BUY | `raw_price × (1 + slippage_pct)` | Long entry, Short exit |
| SELL | `raw_price × (1 − slippage_pct)` | Short entry, Long exit |

All slippage is **adverse** — the trader always gets a worse price.

`slippage_cost` = `|fill_price − raw_price| × qty` (informational only).

---

## 5. Gap Behavior

### Stop-loss gaps

| Side | Condition | Raw exit price |
|------|-----------|---------------|
| LONG | `candle_open ≤ sl_price` | `candle_open` (worse than SL) |
| SHORT | `candle_open ≥ sl_price` | `candle_open` (worse than SL) |

### Take-profit gaps

| Side | Condition | Raw exit price |
|------|-----------|---------------|
| LONG | `candle_open ≥ tp_price` | `candle_open` (better than TP) |
| SHORT | `candle_open ≤ tp_price` | `candle_open` (better than TP) |

After determining the raw exit price, normal adverse slippage is applied.

---

## 6. Intrabar Behavior

When the same candle touches **both** SL and TP:

| Policy | Behavior | Exit reason |
|--------|----------|-------------|
| `conservative` (default) | Assume SL hit first | `stop_loss` |
| `optimistic` | Assume TP hit first | `take_profit` |
| `skip` | Close at candle close | `ambiguous_close` |

Every ambiguous candle is:
- Recorded as `trade.ambiguous_bar = True`
- Tagged with `trade.intrabar_policy_used = "<policy>"`
- Counted in `result.ambiguous_bars`

---

## 7. R-Multiple Formula

### At entry
```
initial_risk_usd = |entry_price − initial_stop_price| × qty
```

`initial_stop_price` is frozen at entry time.  It does **not** change when:
- trailing stop activates
- stop moves to break-even
- strategy exit overrides

### At close
```
r_multiple = net_pnl / initial_risk_usd
```

If `initial_risk_usd == 0` (no stop defined), `r_multiple = 0.0`.

### Planned RR
```
planned_rr = tp_distance / sl_distance
```
Stored on both `Position` and `Trade` for post-analysis.

---

## 8. Tests Added

### Batch 1 — Accounting (7 tests)
| # | Test | Purpose |
|---|------|---------|
| 1 | Winning LONG net PnL | `net_pnl = gross − commission` |
| 2 | Winning LONG final equity | `equity = initial + net_pnl` |
| 3 | Winning LONG equity invariant | `Σnet_pnl` check |
| 4 | Losing LONG net PnL | Negative PnL decomposition |
| 5 | Losing LONG final equity | Loss reflected |
| 6 | Losing LONG equity invariant | Invariant holds for losses |
| 7 | Net PnL decomposition | `net = gross − all_costs` |

### Batch 2 — Execution (20 tests)
| # | Test | Purpose |
|---|------|---------|
| 1-5 | Adverse slippage (BUY/SELL/zero) | Fill price direction |
| 6-7 | Fill-based SL/TP | LONG and SHORT levels |
| 8-9 | Planned RR preserved | RR stays constant |
| 10-12 | Invalid stop rejected | ValueError on bad inputs |
| 13 | Commission regression w/ slippage | Approach A invariant |
| 14-15 | Equity invariant w/ slippage | LONG and SHORT |
| 16-17 | Engine integration | Fill prices end-to-end |
| 18-20 | Signal-to-distances | Adapter conversion |

### Batch 3 — Engine behavior (21 tests)
| # | Test | Purpose |
|---|------|---------|
| 1-4 | Gap-aware exits | SL/TP gap-through fills |
| 5-8 | Intrabar policy | Conservative/optimistic LONG/SHORT |
| 9 | Ambiguous event recorded | `ambiguous_bar=True`, policy stored |
| 10 | Default policy | `conservative` is default |
| 11-14 | R-multiple | Positive/negative R, fee/slip impact |
| 15 | Moving stop | `initial_risk_usd` frozen |
| 16-17 | end_of_data close | LONG and SHORT |
| 18 | end_of_data accounting | All fields populated |
| 19 | Commission deducted once | Formula check |
| 20 | Equity invariant | Master invariant |
| 21 | R-multiple formula | `r = net_pnl / initial_risk` |

**Total: 48 tests**

---

## 9. Test Results

```
48 passed in 0.74s
```

Lint/type results:
- **ruff**: ✅ All checks passed
- **black**: ✅ All files formatted
- **mypy**: ✅ No issues found in 2 source files

Full unit suite (including unrelated tests): 53 pre-existing failures
(data_layer, intelligence_layer, models, portfolio_layer — all unrelated
to backtest changes).

---

## 10. Known Remaining Limitations

1. `funding_cost` is hardcoded to `0.0` — future funding rate model needed
2. Trailing stop uses ideal peak price (no slippage on peak tracking)
3. No intrabar fill simulation beyond SL/TP ambiguity
4. `r_multiple = 0.0` when no stop is set (no risk baseline)
5. No maximum slippage cap
6. No partial fill simulation
7. No multi-asset correlation effects
8. Mark-to-market equity curve uses raw close (no slippage)

---

## 11. Work Intentionally Deferred to Phase 2

| Item | Reason |
|------|--------|
| Risk-based position sizing | Separate concern from accounting correctness |
| Strategy/runtime parity | Requires runtime layer alignment |
| Daily Sharpe correction | Metrics layer, not accounting |
| Model validation | Intelligence layer scope |
| PostgreSQL migration | Infrastructure scope |
| Strategy tuning | Explicitly prohibited in Phase 1 |

---

## Codebase Search Results

### `commission` in research/backtest/
- `engine.py:49` — config field definition
- `engine.py:104` — Trade field (backward compat)
- `engine.py:552-553` — entry_fee and exit_fee calculation (ONCE)
- `engine.py:563` — `commission = entry_fee + exit_fee` (informational)
- **No duplicate deduction found.** ✅

### `equity +=` in research/backtest/
- `engine.py:213` — in-loop close: `equity += trade.net_pnl`
- `engine.py:282` — end-of-data close: `equity += trade.net_pnl`
- **Both use net_pnl from `_close_position`.** ✅

### `equity -=` in research/backtest/
- **No results.** ✅

### `net_pnl` in research/backtest/
- Computed exactly once at `engine.py:564`
- Used for equity update at lines 213 and 282 only
- Used for R-multiple calculation at line 571
- **No duplicate deduction found.** ✅

---

## Conclusion

Phase 1 is **COMPLETE**. All accounting invariants are verified:
- Every cost is counted exactly once
- Long and short slippage are adverse
- SL and TP are calculated from actual fill price
- Gaps use executable prices
- Ambiguous candles are explicit
- Every trade has initial risk and R-multiple
- Forced close uses the central accounting path
- All 48 tests pass
- Final equity invariant passes
