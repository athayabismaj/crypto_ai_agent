**crypto_ai_agent**

Struktur Proyek Final --- Production-Grade

*Binance Spot + Futures \| Paper → Shadow → Live Mode*

  --------------- ------------- ---------------- ------------------
   **🔥 KRITIS**   **✅ BARU**   **⚠️ PENTING**   **--- STANDARD**

  --------------- ------------- ---------------- ------------------

**01 Research Layer**

*Seluruh eksperimen offline. Tidak menyentuh uang nyata. Output: model .pkl + laporan backtest.*

**data/**

  --------------------------------------------------------------------------------------------------
  **Path**                  **File**         **Fungsi**                                  **Status**
  ------------------------- ---------------- ------------------------------------------ ------------
  data/raw/                 **\*.parquet**   Data OHLCV mentah dari exchange            

  data/processed/           **\*.parquet**   Data bersih setelah cleaning               

  data/features/            **\*.parquet**   Hasil feature engineering siap training    

  data/orderbook_samples/   **\*.json**      Depth data untuk simulasi fill realistis    🔥 KRITIS
  --------------------------------------------------------------------------------------------------

**pipeline/**

  ------------------------------------------------------------------------------------------------------
  **Path**    **File**                     **Fungsi**                                        **Status**
  ----------- ---------------------------- ------------------------------------------------ ------------
  pipeline/   **fetch_data.py**            Ambil data OHLCV + funding rate dari exchange    

  pipeline/   **clean_data.py**            Hapus outlier, fill gap, normalisasi timestamp   

  pipeline/   **resample_data.py**         Konversi timeframe (1m → 5m → 1h)                

  pipeline/   **feature_engineering.py**   Hitung indikator teknikal + fitur ML             
  ------------------------------------------------------------------------------------------------------

**validation/**

  -----------------------------------------------------------------------------------------------------
  **Path**      **File**                   **Fungsi**                                       **Status**
  ------------- -------------------------- ----------------------------------------------- ------------
  validation/   **data_quality.py**        Cek missing values, outlier, duplikat           

  validation/   **leakage_check.py**       Deteksi data bocor ke masa depan (look-ahead)    🔥 KRITIS

  validation/   **consistency_check.py**   Validasi konsistensi antar timeframe            
  -----------------------------------------------------------------------------------------------------

**modeling/**

  ----------------------------------------------------------------------------------------------------
  **Path**    **File**                    **Fungsi**                                       **Status**
  ----------- --------------------------- ----------------------------------------------- ------------
  modeling/   **train.py**                Training model ML (LightGBM / XGBoost / NN)     

  modeling/   **walk_forward.py**         Walk-forward validation --- cegah overfitting    🔥 KRITIS

  modeling/   **evaluate.py**             Evaluasi: accuracy, precision, recall, AUC      

  modeling/   **model_registry.py**       Versioning model + metadata tracking            

  modeling/   **feature_importance.py**   Analisis kontribusi fitur tiap model            
  ----------------------------------------------------------------------------------------------------

**backtest/**

  ----------------------------------------------------------------------------------------------------
  **Path**    **File**                     **Fungsi**                                      **Status**
  ----------- ---------------------------- ---------------------------------------------- ------------
  backtest/   **engine.py**                Engine backtest utama --- event-driven         

  backtest/   **simulator.py**             Simulasi trade single-asset                    

  backtest/   **portfolio_simulator.py**   Simulasi multi-aset dengan rebalancing          🔥 KRITIS

  backtest/   **orderbook_simulator.py**   Simulasi fill realistis via depth snapshot      🔥 KRITIS

  backtest/   **liquidity_model.py**       Model slippage + market impact                  🔥 KRITIS

  backtest/   **execution_emulator.py**    Emulasi delay, retry, partial fill              🔥 KRITIS

  backtest/   **metrics.py**               ROI, max drawdown, Sharpe, Sortino, win rate   

  backtest/   **report.py**                Generate laporan HTML + PDF hasil backtest     
  ----------------------------------------------------------------------------------------------------

**optimization/ & experiments/**

  -------------------------------------------------------------------------------------------
  **Path**        **File**               **Fungsi**                               **Status**
  --------------- ---------------------- --------------------------------------- ------------
  optimization/   **tuner.py**           Runner untuk semua metode optimasi      

  optimization/   **grid_search.py**     Grid search parameter strategi          

  optimization/   **bayesian_opt.py**    Bayesian optimization (lebih efisien)   

  experiments/    **\*.md / \*.ipynb**   Catatan & notebook hasil eksperimen     
  -------------------------------------------------------------------------------------------

**02 Runtime Layer**

*Sistem live. Setiap file di sini menyentuh uang nyata saat paper_mode=False.*

**Root runtime/**

  -----------------------------------------------------------------------------------------------
  **Path**    **File**                 **Fungsi**                                     **Status**
  ----------- ------------------------ --------------------------------------------- ------------
  runtime/    **.env**                 API keys, secrets --- JANGAN commit ke git     🔥 KRITIS

  runtime/    **docker-compose.yml**   Orkestrasi container: agent, monitoring, DB   

  runtime/    **requirements.txt**     Dependency Python runtime (bukan research)    
  -----------------------------------------------------------------------------------------------

**migration/ ✅ BARU**

  -------------------------------------------------------------------------------------------------------
  **Path**              **File**          **Fungsi**                                          **Status**
  --------------------- ----------------- -------------------------------------------------- ------------
  migration/            **migrate.py**    Runner migrasi database dengan rollback support      ✅ BARU

  migration/versions/   **001_init.py**   Versi schema awal (state.db, experience.db, dll)     ✅ BARU

  migration/versions/   **002\_\*.py**    Migrasi inkremental saat upgrade sistem              ✅ BARU
  -------------------------------------------------------------------------------------------------------

**security/**

  --------------------------------------------------------------------------------------------
  **Path**    **File**                **Fungsi**                                   **Status**
  ----------- ----------------------- ------------------------------------------- ------------
  security/   **key_manager.py**      Load & proteksi API key dari .env + vault    🔥 KRITIS

  security/   **encryptor.py**        Enkripsi data sensitif di database           🔥 KRITIS

  security/   **access_control.py**   Kontrol akses endpoint gateway              
  --------------------------------------------------------------------------------------------

**gateway/**

  ----------------------------------------------------------------------------------------
  **Path**    **File**            **Fungsi**                                   **Status**
  ----------- ------------------- ------------------------------------------- ------------
  gateway/    **api.py**          FastAPI server --- entry point eksternal    

  gateway/    **routes.py**       Routing endpoint: status, signal, control   

  gateway/    **auth.py**         JWT authentication + API key validation     

  gateway/    **middleware.py**   Rate limit, logging, error handling         
  ----------------------------------------------------------------------------------------

**03 Agent --- Core**

**core/ ← Jantung sistem**

  -------------------------------------------------------------------------------------------------
  **Path**    **File**               **Fungsi**                                         **Status**
  ----------- ---------------------- ------------------------------------------------- ------------
  core/       **main.py**            Main loop agent --- ticker utama semua layer       🔥 KRITIS

  core/       **config.py**          Load konfigurasi dari .env + file YAML            

  core/       **config_schema.py**   Validasi config via Pydantic saat startup           ✅ BARU

  core/       **modes.py**           Enum mode: LIVE / PAPER / SHADOW                   ⚠️ PENTING

  core/       **scheduler.py**       Task scheduling: per-tick, harian, mingguan       

  core/       **event_bus.py**       Async pub/sub antar layer (asyncio)               

  core/       **safe_mode.py**       Emergency stop --- halt semua aktivitas trading    🔥 KRITIS
  -------------------------------------------------------------------------------------------------

**memory/ ← State persistence**

  ------------------------------------------------------------------------------------------------
  **Path**        **File**                 **Fungsi**                                  **Status**
  --------------- ------------------------ ------------------------------------------ ------------
  memory/         **state.db**             Posisi aktif + status order terbuka         🔥 KRITIS

  memory/         **experience.db**        Histori semua trade yang sudah close       

  memory/         **performance.db**       Statistik performa per strategi            

  memory/         **order_registry.db**    Idempotency store --- cegah double order    🔥 KRITIS

  memory/         **execution_cache.db**   Cache retry tracking per order              🔥 KRITIS

  memory/cache/   **\*.json**              Cache cepat in-memory (TTL pendek)         
  ------------------------------------------------------------------------------------------------

**logs/**

  ----------------------------------------------------------------------------------------------------
  **Path**    **File**                     **Fungsi**                                      **Status**
  ----------- ---------------------------- ---------------------------------------------- ------------
  logs/       **trades_spot.json**         Log semua trade spot (JSON structured)         

  logs/       **trades_futures.json**      Log semua trade futures                        

  logs/       **decisions_spot.json**      Log keputusan strategi spot (with reasoning)   

  logs/       **decisions_futures.json**   Log keputusan strategi futures                 

  logs/       **execution.log**            Log eksekusi order ke exchange                 

  logs/       **idempotency.log**          Log cek duplikasi order                         🔥 KRITIS

  logs/       **errors.log**               Error log dengan stack trace                   
  ----------------------------------------------------------------------------------------------------

**04 Data & Intelligence Layer**

**data_layer/**

  ---------------------------------------------------------------------------------------------------------
  **Path**      **File**                  **Fungsi**                                            **Status**
  ------------- ------------------------- ---------------------------------------------------- ------------
  data_layer/   **market.py**             Fetch OHLCV, ticker, 24h stats                       

  data_layer/   **orderbook.py**          Snapshot & stream orderbook depth                    

  data_layer/   **websocket_client.py**   WebSocket handler Binance (reconnect auto)           

  data_layer/   **validator.py**          Validasi data market sebelum diproses                

  data_layer/   **anomaly_detector.py**   Deteksi spike harga / data corrupt                    ⚠️ PENTING

  data_layer/   **rate_limiter.py**       Global rate limiter API exchange (weight tracking)     ✅ BARU
  ---------------------------------------------------------------------------------------------------------

**intelligence_layer/**

  --------------------------------------------------------------------------------------------------------------
  **Path**              **File**               **Fungsi**                                            **Status**
  --------------------- ---------------------- ---------------------------------------------------- ------------
  intelligence_layer/   **regime.py**          Klasifikasi market: trending / sideways / volatile   

  intelligence_layer/   **volatility.py**      Hitung ATR, realized vol, vol percentile             

  intelligence_layer/   **market_state.py**    Agregasi kondisi market untuk strategi               

  intelligence_layer/   **latency_guard.py**   Proteksi eksekusi saat latency tinggi                 ⚠️ PENTING
  --------------------------------------------------------------------------------------------------------------

**05 Strategy & Portfolio Layer**

**strategy_layer/**

  ----------------------------------------------------------------------------------------------------
  **Path**          **File**                  **Fungsi**                                   **Status**
  ----------------- ------------------------- ------------------------------------------- ------------
  strategy_layer/   **base_strategy.py**      Abstract base class semua strategi          

  strategy_layer/   **spot_strategy.py**      Implementasi strategi spot trading          

  strategy_layer/   **futures_strategy.py**   Implementasi strategi futures + hedge       

  strategy_layer/   **strategy_utils.py**     Helper: signal scoring, filter, validator   
  ----------------------------------------------------------------------------------------------------

**portfolio_layer/ ← Global risk control**

  ------------------------------------------------------------------------------------------------
  **Path**           **File**                 **Fungsi**                               **Status**
  ------------------ ------------------------ --------------------------------------- ------------
  portfolio_layer/   **allocator.py**         Alokasi modal antar strategi + simbol    🔥 KRITIS

  portfolio_layer/   **capital_manager.py**   Monitor & kontrol total equity           🔥 KRITIS

  portfolio_layer/   **correlation.py**       Cegah over-exposure aset berkorelasi     ⚠️ PENTING

  portfolio_layer/   **risk_budget.py**       Distribusi risk budget per strategi      ⚠️ PENTING
  ------------------------------------------------------------------------------------------------

**06 Risk Layer**

*Semua order WAJIB melewati RiskManager sebelum dikirim ke exchange.*

  ------------------------------------------------------------------------------------------------------
  **Path**      **File**                  **Fungsi**                                         **Status**
  ------------- ------------------------- ------------------------------------------------- ------------
  risk_layer/   **risk_manager.py**       Orchestrator: gate tunggal semua validasi risk     🔥 KRITIS

  risk_layer/   **position_size.py**      Kelly / Fixed Fractional / ATR-scaled sizing       🔥 KRITIS

  risk_layer/   **stoploss.py**           Validasi & set stop loss sebelum entry             🔥 KRITIS

  risk_layer/   **exposure_control.py**   Max exposure per simbol + total portfolio          ⚠️ PENTING

  risk_layer/   **leverage_control.py**   Batas leverage per simbol (futures)                ⚠️ PENTING

  risk_layer/   **circuit_breaker.py**    Auto-halt: daily loss / drawdown / streak loss     🔥 KRITIS

  risk_layer/   **pre_trade_check.py**    Sanity check data + market status sebelum order   
  ------------------------------------------------------------------------------------------------------

*Risk flow: PreTradeCheck → CircuitBreaker → LeverageControl → ExposureControl → PositionSizer → StopLoss → APPROVE/BLOCK*

**07 Trade & Execution Layer**

**trade_layer/**

  --------------------------------------------------------------------------------------------------
  **Path**       **File**                 **Fungsi**                                     **Status**
  -------------- ------------------------ --------------------------------------------- ------------
  trade_layer/   **trade.py**             Data class Trade dengan client_order_id        🔥 KRITIS

  trade_layer/   **manager.py**           Orchestrasi: buka, update, tutup trade        

  trade_layer/   **store.py**             Persistensi trade ke SQLite (state.db)        

  trade_layer/   **audit_trail.py**       Immutable log setiap perubahan trade           ⚠️ PENTING

  trade_layer/   **trade_validator.py**   Validasi trade sebelum dikirim ke execution   

  trade_layer/   **idempotency.py**       Anti double-order via order_registry.db        🔥 KRITIS

  trade_layer/   **recovery.py**          Recovery state setelah crash / restart         🔥 KRITIS
  --------------------------------------------------------------------------------------------------

**execution_layer/ ✅ Multi-exchange**

  -------------------------------------------------------------------------------------------------------------------
  **Path**                     **File**                   **Fungsi**                                      **Status**
  ---------------------------- -------------------------- ---------------------------------------------- ------------
  execution_layer/             **exchange.py**            Abstract base class semua exchange connector     ✅ BARU

  execution_layer/exchanges/   **binance.py**             Binance Spot + Futures implementation            ✅ BARU

  execution_layer/exchanges/   **bybit.py**               Bybit implementation                             ✅ BARU

  execution_layer/exchanges/   **okx.py**                 OKX implementation                               ✅ BARU

  execution_layer/             **spot_executor.py**       Eksekutor order spot                           

  execution_layer/             **futures_executor.py**    Eksekutor order futures + margin               

  execution_layer/             **order_splitter.py**      Split order besar (TWAP / VWAP)                 ⚠️ PENTING

  execution_layer/             **smart_router.py**        Routing order antar exchange (best price)        ✅ BARU

  execution_layer/             **latency_tracker.py**     Monitor & catat latency per exchange           

  execution_layer/             **execution_monitor.py**   Monitor status order setelah submit            
  -------------------------------------------------------------------------------------------------------------------

**08 Exit, Monitoring & Sync Layer**

**exit_layer/**

  ---------------------------------------------------------------------------------------------------
  **Path**      **File**              **Fungsi**                                          **Status**
  ------------- --------------------- -------------------------------------------------- ------------
  exit_layer/   **trailing.py**       Trailing stop: ATR-based & percentage-based        

  exit_layer/   **take_profit.py**    TP: single target & partial TP scaling             

  exit_layer/   **break_even.py**     Geser SL ke entry saat profit mencapai threshold   

  exit_layer/   **exit_manager.py**   Koordinasi semua mekanisme exit                     🔥 KRITIS
  ---------------------------------------------------------------------------------------------------

**monitoring/**

  -----------------------------------------------------------------------------------------------
  **Path**      **File**              **Fungsi**                                      **Status**
  ------------- --------------------- ---------------------------------------------- ------------
  monitoring/   **heartbeat.py**      Ping periodik --- deteksi agent hang/mati       🔥 KRITIS

  monitoring/   **connectivity.py**   Cek koneksi ke exchange & internet             

  monitoring/   **health_check.py**   Status semua layer + DB + memory                ⚠️ PENTING

  monitoring/   **alerts.py**         Kirim alert ke Telegram/Discord saat anomali   
  -----------------------------------------------------------------------------------------------

**sync/ ✅ + position_sync**

  ------------------------------------------------------------------------------------------------
  **Path**    **File**                **Fungsi**                                       **Status**
  ----------- ----------------------- ----------------------------------------------- ------------
  sync/       **balance_sync.py**     Sinkronisasi saldo dari exchange ke internal    

  sync/       **order_sync.py**       Sinkronisasi status order terbuka               

  sync/       **position_sync.py**    Rekonsiliasi posisi internal vs exchange real     ✅ BARU

  sync/       **reconciliation.py**   Full audit: PnL, balance, posisi --- daily       ⚠️ PENTING
  ------------------------------------------------------------------------------------------------

**09 Learning & LLM Layer**

**learning_layer/**

  -------------------------------------------------------------------------------------------------------------
  **Path**          **File**                      **Fungsi**                                        **Status**
  ----------------- ----------------------------- ------------------------------------------------ ------------
  learning_layer/   **reflection.py**             Analisis trade historis --- ekstrak pola         

  learning_layer/   **adaptation.py**             Adjust parameter strategi berdasarkan performa   

  learning_layer/   **drift_detection.py**        Deteksi model drift (PSI, KL divergence)          🔥 KRITIS

  learning_layer/   **experience_processor.py**   Proses & simpan experience ke experience.db      

  learning_layer/   **strategy_killer.py**        Auto-disable strategi yang underperform           🔥 KRITIS
  -------------------------------------------------------------------------------------------------------------

**llm_layer/ ✅ + cost & rate control**

  ------------------------------------------------------------------------------------------------
  **Path**     **File**                   **Fungsi**                                   **Status**
  ------------ -------------------------- ------------------------------------------- ------------
  llm_layer/   **llm_client.py**          Client ke Anthropic / OpenAI API            

  llm_layer/   **llm_budget.py**          Cost control: limit USD per hari / bulan      ✅ BARU

  llm_layer/   **llm_rate_limiter.py**    Rate limit panggilan LLM (tokens/min)         ✅ BARU

  llm_layer/   **trade_analyzer.py**      Minta LLM analisis skenario trade           

  llm_layer/   **strategy_feedback.py**   Feedback loop: performa → prompt LLM        

  llm_layer/   **llm_filter.py**          Scoring only --- LLM tidak eksekusi order    🔥 KRITIS
  ------------------------------------------------------------------------------------------------

**10 Notification, Utils & Models**

**notification/**

  -----------------------------------------------------------------------------------------
  **Path**        **File**          **Fungsi**                                  **Status**
  --------------- ----------------- ------------------------------------------ ------------
  notification/   **telegram.py**   Kirim notifikasi ke Telegram Bot           

  notification/   **discord.py**    Kirim notifikasi ke Discord webhook        

  notification/   **notifier.py**   Abstraksi: routing ke channel yang tepat   
  -----------------------------------------------------------------------------------------

**utils/ ✅ + structured logger**

  -----------------------------------------------------------------------------------------------------
  **Path**    **File**                   **Fungsi**                                         **Status**
  ----------- -------------------------- ------------------------------------------------- ------------
  utils/      **logger.py**              Standard Python logger dengan formatter           

  utils/      **structured_logger.py**   JSON structured logging (kompatibel Prometheus)     ✅ BARU

  utils/      **time_utils.py**          UTC helper, candle timing, timestamp converter    

  utils/      **helpers.py**             Helper umum: retry, round_price, safe_div         
  -----------------------------------------------------------------------------------------------------

**models/**

  -----------------------------------------------------------------------------------------------------
  **Path**    **File**                **Fungsi**                                            **Status**
  ----------- ----------------------- ---------------------------------------------------- ------------
  models/     **spot_model.pkl**      Model ML untuk sinyal spot trading                   

  models/     **futures_model.pkl**   Model ML untuk sinyal futures trading                

  models/     **metadata.json**       Info model: versi, tanggal train, fitur, threshold    ⚠️ PENTING
  -----------------------------------------------------------------------------------------------------

**11 Tests Layer**

**unit/**

  -------------------------------------------------------------------------------------------------
  **Path**      **File**                  **Fungsi**                                    **Status**
  ------------- ------------------------- -------------------------------------------- ------------
  tests/unit/   **test_risk.py**          Unit test Risk Layer --- 20+ test case        🔥 KRITIS

  tests/unit/   **test_strategy.py**      Unit test logic strategi spot & futures      

  tests/unit/   **test_idempotency.py**   Test anti double-order --- kasus edge case    🔥 KRITIS

  tests/unit/   **test_execution.py**     Test executor + lot filter + slippage        
  -------------------------------------------------------------------------------------------------

**integration/ & mocks/**

  --------------------------------------------------------------------------------------------------------
  **Path**             **File**                    **Fungsi**                                  **Status**
  -------------------- --------------------------- ------------------------------------------ ------------
  tests/integration/   **test_full_flow.py**       End-to-end: signal → risk → trade → exit    ⚠️ PENTING

  tests/integration/   **test_exchange_flow.py**   Test flow eksekusi ke mock exchange         ⚠️ PENTING

  tests/mocks/         **mock_exchange.py**        Simulasi respons Binance API               

  tests/mocks/         **mock_data.py**            Market data fixture untuk testing          
  --------------------------------------------------------------------------------------------------------

**12 Automation, Observability & Docs**

**automation/**

  ---------------------------------------------------------------------------------------------
  **Path**      **File**                **Fungsi**                                  **Status**
  ------------- ----------------------- ------------------------------------------ ------------
  automation/   **retrain.py**          Auto-retrain model saat drift terdeteksi   

  automation/   **deploy.py**           Deploy model baru ke production            

  automation/   **scheduler.py**        Cron-like scheduler untuk task otomatis    

  automation/   **health_restart.py**   Auto-restart agent jika heartbeat mati      🔥 KRITIS
  ---------------------------------------------------------------------------------------------

**observability/**

  ---------------------------------------------------------------------------------------------------------
  **Path**         **File**                     **Fungsi**                                      **Status**
  ---------------- ---------------------------- ---------------------------------------------- ------------
  observability/   **prometheus_metrics.py**    Expose metrics: PnL, latency, win rate          ⚠️ PENTING

  observability/   **grafana_dashboard.json**   Dashboard: equity curve, drawdown, volume       ⚠️ PENTING

  observability/   **alert_rules.yml**          Alert: DD \> 5%, latency \> 2s, CB triggered    🔥 KRITIS
  ---------------------------------------------------------------------------------------------------------

**audit/**

  ----------------------------------------------------------------------------------------------
  **Path**    **File**               **Fungsi**                                      **Status**
  ----------- ---------------------- ---------------------------------------------- ------------
  audit/      **trade_history.db**   Database permanen semua trade (immutable)       🔥 KRITIS

  audit/      **tax_report.py**      Generate laporan PnL untuk keperluan pajak     

  audit/      **pnl_tracker.py**     Tracking PnL real-time per strategi & simbol   
  ----------------------------------------------------------------------------------------------

**docs/ ✅ + rollback_guide**

  ---------------------------------------------------------------------------------------------------
  **Path**    **File**                  **Fungsi**                                        **Status**
  ----------- ------------------------- ------------------------------------------------ ------------
  docs/       **architecture.md**       Diagram arsitektur + penjelasan layer            

  docs/       **agent_flow.md**         Flow diagram: signal → order → close             

  docs/       **risk_management.md**    Dokumentasi lengkap Risk Layer + config          

  docs/       **deployment_guide.md**   Step-by-step deploy: paper → shadow → live        ⚠️ PENTING

  docs/       **rollback_guide.md**     Prosedur darurat: shutdown, rollback, recovery     ✅ BARU
  ---------------------------------------------------------------------------------------------------

**13 Gap Analysis --- Sebelum vs Sesudah**

*8 gap kritis yang ditemukan dari review awal, semua sudah tertutup di struktur final ini.*

  --------------------------------------------------------------------------------------------------------------
  **Gap yang Ditemukan**                               **Solusi di Struktur Final**                 **Status**
  ---------------------------------------------------- ------------------------------------------- -------------
  Tidak ada config validation saat startup             core/config_schema.py (Pydantic)             **Selesai**

  LLM layer tanpa rate limit & cost control            llm_budget.py + llm_rate_limiter.py          **Selesai**

  tests/ kosong --- tidak ada test kritis              test_risk.py, test_idempotency.py, mocks/    **Selesai**

  Multi-exchange tidak ada abstraksi jelas             exchanges/ base class + binance/bybit/okx    **Selesai**

  Tidak ada position reconciliation vs exchange        sync/position_sync.py                        **Selesai**

  Tidak ada rate limiter terpusat untuk API exchange   data_layer/rate_limiter.py                   **Selesai**

  Tidak ada DB schema versioning / migration           runtime/migration/ + versions/               **Selesai**

  Tidak ada rollback / emergency procedure di docs     docs/rollback_guide.md                       **Selesai**
  --------------------------------------------------------------------------------------------------------------

**14 Risk Layer --- Quick Reference**

*Panduan cepat komponen Risk Layer yang sudah di-generate skeleton-nya.*

  ---------------------------------------------------------------------------------------------------
  **Komponen**          **Tanggung Jawab**                              **Paper Mode Behavior**
  --------------------- ----------------------------------------------- -----------------------------
  **RiskManager**       Gate tunggal --- semua order wajib lewat sini   Block dicatat, tidak reject

  **PreTradeCheck**     Validasi format, symbol, market status          Sama dengan live

  **CircuitBreaker**    Halt saat daily loss / drawdown / streak loss   State machine tetap jalan

  **PositionSizer**     Kelly / Fixed Fractional / ATR-scaled sizing    Sizing tetap dihitung

  **ExposureControl**   Max per-symbol dan total portfolio exposure     Block di-log, tidak reject

  **LeverageControl**   Batas leverage per simbol futures               Validated, tidak reject

  **StopLossManager**   Validasi SL jarak & wajib set sebelum entry     SL opsional, hanya warning
  ---------------------------------------------------------------------------------------------------

**Config key penting:**

- paper_mode = True/False --- wajib False sebelum live

- risk_per_trade_pct = 0.01 --- 1% equity per trade

- max_daily_loss_pct = 0.05 --- 5% → circuit breaker halt

- max_drawdown_pct = 0.10 --- 10% dari peak → halt

- max_open_positions = 5 --- maks posisi bersamaan

- global_max_leverage = 5 --- batas leverage semua simbol

- sizing_method = fixed_fractional \| kelly \| volatility_scaled

**15 Alur Deploy yang Direkomendasikan**

  ------------------------------------------------------------------------------------------------------------------------------------------------------------------
   **Fase**  **Mode**                   **Aktivitas**                                                                                           **Durasi Minimum**
  ---------- -------------------------- ------------------------------------------------------------------------------------------------------- --------------------
    **1**    **Paper Trading**          paper_mode=True. Semua logic jalan, tidak ada order nyata. Validasi risk + sizing.                      *Min. 2 minggu*

    **2**    **Shadow Mode**            Run paralel dengan live --- order dikirim ke exchange tapi tidak di-fill (test-net atau flag khusus).   *Min. 1 minggu*

    **3**    **Live --- Modal Kecil**   paper_mode=False. Modal minimal (10-20%). Monitor circuit breaker & equity setiap hari.                 *Min. 1 bulan*

    **4**    **Live --- Full**          Naikkan modal bertahap. Aktifkan multi-exchange routing. Monitor Grafana dashboard.                     *Ongoing*
  ------------------------------------------------------------------------------------------------------------------------------------------------------------------

*Struktur ini adalah fondasi. Implementasi detail tiap layer bisa di-generate satu per satu.*
