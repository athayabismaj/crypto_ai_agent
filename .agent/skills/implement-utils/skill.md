---
name: implement-utils
description: Panduan implementasi utils/ — layer pertama yang dibuat
---

# Utils — Panduan Implementasi

## File yang Dibuat
- helpers.py     ← round_qty (floor!), calc_pnl, generate_order_id
- time_utils.py  ← utcnow(), mock_time(), tf_to_seconds()
- logger.py      ← get_logger(__name__), bind()
- structured_logger.py ← JSON output

## Aturan Paling Penting
round_qty() WAJIB pakai math.floor:
  round_qty(0.1235, 0.001) → 0.123  (BUKAN 0.124)

utcnow() dari time_utils WAJIB bisa di-mock:
  dari utils.time_utils import mock_time
  mock_time(datetime(...))  # untuk unit test

## Test Coverage: 95%
Test round_qty: floor bukan round
Test calc_pnl: buy win, buy loss, sell win, sell dengan commission
Test generate_order_id: unique, max 36 char, valid characters
Test mock_time: bisa di-set dan di-reset
