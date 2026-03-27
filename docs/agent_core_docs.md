**crypto_ai_agent**

**Dokumentasi: Agent Core**

*main loop · config · modes · scheduler · event bus · safe mode · memory · logs*

Versi 1.0 \| Referensi: runtime_layer_docs.docx

  ------------------------------------------------

  ------------------------------------------------

**1. Overview --- Agent Core**

Agent Core adalah inti dari seluruh runtime. Ia tidak membuat keputusan trading, tidak menganalisis market, dan tidak mengeksekusi order. Perannya adalah:

> **•** Menginisialisasi semua layer dengan urutan yang benar dan aman.
>
> **•** Menjaga main event loop tetap berjalan, stabil, dan fault-tolerant.
>
> **•** Mendistribusikan event antar layer tanpa coupling langsung.
>
> **•** Menyimpan dan memulihkan state saat crash atau restart.
>
> **•** Menyediakan satu titik kontrol untuk halt dan shutdown darurat.

  -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------
  **POSISI DALAM SISTEM:** Agent Core adalah layer yang paling kritis. Bug di sini berdampak pada seluruh sistem. Semua perubahan pada file di core/ wajib melewati code review dan test coverage 100%.

  -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------

**1.1 Peta File & Tanggung Jawab**

  ------------------------------------------------------------------------------------------------------------------------
  **File**               **Tanggung Jawab**                               **Dipanggil Oleh**     **Bergantung Pada**
  ---------------------- ------------------------------------------------ ---------------------- -------------------------
  **main.py**            Entry point --- startup, main loop, shutdown     OS / Docker CMD        Semua layer

  **config.py**          Load & merge config dari .env + YAML             main.py                security/key_manager

  **config_schema.py**   Validasi config via Pydantic                     config.py              pydantic

  **modes.py**           Definisi mode PAPER/SHADOW/LIVE + perilaku       main.py, semua layer   ---

  **scheduler.py**       Task periodik: daily reset, retrain check, dll   main.py                asyncio

  **event_bus.py**       Pub/sub async antar layer                        Semua layer            asyncio.Queue

  **safe_mode.py**       Emergency stop & graceful shutdown               monitoring, main.py    execution, notification
  ------------------------------------------------------------------------------------------------------------------------

**2. main.py --- Entry Point & Main Loop**

**2.1 Startup Sequence --- Tahap Demi Tahap**

Urutan inisialisasi TIDAK BOLEH diubah. Setiap tahap bergantung pada tahap sebelumnya.

  ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------
  **Tahap**   **Aksi**                                                                                    **Gagal →**                                                  **Timeout**
  ----------- ------------------------------------------------------------------------------------------- ------------------------------------------------------------ -------------
  T-01        Setup logging (structured_logger) --- harus pertama agar semua error tercatat               Exit code 1 --- tidak ada informasi diagnosa                 ---

  T-02        Load config.py → validasi via AgentConfig (Pydantic)                                        Log error field yang gagal, exit code 1                      2s

  T-03        Inisialisasi KeyManager, validasi permissions exchange                                      Exit code 1 jika can_withdraw=True atau key invalid          5s

  T-04        Inisialisasi database connections (state.db, order_registry.db, dll)                        Exit code 1 --- tidak bisa lanjut tanpa persistent storage   3s

  T-05        Jalankan migration/migrate.py --- pastikan schema DB up-to-date                             Exit code 1 jika ada migration yang gagal                    30s

  T-06        Inisialisasi DataLayer --- koneksi REST, cek exchange status                                Exit code 1 jika exchange maintenance                        10s

  T-07        Load model ML dari agent/models/ --- validasi feature_names vs config                       Exit code 1 jika model tidak kompatibel                      5s

  T-08        Inisialisasi semua layer: intelligence, portfolio, risk, strategy, trade, execution, exit   Exit code 1 per layer yang gagal                             15s

  T-09        Jalankan RecoveryManager.restore() --- rekonsiliasi state setelah crash                     Masuk safe_mode jika ada unresolved discrepancy              60s

  T-10        Jalankan SyncManager.full_reconciliation() --- sinkronisasi balance + posisi                Alert + lanjut dengan cautious mode                          30s

  T-11        Start WebSocket streams (market data, user stream)                                          Retry 3x lalu exit code 1                                    20s

  T-12        Start Heartbeat & MonitoringSystem                                                          Alert, lanjut (non-fatal)                                    5s

  T-13        Start Scheduler (task periodik)                                                             Alert, lanjut (non-fatal)                                    2s

  T-14        Start main event loop --- mulai menerima candle                                             ---                                                          ---
  ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------

  -------------------------------------------------------------------------------------------------------------------------------------------------------------------------
  **TOTAL STARTUP TIME:** Target startup selesai dalam \< 3 menit. Jika lebih dari 3 menit, log warning. Jika lebih dari 5 menit, restart otomatis via health_restart.py.

  -------------------------------------------------------------------------------------------------------------------------------------------------------------------------

**2.2 Main Loop --- Pseudocode Lengkap**

+-------------------------------------------------------------------+
| async def main_loop(                                              |
|                                                                   |
| data_layer, intel_layer, portfolio, risk_mgr,                     |
|                                                                   |
| strategy, trade_mgr, executor, exit_mgr, sync, monitoring         |
|                                                                   |
| ):                                                                |
|                                                                   |
| async for candle in data_layer.stream():                          |
|                                                                   |
| \# Guard: cek halt flag sebelum apapun                            |
|                                                                   |
| if safe_mode.is_halted():                                         |
|                                                                   |
| continue                                                          |
|                                                                   |
| tick_start = utcnow()                                             |
|                                                                   |
| try:                                                              |
|                                                                   |
| \# ── STEP 1: Validasi data ────────────────────────              |
|                                                                   |
| if not data_layer.validator.is_valid(candle):                     |
|                                                                   |
| log.warning(\'Invalid candle, skip\', candle=candle)              |
|                                                                   |
| continue                                                          |
|                                                                   |
| \# ── STEP 2: Update intelligence ──────────────────              |
|                                                                   |
| market_state = intel_layer.update(candle)                         |
|                                                                   |
| \# ── STEP 3: Kelola posisi aktif (exit checks) ────              |
|                                                                   |
| for position in trade_mgr.get_open_positions():                   |
|                                                                   |
| exit_decision = exit_mgr.evaluate(position, market_state)         |
|                                                                   |
| if exit_decision.action != \'HOLD\':                              |
|                                                                   |
| await trade_mgr.close(position, exit_decision)                    |
|                                                                   |
| \# ── STEP 4: Sync periodik ────────────────────────              |
|                                                                   |
| if scheduler.is_due(\'sync\'):                                    |
|                                                                   |
| await sync.run_sync()                                             |
|                                                                   |
| risk_mgr.update_equity(portfolio.current_equity)                  |
|                                                                   |
| \# ── STEP 5: Generate & proses signal ─────────────              |
|                                                                   |
| signal = strategy.generate_signal(market_state)                   |
|                                                                   |
| if signal is None:                                                |
|                                                                   |
| continue                                                          |
|                                                                   |
| \# ── STEP 6: Risk evaluation ──────────────────────              |
|                                                                   |
| risk_result = risk_mgr.evaluate(                                  |
|                                                                   |
| TradeRequest.from_signal(signal),                                 |
|                                                                   |
| portfolio.get_state()                                             |
|                                                                   |
| )                                                                 |
|                                                                   |
| if not risk_result.is_approved:                                   |
|                                                                   |
| event_bus.publish(SIGNAL_BLOCKED, signal, risk_result)            |
|                                                                   |
| continue                                                          |
|                                                                   |
| \# ── STEP 7: Idempotency check ────────────────────              |
|                                                                   |
| idem = trade_mgr.idempotency.check_or_register(signal)            |
|                                                                   |
| if idem == DUPLICATE:                                             |
|                                                                   |
| continue                                                          |
|                                                                   |
| \# ── STEP 8: Execute ──────────────────────────────              |
|                                                                   |
| trade = await trade_mgr.open(signal, risk_result)                 |
|                                                                   |
| order_resp = await executor.place_order(trade.to_order_request()) |
|                                                                   |
| trade_mgr.confirm(trade, order_resp)                              |
|                                                                   |
| \# ── STEP 9: Post-trade ───────────────────────────              |
|                                                                   |
| portfolio.update(trade)                                           |
|                                                                   |
| event_bus.publish(TRADE_OPENED, trade)                            |
|                                                                   |
| except CircuitBreakerHalt:                                        |
|                                                                   |
| log.error(\'Circuit breaker triggered, halting\')                 |
|                                                                   |
| await safe_mode.emergency_stop(\'Circuit breaker\')               |
|                                                                   |
| break                                                             |
|                                                                   |
| except Exception as e:                                            |
|                                                                   |
| log.error(\'Unhandled tick error\', error=e)                      |
|                                                                   |
| monitoring.record_error(e)                                        |
|                                                                   |
| \# Lanjut ke candle berikutnya --- jangan crash loop              |
|                                                                   |
| finally:                                                          |
|                                                                   |
| tick_duration = (utcnow() - tick_start).total_seconds()           |
|                                                                   |
| metrics.observe(\'tick_duration_seconds\', tick_duration)         |
|                                                                   |
| if tick_duration \> MAX_TICK_DURATION:                            |
|                                                                   |
| log.warning(\'Slow tick\', duration=tick_duration)                |
+-------------------------------------------------------------------+

**2.3 Performance Budget Per Tick**

  -------------------------------------------------------------------------------------------------------------------------
  **Step**                                       **Budget Waktu**   **Aksi Jika Melebihi**
  ---------------------------------------------- ------------------ -------------------------------------------------------
  Validasi data (step 1)                         \< 5 ms            Log warning --- tidak blokir

  Update intelligence (step 2)                   \< 50 ms           Log warning --- gunakan state sebelumnya jika timeout

  Exit checks semua posisi (step 3)              \< 100 ms          Log warning --- posisi tetap di-check

  Signal generation + model inference (step 5)   \< 200 ms          Skip signal jika \> 500ms

  Risk evaluation (step 6)                       \< 30 ms           Harus cepat --- tidak ada I/O di sini

  Order execution (step 8)                       \< 2000 ms         Alert latency_guard, retry logic aktif

  Total tick (candle 1H)                         \< 3000 ms         Alert jika konsisten \> 3s
  -------------------------------------------------------------------------------------------------------------------------

  ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------
  **PENTING:** Main loop TIDAK BOLEH crash karena exception dari satu tick. Semua exception harus di-catch di level tick, di-log, dan loop melanjutkan ke candle berikutnya. Satu-satunya yang membolehkan loop berhenti adalah CircuitBreakerHalt dan SafeMode.halt.

  ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------

**3. config.py & config_schema.py --- Konfigurasi**

**3.1 Hierarki Konfigurasi**

Nilai config diambil dari beberapa sumber dengan prioritas dari atas ke bawah (sumber lebih atas = prioritas lebih tinggi):

> **1.** Environment variables (dari .env yang di-load via python-dotenv)
>
> **2.** File config/agent_config.yaml (nilai default yang bisa dioverride)
>
> **3.** Hardcoded defaults di dalam AgentConfig dataclass

  ----------------------------------------------------------------------------------------------------------------------------------------------
  **ATURAN:** JANGAN pernah hardcode nilai sensitif (API key, password) di dalam kode atau YAML. Semua secret WAJIB dari environment variable.

  ----------------------------------------------------------------------------------------------------------------------------------------------

**3.2 AgentConfig --- Pydantic Model Lengkap**

+-------------------------------------------------------------------+
| from pydantic import BaseModel, Field, validator                  |
|                                                                   |
| from typing import Literal                                        |
|                                                                   |
| class AgentConfig(BaseModel):                                     |
|                                                                   |
| \# ── MODE ────────────────────────────────────────────────       |
|                                                                   |
| mode: Literal\[\'paper\', \'shadow\', \'live\'\] = \'paper\'      |
|                                                                   |
| initial_equity: float = Field(10_000.0, gt=0)                     |
|                                                                   |
| \# ── TRADING UNIVERSE ────────────────────────────────────       |
|                                                                   |
| symbols: list\[str\] = \[\'BTCUSDT\'\]                            |
|                                                                   |
| timeframe: str = \'1h\'                                           |
|                                                                   |
| exchange: str = \'binance\'                                       |
|                                                                   |
| market_type: str = \'spot\' \# spot \| futures                    |
|                                                                   |
| \# ── RISK ────────────────────────────────────────────────       |
|                                                                   |
| risk_per_trade_pct: float = Field(0.01, ge=0.001, le=0.05)        |
|                                                                   |
| max_daily_loss_pct: float = Field(0.05, ge=0.01, le=0.15)         |
|                                                                   |
| max_drawdown_pct: float = Field(0.10, ge=0.05, le=0.30)           |
|                                                                   |
| max_consecutive_loss: int = Field(5, ge=3, le=15)                 |
|                                                                   |
| max_open_positions: int = Field(5, ge=1, le=20)                   |
|                                                                   |
| global_max_leverage: int = Field(3, ge=1, le=20)                  |
|                                                                   |
| \# ── SIZING ──────────────────────────────────────────────       |
|                                                                   |
| sizing_method: str = \'fixed_fractional\'                         |
|                                                                   |
| max_position_pct: float = Field(0.10, ge=0.01, le=0.30)           |
|                                                                   |
| min_notional_usd: float = Field(10.0, ge=5.0)                     |
|                                                                   |
| \# ── EXECUTION ───────────────────────────────────────────       |
|                                                                   |
| execution_delay_ms: int = Field(100, ge=0, le=5000)               |
|                                                                   |
| order_timeout_s: int = Field(30, ge=5, le=300)                    |
|                                                                   |
| max_order_retry: int = Field(3, ge=1, le=10)                      |
|                                                                   |
| slippage_tolerance: float = Field(0.005, ge=0, le=0.02)           |
|                                                                   |
| \# ── STRATEGY ────────────────────────────────────────────       |
|                                                                   |
| strategy_id: str = \'spot_strategy_v1\'                           |
|                                                                   |
| min_signal_confidence:float = Field(0.60, ge=0.3, le=1.0)         |
|                                                                   |
| signal_cooldown: int = Field(3, ge=1, le=20)                      |
|                                                                   |
| \# ── EXIT ────────────────────────────────────────────────       |
|                                                                   |
| default_sl_pct: float = Field(0.02, ge=0.005, le=0.10)            |
|                                                                   |
| default_tp_ratio: float = Field(2.0, ge=1.0, le=10.0)             |
|                                                                   |
| trailing_method: str = \'atr\' \# atr \| percentage \| chandelier |
|                                                                   |
| trail_atr_mult: float = Field(2.0, ge=1.0, le=5.0)                |
|                                                                   |
| breakeven_trigger_r: float = Field(1.0, ge=0.5, le=3.0)           |
|                                                                   |
| max_hold_candles: int = Field(48, ge=10, le=200)                  |
|                                                                   |
| \# ── SYNC ────────────────────────────────────────────────       |
|                                                                   |
| balance_sync_interval_s: int = Field(60, ge=30, le=300)           |
|                                                                   |
| order_sync_interval_s: int = Field(30, ge=15, le=120)             |
|                                                                   |
| position_sync_interval_s: int = Field(60, ge=30, le=300)          |
|                                                                   |
| \# ── LLM (opsional) ──────────────────────────────────────       |
|                                                                   |
| llm_enabled: bool = False                                         |
|                                                                   |
| llm_model: str = \'claude-haiku-4-5-20251001\'                    |
|                                                                   |
| llm_daily_budget_usd: float = Field(1.0, ge=0.0, le=50.0)         |
|                                                                   |
| \# ── NOTIFICATION ────────────────────────────────────────       |
|                                                                   |
| notify_trade_open: bool = True                                    |
|                                                                   |
| notify_trade_close: bool = True                                   |
|                                                                   |
| notify_daily_summary: bool = True                                 |
|                                                                   |
| notify_circuit_break: bool = True                                 |
|                                                                   |
| \# ── VALIDATORS ──────────────────────────────────────────       |
|                                                                   |
| \@validator(\'timeframe\')                                        |
|                                                                   |
| def validate_tf(cls, v):                                          |
|                                                                   |
| valid = \[\'1m\',\'5m\',\'15m\',\'30m\',\'1h\',\'4h\',\'1d\'\]    |
|                                                                   |
| if v not in valid:                                                |
|                                                                   |
| raise ValueError(f\'timeframe harus salah satu dari {valid}\')    |
|                                                                   |
| return v                                                          |
|                                                                   |
| \@validator(\'market_type\')                                      |
|                                                                   |
| def validate_market(cls, v):                                      |
|                                                                   |
| if v not in (\'spot\', \'futures\'):                              |
|                                                                   |
| raise ValueError(\'market_type harus spot atau futures\')         |
|                                                                   |
| return v                                                          |
|                                                                   |
| \@validator(\'global_max_leverage\')                              |
|                                                                   |
| def leverage_spot_check(cls, v, values):                          |
|                                                                   |
| if values.get(\'market_type\') == \'spot\' and v \> 1:            |
|                                                                   |
| raise ValueError(\'Spot trading tidak mendukung leverage \> 1\')  |
|                                                                   |
| return v                                                          |
+-------------------------------------------------------------------+

**3.3 agent_config.yaml --- Template**

+--------------------------------------------------------------+
| \# config/agent_config.yaml                                  |
|                                                              |
| \# Nilai di sini dapat di-override oleh environment variable |
|                                                              |
| mode: paper                                                  |
|                                                              |
| initial_equity: 10000.0                                      |
|                                                              |
| symbols:                                                     |
|                                                              |
| \- BTCUSDT                                                   |
|                                                              |
| \- ETHUSDT                                                   |
|                                                              |
| timeframe: 1h                                                |
|                                                              |
| exchange: binance                                            |
|                                                              |
| market_type: spot                                            |
|                                                              |
| \# Risk                                                      |
|                                                              |
| risk_per_trade_pct: 0.01                                     |
|                                                              |
| max_daily_loss_pct: 0.05                                     |
|                                                              |
| max_drawdown_pct: 0.10                                       |
|                                                              |
| max_open_positions: 5                                        |
|                                                              |
| \# Sizing                                                    |
|                                                              |
| sizing_method: fixed_fractional                              |
|                                                              |
| \# Exit                                                      |
|                                                              |
| trailing_method: atr                                         |
|                                                              |
| trail_atr_mult: 2.0                                          |
|                                                              |
| \# LLM                                                       |
|                                                              |
| llm_enabled: false                                           |
+--------------------------------------------------------------+

**3.4 Config Change Log --- Wajib Diisi**

  -----------------------------------------------------------------------------------------------------------
  **Tanggal**   **Field**            **Nilai Lama**   **Nilai Baru**   **Alasan**                   **PIC**
  ------------- -------------------- ---------------- ---------------- ---------------------------- ---------
  YYYY-MM-DD    risk_per_trade_pct   0.01             0.015            Backtest OOS Sharpe \> 1.5   Dev

  YYYY-MM-DD    max_open_positions   5                7                Tambah simbol baru           Quant
  -----------------------------------------------------------------------------------------------------------

**4. modes.py --- Mode Operasi**

**4.1 AgentMode Enum & Perilaku**

+-----------------------------------------------------------------------+
| from enum import Enum                                                 |
|                                                                       |
| class AgentMode(Enum):                                                |
|                                                                       |
| PAPER = \'paper\'                                                     |
|                                                                       |
| SHADOW = \'shadow\'                                                   |
|                                                                       |
| LIVE = \'live\'                                                       |
|                                                                       |
| class ModeConfig:                                                     |
|                                                                       |
| \"\"\"Mengontrol perilaku berbeda per mode.\"\"\"                     |
|                                                                       |
| \@staticmethod                                                        |
|                                                                       |
| def should_send_order(mode: AgentMode) -\> bool:                      |
|                                                                       |
| return mode in (AgentMode.SHADOW, AgentMode.LIVE)                     |
|                                                                       |
| \@staticmethod                                                        |
|                                                                       |
| def use_real_money(mode: AgentMode) -\> bool:                         |
|                                                                       |
| return mode == AgentMode.LIVE                                         |
|                                                                       |
| \@staticmethod                                                        |
|                                                                       |
| def use_testnet(mode: AgentMode) -\> bool:                            |
|                                                                       |
| return mode == AgentMode.SHADOW                                       |
|                                                                       |
| \@staticmethod                                                        |
|                                                                       |
| def block_on_risk_violation(mode: AgentMode) -\> bool:                |
|                                                                       |
| \"\"\"Paper mode: log tapi tidak block. Live mode: block keras.\"\"\" |
|                                                                       |
| return mode == AgentMode.LIVE                                         |
|                                                                       |
| \@staticmethod                                                        |
|                                                                       |
| def require_sl(mode: AgentMode) -\> bool:                             |
|                                                                       |
| \"\"\"SL wajib di live. Optional di paper (untuk eksperimen).\"\"\"   |
|                                                                       |
| return mode == AgentMode.LIVE                                         |
+-----------------------------------------------------------------------+

**4.2 Perbedaan Perilaku Per Mode --- Detail**

  -------------------------------------------------------------------------------------
  **Behavior**              **PAPER**            **SHADOW**         **LIVE**
  ------------------------- -------------------- ------------------ -------------------
  Kirim order ke exchange   **Tidak**            **Ya (testnet)**   **Ya (mainnet)**

  Gunakan uang nyata        Tidak                Tidak              **Ya**

  Risk block keras          Log saja             Log saja           Block keras

  SL wajib                  Tidak                Tidak              **Ya**

  Fill simulation           Internal simulator   Exchange testnet   Exchange mainnet

  Equity tracking           Simulated            Simulated          Real balance

  Circuit breaker halt      Simulated            Aktif              Aktif + hard stop

  Idempotency enforcement   Aktif (test)         Aktif              Aktif (strict)

  Notifikasi Telegram       Optional             Ya                 Ya (all events)
  -------------------------------------------------------------------------------------

**4.3 Proteksi Transisi Mode**

+------------------------------------------------------------------+
| class ModeTransitionGuard:                                       |
|                                                                  |
| \"\"\"                                                           |
|                                                                  |
| Validasi sebelum mode bisa diubah.                               |
|                                                                  |
| Dipanggil dari gateway/routes.py saat ada request ganti mode.    |
|                                                                  |
| \"\"\"                                                           |
|                                                                  |
| def validate_transition(                                         |
|                                                                  |
| self,                                                            |
|                                                                  |
| from_mode: AgentMode,                                            |
|                                                                  |
| to_mode: AgentMode,                                              |
|                                                                  |
| current_state: SystemState                                       |
|                                                                  |
| ) -\> TransitionResult:                                          |
|                                                                  |
| \# Aturan transisi yang diizinkan:                               |
|                                                                  |
| ALLOWED = {                                                      |
|                                                                  |
| AgentMode.PAPER: \[AgentMode.SHADOW\],                           |
|                                                                  |
| AgentMode.SHADOW: \[AgentMode.LIVE, AgentMode.PAPER\],           |
|                                                                  |
| AgentMode.LIVE: \[AgentMode.SHADOW\], \# tidak langsung ke PAPER |
|                                                                  |
| }                                                                |
|                                                                  |
| if to_mode not in ALLOWED\[from_mode\]:                          |
|                                                                  |
| return TransitionResult.FORBIDDEN                                |
|                                                                  |
| \# Syarat wajib sebelum transisi:                                |
|                                                                  |
| if current_state.open_positions_count \> 0:                      |
|                                                                  |
| return TransitionResult.HAS_OPEN_POSITIONS                       |
|                                                                  |
| if current_state.pending_orders_count \> 0:                      |
|                                                                  |
| return TransitionResult.HAS_PENDING_ORDERS                       |
|                                                                  |
| return TransitionResult.ALLOWED                                  |
+------------------------------------------------------------------+

**5. scheduler.py --- Task Periodik**

Mengelola semua task yang berjalan secara berkala. Tidak menggunakan cron eksternal --- semua dijadwalkan dalam asyncio event loop yang sama.

**5.1 Daftar Task Terjadwal**

  ----------------------------------------------------------------------------------------------------------------------------------
  **Task ID**        **Interval**      **Fungsi**                                                    **Failure Action**
  ------------------ ----------------- ------------------------------------------------------------- -------------------------------
  daily_reset        UTC 00:00         risk_mgr.reset_daily_stats(), circuit_breaker.reset_daily()   Alert + retry besok

  balance_sync       60 detik          sync.balance_sync.run()                                       Log warning, lanjut

  order_sync         30 detik          sync.order_sync.run()                                         Log warning, lanjut

  position_sync      60 detik          sync.position_sync.run()                                      Alert + log

  health_check       30 detik          monitoring.health_check.run()                                 Alert jika komponen unhealthy

  drift_check        Harian 06:00      learning.drift_detection.check()                              Alert jika PSI \> 0.2

  strategy_eval      Harian 07:00      learning.strategy_killer.evaluate_all()                       Log hasil, disable jika perlu

  daily_reconcile    UTC 00:30         sync.reconciliation.run_daily()                               Alert jika ada discrepancy

  daily_summary      UTC 23:55         notifier.send_daily_summary()                                 Log error, lanjut

  llm_budget_reset   UTC 00:00         llm_budget.reset_daily()                                      Log warning

  db_vacuum          Mingguan Minggu   VACUUM semua database SQLite                                  Alert, coba lagi minggu depan

  log_rotation       Harian 01:00      Compress & archive log \> 7 hari                              Alert disk space
  ----------------------------------------------------------------------------------------------------------------------------------

**5.2 Interface Scheduler**

+---------------------------------------------------------------------------------+
| class Scheduler:                                                                |
|                                                                                 |
| def \_\_init\_\_(self, config: AgentConfig):                                    |
|                                                                                 |
| self.\_tasks: dict\[str, ScheduledTask\] = {}                                   |
|                                                                                 |
| self.\_loop = asyncio.get_event_loop()                                          |
|                                                                                 |
| def register(                                                                   |
|                                                                                 |
| self,                                                                           |
|                                                                                 |
| task_id: str,                                                                   |
|                                                                                 |
| coro: Coroutine,                                                                |
|                                                                                 |
| interval: timedelta \| CronSchedule,                                            |
|                                                                                 |
| timeout: int = 60,                                                              |
|                                                                                 |
| on_error: Callable = None                                                       |
|                                                                                 |
| ) -\> None:                                                                     |
|                                                                                 |
| \"\"\"Register task ke scheduler. Dipanggil di main.py saat startup.\"\"\"      |
|                                                                                 |
| def is_due(self, task_id: str) -\> bool:                                        |
|                                                                                 |
| \"\"\"                                                                          |
|                                                                                 |
| Cek apakah task sudah waktunya jalan.                                           |
|                                                                                 |
| Digunakan di main_loop untuk task yang di-trigger per tick.                     |
|                                                                                 |
| Contoh: if scheduler.is_due(\'balance_sync\'): await sync.balance_sync.run()    |
|                                                                                 |
| \"\"\"                                                                          |
|                                                                                 |
| async def start(self) -\> None:                                                 |
|                                                                                 |
| \"\"\"Start semua background task. Dipanggil setelah semua layer siap.\"\"\"    |
|                                                                                 |
| async def stop(self) -\> None:                                                  |
|                                                                                 |
| \"\"\"Graceful stop semua task. Tunggu task yang sedang berjalan selesai.\"\"\" |
+---------------------------------------------------------------------------------+

**5.3 CronSchedule --- Format**

+---------------------------------------------------------------------------+
| \# Format: CronSchedule(hour, minute, weekday=None)                       |
|                                                                           |
| \# weekday: 0=Senin, 6=Minggu, None=setiap hari                           |
|                                                                           |
| daily_reset = CronSchedule(hour=0, minute=0) \# UTC 00:00 setiap hari     |
|                                                                           |
| daily_summary = CronSchedule(hour=23, minute=55) \# UTC 23:55 setiap hari |
|                                                                           |
| weekly_vacuum = CronSchedule(hour=2, minute=0, weekday=6) \# Minggu 02:00 |
|                                                                           |
| \# Interval biasa (timedelta)                                             |
|                                                                           |
| balance_sync = timedelta(seconds=60)                                      |
|                                                                           |
| order_sync = timedelta(seconds=30)                                        |
+---------------------------------------------------------------------------+

**6. event_bus.py --- Komunikasi Antar Layer**

Event bus memungkinkan layer berkomunikasi tanpa saling import langsung. Publisher tidak tahu siapa yang mendengarkan. Subscriber tidak tahu siapa yang mempublish.

**6.1 Implementasi**

+---------------------------------------------------------------------------------------+
| import asyncio                                                                        |
|                                                                                       |
| from dataclasses import dataclass                                                     |
|                                                                                       |
| from enum import Enum                                                                 |
|                                                                                       |
| from typing import Callable, Coroutine                                                |
|                                                                                       |
| class EventType(Enum):                                                                |
|                                                                                       |
| \# Data events                                                                        |
|                                                                                       |
| CANDLE_READY = \'candle_ready\'                                                       |
|                                                                                       |
| ORDERBOOK_UPDATE = \'orderbook_update\'                                               |
|                                                                                       |
| TICKER_UPDATE = \'ticker_update\'                                                     |
|                                                                                       |
| \# Intelligence events                                                                |
|                                                                                       |
| REGIME_CHANGED = \'regime_changed\'                                                   |
|                                                                                       |
| VOLATILITY_SPIKE = \'volatility_spike\'                                               |
|                                                                                       |
| \# Strategy events                                                                    |
|                                                                                       |
| SIGNAL_GENERATED = \'signal_generated\'                                               |
|                                                                                       |
| SIGNAL_BLOCKED = \'signal_blocked\'                                                   |
|                                                                                       |
| \# Trade events                                                                       |
|                                                                                       |
| TRADE_OPENED = \'trade_opened\'                                                       |
|                                                                                       |
| TRADE_CLOSED = \'trade_closed\'                                                       |
|                                                                                       |
| TRADE_UPDATED = \'trade_updated\' \# SL/TP diubah                                     |
|                                                                                       |
| \# Risk events                                                                        |
|                                                                                       |
| CIRCUIT_BREAKER = \'circuit_breaker\'                                                 |
|                                                                                       |
| RISK_VIOLATION = \'risk_violation\'                                                   |
|                                                                                       |
| EQUITY_UPDATE = \'equity_update\'                                                     |
|                                                                                       |
| \# System events                                                                      |
|                                                                                       |
| SYSTEM_ERROR = \'system_error\'                                                       |
|                                                                                       |
| HEALTH_STATUS = \'health_status\'                                                     |
|                                                                                       |
| SAFE_MODE_TRIGGERED = \'safe_mode_triggered\'                                         |
|                                                                                       |
| \@dataclass                                                                           |
|                                                                                       |
| class Event:                                                                          |
|                                                                                       |
| event_type: EventType                                                                 |
|                                                                                       |
| payload: any                                                                          |
|                                                                                       |
| timestamp: datetime                                                                   |
|                                                                                       |
| source: str \# nama layer yang mempublish                                             |
|                                                                                       |
| class EventBus:                                                                       |
|                                                                                       |
| def \_\_init\_\_(self):                                                               |
|                                                                                       |
| self.\_subscribers: dict\[EventType, list\[Callable\]\] = defaultdict(list)           |
|                                                                                       |
| self.\_queue: asyncio.Queue\[Event\] = asyncio.Queue(maxsize=1000)                    |
|                                                                                       |
| def subscribe(self, event_type: EventType, handler: Callable) -\> None:               |
|                                                                                       |
| \"\"\"Register handler untuk event type tertentu.\"\"\"                               |
|                                                                                       |
| self.\_subscribers\[event_type\].append(handler)                                      |
|                                                                                       |
| def publish(self, event_type: EventType, payload: any, source: str) -\> None:         |
|                                                                                       |
| \"\"\"Non-blocking publish --- masukkan ke queue.\"\"\"                               |
|                                                                                       |
| event = Event(event_type, payload, utcnow(), source)                                  |
|                                                                                       |
| try:                                                                                  |
|                                                                                       |
| self.\_queue.put_nowait(event)                                                        |
|                                                                                       |
| except asyncio.QueueFull:                                                             |
|                                                                                       |
| log.error(\'EventBus queue full --- event dropped\', event_type=event_type)           |
|                                                                                       |
| async def dispatch_loop(self) -\> None:                                               |
|                                                                                       |
| \"\"\"Background loop yang mengambil event dari queue dan dispatch ke handlers.\"\"\" |
|                                                                                       |
| async for event in self.\_queue:                                                      |
|                                                                                       |
| for handler in self.\_subscribers\[event.event_type\]:                                |
|                                                                                       |
| try:                                                                                  |
|                                                                                       |
| await handler(event)                                                                  |
|                                                                                       |
| except Exception as e:                                                                |
|                                                                                       |
| log.error(\'Handler error\', handler=handler, error=e)                                |
+---------------------------------------------------------------------------------------+

**6.2 Event Routing Table**

  ------------------------------------------------------------------------------------------------
  **EventType**         **Publisher**     **Subscriber(s)**                     **Payload Type**
  --------------------- ----------------- ------------------------------------- ------------------
  CANDLE_READY          data_layer        intel, strategy, exit_mgr             Candle

  REGIME_CHANGED        intel_layer       strategy, notification                MarketRegime

  SIGNAL_GENERATED      strategy_layer    risk_layer, trade_layer, llm_layer    Signal

  SIGNAL_BLOCKED        risk_layer        learning_layer, notification          BlockedSignal

  TRADE_OPENED          trade_layer       exit_layer, portfolio, monitoring     Trade

  TRADE_CLOSED          exit_layer        trade_layer, portfolio, learning      TradeResult

  CIRCUIT_BREAKER       risk_layer        main_loop, monitoring, notification   CircuitState

  EQUITY_UPDATE         portfolio_layer   risk_layer                            float

  SYSTEM_ERROR          Semua layer       monitoring, notification              ErrorReport

  SAFE_MODE_TRIGGERED   safe_mode         main_loop, notification               SafeModeEvent
  ------------------------------------------------------------------------------------------------

  ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------
  **ATURAN:** Handler event TIDAK BOLEH melakukan operasi blocking (I/O langsung, query DB berat, request HTTP). Handler hanya boleh: update state di memory, push ke queue lain, atau trigger coroutine ringan.

  ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------

**7. safe_mode.py --- Emergency Stop**

SafeMode adalah mekanisme shutdown darurat yang memastikan sistem berhenti dengan aman tanpa meninggalkan posisi tanpa pantauan.

**7.1 Trigger Conditions**

  ------------------------------------------------------------------------------------------------------------------------
  **Trigger**                                 **Source**     **Auto atau Manual**   **Close Posisi?**
  ------------------------------------------- -------------- ---------------------- --------------------------------------
  Circuit breaker HALTED                      risk_layer     Auto                   Tidak (posisi tetap, SL masih aktif)

  Exchange maintenance terdeteksi             data_layer     Auto                   Tidak

  Unresolved discrepancy di recovery          trade_layer    Auto                   Tidak

  API key tidak valid / permissions berubah   security       Auto                   Tidak

  WebSocket mati \> 5 menit tanpa reconnect   data_layer     Auto                   Tidak

  Memory usage \> 90% threshold               monitoring     Auto                   Tidak

  Manual halt via gateway endpoint            gateway/api    Manual                 Optional (parameter)

  Manual halt via Telegram command            notification   Manual                 Optional (parameter)
  ------------------------------------------------------------------------------------------------------------------------

**7.2 Emergency Stop --- Implementasi**

+---------------------------------------------------------------+
| class SafeMode:                                               |
|                                                               |
| \_halt_flag: asyncio.Event = asyncio.Event()                  |
|                                                               |
| def is_halted(self) -\> bool:                                 |
|                                                               |
| return self.\_halt_flag.is_set()                              |
|                                                               |
| async def emergency_stop(                                     |
|                                                               |
| self,                                                         |
|                                                               |
| reason: str,                                                  |
|                                                               |
| close_positions: bool = False,                                |
|                                                               |
| exit_code: int = 2                                            |
|                                                               |
| ) -\> None:                                                   |
|                                                               |
| \"\"\"                                                        |
|                                                               |
| URUTAN SHUTDOWN DARURAT --- JANGAN UBAH URUTANNYA:            |
|                                                               |
| \"\"\"                                                        |
|                                                               |
| \# Step 1: Set halt flag --- main_loop.tick() langsung return |
|                                                               |
| self.\_halt_flag.set()                                        |
|                                                               |
| log.critical(\'SAFE MODE TRIGGERED\', reason=reason)          |
|                                                               |
| \# Step 2: Publish event ke semua subscriber                  |
|                                                               |
| event_bus.publish(SAFE_MODE_TRIGGERED, reason, \'safe_mode\') |
|                                                               |
| \# Step 3: Opsional --- close semua posisi                    |
|                                                               |
| if close_positions:                                           |
|                                                               |
| await self.\_close_all_positions()                            |
|                                                               |
| \# Step 4: Cancel semua pending orders                        |
|                                                               |
| await self.\_cancel_all_pending_orders()                      |
|                                                               |
| \# Step 5: Simpan state snapshot                              |
|                                                               |
| await self.\_save_state_snapshot(reason)                      |
|                                                               |
| \# Step 6: Kirim notifikasi                                   |
|                                                               |
| await notifier.send_emergency_alert(                          |
|                                                               |
| reason=reason,                                                |
|                                                               |
| equity=portfolio.current_equity,                              |
|                                                               |
| open_positions=trade_mgr.count_open_positions()               |
|                                                               |
| )                                                             |
|                                                               |
| \# Step 7: Stop scheduler tasks                               |
|                                                               |
| await scheduler.stop()                                        |
|                                                               |
| \# Step 8: Close DB connections gracefully                    |
|                                                               |
| await db_manager.close_all()                                  |
|                                                               |
| \# Step 9: Exit                                               |
|                                                               |
| log.info(\'Safe mode complete, exiting\', code=exit_code)     |
|                                                               |
| sys.exit(exit_code)                                           |
|                                                               |
| async def graceful_shutdown(self) -\> None:                   |
|                                                               |
| \"\"\"                                                        |
|                                                               |
| Shutdown normal (maintenance, update):                        |
|                                                               |
| 1\. Hentikan terima signal baru                               |
|                                                               |
| 2\. Tunggu tick yang sedang berjalan selesai (timeout 30s)    |
|                                                               |
| 3\. Stop scheduler                                            |
|                                                               |
| 4\. Simpan state                                              |
|                                                               |
| 5\. Exit code 0                                               |
|                                                               |
| \"\"\"                                                        |
+---------------------------------------------------------------+

**7.3 State Snapshot --- Format**

+--------------------------------------------------------------------------------+
| \# Disimpan ke agent/memory/snapshots/YYYYMMDD_HHMMSS_reason.json              |
|                                                                                |
| {                                                                              |
|                                                                                |
| \"snapshot_id\": \"20241115_083000_circuit_breaker\",                          |
|                                                                                |
| \"reason\": \"Circuit breaker HALTED --- daily loss 5.2%\",                    |
|                                                                                |
| \"timestamp\": \"2024-11-15T08:30:00Z\",                                       |
|                                                                                |
| \"mode\": \"live\",                                                            |
|                                                                                |
| \"equity\": 9480.0,                                                            |
|                                                                                |
| \"daily_pnl\": -520.0,                                                         |
|                                                                                |
| \"open_positions\": \[                                                         |
|                                                                                |
| {\"symbol\": \"BTCUSDT\", \"side\": \"BUY\", \"qty\": 0.001, \"entry\": 50000} |
|                                                                                |
| \],                                                                            |
|                                                                                |
| \"pending_orders\": \[\],                                                      |
|                                                                                |
| \"circuit_state\": \"HALTED\",                                                 |
|                                                                                |
| \"halt_reasons\": \[\"daily_loss_pct 5.2% \> max 5.0%\"\]                      |
|                                                                                |
| }                                                                              |
+--------------------------------------------------------------------------------+

**8. memory/ --- Persistent Storage**

Semua state disimpan ke SQLite database. Setiap database punya schema yang di-manage oleh migration/. Tidak ada direct SQL di luar layer yang ditentukan.

**8.1 Database Map**

  -------------------------------------------------------------------------------------------------------------------------------------------
  **File**                 **Isi**                                **Dibaca Oleh**           **Ditulis Oleh**            **Ukuran Estimasi**
  ------------------------ -------------------------------------- ------------------------- --------------------------- ---------------------
  **state.db**             Posisi aktif, status order terbuka     trade_layer, exit_layer   trade_layer, sync           \< 10 MB

  **experience.db**        Histori semua trade yang sudah close   learning_layer, audit     trade_layer                 \< 100 MB / tahun

  **performance.db**       Statistik performa per strategi        strategy_killer, risk     trade_layer (after close)   \< 5 MB

  **order_registry.db**    Idempotency store --- semua order ID   idempotency.py            idempotency.py              \< 20 MB / tahun

  **execution_cache.db**   Cache retry tracking per order         execution_layer           execution_layer             \< 5 MB
  -------------------------------------------------------------------------------------------------------------------------------------------

**8.2 Schema --- state.db**

+-------------------------------------------------------+
| \-- Tabel utama posisi aktif                          |
|                                                       |
| CREATE TABLE positions (                              |
|                                                       |
| trade_id TEXT PRIMARY KEY,                            |
|                                                       |
| client_order_id TEXT UNIQUE NOT NULL,                 |
|                                                       |
| exchange_order_id TEXT,                               |
|                                                       |
| symbol TEXT NOT NULL,                                 |
|                                                       |
| side TEXT NOT NULL, \-- BUY \| SELL                   |
|                                                       |
| order_type TEXT NOT NULL,                             |
|                                                       |
| requested_qty REAL NOT NULL,                          |
|                                                       |
| filled_qty REAL DEFAULT 0,                            |
|                                                       |
| avg_fill_price REAL DEFAULT 0,                        |
|                                                       |
| sl_price REAL,                                        |
|                                                       |
| tp_price REAL,                                        |
|                                                       |
| risk_amount_usd REAL,                                 |
|                                                       |
| status TEXT DEFAULT \'pending\',                      |
|                                                       |
| strategy_id TEXT,                                     |
|                                                       |
| mode TEXT NOT NULL,                                   |
|                                                       |
| created_at TEXT NOT NULL, \-- ISO 8601 UTC            |
|                                                       |
| opened_at TEXT,                                       |
|                                                       |
| updated_at TEXT                                       |
|                                                       |
| );                                                    |
|                                                       |
| \-- Audit trail --- APPEND ONLY, tidak pernah dihapus |
|                                                       |
| CREATE TABLE audit_events (                           |
|                                                       |
| event_id TEXT PRIMARY KEY,                            |
|                                                       |
| trade_id TEXT NOT NULL,                               |
|                                                       |
| event_type TEXT NOT NULL,                             |
|                                                       |
| timestamp TEXT NOT NULL,                              |
|                                                       |
| actor TEXT NOT NULL,                                  |
|                                                       |
| details TEXT NOT NULL, \-- JSON string                |
|                                                       |
| equity_snapshot REAL                                  |
|                                                       |
| );                                                    |
|                                                       |
| \-- State snapshot saat safe mode                     |
|                                                       |
| CREATE TABLE snapshots (                              |
|                                                       |
| snapshot_id TEXT PRIMARY KEY,                         |
|                                                       |
| reason TEXT NOT NULL,                                 |
|                                                       |
| timestamp TEXT NOT NULL,                              |
|                                                       |
| payload TEXT NOT NULL \-- JSON string                 |
|                                                       |
| );                                                    |
+-------------------------------------------------------+

**8.3 Schema --- order_registry.db**

+-------------------------------------------------------------------------------+
| CREATE TABLE order_registry (                                                 |
|                                                                               |
| client_order_id TEXT PRIMARY KEY,                                             |
|                                                                               |
| strategy_id TEXT NOT NULL,                                                    |
|                                                                               |
| symbol TEXT NOT NULL,                                                         |
|                                                                               |
| status TEXT NOT NULL, \-- pending\|confirmed\|failed\|duplicate               |
|                                                                               |
| exchange_order_id TEXT,                                                       |
|                                                                               |
| created_at TEXT NOT NULL,                                                     |
|                                                                               |
| confirmed_at TEXT,                                                            |
|                                                                               |
| failed_at TEXT,                                                               |
|                                                                               |
| failure_reason TEXT,                                                          |
|                                                                               |
| retry_count INTEGER DEFAULT 0                                                 |
|                                                                               |
| );                                                                            |
|                                                                               |
| \-- Index untuk query cepat                                                   |
|                                                                               |
| CREATE INDEX idx_status ON order_registry(status);                            |
|                                                                               |
| CREATE INDEX idx_created ON order_registry(created_at);                       |
|                                                                               |
| \-- Pembersihan otomatis: hapus record \> 30 hari yang sudah confirmed/failed |
|                                                                               |
| \-- Dilakukan oleh scheduler.db_vacuum task                                   |
+-------------------------------------------------------------------------------+

**8.4 Schema --- experience.db**

+-----------------------------------------------------------------------+
| CREATE TABLE closed_trades (                                          |
|                                                                       |
| trade_id TEXT PRIMARY KEY,                                            |
|                                                                       |
| client_order_id TEXT UNIQUE NOT NULL,                                 |
|                                                                       |
| symbol TEXT NOT NULL,                                                 |
|                                                                       |
| side TEXT NOT NULL,                                                   |
|                                                                       |
| entry_price REAL NOT NULL,                                            |
|                                                                       |
| exit_price REAL NOT NULL,                                             |
|                                                                       |
| qty REAL NOT NULL,                                                    |
|                                                                       |
| pnl_usd REAL NOT NULL,                                                |
|                                                                       |
| pnl_pct REAL NOT NULL,                                                |
|                                                                       |
| commission_usd REAL NOT NULL,                                         |
|                                                                       |
| strategy_id TEXT NOT NULL,                                            |
|                                                                       |
| exit_reason TEXT NOT NULL, \-- SL\|TP\|TRAIL\|SIGNAL\|TIMEOUT\|MANUAL |
|                                                                       |
| hold_candles INTEGER,                                                 |
|                                                                       |
| regime_at_entry TEXT,                                                 |
|                                                                       |
| vol_regime_entry TEXT,                                                |
|                                                                       |
| mode TEXT NOT NULL,                                                   |
|                                                                       |
| opened_at TEXT NOT NULL,                                              |
|                                                                       |
| closed_at TEXT NOT NULL                                               |
|                                                                       |
| );                                                                    |
|                                                                       |
| \-- Untuk query performa strategi                                     |
|                                                                       |
| CREATE INDEX idx_strategy ON closed_trades(strategy_id, closed_at);   |
|                                                                       |
| CREATE INDEX idx_symbol ON closed_trades(symbol, closed_at);          |
+-----------------------------------------------------------------------+

**8.5 Schema --- performance.db**

+-------------------------------------------------------------------+
| CREATE TABLE strategy_stats (                                     |
|                                                                   |
| strategy_id TEXT NOT NULL,                                        |
|                                                                   |
| period TEXT NOT NULL, \-- \'7d\' \| \'30d\' \| \'90d\' \| \'all\' |
|                                                                   |
| updated_at TEXT NOT NULL,                                         |
|                                                                   |
| total_trades INTEGER,                                             |
|                                                                   |
| winning_trades INTEGER,                                           |
|                                                                   |
| win_rate REAL,                                                    |
|                                                                   |
| avg_win_usd REAL,                                                 |
|                                                                   |
| avg_loss_usd REAL,                                                |
|                                                                   |
| profit_factor REAL,                                               |
|                                                                   |
| sharpe_ratio REAL,                                                |
|                                                                   |
| max_drawdown_pct REAL,                                            |
|                                                                   |
| avg_hold_candles REAL,                                            |
|                                                                   |
| total_pnl_usd REAL,                                               |
|                                                                   |
| PRIMARY KEY (strategy_id, period)                                 |
|                                                                   |
| );                                                                |
|                                                                   |
| \-- Diupdate oleh learning_layer setiap harian (drift_check task) |
+-------------------------------------------------------------------+

  ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------
  **ATURAN DATABASE:** Tidak ada raw SQL di luar file yang ditentukan. Semua akses DB melalui class repository yang ditentukan per domain. Contoh: state.db hanya diakses via TradeStore, bukan langsung dari main.py.

  ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------

**8.6 Backup Policy**

  ---------------------------------------------------------------------------------------------------
  **Database**         **Frekuensi Backup**                    **Retensi**   **Lokasi**
  -------------------- --------------------------------------- ------------- ------------------------
  state.db             Sebelum setiap restart + harian 00:00   7 hari        agent/memory/backups/

  experience.db        Harian 00:15                            30 hari       agent/memory/backups/

  order_registry.db    Harian 00:20                            30 hari       agent/memory/backups/

  performance.db       Mingguan                                90 hari       agent/memory/backups/

  audit/ (immutable)   Mingguan + cloud sync                   Permanen      audit/ + cloud storage
  ---------------------------------------------------------------------------------------------------

**9. logs/ --- Struktur Log & Format**

**9.1 Log File Map**

  ----------------------------------------------------------------------------------------------------------------
  **File**                 **Isi**                                         **Format**   **Rotasi**   **Retensi**
  ------------------------ ----------------------------------------------- ------------ ------------ -------------
  trades_spot.json         Semua trade spot --- open + close               JSON lines   Harian       90 hari

  trades_futures.json      Semua trade futures                             JSON lines   Harian       90 hari

  decisions_spot.json      Setiap keputusan strategi (signal + block)      JSON lines   Harian       30 hari

  decisions_futures.json   Keputusan strategi futures                      JSON lines   Harian       30 hari

  execution.log            Semua interaksi dengan exchange API             JSON lines   Harian       30 hari

  idempotency.log          Semua idempotency check --- PROCEED/DUPLICATE   JSON lines   Harian       30 hari

  errors.log               Semua error dengan stack trace                  JSON lines   Harian       90 hari
  ----------------------------------------------------------------------------------------------------------------

**9.2 Format JSON --- trades_spot.json**

+----------------------------------------------------------+
| \# Satu baris = satu event trade (open atau close)       |
|                                                          |
| {                                                        |
|                                                          |
| \"timestamp\": \"2024-11-15T08:30:00.123Z\",             |
|                                                          |
| \"event\": \"trade_opened\",                             |
|                                                          |
| \"trade_id\": \"uuid-\...\",                             |
|                                                          |
| \"client_order_id\": \"spot_001_BTCUSDT_1731658200000\", |
|                                                          |
| \"symbol\": \"BTCUSDT\",                                 |
|                                                          |
| \"side\": \"BUY\",                                       |
|                                                          |
| \"order_type\": \"market\",                              |
|                                                          |
| \"requested_qty\": 0.001,                                |
|                                                          |
| \"filled_qty\": 0.001,                                   |
|                                                          |
| \"avg_fill_price\": 50000.0,                             |
|                                                          |
| \"sl_price\": 49000.0,                                   |
|                                                          |
| \"tp_price\": 52000.0,                                   |
|                                                          |
| \"risk_amount_usd\": 50.0,                               |
|                                                          |
| \"strategy_id\": \"spot_strategy_v1\",                   |
|                                                          |
| \"signal_confidence\":0.72,                              |
|                                                          |
| \"regime\": \"strong_trend_up\",                         |
|                                                          |
| \"mode\": \"paper\",                                     |
|                                                          |
| \"equity_before\": 10000.0,                              |
|                                                          |
| \"commission_usd\": 0.05                                 |
|                                                          |
| }                                                        |
|                                                          |
| \# Saat close, tambahkan field:                          |
|                                                          |
| {                                                        |
|                                                          |
| \"event\": \"trade_closed\",                             |
|                                                          |
| \... (field di atas) \...,                               |
|                                                          |
| \"exit_price\": 51500.0,                                 |
|                                                          |
| \"exit_reason\": \"TP\",                                 |
|                                                          |
| \"pnl_usd\": 150.0,                                      |
|                                                          |
| \"pnl_pct\": 3.00,                                       |
|                                                          |
| \"hold_candles\": 12,                                    |
|                                                          |
| \"equity_after\": 10150.0                                |
|                                                          |
| }                                                        |
+----------------------------------------------------------+

**9.3 Format JSON --- decisions_spot.json**

+----------------------------------------------------------------------+
| \# Mencatat setiap signal yang dihasilkan, termasuk yang di-block    |
|                                                                      |
| {                                                                    |
|                                                                      |
| \"timestamp\": \"2024-11-15T08:30:00Z\",                             |
|                                                                      |
| \"event\": \"signal_generated\",                                     |
|                                                                      |
| \"symbol\": \"BTCUSDT\",                                             |
|                                                                      |
| \"side\": \"BUY\",                                                   |
|                                                                      |
| \"confidence\": 0.72,                                                |
|                                                                      |
| \"strategy_id\": \"spot_strategy_v1\",                               |
|                                                                      |
| \"reasoning\": \"RSI oversold + EMA bullish cross + strong regime\", |
|                                                                      |
| \"model_output\": 0.0082,                                            |
|                                                                      |
| \"regime\": \"strong_trend_up\",                                     |
|                                                                      |
| \"vol_regime\": \"normal\",                                          |
|                                                                      |
| \"outcome\": \"approved\", // approved \| blocked \| duplicate       |
|                                                                      |
| \"block_reason\": null, // diisi jika blocked                        |
|                                                                      |
| \"risk_verdict\": \"approved\",                                      |
|                                                                      |
| \"approved_qty\": 0.001                                              |
|                                                                      |
| }                                                                    |
+----------------------------------------------------------------------+

**9.4 Format JSON --- idempotency.log**

+-------------------------------------------------------------------+
| {                                                                 |
|                                                                   |
| \"timestamp\": \"2024-11-15T08:30:00Z\",                          |
|                                                                   |
| \"client_order_id\": \"spot_001_BTCUSDT_1731658200000\",          |
|                                                                   |
| \"check_result\": \"PROCEED\", // PROCEED \| DUPLICATE \| PENDING |
|                                                                   |
| \"action\": \"registered\",                                       |
|                                                                   |
| \"symbol\": \"BTCUSDT\",                                          |
|                                                                   |
| \"strategy_id\": \"spot_strategy_v1\"                             |
|                                                                   |
| }                                                                 |
|                                                                   |
| \# Jika DUPLICATE:                                                |
|                                                                   |
| {                                                                 |
|                                                                   |
| \"check_result\": \"DUPLICATE\",                                  |
|                                                                   |
| \"action\": \"blocked\",                                          |
|                                                                   |
| \"existing_status\": \"confirmed\",                               |
|                                                                   |
| \"existing_ex_id\": \"12345678\"                                  |
|                                                                   |
| }                                                                 |
+-------------------------------------------------------------------+

**9.5 Log Level Convention**

  -----------------------------------------------------------------------------------------------------------------------------
  **Level**      **Kapan Digunakan**                                         **Contoh**
  -------------- ----------------------------------------------------------- --------------------------------------------------
  **DEBUG**      Detail internal untuk debugging. Dimatikan di production.   Tick duration, feature values, orderbook depth

  **INFO**       Event normal yang penting dicatat.                          Trade opened, sync completed, config loaded

  **WARNING**    Kondisi tidak normal tapi sistem masih bisa lanjut.         Slow tick, rate limit warning, spread tinggi

  **ERROR**      Kegagalan yang perlu investigasi tapi tidak fatal.          Order rejected, sync gagal, handler error

  **CRITICAL**   Kegagalan fatal yang membutuhkan intervensi segera.         Safe mode triggered, circuit breaker, DB corrupt
  -----------------------------------------------------------------------------------------------------------------------------

**10. migration/ --- Versioning Database Schema**

Setiap perubahan schema database (tambah kolom, ubah tipe, tambah tabel) HARUS melalui migration. Tidak boleh alter tabel langsung di production.

**10.1 migrate.py --- Interface**

+----------------------------------------------------------------------------+
| class MigrationRunner:                                                     |
|                                                                            |
| def \_\_init\_\_(self, db_path: str):                                      |
|                                                                            |
| self.db_path = db_path                                                     |
|                                                                            |
| def run(self) -\> MigrationReport:                                         |
|                                                                            |
| \"\"\"                                                                     |
|                                                                            |
| 1\. Cek versi schema saat ini (dari tabel \_schema_version)                |
|                                                                            |
| 2\. Temukan migration file yang belum dijalankan                           |
|                                                                            |
| 3\. Jalankan dalam transaksi --- jika gagal, rollback                      |
|                                                                            |
| 4\. Update \_schema_version                                                |
|                                                                            |
| 5\. Return MigrationReport                                                 |
|                                                                            |
| \"\"\"                                                                     |
|                                                                            |
| def rollback(self, target_version: int) -\> bool:                          |
|                                                                            |
| \"\"\"Rollback ke versi tertentu menggunakan down() method.\"\"\"          |
|                                                                            |
| def status(self) -\> list\[MigrationStatus\]:                              |
|                                                                            |
| \"\"\"List semua migration --- mana yang sudah dan belum dijalankan.\"\"\" |
+----------------------------------------------------------------------------+

**10.2 Format Migration File**

+-------------------------------------------------------------------------+
| \# migrations/versions/001_initial_schema.py                            |
|                                                                         |
| MIGRATION_ID = 1                                                        |
|                                                                         |
| DESCRIPTION = \'Initial schema --- positions, audit_events, snapshots\' |
|                                                                         |
| def up(cursor) -\> None:                                                |
|                                                                         |
| \"\"\"Jalankan saat migrate forward.\"\"\"                              |
|                                                                         |
| cursor.execute(\'\'\'                                                   |
|                                                                         |
| CREATE TABLE positions (                                                |
|                                                                         |
| trade_id TEXT PRIMARY KEY,                                              |
|                                                                         |
| \... (lihat section 8.2)                                                |
|                                                                         |
| )                                                                       |
|                                                                         |
| \'\'\')                                                                 |
|                                                                         |
| def down(cursor) -\> None:                                              |
|                                                                         |
| \"\"\"Jalankan saat rollback.\"\"\"                                     |
|                                                                         |
| cursor.execute(\'DROP TABLE IF EXISTS positions\')                      |
|                                                                         |
| \# \-\--                                                                |
|                                                                         |
| \# migrations/versions/002_add_commission_column.py                     |
|                                                                         |
| MIGRATION_ID = 2                                                        |
|                                                                         |
| DESCRIPTION = \'Add commission_usd column to positions\'                |
|                                                                         |
| def up(cursor) -\> None:                                                |
|                                                                         |
| cursor.execute(                                                         |
|                                                                         |
| \'ALTER TABLE positions ADD COLUMN commission_usd REAL DEFAULT 0\'      |
|                                                                         |
| )                                                                       |
|                                                                         |
| def down(cursor) -\> None:                                              |
|                                                                         |
| \# SQLite tidak support DROP COLUMN sebelum 3.35                        |
|                                                                         |
| \# Buat tabel baru tanpa kolom, copy data, rename                       |
|                                                                         |
| cursor.execute(\'CREATE TABLE positions_tmp AS \...\')                  |
|                                                                         |
| cursor.execute(\'DROP TABLE positions\')                                |
|                                                                         |
| cursor.execute(\'ALTER TABLE positions_tmp RENAME TO positions\')       |
+-------------------------------------------------------------------------+

  ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------
  **ATURAN WAJIB:** Setiap migration file harus punya up() dan down(). Nomor MIGRATION_ID harus berurutan. TIDAK BOLEH edit migration yang sudah pernah dijalankan di production --- buat migration baru.

  ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------

**11. models/ --- Model ML di Runtime**

**11.1 Struktur Folder**

+-------------------------------------------------------------+
| agent/models/                                               |
|                                                             |
| ├── spot_model.pkl ← model aktif untuk spot trading         |
|                                                             |
| ├── futures_model.pkl ← model aktif untuk futures trading   |
|                                                             |
| ├── metadata.json ← kontrak interface dengan research layer |
|                                                             |
| └── archive/ ← model lama (untuk rollback cepat)            |
|                                                             |
| ├── spot_model_v1.2.1.pkl                                   |
|                                                             |
| └── spot_model_v1.2.1_metadata.json                         |
+-------------------------------------------------------------+

**11.2 metadata.json --- Kontrak Interface**

+-----------------------------------------------------------+
| {                                                         |
|                                                           |
| \"model_id\": \"spot_lgbm_v2_3_0\",                       |
|                                                           |
| \"version\": \"2.3.0\",                                   |
|                                                           |
| \"created_at\": \"2024-11-10T08:00:00Z\",                 |
|                                                           |
| \"deployed_at\": \"2024-11-15T00:00:00Z\",                |
|                                                           |
| \"model_type\": \"lightgbm\",                             |
|                                                           |
| \"symbol\": \"BTCUSDT\",                                  |
|                                                           |
| \"timeframe\": \"1h\",                                    |
|                                                           |
| \"train_period\": \[\"2020-01-01\", \"2024-10-31\"\],     |
|                                                           |
| \"feature_names\": \[                                     |
|                                                           |
| \"rsi_7\", \"rsi_14\", \"rsi_21\",                        |
|                                                           |
| \"ema_ratio_9_21\", \"ema_ratio_21_50\",                  |
|                                                           |
| \"atr_14\", \"atr_percentile\",                           |
|                                                           |
| \"bb_width_20\", \"volume_ratio_20\",                     |
|                                                           |
| \"log_return_1\", \"log_return_5\", \"log_return_20\",    |
|                                                           |
| \"adx_14\", \"funding_rate\"                              |
|                                                           |
| \],                                                       |
|                                                           |
| \"feature_count\": 47,                                    |
|                                                           |
| \"target_col\": \"target_return_4h\",                     |
|                                                           |
| \"prediction_type\": \"regression\",                      |
|                                                           |
| \"thresholds\": {                                         |
|                                                           |
| \"entry_long\": 0.003,                                    |
|                                                           |
| \"entry_short\": -0.003,                                  |
|                                                           |
| \"min_confidence\": 0.60                                  |
|                                                           |
| },                                                        |
|                                                           |
| \"metrics\": {                                            |
|                                                           |
| \"ic_mean\": 0.078,                                       |
|                                                           |
| \"icir\": 1.85,                                           |
|                                                           |
| \"dir_accuracy\": 0.543,                                  |
|                                                           |
| \"sharpe_signal\": 1.12,                                  |
|                                                           |
| \"backtest_sharpe\":1.34,                                 |
|                                                           |
| \"backtest_maxdd\": 0.162                                 |
|                                                           |
| },                                                        |
|                                                           |
| \"status\": \"production\",                               |
|                                                           |
| \"replaces\": \"spot_lgbm_v2_2_1\",                       |
|                                                           |
| \"compatible_modes\": \[\"paper\", \"shadow\", \"live\"\] |
|                                                           |
| }                                                         |
+-----------------------------------------------------------+

**11.3 Validasi Model Saat Load**

+-------------------------------------------------------------------------+
| class ModelLoader:                                                      |
|                                                                         |
| def load(self, model_path: str, meta_path: str) -\> TrainedModel:       |
|                                                                         |
| model = joblib.load(model_path)                                         |
|                                                                         |
| metadata = json.load(open(meta_path))                                   |
|                                                                         |
| \# Validasi 1: feature_names harus cocok dengan runtime FeatureConfig   |
|                                                                         |
| runtime_features = FeatureConfig().get_feature_names()                  |
|                                                                         |
| if metadata\[\'feature_names\'\] != runtime_features:                   |
|                                                                         |
| raise ModelCompatibilityError(                                          |
|                                                                         |
| f\'Feature mismatch: model={metadata\[\"feature_names\"\]\[:3\]}\...\'  |
|                                                                         |
| f\' runtime={runtime_features\[:3\]}\...\'                              |
|                                                                         |
| )                                                                       |
|                                                                         |
| \# Validasi 2: model harus punya method predict()                       |
|                                                                         |
| if not hasattr(model, \'predict\'):                                     |
|                                                                         |
| raise ModelCompatibilityError(\'Model tidak punya method predict()\')   |
|                                                                         |
| \# Validasi 3: status harus \'production\' atau \'validated\'           |
|                                                                         |
| if metadata\[\'status\'\] not in (\'production\', \'validated\'):       |
|                                                                         |
| raise ModelStatusError(                                                 |
|                                                                         |
| f\'Model status adalah {metadata\[\"status\"\]} --- tidak bisa deploy\' |
|                                                                         |
| )                                                                       |
|                                                                         |
| return TrainedModel(model=model, metadata=metadata)                     |
+-------------------------------------------------------------------------+

**12. Checklist Implementasi Agent Core**

**12.1 Checklist core/**

  ----------------------------------------------------------------------------------------------------------------------------------------------------
  **No**   **Item**                                                              **Verifikasi**                                             **Done**
  -------- --------------------------------------------------------------------- ---------------------------------------------------------- ----------
  1        main.py mengikuti urutan startup T-01 sampai T-14                     Code review + startup log menunjukkan semua tahap          ☐

  2        Main loop catch semua exception di level tick --- tidak crash total   Unit test: inject error di setiap step, loop tetap jalan   ☐

  3        AgentConfig Pydantic validation di-test untuk semua edge case         pytest test_config.py --- semua boundary value tested      ☐

  4        Semua validator di AgentConfig di-test (leverage spot, mode, dll)     100% branch coverage untuk validators                      ☐

  5        Transisi mode divalidasi oleh ModeTransitionGuard                     Test: transisi PAPER→LIVE diblokir (harus lewat SHADOW)    ☐

  6        config_schema.py reject config dengan field tidak valid               pytest: inject invalid value, cek ValueError message       ☐
  ----------------------------------------------------------------------------------------------------------------------------------------------------

**12.2 Checklist scheduler/**

  -------------------------------------------------------------------------------------------------------------------------------
  **No**   **Item**                                             **Verifikasi**                                         **Done**
  -------- ---------------------------------------------------- ------------------------------------------------------ ----------
  1        Semua task terdaftar di scheduler saat startup       Log startup menampilkan daftar task + interval         ☐

  2        daily_reset dipastikan jalan tepat UTC 00:00         Test dengan mock datetime                              ☐

  3        Task yang gagal tidak menghentikan task lain         Inject error di satu task, cek task lain tetap jalan   ☐

  4        Scheduler stop gracefully saat graceful_shutdown()   Tunggu task aktif selesai, tidak force kill            ☐
  -------------------------------------------------------------------------------------------------------------------------------

**12.3 Checklist event_bus/**

  ------------------------------------------------------------------------------------------------------------------------------------
  **No**   **Item**                                            **Verifikasi**                                               **Done**
  -------- --------------------------------------------------- ------------------------------------------------------------ ----------
  1        Handler error tidak menghentikan dispatch loop      Inject exception di handler, cek event berikutnya diproses   ☐

  2        Queue full tidak crash publisher --- log dan skip   Isi queue sampai maxsize, publish satu lagi                  ☐

  3        Semua EventType memiliki minimal satu subscriber    Test: publish setiap event type, cek ada yang receive        ☐
  ------------------------------------------------------------------------------------------------------------------------------------

**12.4 Checklist safe_mode/**

  --------------------------------------------------------------------------------------------------------------------------------------
  **No**   **Item**                                            **Verifikasi**                                                 **Done**
  -------- --------------------------------------------------- -------------------------------------------------------------- ----------
  1        emergency_stop() berjalan dalam urutan yang benar   Integration test dengan mock executor & notifier               ☐

  2        State snapshot tersimpan sebelum exit               Cek file snapshot ada di memory/snapshots/ setelah trigger     ☐

  3        Notifikasi terkirim sebelum sys.exit()              Mock notifier.send_emergency_alert --- dipastikan terpanggil   ☐

  4        is_halted() di-check di awal setiap tick            Code review main_loop                                          ☐
  --------------------------------------------------------------------------------------------------------------------------------------

**12.5 Checklist memory/**

  -------------------------------------------------------------------------------------------------------------------------------------------------
  **No**   **Item**                                                    **Verifikasi**                                                    **Done**
  -------- ----------------------------------------------------------- ----------------------------------------------------------------- ----------
  1        Migration dijalankan saat startup sebelum layer lain init   Log: \'Migration complete, version N\' sebelum layer init         ☐

  2        Setiap DB punya backup sebelum migration baru dijalankan    migrate.py membuat backup sebelum run                             ☐

  3        Tidak ada raw SQL di luar file repository yang ditentukan   grep -r \'execute(\' \--include=\'\*.py\' \| grep -v repository   ☐

  4        audit_events table benar-benar append-only                  Tidak ada UPDATE atau DELETE query untuk tabel ini                ☐

  5        Backup berjalan sesuai jadwal                               Cek file backup ada di memory/backups/ dengan timestamp           ☐
  -------------------------------------------------------------------------------------------------------------------------------------------------

+:------------------------------------------------------------------------------------------------------------------------:+
| **Dokumen ini adalah kontrak implementasi Agent Core.**                                                                  |
|                                                                                                                          |
| Perubahan pada urutan startup, EventType, schema database, atau format log WAJIB diupdate di sini sebelum merge ke main. |
|                                                                                                                          |
| *Referensi terkait: research_layer_docs.docx • runtime_layer_docs.docx • risk_layer skeleton code*                       |
+--------------------------------------------------------------------------------------------------------------------------+
