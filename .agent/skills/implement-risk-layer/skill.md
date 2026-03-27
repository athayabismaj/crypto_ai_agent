---
name: implement-risk-layer
description: Panduan implementasi risk_layer/ — layer paling kritis
---

# Risk Layer — Panduan Implementasi

## Urutan File (HARUS diikuti)
1. pre_trade_check.py   ← validasi data & market
2. circuit_breaker.py   ← state machine HALT
3. position_size.py     ← sizing: fixed/kelly/volatility
4. stoploss.py          ← validasi & kalkulasi SL
5. exposure_control.py  ← batas exposure portfolio
6. leverage_control.py  ← batas leverage futures
7. risk_manager.py      ← koordinasi semua (terakhir)

## Kontrak Paling Kritis
RiskResult dataclass (output evaluate()):
  verdict: RiskVerdict     # APPROVED | WARNED | BLOCKED
  approved_quantity: float # 0 jika BLOCKED
  sl_price: float
  risk_amount_usd: float

Paper mode behavior:
  BLOCKED → ubah ke WARNED, qty tetap diisi
  APPROVED → tetap APPROVED
  Artinya: paper mode tidak pernah benar-benar block

## Test Coverage: 100% WAJIB
Skenario wajib ada:
- approve trade normal
- block zero quantity
- block zero sl_price
- circuit breaker halt pada DD > 10%
- circuit breaker warn pada daily loss > 3%
- kelly dengan win_rate = 0 tidak crash
- lot filter menggunakan math.floor bukan round
- paper mode: blocked jadi warned
- concurrent circuit breaker (thread safety)
