**crypto_ai_agent**

**Dokumentasi Layer: Runtime**

*Interface contract • Config keys • Design decisions • Data flow • Error handling*

Binance Spot + Futures \| Paper → Shadow → Live \| Versi 1.0

  ------------------------------------------------

  ------------------------------------------------

**1. Overview --- Runtime Layer**

Runtime adalah sistem live yang mengeksekusi trading secara otomatis. Berbeda dengan Research, setiap komponen di sini berpotensi menghasilkan atau kehilangan uang nyata. Prinsip utama:

> **•** Fault tolerant --- crash tidak boleh menyebabkan posisi terbuka tanpa pantauan.
>
> **•** Idempotent --- restart tidak boleh menghasilkan order ganda.
>
> **•** Observable --- setiap keputusan harus bisa di-audit setelah kejadian.
>
> **•** Mode-aware --- paper/shadow/live berperilaku berbeda tapi melewati code path yang sama.

  ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------
  **ATURAN KERAS:** Tidak ada koneksi langsung dari research/ ke runtime/ dalam satu process. Artefak (model, config, params) dipindahkan via file sistem melalui automation/deploy.py.

  ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------

**1.1 Peta Sublayer & Urutan Inisialisasi**

  ---------------------------------------------------------------------------------------------------------------------
  **Urutan**   **Sublayer**              **Tanggung Jawab Utama**                    **Bergantung Pada**
  ------------ ------------------------- ------------------------------------------- ----------------------------------
  1            **security/**             Load & proteksi API key, enkripsi secrets   ---

  2            **core/config**           Validasi semua config via Pydantic          security/

  3            **data_layer/**           Koneksi WebSocket & REST ke exchange        security/, core/config

  4            **intelligence_layer/**   Klasifikasi regime & volatilitas            data_layer/

  5            **portfolio_layer/**      Alokasi modal & risk budget global          core/config

  6            **risk_layer/**           Inisialisasi semua risk guard               portfolio_layer/

  7            **strategy_layer/**       Load model ML & parameter strategi          risk_layer/, intelligence_layer/

  8            **trade_layer/**          Restore state dari DB, cek open orders      risk_layer/

  9            **execution_layer/**      Koneksi ke exchange executor                security/, data_layer/

  10           **exit_layer/**           Register open positions untuk monitoring    trade_layer/

  11           **sync/**                 Rekonsiliasi awal: balance, posisi, order   execution_layer/

  12           **monitoring/**           Mulai heartbeat & health check              Semua layer

  13           **core/main loop**        Mulai event loop utama                      Semua siap
  ---------------------------------------------------------------------------------------------------------------------

**1.2 Mode Operasi**

  ----------------------------------------------------------------------------------------------------------------------------
  **Mode**     **paper_mode**   **Kirim Order?**   **Fill Order?**     **Gunakan Modal Nyata?**   **Kapan Digunakan**
  ------------ ---------------- ------------------ ------------------- -------------------------- ----------------------------
  **PAPER**    True             Tidak              Simulasi internal   Tidak                      Development & awal testing

  **SHADOW**   False            Ya (test-net)      Ya (test-net)       Tidak                      Validasi sebelum live

  **LIVE**     False            Ya (mainnet)       Ya (mainnet)        Ya                         Production
  ----------------------------------------------------------------------------------------------------------------------------

  ---------------------------------------------------------------------------------------------------------------------------------------------------------
  **TRANSISI MODE:** Perubahan mode TIDAK BOLEH dilakukan saat ada posisi terbuka. Tutup semua posisi dulu, pastikan state.db bersih, baru ubah modes.py.

  ---------------------------------------------------------------------------------------------------------------------------------------------------------

**2. security/ --- Keamanan & Proteksi Secret**

Layer pertama yang diinisialisasi. Tidak ada komponen lain yang boleh mengakses API key secara langsung --- semua harus melalui KeyManager.

**2.1 key_manager.py**

**Interface Publik**

  ------------------------------------------------------------------------------------------------------------------------------
  **Fungsi**               **Parameter**                                  **Return**                          **Exception**
  ------------------------ ---------------------------------------------- ----------------------------------- ------------------
  get_api_key()            exchange: str, env: str=\'production\'         str --- key terenkripsi di memory   KeyNotFoundError

  get_api_secret()         exchange: str, env: str=\'production\'         str                                 KeyNotFoundError

  rotate_key()             exchange: str, new_key: str, new_secret: str   bool                                RotationError

  validate_permissions()   exchange: str                                  PermissionReport                    APIError
  ------------------------------------------------------------------------------------------------------------------------------

**PermissionReport Dataclass**

+-------------------------------------------------------------------+
| \@dataclass                                                       |
|                                                                   |
| class PermissionReport:                                           |
|                                                                   |
| exchange: str                                                     |
|                                                                   |
| can_read: bool \# baca balance & posisi                           |
|                                                                   |
| can_trade: bool \# buka & tutup order                             |
|                                                                   |
| can_withdraw: bool \# HARUS False --- agent tidak boleh withdraw  |
|                                                                   |
| ip_restricted: bool \# True = key hanya valid dari IP tertentu    |
|                                                                   |
| passed: bool \# True jika can_read+can_trade dan NOT can_withdraw |
+-------------------------------------------------------------------+

  -----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------
  **KRITIS:** API key yang digunakan agent WAJIB memiliki can_withdraw=False. Jika validate_permissions() mengembalikan can_withdraw=True, sistem WAJIB shutdown dan alert dikirim.

  -----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------

**2.2 encryptor.py**

  ---------------------------------------------------------------------------------------
  **Fungsi**        **Parameter**                    **Return**
  ----------------- -------------------------------- ------------------------------------
  encrypt()         data: str \| bytes, key: bytes   bytes --- ciphertext

  decrypt()         ciphertext: bytes, key: bytes    str

  generate_key()    ---                              bytes --- 32-byte Fernet key

  hash_order_id()   client_order_id: str             str --- SHA-256 hash untuk logging
  ---------------------------------------------------------------------------------------

> **•** Gunakan Fernet (AES-128-CBC + HMAC-SHA256) dari library cryptography.
>
> **•** Master key disimpan di environment variable CRYPTO_AGENT_MASTER_KEY, bukan di .env file.
>
> **•** Data di state.db, experience.db, dan order_registry.db di-enkripsi sebelum disimpan.

**2.3 .env --- Template Lengkap**

+-------------------------------------------------------------------+
| \# Exchange API (WAJIB diisi sebelum deploy)                      |
|                                                                   |
| BINANCE_API_KEY=                                                  |
|                                                                   |
| BINANCE_API_SECRET=                                               |
|                                                                   |
| BINANCE_TESTNET_KEY=                                              |
|                                                                   |
| BINANCE_TESTNET_SECRET=                                           |
|                                                                   |
| \# Master encryption key                                          |
|                                                                   |
| CRYPTO_AGENT_MASTER_KEY= \# generate via encryptor.generate_key() |
|                                                                   |
| \# Mode                                                           |
|                                                                   |
| AGENT_MODE=paper \# paper \| shadow \| live                       |
|                                                                   |
| INITIAL_EQUITY=10000.0                                            |
|                                                                   |
| \# Notification                                                   |
|                                                                   |
| TELEGRAM_BOT_TOKEN=                                               |
|                                                                   |
| TELEGRAM_CHAT_ID=                                                 |
|                                                                   |
| DISCORD_WEBHOOK_URL=                                              |
|                                                                   |
| \# Gateway                                                        |
|                                                                   |
| GATEWAY_SECRET_KEY= \# JWT secret untuk API gateway               |
|                                                                   |
| GATEWAY_PORT=8080                                                 |
|                                                                   |
| \# Database paths                                                 |
|                                                                   |
| STATE_DB_PATH=agent/memory/state.db                               |
|                                                                   |
| EXPERIENCE_DB_PATH=agent/memory/experience.db                     |
|                                                                   |
| ORDER_REGISTRY_DB_PATH=agent/memory/order_registry.db             |
|                                                                   |
| \# LLM (opsional)                                                 |
|                                                                   |
| ANTHROPIC_API_KEY=                                                |
|                                                                   |
| LLM_DAILY_BUDGET_USD=1.0                                          |
+-------------------------------------------------------------------+

**3. core/ --- Jantung Sistem**

**3.1 config.py & config_schema.py**

config.py membaca nilai dari .env dan file YAML. config_schema.py memvalidasi semua nilai menggunakan Pydantic sebelum sistem boleh berjalan.

**AgentConfig --- Pydantic Model**

+-------------------------------------------------------------------+
| from pydantic import BaseModel, validator, Field                  |
|                                                                   |
| class AgentConfig(BaseModel):                                     |
|                                                                   |
| \# Mode                                                           |
|                                                                   |
| mode: str = \'paper\' \# paper\|shadow\|live                      |
|                                                                   |
| initial_equity: float = 10_000.0                                  |
|                                                                   |
| \# Risk                                                           |
|                                                                   |
| risk_per_trade_pct: float = Field(0.01, ge=0.001, le=0.05)        |
|                                                                   |
| max_daily_loss_pct: float = Field(0.05, ge=0.01, le=0.15)         |
|                                                                   |
| max_drawdown_pct: float = Field(0.10, ge=0.05, le=0.30)           |
|                                                                   |
| max_open_positions: int = Field(5, ge=1, le=20)                   |
|                                                                   |
| global_max_leverage: int = Field(5, ge=1, le=20)                  |
|                                                                   |
| \# Execution                                                      |
|                                                                   |
| symbols: list\[str\] = \[\'BTCUSDT\'\]                            |
|                                                                   |
| timeframe: str = \'1h\'                                           |
|                                                                   |
| execution_delay_ms: int = Field(100, ge=0, le=5000)               |
|                                                                   |
| \# Sizing                                                         |
|                                                                   |
| sizing_method: str = \'fixed_fractional\'                         |
|                                                                   |
| \# LLM                                                            |
|                                                                   |
| llm_enabled: bool = False                                         |
|                                                                   |
| llm_daily_budget_usd:float = 1.0                                  |
|                                                                   |
| \@validator(\'mode\')                                             |
|                                                                   |
| def validate_mode(cls, v):                                        |
|                                                                   |
| if v not in (\'paper\',\'shadow\',\'live\'):                      |
|                                                                   |
| raise ValueError(f\'mode harus paper\|shadow\|live, dapat: {v}\') |
|                                                                   |
| return v                                                          |
+-------------------------------------------------------------------+

  ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------
  **VALIDASI STARTUP:** Jika AgentConfig gagal diparse (ada field tidak valid), main.py WAJIB exit dengan code 1 dan log pesan error yang jelas. Sistem tidak boleh jalan dengan config yang salah.

  ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------

**3.2 main.py --- Main Loop**

**Alur Startup Lengkap**

+------------------------------------------------------------------------+
| async def main():                                                      |
|                                                                        |
| \# 1. Load & validate config                                           |
|                                                                        |
| config = AgentConfig(\*\*load_env())                                   |
|                                                                        |
| \# 2. Inisialisasi security                                            |
|                                                                        |
| key_mgr = KeyManager(config)                                           |
|                                                                        |
| perms = key_mgr.validate_permissions(\'binance\')                      |
|                                                                        |
| assert not perms.can_withdraw, \'FATAL: API key punya izin withdraw!\' |
|                                                                        |
| \# 3. Inisialisasi semua layer (urutan penting)                        |
|                                                                        |
| data_layer = DataLayer(config, key_mgr)                                |
|                                                                        |
| intel_layer = IntelligenceLayer(config)                                |
|                                                                        |
| portfolio = PortfolioLayer(config)                                     |
|                                                                        |
| risk_mgr = RiskManager(config)                                         |
|                                                                        |
| strategy = load_strategy(config)                                       |
|                                                                        |
| trade_mgr = TradeManager(config, risk_mgr)                             |
|                                                                        |
| executor = ExecutionLayer(config, key_mgr)                             |
|                                                                        |
| exit_mgr = ExitManager(config)                                         |
|                                                                        |
| sync = SyncManager(config, executor)                                   |
|                                                                        |
| monitoring = MonitoringSystem(config)                                  |
|                                                                        |
| \# 4. Recovery: restore state sebelum mulai                            |
|                                                                        |
| await trade_mgr.recovery.restore()                                     |
|                                                                        |
| await sync.full_reconciliation()                                       |
|                                                                        |
| \# 5. Mulai heartbeat & monitoring                                     |
|                                                                        |
| await monitoring.start()                                               |
|                                                                        |
| \# 6. Main event loop                                                  |
|                                                                        |
| async for candle in data_layer.stream():                               |
|                                                                        |
| await tick(candle, \...all_layers\...)                                 |
+------------------------------------------------------------------------+

**tick() --- Urutan Eksekusi Per Candle**

  ----------------------------------------------------------------------------------------------------------------------------------
  **Step**   **Aksi**                                                  **Layer**                 **Gagal → Aksi**
  ---------- --------------------------------------------------------- ------------------------- -----------------------------------
  1          Validasi & anomaly check data candle masuk                data_layer/validator      Skip candle, log warning

  2          Update intelligence: regime, volatility, market_state     intelligence_layer/       Gunakan state sebelumnya

  3          Update exit monitoring: cek SL/TP/trailing posisi aktif   exit_layer/               Alert + emergency close

  4          Sync balance & posisi jika interval terpenuhi             sync/                     Log error, jangan skip risk check

  5          Panggil strategy.generate_signal(market_state)            strategy_layer/           Skip signal, tidak trade

  6          Evaluasi signal melalui risk_manager.evaluate()           risk_layer/               Block trade, log reason

  7          Buat trade request & cek idempotency                      trade_layer/idempotency   Lempar DuplicateOrderError

  8          Eksekusi order ke exchange                                execution_layer/          Retry N kali, lalu alert

  9          Simpan trade ke store & audit_trail                       trade_layer/store         Log error, jangan crash

  10         Update portfolio state & equity                           portfolio_layer/          Alert jika equity drop drastis

  11         Trigger LLM advisor jika enabled & budget cukup           llm_layer/                Skip, tidak blocking
  ----------------------------------------------------------------------------------------------------------------------------------

**3.3 event_bus.py**

Pub/sub async antar layer menggunakan asyncio.Queue. Tidak ada layer yang boleh import layer lain secara langsung untuk komunikasi event.

**Event Types**

  --------------------------------------------------------------------------------------------------
  **Event**          **Publisher**     **Subscriber(s)**                     **Payload**
  ------------------ ----------------- ------------------------------------- -----------------------
  CANDLE_READY       data_layer        intelligence, strategy                Candle dataclass

  SIGNAL_GENERATED   strategy_layer    risk_layer, trade_layer               Signal dataclass

  TRADE_OPENED       trade_layer       exit_layer, portfolio, monitoring     Trade dataclass

  TRADE_CLOSED       exit_layer        trade_layer, portfolio, learning      TradeResult dataclass

  CIRCUIT_BREAKER    risk_layer        main_loop, monitoring, notification   CircuitState enum

  EQUITY_UPDATE      portfolio_layer   risk_layer                            float --- new equity

  SYSTEM_ERROR       Semua layer       monitoring, notification              ErrorReport dataclass
  --------------------------------------------------------------------------------------------------

**3.4 safe_mode.py --- Emergency Stop**

+--------------------------------------------------------------+
| class SafeMode:                                              |
|                                                              |
| async def emergency_stop(self, reason: str):                 |
|                                                              |
| \"\"\"                                                       |
|                                                              |
| Urutan shutdown darurat yang aman:                           |
|                                                              |
| 1\. Set global halt flag → semua tick() langsung return      |
|                                                              |
| 2\. Cancel semua open orders di exchange                     |
|                                                              |
| 3\. Opsional: close semua posisi (jika close_positions=True) |
|                                                              |
| 4\. Simpan state snapshot ke disk                            |
|                                                              |
| 5\. Kirim alert ke Telegram + Discord                        |
|                                                              |
| 6\. Log reason ke errors.log                                 |
|                                                              |
| 7\. Exit process dengan code 2 (bukan 0)                     |
|                                                              |
| \"\"\"                                                       |
|                                                              |
| async def graceful_shutdown(self):                           |
|                                                              |
| \"\"\"                                                       |
|                                                              |
| Shutdown normal (misal: maintenance):                        |
|                                                              |
| 1\. Hentikan terima signal baru                              |
|                                                              |
| 2\. Tunggu trade yang sedang proses selesai (timeout 30s)    |
|                                                              |
| 3\. Simpan state                                             |
|                                                              |
| 4\. Exit code 0                                              |
|                                                              |
| \"\"\"                                                       |
+--------------------------------------------------------------+

**4. data_layer/ --- Input Market**

**4.1 market.py --- REST Data**

**Interface Publik**

  ----------------------------------------------------------------------------------------------
  **Fungsi**          **Parameter**                          **Return**          **Cache TTL**
  ------------------- -------------------------------------- ------------------- ---------------
  get_ticker()        symbol: str                            Ticker dataclass    5 detik

  get_ohlcv()         symbol: str, tf: str, limit: int=100   list\[Candle\]      1 candle (tf)

  get_balance()       asset: str=\'USDT\'                    Balance dataclass   10 detik

  get_open_orders()   symbol: str=None                       list\[Order\]       No cache

  get_position()      symbol: str (futures only)             Position \| None    5 detik
  ----------------------------------------------------------------------------------------------

**Ticker Dataclass**

+-----------------------------------------------+
| \@dataclass                                   |
|                                               |
| class Ticker:                                 |
|                                               |
| symbol: str                                   |
|                                               |
| bid: float                                    |
|                                               |
| ask: float                                    |
|                                               |
| last: float                                   |
|                                               |
| spread_pct: float \# (ask - bid) / mid \* 100 |
|                                               |
| volume_24h: float                             |
|                                               |
| timestamp: datetime                           |
+-----------------------------------------------+

**4.2 websocket_client.py**

Mengelola koneksi WebSocket ke Binance untuk data real-time. Harus auto-reconnect dan tidak boleh kehilangan candle saat reconnect.

**Interface Publik**

  --------------------------------------------------------------------------------------------------------------------
  **Fungsi**                **Parameter**                                    **Return**
  ------------------------- ------------------------------------------------ -----------------------------------------
  subscribe_kline()         symbol: str, tf: str, callback: Callable         asyncio.Task

  subscribe_orderbook()     symbol: str, depth: int=20, callback: Callable   asyncio.Task

  subscribe_user_stream()   callback: Callable                               asyncio.Task --- order updates, balance

  disconnect()              ---                                              None
  --------------------------------------------------------------------------------------------------------------------

**Reconnect Policy**

  -----------------------------------------------------------------------------------------------------------------------
  **Kondisi**               **Aksi**                                             **Max Retry**   **Alert?**
  ------------------------- ---------------------------------------------------- --------------- ------------------------
  Connection lost           Reconnect exponential backoff (1s, 2s, 4s, 8s\...)   10x             Setelah 3x gagal

  Message timeout (\>30s)   Force reconnect                                      Infinite        Setelah 60s tanpa data

  Invalid message           Log & skip                                           ---             Setelah 10x berturut

  Listen key expired        Refresh listen key & reconnect                       3x              Ya
  -----------------------------------------------------------------------------------------------------------------------

**4.3 rate_limiter.py --- Global API Rate Limiter**

Binance menggunakan sistem weight per endpoint. Rate limiter ini mencegah ban dengan melacak konsumsi weight secara terpusat.

+------------------------------------------------------------------------+
| class BinanceRateLimiter:                                              |
|                                                                        |
| \# Limit Binance (per 1 menit rolling window)                          |
|                                                                        |
| WEIGHT_LIMIT = 1200 \# request weight                                  |
|                                                                        |
| ORDER_LIMIT_10S = 50 \# order per 10 detik                             |
|                                                                        |
| ORDER_LIMIT_1D = 160000 \# order per hari                              |
|                                                                        |
| \# Weight per endpoint (sebagian)                                      |
|                                                                        |
| WEIGHT = {                                                             |
|                                                                        |
| \'GET /api/v3/ticker\': 2,                                             |
|                                                                        |
| \'GET /api/v3/klines\': 2,                                             |
|                                                                        |
| \'POST /api/v3/order\': 1,                                             |
|                                                                        |
| \'GET /api/v3/account\': 20,                                           |
|                                                                        |
| \'GET /fapi/v2/account\':5,                                            |
|                                                                        |
| }                                                                      |
|                                                                        |
| async def acquire(self, endpoint: str, weight: int = None):            |
|                                                                        |
| \"\"\"Tunggu sampai weight tersedia sebelum request boleh jalan.\"\"\" |
+------------------------------------------------------------------------+

**4.4 anomaly_detector.py**

  -----------------------------------------------------------------------------------------------------------------------
  **Anomali**                           **Deteksi**                         **Aksi**
  ------------------------------------- ----------------------------------- ---------------------------------------------
  Price spike \> 5% dalam 1 candle      abs(log_return) \> 0.05             Skip candle, log warning, tunggu konfirmasi

  Volume 0 pada liquid pair             volume == 0 untuk BTCUSDT/ETHUSDT   Skip candle, alert

  Stale data (timestamp tidak update)   last_ts == prev_ts lebih 2× tf      Trigger reconnect websocket

  Spread \> 1% (BTCUSDT)                (ask-bid)/mid \> 0.01               Larang eksekusi market order

  Bid \> Ask (crossed book)             bid \>= ask                         Halt trading, alert kritis
  -----------------------------------------------------------------------------------------------------------------------

**5. intelligence_layer/ --- Pemahaman Market**

**5.1 regime.py**

Mengklasifikasikan kondisi market ke dalam regime. Strategi yang berbeda diaktifkan berdasarkan regime aktif.

**MarketRegime Enum**

+---------------------------------------------------------------+
| class MarketRegime(Enum):                                     |
|                                                               |
| STRONG_TREND_UP = \'strong_trend_up\'                         |
|                                                               |
| WEAK_TREND_UP = \'weak_trend_up\'                             |
|                                                               |
| SIDEWAYS = \'sideways\'                                       |
|                                                               |
| WEAK_TREND_DOWN = \'weak_trend_down\'                         |
|                                                               |
| STRONG_TREND_DOWN = \'strong_trend_down\'                     |
|                                                               |
| HIGH_VOLATILITY = \'high_volatility\' \# override regime lain |
|                                                               |
| UNDEFINED = \'undefined\' \# saat data tidak cukup            |
+---------------------------------------------------------------+

**Klasifikasi Regime**

  --------------------------------------------------------------------------------------------
  **Kondisi**                     **Regime**          **Strategi yang Aktif**
  ------------------------------- ------------------- ----------------------------------------
  ADX \> 30 dan close \> EMA50    STRONG_TREND_UP     Trend following, long bias

  ADX 20--30 dan close \> EMA50   WEAK_TREND_UP       Trend following konservatif

  ADX \< 20                       SIDEWAYS            Mean reversion, reduce position size

  ADX 20--30 dan close \< EMA50   WEAK_TREND_DOWN     Trend following, short bias (futures)

  ADX \> 30 dan close \< EMA50    STRONG_TREND_DOWN   Trend following, short bias agresif

  ATR percentile \> 85%           HIGH_VOLATILITY     Kurangi position size 50%, perketat SL
  --------------------------------------------------------------------------------------------

**Interface Publik**

  ------------------------------------------------------------------------------------------------------------
  **Fungsi**           **Parameter**                        **Return**
  -------------------- ------------------------------------ --------------------------------------------------
  classify()           df: pd.DataFrame (candle historis)   MarketRegime

  get_regime_score()   df: pd.DataFrame                     dict\[regime, confidence_0_to_1\]

  is_regime_stable()   df, lookback: int=5                  bool --- True jika regime sama N candle terakhir
  ------------------------------------------------------------------------------------------------------------

**5.2 volatility.py**

  ---------------------------------------------------------------------------------------------------------------
  **Fungsi**             **Return**                        **Formula**
  ---------------------- --------------------------------- ------------------------------------------------------
  get_atr()              float                             ATR(14) via ta-lib

  get_atr_percentile()   float 0--100                      ATR saat ini vs rolling 252 candle

  get_realized_vol()     float --- annualized              std(log_return, 20) × sqrt(252×24) untuk 1H

  get_vol_regime()       str: low\|normal\|high\|extreme   Berdasarkan ATR percentile: \<25\|25-75\|75-90\|\>90
  ---------------------------------------------------------------------------------------------------------------

**5.3 market_state.py --- Agregat**

**MarketState Dataclass --- Input untuk Strategi**

+----------------------------------------------------------------------+
| \@dataclass                                                          |
|                                                                      |
| class MarketState:                                                   |
|                                                                      |
| symbol: str                                                          |
|                                                                      |
| timestamp: datetime                                                  |
|                                                                      |
| \# Price                                                             |
|                                                                      |
| last_price: float                                                    |
|                                                                      |
| bid: float                                                           |
|                                                                      |
| ask: float                                                           |
|                                                                      |
| spread_pct: float                                                    |
|                                                                      |
| \# Candle                                                            |
|                                                                      |
| latest_candle: Candle                                                |
|                                                                      |
| candles_df: pd.DataFrame \# 200 candle historis                      |
|                                                                      |
| \# Intelligence                                                      |
|                                                                      |
| regime: MarketRegime                                                 |
|                                                                      |
| regime_confidence:float                                              |
|                                                                      |
| vol_regime: str \# low\|normal\|high\|extreme                        |
|                                                                      |
| atr: float                                                           |
|                                                                      |
| atr_percentile: float                                                |
|                                                                      |
| \# Features (output feature_engineering, siap untuk model)           |
|                                                                      |
| features: pd.Series \# nama kolom = feature_names dari metadata.json |
|                                                                      |
| \# Portfolio context                                                 |
|                                                                      |
| open_positions: dict \# symbol → Position                            |
|                                                                      |
| available_equity: float                                              |
+----------------------------------------------------------------------+

**6. strategy_layer/ --- Sinyal Trading**

**6.1 base_strategy.py --- Abstract Base**

+---------------------------------------------------------------------------------------+
| from abc import ABC, abstractmethod                                                   |
|                                                                                       |
| class BaseStrategy(ABC):                                                              |
|                                                                                       |
| def \_\_init\_\_(self, config: AgentConfig, model: TrainedModel):                     |
|                                                                                       |
| self.config = config                                                                  |
|                                                                                       |
| self.model = model                                                                    |
|                                                                                       |
| \@abstractmethod                                                                      |
|                                                                                       |
| def generate_signal(self, state: MarketState) -\> Signal \| None:                     |
|                                                                                       |
| \"\"\"                                                                                |
|                                                                                       |
| Kembalikan Signal jika ada peluang, None jika tidak.                                  |
|                                                                                       |
| TIDAK boleh: akses exchange, simpan ke DB, kirim notifikasi.                          |
|                                                                                       |
| BOLEH: baca state, panggil model.predict(), hitung threshold.                         |
|                                                                                       |
| \"\"\"                                                                                |
|                                                                                       |
| \@abstractmethod                                                                      |
|                                                                                       |
| def should_exit(self, position: Position, state: MarketState) -\> ExitSignal \| None: |
|                                                                                       |
| \"\"\"Evaluasi apakah posisi yang ada perlu ditutup.\"\"\"                            |
|                                                                                       |
| def get_signal_confidence(self, state: MarketState) -\> float:                        |
|                                                                                       |
| \"\"\"Override untuk strategi yang butuh confidence scoring.\"\"\"                    |
|                                                                                       |
| return 1.0                                                                            |
+---------------------------------------------------------------------------------------+

**Signal Dataclass**

+----------------------------------------------------+
| \@dataclass                                        |
|                                                    |
| class Signal:                                      |
|                                                    |
| symbol: str                                        |
|                                                    |
| side: str \# BUY \| SELL                           |
|                                                    |
| signal_type: str \# market \| limit \| conditional |
|                                                    |
| confidence: float \# 0.0 -- 1.0                    |
|                                                    |
| suggested_price: float \# 0.0 = market price       |
|                                                    |
| suggested_sl: float \# stop loss price             |
|                                                    |
| suggested_tp: float \| None \# take profit price   |
|                                                    |
| strategy_id: str \# identifier strategi            |
|                                                    |
| reasoning: str \# untuk logging & LLM review       |
|                                                    |
| timestamp: datetime                                |
|                                                    |
| metadata: dict \# data tambahan bebas              |
+----------------------------------------------------+

**6.2 Config Keys Strategy**

  -------------------------------------------------------------------------------------------------------------
  **Key**                   **Default**   **Keterangan**
  ------------------------- ------------- ---------------------------------------------------------------------
  MIN_SIGNAL_CONFIDENCE     0.60          Confidence minimum untuk signal diproses. Di bawah ini → diabaikan.

  SIGNAL_COOLDOWN_CANDLES   3             Jumlah candle minimum antara dua signal pada simbol yang sama.

  MAX_SIGNALS_PER_TICK      2             Maksimal signal yang diproses per candle. Cegah burst order.

  REGIME_FILTER             True          Jika True: signal diabaikan saat regime HIGH_VOLATILITY.

  MODEL_THRESHOLD           0.0           Threshold prediksi model (return atau probabilitas) untuk masuk.
  -------------------------------------------------------------------------------------------------------------

**7. portfolio_layer/ --- Kontrol Modal Global**

Portfolio layer adalah pengawas modal di level tertinggi. Ia tidak membuat keputusan trading tapi memastikan tidak ada strategi yang mengambil lebih dari jatahnya.

**7.1 allocator.py**

**Interface Publik**

  --------------------------------------------------------------------------------------------------
  **Fungsi**            **Parameter**                     **Return**
  --------------------- --------------------------------- ------------------------------------------
  get_allocation()      strategy_id: str, symbol: str     float --- max USDT yang boleh digunakan

  update_allocation()   strategy_id: str, used: float     None --- update internal tracking

  rebalance()           portfolio_state: PortfolioState   dict\[strategy_id, new_allocation\]

  get_free_equity()     ---                               float --- equity yang belum dialokasikan
  --------------------------------------------------------------------------------------------------

**Aturan Alokasi**

  --------------------------------------------------------------------------------------------------
  **Aturan**           **Formula**                              **Config Key**
  -------------------- ---------------------------------------- ------------------------------------
  Max per strategi     total_equity × max_strategy_pct          MAX_STRATEGY_ALLOCATION_PCT = 0.40

  Max per simbol       total_equity × max_symbol_pct            MAX_SYMBOL_ALLOCATION_PCT = 0.20

  Reserve cash         total_equity × min_cash_reserve          MIN_CASH_RESERVE_PCT = 0.10

  Correlation budget   Aset berkorelasi max combined exposure   MAX_CORRELATED_PCT = 0.35
  --------------------------------------------------------------------------------------------------

**7.2 capital_manager.py**

+----------------------------------------------------------+
| class CapitalManager:                                    |
|                                                          |
| def update(self, new_equity: float) -\> CapitalStatus:   |
|                                                          |
| \"\"\"                                                   |
|                                                          |
| Dipanggil setelah setiap trade close atau sync periodik. |
|                                                          |
| Kembalikan CapitalStatus yang berisi:                    |
|                                                          |
| \- daily_pnl: float                                      |
|                                                          |
| \- drawdown_pct: float (dari peak)                       |
|                                                          |
| \- equity_at_risk: float (exposure terbuka saat ini)     |
|                                                          |
| \- safe_to_trade: bool                                   |
|                                                          |
| \- warnings: list\[str\]                                 |
|                                                          |
| \"\"\"                                                   |
|                                                          |
| def compound_equity(self) -\> float:                     |
|                                                          |
| \"\"\"                                                   |
|                                                          |
| Untuk paper mode: hitung equity yang sudah di-compound.  |
|                                                          |
| Untuk live mode: ambil dari sync/balance_sync.py.        |
|                                                          |
| \"\"\"                                                   |
+----------------------------------------------------------+

**8. risk_layer/ --- Gate Utama Sebelum Order**

Detail lengkap Risk Layer sudah didokumentasikan terpisah di skeleton code yang di-generate. Bagian ini fokus pada integrasi dengan layer lain.

**8.1 Interface yang Dikonsumsi Layer Lain**

  ------------------------------------------------------------------------------------------------------------------------------------------------
  **Caller**                   **Memanggil**                           **Input Wajib di portfolio_state**
  ---------------------------- --------------------------------------- ---------------------------------------------------------------------------
  trade_layer/manager.py       risk_manager.evaluate(request, state)   equity, open_positions, exchange_status, market_halted, last_price_SYMBOL

  trade_layer/manager.py       risk_manager.update_equity(equity)      float --- equity terbaru dari balance_sync

  core/scheduler.py            risk_manager.reset_daily_stats()        Dipanggil tepat UTC 00:00

  exit_layer/exit_manager.py   circuit_breaker.record_loss/win()       Dipanggil setelah setiap trade close
  ------------------------------------------------------------------------------------------------------------------------------------------------

**8.2 portfolio_state --- Format Lengkap**

+-----------------------------------------------------------+
| \# Dict yang dikirim ke risk_manager.evaluate()           |
|                                                           |
| portfolio_state = {                                       |
|                                                           |
| \# Wajib                                                  |
|                                                           |
| \'equity\': 10_000.0, \# float --- equity saat ini        |
|                                                           |
| \'open_positions\': { \# dict\[symbol, PositionInfo\]     |
|                                                           |
| \'BTCUSDT\': {                                            |
|                                                           |
| \'notional\': 500.0, \# qty × entry_price                 |
|                                                           |
| \'side\': \'BUY\',                                        |
|                                                           |
| \'qty\': 0.01,                                            |
|                                                           |
| }                                                         |
|                                                           |
| },                                                        |
|                                                           |
| \'exchange_status\': \'normal\', \# normal \| maintenance |
|                                                           |
| \'market_halted\': False,                                 |
|                                                           |
| \# Harga (untuk setiap simbol yang diperdagangkan)        |
|                                                           |
| \'last_price_BTCUSDT\': 50_000.0,                         |
|                                                           |
| \# Opsional (untuk sizing method tertentu)                |
|                                                           |
| \'atr_BTCUSDT\': 800.0, \# untuk volatility_scaled        |
|                                                           |
| \'strategy_stats\': { \# untuk kelly sizing               |
|                                                           |
| \'strategy_001\': {                                       |
|                                                           |
| \'win_rate\': 0.55,                                       |
|                                                           |
| \'avg_risk_reward\': 1.8,                                 |
|                                                           |
| }                                                         |
|                                                           |
| },                                                        |
|                                                           |
| }                                                         |
+-----------------------------------------------------------+

**9. trade_layer/ --- Orkestrasi Trade**

**9.1 trade.py --- Trade Dataclass**

+---------------------------------------------------------------------------+
| \@dataclass                                                               |
|                                                                           |
| class Trade:                                                              |
|                                                                           |
| \# Identitas --- IMMUTABLE setelah dibuat                                 |
|                                                                           |
| trade_id: str \# UUID v4                                                  |
|                                                                           |
| client_order_id: str \# format: {strategy_id}\_{symbol}\_{timestamp_ms}   |
|                                                                           |
| exchange_order_id:str \| None = None \# diisi setelah konfirmasi exchange |
|                                                                           |
| \# Detail                                                                 |
|                                                                           |
| symbol: str                                                               |
|                                                                           |
| side: str \# BUY \| SELL                                                  |
|                                                                           |
| order_type: str \# market \| limit                                        |
|                                                                           |
| requested_qty: float                                                      |
|                                                                           |
| filled_qty: float = 0.0                                                   |
|                                                                           |
| avg_fill_price: float = 0.0                                               |
|                                                                           |
| \# Risk params                                                            |
|                                                                           |
| sl_price: float = 0.0                                                     |
|                                                                           |
| tp_price: float = 0.0                                                     |
|                                                                           |
| risk_amount_usd: float = 0.0                                              |
|                                                                           |
| \# Status                                                                 |
|                                                                           |
| status: str = \'pending\' \# pending\|open\|closed\|cancelled\|failed     |
|                                                                           |
| strategy_id: str = \'\'                                                   |
|                                                                           |
| mode: str = \'paper\'                                                     |
|                                                                           |
| \# Timestamps                                                             |
|                                                                           |
| created_at: datetime = field(default_factory=datetime.utcnow)             |
|                                                                           |
| opened_at: datetime \| None = None                                        |
|                                                                           |
| closed_at: datetime \| None = None                                        |
|                                                                           |
| \# PnL (diisi saat close)                                                 |
|                                                                           |
| pnl_usd: float = 0.0                                                      |
|                                                                           |
| pnl_pct: float = 0.0                                                      |
|                                                                           |
| commission_usd: float = 0.0                                               |
+---------------------------------------------------------------------------+

**9.2 idempotency.py --- Anti Double Order**

Mencegah order ganda yang bisa terjadi saat: restart agent, timeout jaringan, atau bug di loop.

**Cara Kerja**

> **1.** Sebelum kirim order, hash client_order_id dan cek di order_registry.db.
>
> **2.** Jika sudah ada: return status order yang existing, JANGAN kirim baru.
>
> **3.** Jika belum ada: simpan ke registry dengan status=\'pending\', lanjut kirim.
>
> **4.** Setelah konfirmasi exchange: update status=\'confirmed\'.
>
> **5.** Registry entry disimpan minimal 24 jam setelah close.

+---------------------------------------------------------------------------+
| class IdempotencyManager:                                                 |
|                                                                           |
| def check_or_register(self, client_order_id: str) -\> IdempotencyResult:  |
|                                                                           |
| \"\"\"                                                                    |
|                                                                           |
| Return:                                                                   |
|                                                                           |
| \- IdempotencyResult.PROCEED jika order baru, langsung daftar             |
|                                                                           |
| \- IdempotencyResult.DUPLICATE jika sudah ada (jangan kirim lagi)         |
|                                                                           |
| \- IdempotencyResult.PENDING jika sedang diproses (tunggu konfirmasi)     |
|                                                                           |
| \"\"\"                                                                    |
|                                                                           |
| def confirm(self, client_order_id: str, exchange_order_id: str) -\> None: |
|                                                                           |
| \"\"\"Dipanggil setelah exchange konfirmasi order diterima.\"\"\"         |
|                                                                           |
| def mark_failed(self, client_order_id: str, reason: str) -\> None:        |
|                                                                           |
| \"\"\"Dipanggil jika order gagal total setelah semua retry.\"\"\"         |
+---------------------------------------------------------------------------+

**9.3 recovery.py --- State Recovery**

Dipanggil saat startup untuk memastikan internal state sinkron dengan kondisi exchange setelah crash.

**Recovery Flow**

+---------------------------------------------------------------------+
| class RecoveryManager:                                              |
|                                                                     |
| async def restore(self) -\> RecoveryReport:                         |
|                                                                     |
| \"\"\"                                                              |
|                                                                     |
| Urutan recovery saat startup:                                       |
|                                                                     |
| 1\. Load semua trade dengan status=\'open\' dari state.db           |
|                                                                     |
| 2\. Query exchange untuk setiap open order (by client_order_id)     |
|                                                                     |
| 3\. Reconcile perbedaan:                                            |
|                                                                     |
| \- Order ada di DB tapi tidak di exchange → mark cancelled          |
|                                                                     |
| \- Order ada di exchange tapi tidak di DB → tambahkan (ghost order) |
|                                                                     |
| \- Status berbeda → update DB ke status exchange (truth of record)  |
|                                                                     |
| 4\. Load open positions dari exchange                               |
|                                                                     |
| 5\. Pastikan setiap posisi punya SL terpasang                       |
|                                                                     |
| 6\. Kembalikan RecoveryReport (summary apa yang direcovery)         |
|                                                                     |
| \"\"\"                                                              |
+---------------------------------------------------------------------+

  -----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------
  **ATURAN:** recovery.py WAJIB selesai sukses sebelum main loop mulai menerima signal. Jika recovery gagal atau ada discrepancy yang tidak bisa diselesaikan otomatis, sistem WAJIB masuk safe_mode.

  -----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------

**9.4 audit_trail.py**

+----------------------------------------------------------------------------------+
| \# Setiap event pada sebuah trade dicatat sebagai immutable record               |
|                                                                                  |
| \@dataclass                                                                      |
|                                                                                  |
| class AuditEvent:                                                                |
|                                                                                  |
| event_id: str \# UUID                                                            |
|                                                                                  |
| trade_id: str                                                                    |
|                                                                                  |
| event_type: str \# CREATED\|SUBMITTED\|FILLED\|SL_HIT\|TP_HIT\|CLOSED\|CANCELLED |
|                                                                                  |
| timestamp: datetime                                                              |
|                                                                                  |
| actor: str \# \'strategy\'\|\'risk\'\|\'exit_manager\'\|\'manual\'\|\'recovery\' |
|                                                                                  |
| details: dict \# data spesifik per event type                                    |
|                                                                                  |
| equity_snapshot: float \# equity saat event terjadi                              |
|                                                                                  |
| \# Audit trail TIDAK BOLEH dihapus atau diedit.                                  |
|                                                                                  |
| \# Jika ada koreksi, tambahkan event CORRECTION di atasnya.                      |
+----------------------------------------------------------------------------------+

**10. execution_layer/ --- Eksekusi ke Exchange**

**10.1 exchange.py --- Abstract Base**

+-------------------------------------------------------------------------------------------+
| from abc import ABC, abstractmethod                                                       |
|                                                                                           |
| class BaseExchange(ABC):                                                                  |
|                                                                                           |
| \@abstractmethod                                                                          |
|                                                                                           |
| async def place_order(self, order: OrderRequest) -\> OrderResponse: \...                  |
|                                                                                           |
| \@abstractmethod                                                                          |
|                                                                                           |
| async def cancel_order(self, symbol: str, order_id: str) -\> bool: \...                   |
|                                                                                           |
| \@abstractmethod                                                                          |
|                                                                                           |
| async def get_order_status(self, symbol: str, client_order_id: str) -\> OrderStatus: \... |
|                                                                                           |
| \@abstractmethod                                                                          |
|                                                                                           |
| async def get_balance(self, asset: str) -\> float: \...                                   |
|                                                                                           |
| \@abstractmethod                                                                          |
|                                                                                           |
| async def get_position(self, symbol: str) -\> Position \| None: \...                      |
|                                                                                           |
| \@abstractmethod                                                                          |
|                                                                                           |
| async def set_leverage(self, symbol: str, leverage: int) -\> bool: \...                   |
|                                                                                           |
| \@abstractmethod                                                                          |
|                                                                                           |
| def get_lot_filter(self, symbol: str) -\> LotFilter: \...                                 |
+-------------------------------------------------------------------------------------------+

**10.2 OrderRequest & OrderResponse Dataclass**

+-------------------------------------------------------------------+
| \@dataclass                                                       |
|                                                                   |
| class OrderRequest:                                               |
|                                                                   |
| symbol: str                                                       |
|                                                                   |
| side: str \# BUY \| SELL                                          |
|                                                                   |
| order_type: str \# MARKET \| LIMIT \| STOP_MARKET                 |
|                                                                   |
| quantity: float \# sudah di-round ke step_size                    |
|                                                                   |
| price: float \# 0.0 untuk market                                  |
|                                                                   |
| stop_price: float \# untuk STOP_MARKET                            |
|                                                                   |
| client_order_id: str \# idempotency key                           |
|                                                                   |
| is_futures: bool = False                                          |
|                                                                   |
| reduce_only: bool = False \# hanya untuk close posisi             |
|                                                                   |
| time_in_force: str = \'GTC\' \# GTC \| IOC \| FOK                 |
|                                                                   |
| \@dataclass                                                       |
|                                                                   |
| class OrderResponse:                                              |
|                                                                   |
| exchange_order_id: str                                            |
|                                                                   |
| client_order_id: str                                              |
|                                                                   |
| status: str \# NEW\|FILLED\|PARTIALLY_FILLED\|CANCELLED\|REJECTED |
|                                                                   |
| filled_qty: float                                                 |
|                                                                   |
| avg_price: float                                                  |
|                                                                   |
| commission: float                                                 |
|                                                                   |
| commission_asset: str \# \'USDT\' atau \'BNB\'                    |
|                                                                   |
| timestamp: datetime                                               |
|                                                                   |
| raw_response: dict \# response mentah dari exchange               |
+-------------------------------------------------------------------+

**10.3 smart_router.py --- Routing Antar Exchange**

  ----------------------------------------------------------------------------------------------------------------------------
  **Routing Strategy**   **Kapan Digunakan**                **Cara Kerja**
  ---------------------- ---------------------------------- ------------------------------------------------------------------
  Best Price             Default                            Bandingkan spread + commission di semua exchange, pilih termurah

  Primary Exchange       Jika best price selisih \< 0.05%   Selalu gunakan exchange utama (Binance) untuk konsistensi

  Fallback               Primary down/rate limited          Otomatis pindah ke secondary exchange

  Split Order            Order size \> 30% ADV exchange     Pecah order ke 2-3 exchange untuk mengurangi impact
  ----------------------------------------------------------------------------------------------------------------------------

**10.4 Retry Policy --- Execution**

  ----------------------------------------------------------------------------------------------------------
  **Error Type**         **Max Retry**   **Delay**                **Alert?**   **Aksi Akhir**
  ---------------------- --------------- ------------------------ ------------ -----------------------------
  Network timeout        3x              1s, 2s, 4s               Setelah 3x   mark_failed() + log

  Rate limit (429)       5x              5s, 10s, 20s, 40s, 80s   Setelah 2x   Tunggu window reset

  Order rejected (400)   1x              0s                       Ya           Cek alasan, kemungkinan bug

  Insufficient balance   0x              ---                      Ya           Block signal, alert

  Exchange maintenance   Infinite        60s interval             Segera       Tunggu maintenance selesai
  ----------------------------------------------------------------------------------------------------------

**11. exit_layer/ --- Manajemen Keluar Posisi**

**11.1 exit_manager.py --- Koordinator**

Dipanggil setiap tick untuk setiap posisi aktif. Mengecek semua kondisi exit secara berurutan.

+------------------------------------------------------------------------------------+
| class ExitManager:                                                                 |
|                                                                                    |
| async def evaluate(self, position: Position, state: MarketState) -\> ExitDecision: |
|                                                                                    |
| \"\"\"                                                                             |
|                                                                                    |
| Urutan pengecekan (berhenti saat pertama terpenuhi):                               |
|                                                                                    |
| 1\. Hard SL: harga tembus sl_price → EXIT_SL                                       |
|                                                                                    |
| 2\. Hard TP: harga tembus tp_price → EXIT_TP                                       |
|                                                                                    |
| 3\. Break-even: profit \> threshold, geser SL ke entry → UPDATE_SL                 |
|                                                                                    |
| 4\. Trailing stop: update trail level → UPDATE_SL atau EXIT_TRAIL                  |
|                                                                                    |
| 5\. Strategy exit: strategy.should_exit() → EXIT_SIGNAL                            |
|                                                                                    |
| 6\. Time-based exit: posisi terlalu lama tanpa progress → EXIT_TIMEOUT             |
|                                                                                    |
| 7\. Tidak ada → HOLD                                                               |
|                                                                                    |
| \"\"\"                                                                             |
+------------------------------------------------------------------------------------+

**ExitDecision Dataclass**

+-----------------------------------------------------------------------------------------+
| \@dataclass                                                                             |
|                                                                                         |
| class ExitDecision:                                                                     |
|                                                                                         |
| action: str \# HOLD\|EXIT_SL\|EXIT_TP\|EXIT_TRAIL\|EXIT_SIGNAL\|EXIT_TIMEOUT\|UPDATE_SL |
|                                                                                         |
| exit_price: float \# harga exit (0 jika HOLD atau UPDATE_SL)                            |
|                                                                                         |
| new_sl: float \# sl baru (hanya untuk UPDATE_SL)                                        |
|                                                                                         |
| reason: str                                                                             |
|                                                                                         |
| urgency: str \# normal \| urgent (urgent = market order, tidak tunggu)                  |
+-----------------------------------------------------------------------------------------+

**11.2 trailing.py --- Trailing Stop**

  -----------------------------------------------------------------------------------------------------------------------
  **Method**       **Formula**                           **Config Key**            **Kapan Dipakai**
  ---------------- ------------------------------------- ------------------------- --------------------------------------
  **ATR-based**    trail = high - (N × ATR)              TRAIL_ATR_MULT = 2.0      Default --- menyesuaikan volatilitas

  **Percentage**   trail = high × (1 - pct)              TRAIL_PCT = 0.02          Pair dengan volatilitas rendah

  **Chandelier**   trail = highest_high(N) - (M × ATR)   TRAIL_CHANDELIER_N = 22   Trend following jangka panjang
  -----------------------------------------------------------------------------------------------------------------------

**Config Keys Exit Layer**

  --------------------------------------------------------------------------------------------------
  **Key**                 **Default**   **Keterangan**
  ----------------------- ------------- ------------------------------------------------------------
  BREAKEVEN_TRIGGER_R     1.0           Aktifkan break-even setelah profit = 1× risk (1R)

  BREAKEVEN_BUFFER_PIPS   5             Buffer di atas entry saat geser ke break-even

  PARTIAL_TP_ENABLED      True          Tutup 50% posisi saat TP1, sisanya trailing

  PARTIAL_TP_RATIO        0.5           Proporsi posisi yang ditutup di TP pertama

  MAX_HOLD_CANDLES        48            Paksa close jika posisi terbuka \> N candle tanpa progress
  --------------------------------------------------------------------------------------------------

**12. monitoring/ & sync/ --- Kesehatan Sistem**

**12.1 heartbeat.py**

+------------------------------------------------------------+
| class Heartbeat:                                           |
|                                                            |
| INTERVAL_SECONDS = 30                                      |
|                                                            |
| DEAD_THRESHOLD = 90 \# jika tidak ada heartbeat 90s → DEAD |
|                                                            |
| async def start(self):                                     |
|                                                            |
| \"\"\"                                                     |
|                                                            |
| Setiap 30 detik:                                           |
|                                                            |
| \- Tulis timestamp ke heartbeat.db                         |
|                                                            |
| \- Cek apakah semua coroutine masih running                |
|                                                            |
| \- Update metrics Prometheus (agent_alive gauge)           |
|                                                            |
| \"\"\"                                                     |
|                                                            |
| async def check(self) -\> HeartbeatStatus:                 |
|                                                            |
| \"\"\"                                                     |
|                                                            |
| Dipanggil dari health_restart.py (automation).             |
|                                                            |
| Return ALIVE \| STALE \| DEAD                              |
|                                                            |
| STALE = heartbeat ada tapi sudah lama (30--90 detik)       |
|                                                            |
| DEAD = tidak ada heartbeat \> 90 detik → trigger restart   |
|                                                            |
| \"\"\"                                                     |
+------------------------------------------------------------+

**12.2 health_check.py**

  ------------------------------------------------------------------------------------------
  **Komponen**    **Check**                **Threshold**               **Aksi Jika Gagal**
  --------------- ------------------------ --------------------------- ---------------------
  WebSocket       Last message timestamp   \< 60 detik lalu            Reconnect WS

  Exchange REST   Ping /api/v3/ping        Response \< 2000ms          Alert + retry

  Database        SELECT 1 dari state.db   \< 100ms                    Alert kritis

  Memory usage    RSS process              \< 500 MB                   Alert + log

  Disk space      Free space data dir      \< 1 GB                     Alert kritis

  Open orders     Jumlah order pending     \< max_open_positions × 2   Alert jika stuck
  ------------------------------------------------------------------------------------------

**12.3 sync/ --- Protokol Rekonsiliasi**

  ------------------------------------------------------------------------------------------------------------------------
  **File**            **Frekuensi**        **Apa yang di-sync**                **Konflik → Aksi**
  ------------------- -------------------- ----------------------------------- -------------------------------------------
  balance_sync.py     Setiap 60 detik      USDT balance aktual dari exchange   Update internal, alert jika selisih \> 1%

  order_sync.py       Setiap 30 detik      Status semua open order             Update DB ke status exchange

  position_sync.py    Setiap 60 detik      Posisi futures aktual vs internal   Alert + log --- butuh investigasi manual

  reconciliation.py   Harian (UTC 00:00)   Full audit: PnL, fee, semua trade   Generate laporan, simpan ke audit/
  ------------------------------------------------------------------------------------------------------------------------

  -----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------
  **TRUTH OF RECORD:** Exchange selalu menjadi sumber kebenaran (truth of record). Jika internal state berbeda dengan exchange, SELALU update internal mengikuti exchange --- bukan sebaliknya.

  -----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------

**13. learning_layer/ --- Adaptasi & Refleksi**

**13.1 drift_detection.py**

Mendeteksi apakah distribusi fitur input bergeser dari distribusi saat training. Ini adalah early warning system untuk model degradation.

  ------------------------------------------------------------------------------------------------------------------
  **Metrik**                **Formula**                               **Threshold Alert**   **Frekuensi**
  ------------------------- ----------------------------------------- --------------------- ------------------------
  PSI per fitur             Population Stability Index                PSI \> 0.2            Harian

  IC rolling                Spearman(pred, actual) 20 hari terakhir   IC \< 0.03            Harian

  Win rate drift            WR 30 hari vs baseline                    Turun \> 10%          Harian

  Prediction distribution   KL divergence pred vs training dist       KL \> 0.5             Per batch 100 prediksi
  ------------------------------------------------------------------------------------------------------------------

**13.2 strategy_killer.py --- Auto-Disable**

+---------------------------------------------------------------+
| class StrategyKiller:                                         |
|                                                               |
| \"\"\"                                                        |
|                                                               |
| Auto-disable strategi yang underperform.                      |
|                                                               |
| Kriteria disable (ANY terpenuhi):                             |
|                                                               |
| \- Consecutive losses \> MAX_CONSECUTIVE_LOSSES (default: 7)  |
|                                                               |
| \- Sharpe 30 hari \< SHARPE_KILL_THRESHOLD (default: -0.5)    |
|                                                               |
| \- Win rate 30 hari \< WIN_RATE_KILL_THRESHOLD (default: 35%) |
|                                                               |
| \- Max drawdown 30 hari \> DD_KILL_THRESHOLD (default: 15%)   |
|                                                               |
| Saat disabled:                                                |
|                                                               |
| 1\. Stop terima signal baru dari strategi ini                 |
|                                                               |
| 2\. Posisi yang sudah ada tetap di-manage sampai close        |
|                                                               |
| 3\. Kirim notifikasi: strategi X di-disable beserta alasannya |
|                                                               |
| 4\. Simpan ke disabled_strategies.json untuk audit            |
|                                                               |
| 5\. Trigger re-evaluation setelah 7 hari (atau manual review) |
|                                                               |
| \"\"\"                                                        |
+---------------------------------------------------------------+

**14. llm_layer/ --- AI Advisor (Opsional)**

  ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------
  **PRINSIP UTAMA:** LLM hanya berperan sebagai advisor/filter dengan bobot pada confidence scoring. LLM TIDAK PERNAH mengeksekusi order secara langsung. Eksekusi tetap melalui risk_layer dan trade_layer.

  ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------

**14.1 llm_budget.py --- Cost Control**

+---------------------------------------------------------------------+
| class LLMBudget:                                                    |
|                                                                     |
| async def can_call(self, estimated_tokens: int) -\> bool:           |
|                                                                     |
| \"\"\"                                                              |
|                                                                     |
| Cek apakah masih ada budget untuk panggil LLM.                      |
|                                                                     |
| Hitung: estimated_cost = tokens × price_per_token                   |
|                                                                     |
| Return False jika daily_spent + estimated_cost \> DAILY_BUDGET_USD  |
|                                                                     |
| \"\"\"                                                              |
|                                                                     |
| async def record_usage(self, input_tokens: int, output_tokens: int, |
|                                                                     |
| model: str) -\> float:                                              |
|                                                                     |
| \"\"\"Catat pemakaian, return actual cost USD.\"\"\"                |
|                                                                     |
| def get_remaining_budget(self) -\> float:                           |
|                                                                     |
| \"\"\"Return sisa budget hari ini dalam USD.\"\"\"                  |
+---------------------------------------------------------------------+

**Harga Model (Referensi --- update sesuai tarif terbaru)**

  ------------------------------------------------------------------------------------------------------------------------------
  **Model**          **Input (per 1M token)**   **Output (per 1M token)**   **Rekomendasi Penggunaan**
  ------------------ -------------------------- --------------------------- ----------------------------------------------------
  claude-sonnet-4    \$3.00                     \$15.00                     Default --- balance antara kualitas dan biaya

  claude-haiku-4.5   \$0.25                     \$1.25                      Untuk analisis frekuensi tinggi (per sinyal)

  claude-opus-4      \$15.00                    \$75.00                     Hanya untuk analisis mendalam --- batasi 1-2x/hari
  ------------------------------------------------------------------------------------------------------------------------------

**14.2 llm_filter.py --- Scoring Only**

LLM menghasilkan confidence multiplier (0.0--1.5) yang dikalikan dengan signal confidence asli. Bukan keputusan biner masuk/tidak masuk.

+--------------------------------------------------------------------------------+
| class LLMFilter:                                                               |
|                                                                                |
| async def score_signal(self, signal: Signal, state: MarketState) -\> LLMScore: |
|                                                                                |
| \"\"\"                                                                         |
|                                                                                |
| Input ke LLM:                                                                  |
|                                                                                |
| \- Kondisi market saat ini (regime, volatility, spread)                        |
|                                                                                |
| \- Signal yang dihasilkan strategi                                             |
|                                                                                |
| \- 10 trade terakhir untuk konteks                                             |
|                                                                                |
| Output dari LLM (JSON):                                                        |
|                                                                                |
| {                                                                              |
|                                                                                |
| \'confidence_multiplier\': 0.8, // 0.0--1.5                                    |
|                                                                                |
| \'reasoning\': \'string\',                                                     |
|                                                                                |
| \'concerns\': \[\'list of concerns\'\],                                        |
|                                                                                |
| \'proceed\': true                                                              |
|                                                                                |
| }                                                                              |
|                                                                                |
| Final confidence = signal.confidence × multiplier                              |
|                                                                                |
| Jika final \< MIN_SIGNAL_CONFIDENCE → signal diabaikan                         |
|                                                                                |
| \"\"\"                                                                         |
+--------------------------------------------------------------------------------+

**15. notification/ & utils/ --- Pendukung**

**15.1 notifier.py --- Routing Notifikasi**

  -----------------------------------------------------------------------------------------------------------
  **Event**                   **Channel**          **Priority**   **Format**
  --------------------------- -------------------- -------------- -------------------------------------------
  Trade opened                Telegram             Normal         Symbol, side, qty, price, SL, TP

  Trade closed (profit)       Telegram             Normal         Symbol, PnL USD, PnL%, durasi

  Trade closed (loss)         Telegram + Discord   High           Symbol, PnL USD, PnL%, alasan exit

  Circuit breaker triggered   Telegram + Discord   URGENT         Alasan, equity saat ini, daily PnL

  Strategy disabled           Telegram + Discord   High           Strategy ID, alasan, performa

  System error                Telegram + Discord   URGENT         Error message + stack trace

  Daily summary               Telegram             Normal         Total PnL, win rate, jumlah trade, equity
  -----------------------------------------------------------------------------------------------------------

**15.2 structured_logger.py --- JSON Logging**

Semua log ditulis dalam format JSON agar bisa diquery Prometheus dan ditampilkan di Grafana.

+----------------------------------------------------------+
| \# Format log standar (setiap baris = satu JSON object)  |
|                                                          |
| {                                                        |
|                                                          |
| \"timestamp\": \"2024-11-15T08:30:00.123Z\",             |
|                                                          |
| \"level\": \"INFO\",                                     |
|                                                          |
| \"layer\": \"trade_layer\",                              |
|                                                          |
| \"event\": \"trade_opened\",                             |
|                                                          |
| \"trade_id\": \"uuid-\...\",                             |
|                                                          |
| \"symbol\": \"BTCUSDT\",                                 |
|                                                          |
| \"side\": \"BUY\",                                       |
|                                                          |
| \"qty\": 0.001,                                          |
|                                                          |
| \"price\": 50000.0,                                      |
|                                                          |
| \"mode\": \"paper\",                                     |
|                                                          |
| \"equity\": 10234.5                                      |
|                                                          |
| }                                                        |
|                                                          |
| \# Field WAJIB di setiap log:                            |
|                                                          |
| \# timestamp (ISO 8601 UTC), level, layer, event         |
|                                                          |
| \# Field opsional: trade_id, symbol, strategy_id, equity |
+----------------------------------------------------------+

**15.3 utils/helpers.py --- Fungsi Umum**

  -----------------------------------------------------------------------------------------------------------------------------------------
  **Fungsi**      **Parameter**                          **Return**                      **Catatan**
  --------------- -------------------------------------- ------------------------------- --------------------------------------------------
  round_qty()     qty: float, step: float                float                           Floor ke step_size --- JANGAN round biasa

  round_price()   price: float, tick: float              float                           Round ke tick_size terdekat

  safe_div()      a: float, b: float, default: float=0   float                           Cegah ZeroDivisionError

  retry_async()   coro, max_retry, backoff               Any                             Decorator untuk retry dengan exponential backoff

  utcnow()        ---                                    datetime (timezone-aware UTC)   Selalu gunakan ini, bukan datetime.utcnow()
  -----------------------------------------------------------------------------------------------------------------------------------------

**16. Checklist Deploy Runtime ke Live Mode**

  ------------------------------------------------------------------------------------------------------------------------------------------------------------
  **No**   **Checklist Item**                                                    **Cara Verifikasi**                                                **PIC**
  -------- --------------------------------------------------------------------- ------------------------------------------------------------------ ----------
  1        .env sudah diisi semua field wajib (API key, master key, notif)       python -c \'from core.config import AgentConfig; AgentConfig()\'   DevOps

  2        validate_permissions() → can_withdraw=False                           Log saat startup: \'Permissions OK\'                               Security

  3        AgentConfig lulus Pydantic validation tanpa error                     Tidak ada ValueError saat startup                                  Dev

  4        recovery.py selesai tanpa unresolved discrepancy                      Log: \'Recovery complete, 0 discrepancies\'                        Dev

  5        full_reconciliation() menunjukkan balance cocok                       balance_sync delta \< 0.1%                                         Ops

  6        Semua heartbeat check hijau                                           health_check.py semua komponen HEALTHY                             Ops

  7        Paper mode minimal 2 minggu dengan PnL positif                        performance.db menunjukkan Sharpe \> 1                             Quant

  8        Shadow mode minimal 1 minggu --- tidak ada ghost order                order_registry.db tidak ada DUPLICATE                              Dev

  9        Rate limiter dikonfigurasi sesuai tier akun Binance                   Tidak ada 429 error di paper/shadow                                Dev

  10       Circuit breaker threshold disesuaikan modal                           max_daily_loss_pct sesuai risk appetite                            Quant

  11       Notifikasi Telegram + Discord berfungsi                               Kirim test alert manual                                            Ops

  12       Grafana dashboard menampilkan semua metrik                            prometheus_metrics endpoint accessible                             Ops

  13       rollback_guide.md sudah dibaca dan dipahami                           Team acknowledgment                                                All

  14       Backup state.db dilakukan sebelum live                                Cron backup aktif                                                  Ops

  15       Initial capital dikunci --- tidak lebih dari budget yang disepakati   INITIAL_EQUITY di .env = budget yang disetujui                     Lead
  ------------------------------------------------------------------------------------------------------------------------------------------------------------

+:--------------------------------------------------------------------------------------------------------------------------------------:+
| **Dokumen ini adalah kontrak implementasi Runtime Layer.**                                                                             |
|                                                                                                                                        |
| Perubahan pada interface publik, config key default, atau format dataclass WAJIB diupdate di dokumen ini sebelum merge ke main branch. |
|                                                                                                                                        |
| *Referensi terkait: research_layer_docs.docx • risk_layer skeleton code*                                                               |
+----------------------------------------------------------------------------------------------------------------------------------------+
