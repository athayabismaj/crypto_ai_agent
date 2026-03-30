# 🤖 Crypto AI Trading Agent — Project Context

> **Tujuan file ini**: Brifing lengkap untuk developer (manusia maupun AI agent) yang baru bergabung di proyek ini.
> Baca file ini PERTAMA sebelum membaca kode atau membuat perubahan apapun.

---

## 1. Visi Proyek

Membangun **AI trading agent otonom** untuk pasar kripto yang mampu:
- Menganalisis data pasar secara real-time (harga, volume, orderbook)
- Mengklasifikasikan kondisi pasar (trend, sideways, high-volatility)
- Menghasilkan sinyal trading (BUY/SELL) via model ML
- Mengelola portfolio multi-aset dengan pembatasan risiko institusional
- Mengeksekusi order ke exchange (Binance, Bybit, OKX)
- Belajar dari kesalahan dan memperbaiki strategi secara otomatis

**Filosofi Inti**: *Safety-First*. Sistem boleh lambat tapi TIDAK BOLEH kehilangan modal secara tidak terkendali.

---

## 2. Tech Stack

| Komponen | Teknologi |
|---|---|
| Bahasa | **Python 3.14** |
| Type Checking | `mypy` (strict) |
| Linting | `ruff` |
| Formatting | `black` |
| Pre-commit | `pre-commit` hooks (black → ruff → mypy) |
| Async Runtime | `asyncio` |
| Math/Stats | `numpy`, `pandas` |
| Config | Pydantic V2 (`BaseModel`) |
| Testing | `pytest` |

---

## 3. Arsitektur Layer — Status Implementasi

Proyek ini menggunakan arsitektur berlapis (*layered architecture*). Setiap layer adalah modul Python independent yang berkomunikasi melalui dataclass dan EventBus.

### ✅ SELESAI (Production-Grade)

| Layer | Direktori | File Utama | Deskripsi |
|---|---|---|---|
| **Core** | `runtime/agent/core/` | `config.py`, `config_schema.py`, `event_bus.py`, `safe_mode.py`, `scheduler.py`, `modes.py` | Entry point, konfigurasi Pydantic, EventBus async, SafeMode emergency, Scheduler periodik |
| **Utils** | `runtime/agent/utils/` | Berbagai helper | Utilitas umum (logging, formatting, dll) |
| **Security** | `runtime/agent/security/` | Encryption, auth | Keamanan API key dan kredensial |
| **Data Layer** | `runtime/agent/data_layer/` | `market.py`, `websocket_client.py`, `validator.py`, `anomaly_detector.py`, `orderbook.py`, `rate_limiter.py` | REST client + WebSocket multiplexed, data validation, anomaly detection (price spike, zero volume, crossed book), orderbook management, token bucket rate limiter |
| **Intelligence Layer** | `runtime/agent/intelligence_layer/` | `volatility.py`, `regime.py`, `latency_guard.py`, `market_state.py` | ATR/Realized Vol (numpy vectorized), ADX/EMA regime classifier, latency monitor, MarketState aggregator ("The Nexus") |
| **Strategy Layer** | `runtime/agent/strategy_layer/` | `base_strategy.py`, `spot_strategy.py`, `futures_strategy.py`, `strategy_utils.py`, `strategy_registry.py` | BaseStrategy abstract contract, SpotStrategy (long-only), FuturesStrategy (short + hedge + funding rate), StrategyRegistry (multi-strategy lifecycle + conflict resolution), SignalProcessor (5-gate portfolio validation), score_signal() weighted composite |
| **Portfolio Layer** | `runtime/agent/portfolio_layer/` | `allocator.py`, `capital_manager.py`, `correlation.py`, `risk_budget.py` | PortfolioAllocator (per-symbol + per-strategy quota), CapitalManager (CapitalStatus 4-dimension warnings, Paper/Live mode), CorrelationController (CORRELATION_GROUPS + dynamic rolling), RiskBudgetManager (per-trade USD risk tracking, daily budget, W/L accounting) |

### 🔶 ADA SKELETON (Perlu Audit/Upgrade)

| Layer | Direktori | File Utama | Status |
|---|---|---|---|
| **Risk Layer** | `runtime/agent/risk_layer/` | `risk_manager.py`, `position_size.py`, `circuit_breaker.py`, `stoploss.py`, `pre_trade_check.py`, `exposure_control.py`, `leverage_control.py` | Skeleton dari commit awal. **Perlu audit**: pastikan integrasi dengan PortfolioAllocator/CorrelationController/RiskBudgetManager yang baru di-rewrite |
| **Trade Layer** | `runtime/agent/trade_layer/` | `manager.py`, `trade.py`, `store.py`, `recovery.py`, `audit_trail.py`, `idempotency.py`, `trade_validator.py` | Skeleton dari commit awal. Perlu integrasi dengan Risk Layer dan CapitalManager |
| **Execution Layer** | `runtime/agent/execution_layer/` | `exchange.py`, `spot_executor.py`, `futures_executor.py`, `execution_monitor.py`, `smart_router.py`, `order_splitter.py`, `latency_tracker.py` | Skeleton dari commit awal. Exchange connector base class ada |
| **Models** | `runtime/agent/models/` | Dataclass definitions | MarketState, Position, TrainedModel. **Sudah dipakai** oleh layer lain |

### ❌ BELUM DIIMPLEMENTASI (Hanya `__init__.py`)

| Layer | Direktori | Dokumentasi Referensi |
|---|---|---|
| **Exit Layer** | `runtime/agent/exit_layer/` | `docs/exit_monitoring_sync_docs.md` |
| **Monitoring** | `runtime/agent/monitoring/` | `docs/exit_monitoring_sync_docs.md` |
| **Sync Layer** | `runtime/agent/sync/` | `docs/exit_monitoring_sync_docs.md` |
| **Learning Layer** | `runtime/agent/learning_layer/` | `docs/learning_llm_docs.md` |
| **LLM Layer** | `runtime/agent/llm_layer/` | `docs/learning_llm_docs.md` |
| **News Layer** | `runtime/agent/news_layer/` | `docs/research_layer_docs.md` |
| **Notification** | `runtime/agent/notification/` | `docs/notification_utils_models_docs.md` |
| **Logs** | `runtime/agent/logs/` | `docs/automation_observability_docs.md` |

---

## 4. Keputusan Arsitektur Penting

Keputusan-keputusan ini sudah final dan harus diikuti. Jangan bertanya ulang ke user.

### 4.1 Strategy Layer

| Keputusan | Detail |
|---|---|
| **Decoupling mutlak** | Strategy TIDAK menyentuh saldo/portfolio. Dia hanya output `Signal`. Portfolio Layer yang putuskan boleh/tidak |
| **Pure function scoring** | `score_signal()` = weighted composite (40% pred, 25% regime, 15% vol, 10% imbalance, 10% spread) |
| **Counter-trend penalti** | Signal BUY saat STRONG_TREND_DOWN → penalti 70% pada skor regime |
| **Conflict resolution** | BUY + SELL di simbol yang sama → **BLOCK KEDUANYA** (pasar choppy = jangan trade) |
| **MAX_SIGNALS_PER_TICK** | Default 3 sinyal per tick. Protect execution overload |
| **Trailing stop** | Aktivasi setelah +3% profit; trigger close saat pullback -1.5% dari peak |
| **SL berbasis structure** | `align_sl_to_structure()` → SL diletakkan di bawah swing low/di atas swing high (bukan flat ATR) |

### 4.2 Portfolio Layer

| Keputusan | Detail |
|---|---|
| **Leverage tetap 3x** | Untuk fase awal, leverage futures di-lock **3x** (bukan dinamis). Alasan: keamanan modal saat uji coba |
| **Correlation caching 24h** | Matriks korelasi dihitung **sekali per hari** (bukan setiap tick). CPU-efficient |
| **CORRELATION_GROUPS** | Grup statis default: `btc_eth` (0.85, max 35%), `eth_alts` (0.75, max 40%), `large_caps` (0.65, max 60%), `defi_tokens` (0.70, max 20%) |
| **Dynamic correlation opsional** | Bisa diaktifkan via `dynamic_correlation=True`. Rolling 90-candle via Pandas |
| **Risk budget per-trade** | Hitung **USD at risk** (entry−SL) × qty, BUKAN notional. Ini perbedaan kunci antara ritel dan institusi |
| **Daily reset UTC 00:00** | Budget harian di-reset oleh scheduler. Strategi yang di-halt hari ini bisa trading lagi besok (kecuali TERMINATED) |
| **Paper/Live mode** | `paper_mode=True` → equity disimulasikan internal. `False` → sync dari exchange balance |
| **4 dimensi warning** | Daily loss (warn -3%, crit -5%), Drawdown (warn -7%, crit -10%), Exposure (warn 70%, crit 85%), Unrealized (warn -3%, crit -6%) |

### 4.3 Data & Intelligence Layer

| Keputusan | Detail |
|---|---|
| **Numpy vectorized** | Semua kalkulasi volatilitas (ATR, realized vol) menggunakan numpy — bukan loop Python |
| **MarketState = The Nexus** | Satu dataclass (`MarketState`) yang mengagregasikan semua sensor data. Strategy layer HANYA menerima objek ini |
| **Safety-first halt** | Bot berhenti otomatis jika: data quality "bad", network latency "critical", anomaly terdeteksi |
| **Anomaly types** | Price spike (>3σ), Zero volume, Crossed orderbook, Stale data (>30s) |

### 4.4 Coding Standards

| Aturan | Detail |
|---|---|
| **Type hints wajib** | Semua fungsi publik HARUS punya type annotation lengkap |
| **`X \| Y` bukan `Optional`** | Gunakan `float \| None` bukan `Optional[float]` (ruff UP007) |
| **`from __future__ import annotations`** | Wajib di setiap file baru (forward reference support) |
| **Docstring Indonesia** | Docstring dan komentar BOLEH dalam Bahasa Indonesia (preferensi user) |
| **Pre-commit hooks** | Semua commit melewati: `black` → `ruff --fix` → `mypy`. Commit GAGAL jika salah satu fail |
| **Commit message** | Format conventional commit: `feat:`, `fix:`, `refactor:`, `docs:` |

---

## 5. Alur Data (Signal Flow)

```
[Exchange WebSocket] 
    ↓ raw candle/ticker/orderbook
[Data Layer] validator.py → anomaly_detector.py
    ↓ validated data
[Intelligence Layer] volatility.py + regime.py + latency_guard.py → market_state.py
    ↓ MarketState object
[Strategy Layer] spot_strategy.py / futures_strategy.py → generate_signal()
    ↓ Signal dataclass
[StrategyRegistry] conflict resolution + priority sort
    ↓ filtered signals
[SignalProcessor] 5-gate validation:
    Gate 0: MarketState.is_safe_to_trade?
    Gate 1: CapitalManager.safe_to_trade?
    Gate 2: PortfolioAllocator.can_open?
    Gate 3: CorrelationController.check?
    Gate 4: RiskBudgetManager.can_take_risk?
    ↓ ProcessResult.APPROVED
[Risk Layer] position_size.py + pre_trade_check.py → final sizing
    ↓ TradeRequest
[Trade Layer] manager.py → idempotency.py → exchange order
    ↓ on fill
[CapitalManager] record_trade_open/close
[RiskBudgetManager] record_risk_taken/realized
[PortfolioAllocator] record_opened/closed
```

---

## 6. Dokumentasi Referensi

Semua spesifikasi detail ada di `docs/`:

| File | Isi |
|---|---|
| `crypto_ai_agent_structure.md` | Gambaran besar arsitektur & roadmap layer |
| `agent_core_docs.md` | Spesifikasi Core layer (config, event_bus, scheduler) |
| `data_intelligence_docs.md` | Spesifikasi Data + Intelligence layer |
| `strategy_portfolio_docs.md` | Spesifikasi Strategy + Portfolio layer |
| `risk_layer_docs.md` | Spesifikasi Risk layer |
| `trade_execution_docs.md` | Spesifikasi Trade + Execution layer |
| `exit_monitoring_sync_docs.md` | Spesifikasi Exit + Monitoring + Sync layer |
| `learning_llm_docs.md` | Spesifikasi Learning + LLM layer |
| `research_layer_docs.md` | Spesifikasi News/Research layer |
| `notification_utils_models_docs.md` | Spesifikasi Notification + Utils + Models |
| `tests_layer_docs.md` | Spesifikasi testing strategy |
| `automation_observability_docs.md` | CI/CD + observability |
| `runtime_layer_docs.md` | Runtime & deployment |

---

## 7. Prioritas Implementasi Selanjutnya (Roadmap)

Urutan yang direkomendasikan berdasarkan dependency:

1. **🔴 Risk Layer (AUDIT)** — File skeleton ada, tapi belum terintegrasi dengan Portfolio Layer yang baru. Pastikan `risk_manager.py` memanggil `CorrelationController.check()` dan `RiskBudgetManager.can_take_risk()`.

2. **🟡 Exit Layer** — Logika kapan menutup posisi (trailing stop, time-based exit, regime flip exit). Docs: `exit_monitoring_sync_docs.md`.

3. **🟡 Monitoring + Sync** — Health check, balance sync dari exchange, equity reconciliation. Docs: `exit_monitoring_sync_docs.md`.

4. **🔵 Learning Layer** — Mekanisme agent belajar dari kesalahan. Trade journal, model retraining trigger, strategy performance grading. Docs: `learning_llm_docs.md`.

5. **🔵 LLM Layer** — Filter sinyal via LLM (contoh: "Apakah berita terbaru mendukung sinyal BUY ini?"). Docs: `learning_llm_docs.md`.

6. **⚪ Notification** — Telegram/Discord alert. Docs: `notification_utils_models_docs.md`.

7. **⚪ News Layer** — Sentiment analysis dari berita kripto. Docs: `research_layer_docs.md`.

---

## 8. Cara Memulai Development

```bash
# Clone
git clone https://github.com/athayabismaj/crypto_ai_agent.git
cd crypto_ai_agent

# Virtual environment
python -m venv .venv
.venv\Scripts\activate   # Windows
# source .venv/bin/activate  # Linux/Mac

# Install dependencies
pip install -r requirements.txt

# Pre-commit hooks
pre-commit install

# Jalankan tests
pytest tests/ -v

# Type check
mypy runtime/agent/ --ignore-missing-imports
```

---

## 9. Catatan Untuk AI Agent Baru

Jika Anda adalah AI coding assistant yang baru membaca proyek ini:

1. **Baca `CONTEXT.md` ini terlebih dahulu** (Anda sedang membacanya ✓)
2. **Baca `docs/crypto_ai_agent_structure.md`** untuk roadmap global
3. **Baca dokumentasi layer yang relevan** di `docs/` sebelum coding
4. **Ikuti coding standards** di section 4.4 (type hints, ruff, black, mypy)
5. **Jangan ubah keputusan di section 4** tanpa diskusi dengan user
6. **Layer yang BELUM diimplementasi** hanya punya `__init__.py` — cek section 3
7. **Layer yang punya SKELETON** perlu audit kecocokan dengan komponen baru
8. **Selalu jalankan `git add -A ; git commit`** — pre-commit hooks akan jalan otomatis
9. **Commit message dalam Bahasa Inggris**, komentar kode boleh Bahasa Indonesia

> **Terakhir diperbarui**: 2026-03-30 oleh AI Agent session (Strategy + Portfolio Layer Phase 2+3 complete)
