# crypto_ai_agent — Universal Agent Rules
# Versi: 1.0
# Dibaca oleh: Claude Code, Antigravity, Codex, dan tool lainnya

## BACA INI PERTAMA KALI
Sebelum melakukan apapun, baca:
1. docs/dev_workflow_tech_stack.md  ← urutan pembangunan
2. Dokumen layer yang relevan di docs/

## Proyek
AI trading agent untuk Binance Spot + Futures.
Bahasa: Python 3.11 | Async: asyncio | Mode: Paper → Shadow → Live

## Arsitektur — Urutan Layer
Layer dibangun dari bawah ke atas. Jangan loncat:
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

## Aturan Koding — TIDAK BOLEH DILANGGAR
1. TIDAK ada datetime.utcnow() langsung
   → WAJIB pakai utils/time_utils.utcnow()

2. round_qty() WAJIB pakai math.floor bukan round()
   → Alasan: hindari over-order di exchange

3. TIDAK ada I/O (network, DB, file) di dalam:
   → strategy_layer/, intelligence_layer/, exit_layer/
   → Layer ini harus pure computation

4. Paper mode TIDAK boleh kirim order ke exchange nyata
   → Cek: if config.mode == 'paper': return self._simulate_fill()

5. SETIAP file baru WAJIB ada unit test pasangannya
   → runtime/agent/X/file.py → tests/unit/test_X_file.py

6. Idempotency WAJIB atomic
   → Gunakan INSERT OR IGNORE di SQLite

7. Audit trail TIDAK boleh diupdate atau dihapus
   → Hanya INSERT, tidak ada UPDATE atau DELETE

8. Risk layer adalah satu-satunya pintu ke execution
   → Tidak ada order dikirim tanpa melewati RiskManager.evaluate()

## Dokumen Referensi per Layer
Sebelum implement layer apapun, baca dokumen ini:
- utils, notification, models → docs/notification_utils_models_docs.md
- core (main, config, modes)  → docs/agent_core_docs.md
- data + intelligence         → docs/data_intelligence_docs.md
- strategy + portfolio        → docs/strategy_portfolio_docs.md
- risk                        → docs/risk_layer_docs.md
- trade + execution           → docs/trade_execution_docs.md
- exit + monitoring + sync    → docs/exit_monitoring_sync_docs.md
- learning + llm              → docs/learning_llm_docs.md
- tests                       → docs/tests_layer_docs.md
- tech stack & urutan         → docs/dev_workflow_tech_stack.md

## Coverage Requirements
- risk_layer/         → 100% (wajib mutlak)
- trade_layer/        → 100% (idempotency & recovery)
- exit_layer/         → 100% (SL/TP harus presisi)
- execution_layer/    → 95%
- strategy_layer/     → 90%
- utils/              → 95%
- semua layer lain    → minimal 85%

## Status Layer (update saat layer selesai)
- [ ] utils/
- [ ] security/
- [ ] core/
- [ ] data_layer/
- [ ] intelligence_layer/
- [ ] strategy_layer/
- [ ] portfolio_layer/
- [ ] risk_layer/
- [ ] trade_layer/
- [ ] execution_layer/
- [ ] exit_layer/
- [ ] monitoring/
- [ ] sync/
- [ ] notification/
- [ ] learning_layer/
- [ ] llm_layer/

## Cara Menandai Layer Selesai
Setelah layer lulus semua test:
1. Ubah [ ] menjadi [x] di Status Layer di atas
2. Jalankan: git commit -m "feat(layer): complete implementation"
