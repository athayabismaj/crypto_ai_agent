**crypto_ai_agent**

**Dokumentasi: Data Layer**

**&**

**Intelligence Layer**

*market · orderbook · websocket · validator · anomaly_detector · rate_limiter*

*regime · volatility · market_state · latency_guard*

Versi 1.0 \| Referensi: agent_core_docs.docx · runtime_layer_docs.docx

  ------------------------------------------------

  ------------------------------------------------

**1. Overview --- Dua Layer, Satu Tujuan**

Data Layer dan Intelligence Layer bekerja secara berpasangan. Data Layer bertugas mengumpulkan dan memvalidasi data mentah dari exchange. Intelligence Layer mengubah data tersebut menjadi pemahaman market yang bisa dikonsumsi oleh Strategy Layer.

  ----------------------------------------------------------------------------------------------------
  **Aspek**        **Data Layer**                           **Intelligence Layer**
  ---------------- ---------------------------------------- ------------------------------------------
  Input            Exchange API, WebSocket stream           Candle tervalidasi, ticker, orderbook

  Output           Candle, Ticker, Orderbook tervalidasi    MarketState --- aggregat siap pakai

  Bisa gagal?      Ya --- koneksi drop, data corrupt        Lebih toleran --- pakai state sebelumnya

  I/O?             Ya --- network, WebSocket, REST          Tidak --- pure komputasi in-memory

  Thread-safe?     Tidak --- single async task per stream   Ya --- bisa dipanggil dari mana saja

  Dipanggil oleh   main_loop, scheduler                     main_loop (setiap tick)
  ----------------------------------------------------------------------------------------------------

**1.1 Data Flow Lengkap**

+---------------------------------------------------------------------+
| Exchange (REST / WebSocket)                                         |
|                                                                     |
| │                                                                   |
|                                                                     |
| ▼                                                                   |
|                                                                     |
| data_layer/websocket_client.py ← stream candle raw                  |
|                                                                     |
| data_layer/market.py ← REST polling (fallback + enrichment)         |
|                                                                     |
| data_layer/orderbook.py ← depth snapshot & stream                   |
|                                                                     |
| │                                                                   |
|                                                                     |
| ▼                                                                   |
|                                                                     |
| data_layer/validator.py ← cek format, range, timestamp              |
|                                                                     |
| data_layer/anomaly_detector.py ← deteksi spike, stale, crossed book |
|                                                                     |
| data_layer/rate_limiter.py ← throttle sebelum setiap request        |
|                                                                     |
| │                                                                   |
|                                                                     |
| ▼ (Candle, Ticker, Orderbook --- sudah bersih)                      |
|                                                                     |
| │                                                                   |
|                                                                     |
| intelligence_layer/regime.py ← trending / sideways / volatile       |
|                                                                     |
| intelligence_layer/volatility.py ← ATR, realized vol, percentile    |
|                                                                     |
| intelligence_layer/market_state.py ← agregasi jadi MarketState      |
|                                                                     |
| intelligence_layer/latency_guard.py ← proteksi saat latency tinggi  |
|                                                                     |
| │                                                                   |
|                                                                     |
| ▼ (MarketState)                                                     |
|                                                                     |
| │                                                                   |
|                                                                     |
| strategy_layer/ ← consume MarketState                               |
+---------------------------------------------------------------------+

**BAGIAN A --- Data Layer**

**2. market.py --- REST Data Feed**

Mengakses data market via REST API. Digunakan untuk: initial data load saat startup, fallback saat WebSocket drop, polling data yang tidak tersedia via stream (funding rate, open interest, account balance).

**2.1 Dataclasses Output**

+-----------------------------------------------------+
| \@dataclass                                         |
|                                                     |
| class Candle:                                       |
|                                                     |
| symbol: str                                         |
|                                                     |
| timeframe: str                                      |
|                                                     |
| timestamp: datetime \# UTC, awal candle             |
|                                                     |
| open: float                                         |
|                                                     |
| high: float                                         |
|                                                     |
| low: float                                          |
|                                                     |
| close: float                                        |
|                                                     |
| volume: float                                       |
|                                                     |
| is_closed: bool \# False jika candle masih berjalan |
|                                                     |
| source: str \# \'websocket\' \| \'rest\'            |
|                                                     |
| \@dataclass                                         |
|                                                     |
| class Ticker:                                       |
|                                                     |
| symbol: str                                         |
|                                                     |
| bid: float                                          |
|                                                     |
| ask: float                                          |
|                                                     |
| last: float                                         |
|                                                     |
| mid: float \# (bid + ask) / 2                       |
|                                                     |
| spread: float \# ask - bid                          |
|                                                     |
| spread_pct: float \# spread / mid \* 100            |
|                                                     |
| volume_24h: float                                   |
|                                                     |
| change_24h: float \# pct change 24h                 |
|                                                     |
| timestamp: datetime                                 |
|                                                     |
| \@dataclass                                         |
|                                                     |
| class Balance:                                      |
|                                                     |
| asset: str                                          |
|                                                     |
| free: float \# tersedia untuk trading               |
|                                                     |
| locked: float \# terkunci dalam order terbuka       |
|                                                     |
| total: float \# free + locked                       |
|                                                     |
| usd_value: float \# estimasi nilai USD              |
|                                                     |
| timestamp: datetime                                 |
|                                                     |
| \@dataclass                                         |
|                                                     |
| class FundingRate:                                  |
|                                                     |
| symbol: str                                         |
|                                                     |
| rate: float \# contoh: 0.0001 = 0.01%               |
|                                                     |
| next_time: datetime \# kapan funding berikutnya     |
|                                                     |
| timestamp: datetime                                 |
+-----------------------------------------------------+

**2.2 Interface Publik**

  ----------------------------------------------------------------------------------------------------------
  **Fungsi**            **Parameter**           **Return**       **Cache TTL**   **Binance Endpoint**
  --------------------- ----------------------- ---------------- --------------- ---------------------------
  get_candles()         symbol, tf, limit=200   list\[Candle\]   1 tf            /api/v3/klines

  get_ticker()          symbol: str             Ticker           5s              /api/v3/ticker/bookTicker

  get_balance()         asset: str=\'USDT\'     Balance          10s             /api/v3/account

  get_open_orders()     symbol: str=None        list\[Order\]    No              /api/v3/openOrders

  get_position()        symbol: str             Position\|None   5s              /fapi/v2/positionRisk

  get_funding_rate()    symbol: str             FundingRate      30s             /fapi/v1/premiumIndex

  get_exchange_info()   symbol: str=None        ExchangeInfo     1 jam           /api/v3/exchangeInfo

  get_server_time()     ---                     datetime         No              /api/v3/time
  ----------------------------------------------------------------------------------------------------------

**2.3 ExchangeInfo & LotFilter**

+--------------------------------------------------------------------------+
| \@dataclass                                                              |
|                                                                          |
| class LotFilter:                                                         |
|                                                                          |
| symbol: str                                                              |
|                                                                          |
| min_qty: float \# minimum order quantity                                 |
|                                                                          |
| max_qty: float \# maximum order quantity                                 |
|                                                                          |
| step_size: float \# kelipatan qty yang valid                             |
|                                                                          |
| min_notional: float \# nilai minimum dalam USDT                          |
|                                                                          |
| tick_size: float \# presisi harga (untuk limit order)                    |
|                                                                          |
| \@dataclass                                                              |
|                                                                          |
| class ExchangeInfo:                                                      |
|                                                                          |
| symbol: str                                                              |
|                                                                          |
| status: str \# \'TRADING\' \| \'BREAK\' \| \'HALT\'                      |
|                                                                          |
| lot_filter: LotFilter                                                    |
|                                                                          |
| price_filter: PriceFilter                                                |
|                                                                          |
| is_spot: bool                                                            |
|                                                                          |
| is_futures: bool                                                         |
|                                                                          |
| \# Cara penggunaan:                                                      |
|                                                                          |
| \# info = await market.get_exchange_info(\'BTCUSDT\')                    |
|                                                                          |
| \# qty = round_qty(raw_qty, info.lot_filter.step_size) \# via helpers.py |
|                                                                          |
| \# notional = qty \* price                                               |
|                                                                          |
| \# assert notional \>= info.lot_filter.min_notional                      |
+--------------------------------------------------------------------------+

**2.4 Caching Policy**

  ------------------------------------------------------------------------------------------------------------------------------
  **Data**                 **Alasan Cache**                                     **Invalidasi**
  ------------------------ ---------------------------------------------------- ------------------------------------------------
  Ticker (5s)              Dibutuhkan setiap tick --- REST polling mahal        Otomatis TTL; WebSocket update juga invalidate

  Balance (10s)            Tidak berubah kecuali ada trade                      Otomatis TTL; trade close juga invalidate

  Position (5s)            Update real-time via user stream WebSocket           WebSocket positionUpdate event invalidate

  ExchangeInfo (1 jam)     Sangat jarang berubah --- hanya saat listing baru    Manual invalidate via gateway endpoint

  Open Orders (no cache)   Harus real-time --- status bisa berubah kapan saja   ---
  ------------------------------------------------------------------------------------------------------------------------------

  -----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------
  **ATURAN CACHE:** Cache disimpan di memory (dict dengan timestamp). Tidak menggunakan Redis atau eksternal cache --- menghindari dependency tambahan dan latency. Jika cache miss, langsung hit REST API.

  -----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------

**3. orderbook.py --- Depth Data**

Mengelola orderbook depth: snapshot periodik via REST dan update real-time via WebSocket. Digunakan oleh intelligence_layer untuk spread analysis dan oleh execution_layer untuk estimasi slippage.

**3.1 Dataclasses**

+---------------------------------------------------------------------------------+
| \@dataclass                                                                     |
|                                                                                 |
| class OrderbookLevel:                                                           |
|                                                                                 |
| price: float                                                                    |
|                                                                                 |
| quantity: float                                                                 |
|                                                                                 |
| \@dataclass                                                                     |
|                                                                                 |
| class Orderbook:                                                                |
|                                                                                 |
| symbol: str                                                                     |
|                                                                                 |
| bids: list\[OrderbookLevel\] \# urutan DESC (harga tertinggi dulu)              |
|                                                                                 |
| asks: list\[OrderbookLevel\] \# urutan ASC (harga terendah dulu)                |
|                                                                                 |
| timestamp: datetime                                                             |
|                                                                                 |
| last_update_id: int \# Binance update sequence ID                               |
|                                                                                 |
| \@property                                                                      |
|                                                                                 |
| def best_bid(self) -\> float: return self.bids\[0\].price if self.bids else 0.0 |
|                                                                                 |
| \@property                                                                      |
|                                                                                 |
| def best_ask(self) -\> float: return self.asks\[0\].price if self.asks else 0.0 |
|                                                                                 |
| \@property                                                                      |
|                                                                                 |
| def mid_price(self) -\> float:                                                  |
|                                                                                 |
| return (self.best_bid + self.best_ask) / 2                                      |
|                                                                                 |
| \@property                                                                      |
|                                                                                 |
| def spread_pct(self) -\> float:                                                 |
|                                                                                 |
| if self.mid_price == 0: return 0.0                                              |
|                                                                                 |
| return (self.best_ask - self.best_bid) / self.mid_price \* 100                  |
|                                                                                 |
| def get_bid_depth(self, pct: float = 0.01) -\> float:                           |
|                                                                                 |
| \"\"\"Total volume bid dalam range pct% dari best bid.\"\"\"                    |
|                                                                                 |
| def get_ask_depth(self, pct: float = 0.01) -\> float:                           |
|                                                                                 |
| \"\"\"Total volume ask dalam range pct% dari best ask.\"\"\"                    |
|                                                                                 |
| def estimate_fill_price(self, qty: float, side: str) -\> float:                 |
|                                                                                 |
| \"\"\"Walk through bids/asks untuk estimasi avg fill price.\"\"\"               |
+---------------------------------------------------------------------------------+

**3.2 Interface Publik**

  -------------------------------------------------------------------------------------------------------------------
  **Fungsi**        **Parameter**                   **Return**      **Keterangan**
  ----------------- ------------------------------- --------------- -------------------------------------------------
  get_snapshot()    symbol: str, depth: int=20      Orderbook       REST call --- untuk initial load

  get_latest()      symbol: str                     Orderbook       Return dari in-memory store (WebSocket updated)

  subscribe()       symbol: str, depth: int=20      asyncio.Task    Start WebSocket stream, update in-memory

  is_fresh()        symbol: str, max_age_s: int=5   bool            Cek apakah orderbook masih fresh

  get_imbalance()   symbol: str, depth: int=5       float -1 to 1   Bid vs ask volume imbalance
  -------------------------------------------------------------------------------------------------------------------

**3.3 Orderbook Imbalance**

+-------------------------------------------------------+
| def get_imbalance(self, depth: int = 5) -\> float:    |
|                                                       |
| \"\"\"                                                |
|                                                       |
| Mengukur tekanan beli vs jual di N level teratas.     |
|                                                       |
| Formula:                                              |
|                                                       |
| bid_vol = sum(bids\[0:depth\].quantity)               |
|                                                       |
| ask_vol = sum(asks\[0:depth\].quantity)               |
|                                                       |
| imbalance = (bid_vol - ask_vol) / (bid_vol + ask_vol) |
|                                                       |
| Interpretasi:                                         |
|                                                       |
| +1.0 = semua volume di bid (tekanan beli kuat)        |
|                                                       |
| 0.0 = seimbang                                        |
|                                                       |
| -1.0 = semua volume di ask (tekanan jual kuat)        |
|                                                       |
| Threshold untuk intelligence_layer:                   |
|                                                       |
| imbalance \> +0.3 → sinyal bullish tambahan           |
|                                                       |
| imbalance \< -0.3 → sinyal bearish tambahan           |
|                                                       |
| \"\"\"                                                |
+-------------------------------------------------------+

**3.4 WebSocket Update Handling**

+---------------------------------------------------------------------------+
| \# Binance WebSocket diff depth stream:                                   |
|                                                                           |
| \# 1. Ambil snapshot awal via REST (depth=1000 untuk akurasi)             |
|                                                                           |
| \# 2. Buffer semua WebSocket event yang masuk                             |
|                                                                           |
| \# 3. Drop event dengan lastUpdateId \<= snapshot.lastUpdateId            |
|                                                                           |
| \# 4. Apply event yang U \<= snapshot.lastUpdateId + 1 \<= u              |
|                                                                           |
| \# 5. Update local orderbook:                                             |
|                                                                           |
| \# - Quantity = 0 → hapus level                                           |
|                                                                           |
| \# - Quantity \> 0 → update/tambah level                                  |
|                                                                           |
| \# 6. Urutkan ulang: bids DESC, asks ASC                                  |
|                                                                           |
| \# Jika ada gap dalam sequence ID → reset dengan ambil snapshot baru      |
|                                                                           |
| \# Ini disebut \'orderbook recovery\' dan harus silent (tidak alert user) |
+---------------------------------------------------------------------------+

**4. websocket_client.py --- Real-time Stream**

Satu-satunya titik masuk untuk semua data real-time. Mengelola multiple WebSocket connections, auto-reconnect, dan distribusi data ke subscriber.

**4.1 Stream yang Di-manage**

  ----------------------------------------------------------------------------------------------------------------------------
  **Stream**         **Binance URI**                **Data**                                       **Callback Destination**
  ------------------ ------------------------------ ---------------------------------------------- ---------------------------
  Kline/Candle       wss://stream\.../kline\_{tf}   OHLCV per candle                               market.py internal buffer

  Book Ticker        wss://stream\.../bookTicker    Best bid/ask real-time                         ticker cache

  Diff Depth         wss://stream\.../depth@100ms   Orderbook incremental update                   orderbook.py store

  User Data Stream   wss://stream\.../userData      Order fills, balance update, position update   sync layer callbacks
  ----------------------------------------------------------------------------------------------------------------------------

**4.2 Interface Publik**

+-----------------------------------------------------------------------+
| class WebSocketClient:                                                |
|                                                                       |
| async def connect(self) -\> None:                                     |
|                                                                       |
| \"\"\"Buat semua koneksi yang diperlukan sesuai config symbols.\"\"\" |
|                                                                       |
| async def disconnect(self) -\> None:                                  |
|                                                                       |
| \"\"\"Tutup semua koneksi secara graceful.\"\"\"                      |
|                                                                       |
| def subscribe_candle(                                                 |
|                                                                       |
| self,                                                                 |
|                                                                       |
| symbol: str,                                                          |
|                                                                       |
| tf: str,                                                              |
|                                                                       |
| callback: Callable\[\[Candle\], Awaitable\[None\]\]                   |
|                                                                       |
| ) -\> None:                                                           |
|                                                                       |
| \"\"\"Register callback untuk candle stream.\"\"\"                    |
|                                                                       |
| def subscribe_ticker(                                                 |
|                                                                       |
| self,                                                                 |
|                                                                       |
| symbol: str,                                                          |
|                                                                       |
| callback: Callable\[\[Ticker\], Awaitable\[None\]\]                   |
|                                                                       |
| ) -\> None:                                                           |
|                                                                       |
| def subscribe_orderbook(                                              |
|                                                                       |
| self,                                                                 |
|                                                                       |
| symbol: str,                                                          |
|                                                                       |
| callback: Callable\[\[Orderbook\], Awaitable\[None\]\]                |
|                                                                       |
| ) -\> None:                                                           |
|                                                                       |
| def subscribe_user_stream(                                            |
|                                                                       |
| self,                                                                 |
|                                                                       |
| on_order_update: Callable,                                            |
|                                                                       |
| on_balance_update: Callable,                                          |
|                                                                       |
| on_position_update: Callable                                          |
|                                                                       |
| ) -\> None:                                                           |
|                                                                       |
| def is_connected(self, symbol: str = None) -\> bool:                  |
|                                                                       |
| \"\"\"Cek status koneksi. None = cek semua koneksi.\"\"\"             |
|                                                                       |
| def get_connection_stats(self) -\> ConnectionStats:                   |
|                                                                       |
| \"\"\"Return latency, message count, uptime per stream.\"\"\"         |
+-----------------------------------------------------------------------+

**4.3 Reconnect Logic --- State Machine**

  --------------------------------------------------------------------------------------------------------------------------------------------------
  **State**          **Kondisi Masuk**                              **Aksi**                                          **Transisi Ke**
  ------------------ ---------------------------------------------- ------------------------------------------------- ------------------------------
  **CONNECTED**      Koneksi berhasil                               Proses pesan normal                               DISCONNECTED

  **DISCONNECTED**   Connection drop / timeout                      Stop proses, mulai timer reconnect                RECONNECTING

  **RECONNECTING**   Timer reconnect elapsed                        Coba reconnect dengan exponential backoff         CONNECTED / FAILED

  **FAILED**         Max retry (10x) habis                          Alert kritis, publish SYSTEM_ERROR ke event_bus   RECONNECTING (setelah 5 min)

  **STALE**          Tidak ada pesan \> MAX_STALE_S (default 30s)   Force disconnect → DISCONNECTED                   DISCONNECTED
  --------------------------------------------------------------------------------------------------------------------------------------------------

**4.4 Listen Key Management (User Stream)**

+-------------------------------------------------------------------------------------+
| \# Binance User Data Stream menggunakan \'listen key\' yang expire setiap 60 menit. |
|                                                                                     |
| \# Sistem harus refresh secara berkala.                                             |
|                                                                                     |
| class ListenKeyManager:                                                             |
|                                                                                     |
| REFRESH_INTERVAL = timedelta(minutes=30) \# refresh sebelum expire                  |
|                                                                                     |
| async def get_or_create(self) -\> str:                                              |
|                                                                                     |
| \"\"\"Buat listen key baru via POST /api/v3/userDataStream.\"\"\"                   |
|                                                                                     |
| async def refresh(self, listen_key: str) -\> bool:                                  |
|                                                                                     |
| \"\"\"Perpanjang listen key via PUT /api/v3/userDataStream.\"\"\"                   |
|                                                                                     |
| async def delete(self, listen_key: str) -\> bool:                                   |
|                                                                                     |
| \"\"\"Hapus listen key saat shutdown.\"\"\"                                         |
|                                                                                     |
| async def start_auto_refresh(self) -\> asyncio.Task:                                |
|                                                                                     |
| \"\"\"                                                                              |
|                                                                                     |
| Background task yang refresh listen key setiap REFRESH_INTERVAL.                    |
|                                                                                     |
| Jika refresh gagal 3x berturut-turut:                                               |
|                                                                                     |
| → Buat listen key baru                                                              |
|                                                                                     |
| → Reconnect WebSocket dengan key baru                                               |
|                                                                                     |
| → Alert warning (bukan critical)                                                    |
|                                                                                     |
| \"\"\"                                                                              |
+-------------------------------------------------------------------------------------+

**4.5 Config Keys WebSocket**

  --------------------------------------------------------------------------------------------------
  **Key**               **Default**   **Valid Range**   **Keterangan**
  --------------------- ------------- ----------------- --------------------------------------------
  WS_MAX_RECONNECT      10            3 -- 50           Max retry sebelum stream dinyatakan FAILED

  WS_RECONNECT_BASE_S   1             0.5 -- 5          Base delay exponential backoff (detik)

  WS_RECONNECT_MAX_S    60            30 -- 300         Max delay backoff

  WS_STALE_TIMEOUT_S    30            10 -- 120         Detik tanpa pesan → anggap stale

  WS_PING_INTERVAL_S    20            10 -- 60          Interval ping untuk keep-alive

  WS_MAX_QUEUE_SIZE     500           100 -- 2000       Buffer queue per stream
  --------------------------------------------------------------------------------------------------

**5. validator.py --- Validasi Data Masuk**

Gate pertama sebelum data masuk ke sistem. Validator memastikan setiap Candle, Ticker, dan Orderbook yang diproses memenuhi syarat minimum integritas.

**5.1 Candle Validation Rules**

  -------------------------------------------------------------------------------------------------------------------------
  **Rule**             **Check**                                                **Aksi Jika Gagal**         **Log Level**
  -------------------- -------------------------------------------------------- --------------------------- ---------------
  OHLC Logic           high \>= max(open, close) AND low \<= min(open, close)   Reject candle               ERROR

  Timestamp order      candle.timestamp \> prev_candle.timestamp                Reject candle               WARNING

  Timestamp gap        gap \<= expected_tf_seconds × MAX_GAP_MULTIPLIER         Log warning, tetap pakai    WARNING

  Price positif        open \> 0 AND high \> 0 AND low \> 0 AND close \> 0      Reject candle               ERROR

  Volume non-negatif   volume \>= 0                                             Reject candle               ERROR

  Candle closed        is_closed = True sebelum diproses strategi               Buffer, tunggu konfirmasi   DEBUG

  Symbol match         candle.symbol == expected_symbol                         Reject candle               ERROR

  Timeframe match      candle.timeframe == expected_tf                          Reject candle               ERROR
  -------------------------------------------------------------------------------------------------------------------------

**5.2 Ticker Validation Rules**

  -------------------------------------------------------------------------------------------------------------------------
  **Rule**          **Check**                                               **Aksi Jika Gagal**
  ----------------- ------------------------------------------------------- -----------------------------------------------
  Bid/Ask positif   bid \> 0 AND ask \> 0                                   Reject ticker, gunakan cache sebelumnya

  Bid \< Ask        bid \< ask (normal book)                                Reject --- crossed book, alert KRITIS

  Spread wajar      spread_pct \< MAX_SPREAD_PCT (default 2.0%)             Log warning, tandai ticker sebagai unreliable

  Freshness         timestamp \>= utcnow() - MAX_TICKER_AGE (default 10s)   Reject, trigger REST refresh
  -------------------------------------------------------------------------------------------------------------------------

**5.3 Interface Publik**

+----------------------------------------------------------------------+
| class DataValidator:                                                 |
|                                                                      |
| def validate_candle(self, candle: Candle) -\> ValidationResult:      |
|                                                                      |
| \"\"\"                                                               |
|                                                                      |
| Return ValidationResult:                                             |
|                                                                      |
| valid: bool                                                          |
|                                                                      |
| errors: list\[str\] --- deskripsi rule yang gagal                    |
|                                                                      |
| warnings:list\[str\] --- kondisi tidak ideal tapi masih bisa dipakai |
|                                                                      |
| \"\"\"                                                               |
|                                                                      |
| def validate_ticker(self, ticker: Ticker) -\> ValidationResult:      |
|                                                                      |
| \...                                                                 |
|                                                                      |
| def validate_orderbook(self, ob: Orderbook) -\> ValidationResult:    |
|                                                                      |
| \...                                                                 |
|                                                                      |
| def get_validation_stats(self) -\> ValidationStats:                  |
|                                                                      |
| \"\"\"                                                               |
|                                                                      |
| Return statistik rolling 1000 validasi terakhir:                     |
|                                                                      |
| \- reject_rate_pct per data type                                     |
|                                                                      |
| \- most_common_errors                                                |
|                                                                      |
| \- avg_warnings_per_candle                                           |
|                                                                      |
| Dipakai oleh monitoring/health_check.py                              |
|                                                                      |
| \"\"\"                                                               |
|                                                                      |
| \@dataclass                                                          |
|                                                                      |
| class ValidationResult:                                              |
|                                                                      |
| valid: bool                                                          |
|                                                                      |
| errors: list\[str\]                                                  |
|                                                                      |
| warnings: list\[str\]                                                |
|                                                                      |
| data_type:str \# \'candle\' \| \'ticker\' \| \'orderbook\'           |
+----------------------------------------------------------------------+

**5.4 Config Keys Validator**

  ------------------------------------------------------------------------------------------------
  **Key**              **Default**   **Keterangan**
  -------------------- ------------- -------------------------------------------------------------
  MAX_GAP_MULTIPLIER   3             Gap antar candle boleh max 3× timeframe sebelum warning

  MAX_SPREAD_PCT       2.0           Spread \> 2% dianggap tidak normal untuk pair liquid

  MAX_TICKER_AGE_S     10            Ticker dianggap stale setelah 10 detik

  MAX_OB_AGE_S         5             Orderbook dianggap stale setelah 5 detik

  STRICT_MODE          False         True = reject pada WARNING, False = hanya reject pada ERROR
  ------------------------------------------------------------------------------------------------

**6. anomaly_detector.py --- Deteksi Kondisi Abnormal**

Mendeteksi kondisi market yang tidak normal yang bisa menyebabkan kerugian jika trading dilanjutkan. Berbeda dengan validator yang cek integritas data --- anomaly detector cek kondisi market itu sendiri.

**6.1 Daftar Anomali & Threshold**

  ------------------------------------------------------------------------------------------------------------------------------------
  **Anomali ID**    **Kondisi**                               **Threshold Default**   **Aksi Sistem**                   **Severity**
  ----------------- ----------------------------------------- ----------------------- --------------------------------- --------------
  PRICE_SPIKE       abs(log_return 1 candle) terlalu besar    abs \> 5%               Skip candle, alert                HIGH

  VOLUME_ZERO       Volume = 0 pada pair liquid (BTCUSDT)     vol == 0                Skip candle, alert                HIGH

  STALE_DATA        Timestamp tidak update                    \> 2× tf                Trigger WS reconnect              CRITICAL

  CROSSED_BOOK      bid \>= ask di orderbook                  bid \>= ask             Halt trading, alert KRITIS        CRITICAL

  WIDE_SPREAD       Spread sangat lebar                       \> 1%                   Larang market order               MEDIUM

  LOW_LIQUIDITY     Depth \< threshold di best 5 level        \< MIN_DEPTH            Kurangi position size 50%         MEDIUM

  RAPID_MOVE        3 candle berturut bergerak searah \> 3%   3× \>3%                 Terapkan regime HIGH_VOLATILITY   MEDIUM

  FUNDING_EXTREME   Funding rate \> N% per 8 jam (futures)    \> 0.1%                 Alert, pertimbangkan keluar       LOW
  ------------------------------------------------------------------------------------------------------------------------------------

**6.2 Interface Publik**

+-------------------------------------------------------------------------------------------------+
| \@dataclass                                                                                     |
|                                                                                                 |
| class AnomalyReport:                                                                            |
|                                                                                                 |
| anomaly_id: str                                                                                 |
|                                                                                                 |
| severity: str \# LOW \| MEDIUM \| HIGH \| CRITICAL                                              |
|                                                                                                 |
| symbol: str                                                                                     |
|                                                                                                 |
| message: str                                                                                    |
|                                                                                                 |
| value: float \# nilai yang memicu anomali                                                       |
|                                                                                                 |
| threshold: float \# threshold yang dilanggar                                                    |
|                                                                                                 |
| timestamp: datetime                                                                             |
|                                                                                                 |
| recommended: str \# aksi yang direkomendasikan                                                  |
|                                                                                                 |
| class AnomalyDetector:                                                                          |
|                                                                                                 |
| def check_candle(self, candle: Candle, prev_candles: list\[Candle\]) -\> list\[AnomalyReport\]: |
|                                                                                                 |
| \"\"\"Cek anomali berbasis candle. Return list kosong jika aman.\"\"\"                          |
|                                                                                                 |
| def check_ticker(self, ticker: Ticker) -\> list\[AnomalyReport\]:                               |
|                                                                                                 |
| \"\"\"Cek anomali berbasis ticker (spread, freshness).\"\"\"                                    |
|                                                                                                 |
| def check_orderbook(self, ob: Orderbook) -\> list\[AnomalyReport\]:                             |
|                                                                                                 |
| \"\"\"Cek crossed book, thin book, wide spread.\"\"\"                                           |
|                                                                                                 |
| def is_safe_to_trade(self, symbol: str) -\> tuple\[bool, list\[AnomalyReport\]\]:               |
|                                                                                                 |
| \"\"\"                                                                                          |
|                                                                                                 |
| Aggregasi semua anomali aktif.                                                                  |
|                                                                                                 |
| Return (True, \[\]) jika aman.                                                                  |
|                                                                                                 |
| Return (False, \[reports\]) jika ada anomali HIGH atau CRITICAL.                                |
|                                                                                                 |
| MEDIUM anomali tidak blokir trading tapi ada di report.                                         |
|                                                                                                 |
| \"\"\"                                                                                          |
|                                                                                                 |
| def get_active_anomalies(self, symbol: str = None) -\> list\[AnomalyReport\]:                   |
|                                                                                                 |
| \"\"\"Anomali yang masih aktif (belum resolved).\"\"\"                                          |
|                                                                                                 |
| def resolve(self, anomaly_id: str, symbol: str) -\> None:                                       |
|                                                                                                 |
| \"\"\"Mark anomali sebagai resolved (kondisi kembali normal).\"\"\"                             |
+-------------------------------------------------------------------------------------------------+

**6.3 Config Keys Anomaly Detector**

  -----------------------------------------------------------------------------------------------
  **Key**                 **Default**   **Keterangan**
  ----------------------- ------------- ---------------------------------------------------------
  PRICE_SPIKE_THRESHOLD   0.05          5% --- \|log_return\| di atas ini = spike

  WIDE_SPREAD_PCT         1.0           1% --- spread di atas ini = wide spread

  MIN_BID_DEPTH_USD       10000         Minimal \$10k di 5 level bid untuk liquid

  RAPID_MOVE_CANDLES      3             Jumlah candle berturut untuk deteksi rapid move

  RAPID_MOVE_THRESHOLD    0.03          3% per candle untuk deteksi rapid move

  ANOMALY_PERSIST_S       300           Anomali dianggap aktif selama 300 detik setelah trigger

  FUNDING_EXTREME_PCT     0.001         0.1% per 8 jam = extreme funding rate
  -----------------------------------------------------------------------------------------------

**7. rate_limiter.py --- Global API Rate Limiter**

Mencegah banned IP dari Binance dengan melacak konsumsi weight secara terpusat. Semua request ke exchange WAJIB melewati rate_limiter.acquire() sebelum dieksekusi.

**7.1 Binance Rate Limit Architecture**

+------------------------------------------------------------+
| \# Binance memiliki 3 jenis limit yang independen:         |
|                                                            |
| \# 1. REQUEST WEIGHT (rolling 1 menit)                     |
|                                                            |
| \# Setiap endpoint punya \'weight\' berbeda                |
|                                                            |
| \# Limit: 1200 weight per menit untuk akun normal          |
|                                                            |
| \# Header response: X-MBX-USED-WEIGHT-1M                   |
|                                                            |
| \# 2. ORDER COUNT (per 10 detik)                           |
|                                                            |
| \# Limit: 50 order per 10 detik                            |
|                                                            |
| \# Header response: X-MBX-ORDER-COUNT-10S                  |
|                                                            |
| \# 3. ORDER COUNT (per hari)                               |
|                                                            |
| \# Limit: 160,000 order per hari                           |
|                                                            |
| \# Header response: X-MBX-ORDER-COUNT-1D                   |
|                                                            |
| \# Jika limit terlampaui:                                  |
|                                                            |
| \# HTTP 429 → agent harus stop request sampai window reset |
|                                                            |
| \# HTTP 418 → IP banned (jika terus request setelah 429)   |
|                                                            |
| \# Strategi aman: jaga penggunaan di bawah 80% limit       |
|                                                            |
| \# Weight target: \< 960 per menit                         |
|                                                            |
| \# Order target: \< 40 per 10 detik                        |
+------------------------------------------------------------+

**7.2 Weight Table --- Endpoint Utama**

  -----------------------------------------------------------------------------------------------
  **Endpoint**                    **Method**   **Weight**   **Catatan**
  ------------------------------- ------------ ------------ -------------------------------------
  GET /api/v3/ticker/bookTicker   GET          2            Satu simbol. Semua simbol = 2×count

  GET /api/v3/klines              GET          2            Per request, berapapun limit-nya

  GET /api/v3/account             GET          20           Cukup berat --- cache 10 detik

  GET /api/v3/openOrders          GET          6            Satu simbol. Semua simbol = 40

  POST /api/v3/order              POST         1            Create order

  DELETE /api/v3/order            DEL          1            Cancel order

  GET /api/v3/order               GET          4            Query order status

  GET /fapi/v2/account            GET          5            Futures account info

  GET /fapi/v2/positionRisk       GET          5            Posisi futures

  GET /api/v3/exchangeInfo        GET          20           Cache 1 jam --- jarang dipanggil
  -----------------------------------------------------------------------------------------------

**7.3 Interface Publik**

+-----------------------------------------------------------------------------+
| class BinanceRateLimiter:                                                   |
|                                                                             |
| async def acquire(                                                          |
|                                                                             |
| self,                                                                       |
|                                                                             |
| endpoint: str,                                                              |
|                                                                             |
| weight: int = None, \# None = lookup dari table otomatis                    |
|                                                                             |
| is_order: bool = False \# True = juga cek order count limit                 |
|                                                                             |
| ) -\> None:                                                                 |
|                                                                             |
| \"\"\"                                                                      |
|                                                                             |
| Tunggu sampai ada \'slot\' yang tersedia.                                   |
|                                                                             |
| Jika current_weight + weight \> WEIGHT_LIMIT × SAFE_RATIO:                  |
|                                                                             |
| await asyncio.sleep(time_until_window_reset)                                |
|                                                                             |
| \"\"\"                                                                      |
|                                                                             |
| def update_from_headers(self, headers: dict) -\> None:                      |
|                                                                             |
| \"\"\"                                                                      |
|                                                                             |
| Dipanggil setelah setiap response dari exchange.                            |
|                                                                             |
| Parse header X-MBX-USED-WEIGHT-1M untuk sync weight aktual.                 |
|                                                                             |
| Ini lebih akurat daripada tracking lokal.                                   |
|                                                                             |
| \"\"\"                                                                      |
|                                                                             |
| def get_current_usage(self) -\> RateLimitStatus:                            |
|                                                                             |
| \"\"\"Return current weight, order count, dan estimasi time to reset.\"\"\" |
|                                                                             |
| def is_near_limit(self, threshold: float = 0.8) -\> bool:                   |
|                                                                             |
| \"\"\"Return True jika penggunaan \> threshold × limit.\"\"\"               |
|                                                                             |
| \@dataclass                                                                 |
|                                                                             |
| class RateLimitStatus:                                                      |
|                                                                             |
| weight_used: int                                                            |
|                                                                             |
| weight_limit: int                                                           |
|                                                                             |
| weight_pct: float                                                           |
|                                                                             |
| orders_10s: int                                                             |
|                                                                             |
| orders_1d: int                                                              |
|                                                                             |
| seconds_to_reset: float                                                     |
|                                                                             |
| is_near_limit: bool                                                         |
+-----------------------------------------------------------------------------+

**7.4 Config Keys Rate Limiter**

  -----------------------------------------------------------------------------------------------------
  **Key**             **Default**   **Keterangan**
  ------------------- ------------- -------------------------------------------------------------------
  WEIGHT_LIMIT        1200          Limit Binance per menit. Jangan ubah kecuali akun VIP.

  WEIGHT_SAFE_RATIO   0.80          Target penggunaan maksimum 80% dari limit.

  ORDER_LIMIT_10S     50            Limit order per 10 detik dari Binance.

  ORDER_SAFE_RATIO    0.80          Target 80% dari order limit.

  SYNC_FROM_HEADERS   True          Selalu true --- header exchange lebih akurat dari tracking lokal.

  WARN_AT_PCT         0.70          Log warning saat penggunaan mencapai 70%.
  -----------------------------------------------------------------------------------------------------

**BAGIAN B --- Intelligence Layer**

Intelligence Layer memproses data bersih dari Data Layer dan menghasilkan MarketState --- satu objek yang merangkum semua yang perlu diketahui strategi tentang kondisi market saat ini.

  -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------
  **PRINSIP:** Intelligence Layer adalah pure computation --- tidak ada I/O, tidak ada network call, tidak ada DB read/write. Semua input datang dari parameter, semua output adalah return value. Ini membuatnya mudah ditest dan deterministik.

  -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------

**8. regime.py --- Klasifikasi Market Regime**

Mengklasifikasikan kondisi market ke dalam regime. Regime adalah metadata paling penting untuk strategi --- ia menentukan apakah strategi trend following atau mean reversion yang lebih cocok.

**8.1 MarketRegime Enum**

+---------------------------------------------------------------------+
| from enum import Enum                                               |
|                                                                     |
| class MarketRegime(Enum):                                           |
|                                                                     |
| STRONG_TREND_UP = \'strong_trend_up\'                               |
|                                                                     |
| WEAK_TREND_UP = \'weak_trend_up\'                                   |
|                                                                     |
| SIDEWAYS = \'sideways\'                                             |
|                                                                     |
| WEAK_TREND_DOWN = \'weak_trend_down\'                               |
|                                                                     |
| STRONG_TREND_DOWN = \'strong_trend_down\'                           |
|                                                                     |
| HIGH_VOLATILITY = \'high_volatility\' \# override semua regime lain |
|                                                                     |
| UNDEFINED = \'undefined\' \# data tidak cukup (\< MIN_HISTORY)      |
+---------------------------------------------------------------------+

**8.2 Algoritma Klasifikasi --- Dua Tahap**

**Tahap 1: Base Regime via ADX + EMA**

  ----------------------------------------------------------------------------------
  **Kondisi**                     **Regime**          **ADX**   **Harga vs EMA50**
  ------------------------------- ------------------- --------- --------------------
  ADX \> 30 DAN close \> EMA50    STRONG_TREND_UP     \> 30     Di atas EMA50

  ADX 20--30 DAN close \> EMA50   WEAK_TREND_UP       20--30    Di atas EMA50

  ADX \< 20                       SIDEWAYS            \< 20     Tidak relevan

  ADX 20--30 DAN close \< EMA50   WEAK_TREND_DOWN     20--30    Di bawah EMA50

  ADX \> 30 DAN close \< EMA50    STRONG_TREND_DOWN   \> 30     Di bawah EMA50
  ----------------------------------------------------------------------------------

**Tahap 2: Volatility Override**

+------------------------------------------------------------+
| \# Setelah base regime ditentukan, cek volatility:         |
|                                                            |
| if atr_percentile \> HIGH_VOL_PERCENTILE: \# default 85%   |
|                                                            |
| regime = MarketRegime.HIGH_VOLATILITY                      |
|                                                            |
| \# Override SEMUA regime lain saat volatilitas ekstrem     |
|                                                            |
| \# Alasan: di volatilitas ekstrem, model ML tidak reliable |
|                                                            |
| \# risk management lebih penting dari signal               |
+------------------------------------------------------------+

**8.3 Interface Publik**

+----------------------------------------------------------------------------+
| \@dataclass                                                                |
|                                                                            |
| class RegimeResult:                                                        |
|                                                                            |
| regime: MarketRegime                                                       |
|                                                                            |
| confidence: float \# 0.0 -- 1.0                                            |
|                                                                            |
| adx: float                                                                 |
|                                                                            |
| adx_trend: str \# \'rising\' \| \'falling\' \| \'flat\'                    |
|                                                                            |
| ema50_distance: float \# (close - ema50) / ema50 × 100 (pct)               |
|                                                                            |
| is_stable: bool \# True jika regime sama N candle terakhir                 |
|                                                                            |
| lookback_candles: int                                                      |
|                                                                            |
| class RegimeClassifier:                                                    |
|                                                                            |
| def classify(self, df: pd.DataFrame) -\> RegimeResult:                     |
|                                                                            |
| \"\"\"                                                                     |
|                                                                            |
| Input: DataFrame dengan kolom: high, low, close, volume                    |
|                                                                            |
| Minimal MIN_HISTORY_CANDLES baris (default: 200)                           |
|                                                                            |
| Output: RegimeResult                                                       |
|                                                                            |
| \"\"\"                                                                     |
|                                                                            |
| def classify_multi_tf(                                                     |
|                                                                            |
| self,                                                                      |
|                                                                            |
| df_h1: pd.DataFrame,                                                       |
|                                                                            |
| df_h4: pd.DataFrame,                                                       |
|                                                                            |
| ) -\> RegimeResult:                                                        |
|                                                                            |
| \"\"\"                                                                     |
|                                                                            |
| Klasifikasi menggunakan dua timeframe.                                     |
|                                                                            |
| H4 sebagai regime utama (macro trend).                                     |
|                                                                            |
| H1 sebagai konfirmasi (micro trend).                                       |
|                                                                            |
| Jika H4 dan H1 bertentangan → SIDEWAYS (konflik regime).                   |
|                                                                            |
| \"\"\"                                                                     |
|                                                                            |
| def is_regime_change(                                                      |
|                                                                            |
| self,                                                                      |
|                                                                            |
| prev: MarketRegime,                                                        |
|                                                                            |
| curr: MarketRegime,                                                        |
|                                                                            |
| ) -\> bool:                                                                |
|                                                                            |
| \"\"\"Deteksi apakah regime baru berbeda signifikan dari sebelumnya.\"\"\" |
+----------------------------------------------------------------------------+

**8.4 Confidence Calculation**

+-----------------------------------------------+
| \# Confidence dihitung berdasarkan:           |
|                                               |
| def \_calculate_confidence(                   |
|                                               |
| adx: float,                                   |
|                                               |
| ema_distance: float,                          |
|                                               |
| adx_trend: str,                               |
|                                               |
| stability: bool                               |
|                                               |
| ) -\> float:                                  |
|                                               |
| score = 0.0                                   |
|                                               |
| \# ADX strength (0.0 -- 0.4)                  |
|                                               |
| if adx \> 40: score += 0.40                   |
|                                               |
| elif adx \> 30: score += 0.30                 |
|                                               |
| elif adx \> 20: score += 0.20                 |
|                                               |
| else: score += 0.05                           |
|                                               |
| \# EMA distance (0.0 -- 0.3)                  |
|                                               |
| score += min(abs(ema_distance) / 5.0, 0.30)   |
|                                               |
| \# ADX trend (0.0 -- 0.2)                     |
|                                               |
| if adx_trend == \'rising\': score += 0.20     |
|                                               |
| elif adx_trend == \'flat\': score += 0.10     |
|                                               |
| \# Stability bonus (0.0 -- 0.1)               |
|                                               |
| if stability: score += 0.10                   |
|                                               |
| return min(score, 1.0)                        |
+-----------------------------------------------+

**8.5 Config Keys Regime**

  ------------------------------------------------------------------------------------------------------
  **Key**                **Default**   **Keterangan**
  ---------------------- ------------- -----------------------------------------------------------------
  ADX_PERIOD             14            Period ADX. 14 adalah standar industri.

  EMA_TREND_PERIOD       50            EMA yang digunakan untuk arah trend.

  ADX_STRONG_THRESHOLD   30            ADX di atas ini = trend kuat.

  ADX_WEAK_THRESHOLD     20            ADX di atas ini = trend lemah.

  HIGH_VOL_PERCENTILE    85            ATR percentile di atas ini → HIGH_VOLATILITY override.

  STABILITY_CANDLES      5             Regime dianggap stabil jika sama selama N candle.

  MIN_HISTORY_CANDLES    200           Minimal data untuk klasifikasi valid. Di bawah ini → UNDEFINED.
  ------------------------------------------------------------------------------------------------------

**9. volatility.py --- Pengukuran Volatilitas**

Menghitung berbagai metrik volatilitas yang digunakan oleh: regime classifier (ATR percentile untuk HIGH_VOL override), position sizer (ATR untuk stop distance), dan exit layer (trailing stop berbasis ATR).

**9.1 Interface Publik**

+-----------------------------------------------------------------------------+
| \@dataclass                                                                 |
|                                                                             |
| class VolatilityMetrics:                                                    |
|                                                                             |
| symbol: str                                                                 |
|                                                                             |
| timeframe: str                                                              |
|                                                                             |
| timestamp: datetime                                                         |
|                                                                             |
| \# ATR                                                                      |
|                                                                             |
| atr: float \# Average True Range nilai absolut                              |
|                                                                             |
| atr_pct: float \# ATR / close × 100                                         |
|                                                                             |
| atr_percentile: float \# posisi ATR saat ini vs 252-candle history (0--100) |
|                                                                             |
| \# Realized volatility                                                      |
|                                                                             |
| realized_vol_20: float \# annualized realized vol 20 candle                 |
|                                                                             |
| realized_vol_5: float \# annualized realized vol 5 candle                   |
|                                                                             |
| \# Volatility regime                                                        |
|                                                                             |
| vol_regime: str \# \'low\' \| \'normal\' \| \'high\' \| \'extreme\'         |
|                                                                             |
| \# BB                                                                       |
|                                                                             |
| bb_width: float \# (upper - lower) / middle                                 |
|                                                                             |
| bb_percentile: float \# BB width percentile vs 100-candle history           |
|                                                                             |
| class VolatilityCalculator:                                                 |
|                                                                             |
| def calculate(self, df: pd.DataFrame) -\> VolatilityMetrics:                |
|                                                                             |
| \"\"\"Hitung semua metrik dari DataFrame OHLCV.\"\"\"                       |
|                                                                             |
| def get_atr(self, df: pd.DataFrame, period: int = 14) -\> float:            |
|                                                                             |
| \"\"\"ATR nilai absolut.\"\"\"                                              |
|                                                                             |
| def get_atr_pct(self, df: pd.DataFrame, period: int = 14) -\> float:        |
|                                                                             |
| \"\"\"ATR sebagai persentase dari harga.\"\"\"                              |
|                                                                             |
| def get_atr_percentile(                                                     |
|                                                                             |
| self,                                                                       |
|                                                                             |
| df: pd.DataFrame,                                                           |
|                                                                             |
| period: int = 14,                                                           |
|                                                                             |
| lookback: int = 252                                                         |
|                                                                             |
| ) -\> float:                                                                |
|                                                                             |
| \"\"\"                                                                      |
|                                                                             |
| Posisi ATR saat ini vs distribusi historis.                                 |
|                                                                             |
| 50 = ATR di median historis.                                                |
|                                                                             |
| 85+ = ATR tinggi (trigger HIGH_VOLATILITY regime).                          |
|                                                                             |
| \"\"\"                                                                      |
|                                                                             |
| def get_realized_vol(                                                       |
|                                                                             |
| self,                                                                       |
|                                                                             |
| df: pd.DataFrame,                                                           |
|                                                                             |
| window: int = 20,                                                           |
|                                                                             |
| annualize: bool = True                                                      |
|                                                                             |
| ) -\> float:                                                                |
|                                                                             |
| \"\"\"                                                                      |
|                                                                             |
| Realized volatility dari log returns.                                       |
|                                                                             |
| annualize = True: × sqrt(252 × candles_per_day)                             |
|                                                                             |
| \"\"\"                                                                      |
+-----------------------------------------------------------------------------+

**9.2 Volatility Regime Classification**

  ---------------------------------------------------------------------------------------------------------------------------------------------------
  **Regime**    **ATR Percentile**   **Aksi Default Sistem**
  ------------- -------------------- ----------------------------------------------------------------------------------------------------------------
  **low**       \< 25                Boleh trading normal. Position size bisa lebih besar (volatilitas rendah = SL lebih dekat = size lebih besar).

  **normal**    25--75               Kondisi ideal. Parameter default berlaku.

  **high**      75--90               Position size dikurangi 30%. Trailing stop lebih lebar. Alert dikirim.

  **extreme**   \> 90                HIGH_VOLATILITY regime diaktifkan. Position size dikurangi 50%. Market order dilarang.
  ---------------------------------------------------------------------------------------------------------------------------------------------------

**9.3 Formula Realized Volatility**

+----------------------------------------------------------------------------------------+
| \# Annualized Realized Volatility                                                      |
|                                                                                        |
| \# Digunakan untuk: position sizing, risk reporting                                    |
|                                                                                        |
| def realized_vol_annualized(log_returns: pd.Series, window: int, tf: str) -\> float:   |
|                                                                                        |
| \# Step 1: rolling std of log returns                                                  |
|                                                                                        |
| rolling_std = log_returns.rolling(window).std()                                        |
|                                                                                        |
| \# Step 2: annualize                                                                   |
|                                                                                        |
| \# Candles per day berdasarkan timeframe:                                              |
|                                                                                        |
| candles_per_day = {\'1m\':1440, \'5m\':288, \'15m\':96, \'1h\':24, \'4h\':6, \'1d\':1} |
|                                                                                        |
| factor = sqrt(252 \* candles_per_day\[tf\])                                            |
|                                                                                        |
| return rolling_std.iloc\[-1\] \* factor                                                |
|                                                                                        |
| \# Contoh interpretasi:                                                                |
|                                                                                        |
| \# realized_vol = 0.60 → ekspektasi gerak 60% per tahun                                |
|                                                                                        |
| \# atau \~3.8% per hari (0.60 / sqrt(252))                                             |
+----------------------------------------------------------------------------------------+

**9.4 Config Keys Volatility**

  -----------------------------------------------------------------------------------------------
  **Key**                  **Default**   **Keterangan**
  ------------------------ ------------- --------------------------------------------------------
  ATR_PERIOD               14            Standard ATR period. Ubah hanya jika ada alasan kuat.

  ATR_LOOKBACK             252           Lookback untuk ATR percentile. 252 ≈ 1 tahun data 1H.

  REALIZED_VOL_WINDOW      20            Window untuk realized volatility.

  BB_PERIOD                20            Period Bollinger Bands.

  BB_STD_DEV               2.0           Std dev multiplier untuk BB.

  HIGH_VOL_PERCENTILE      75            ATR percentile di atas ini → vol_regime = \'high\'.

  EXTREME_VOL_PERCENTILE   90            ATR percentile di atas ini → vol_regime = \'extreme\'.
  -----------------------------------------------------------------------------------------------

**10. market_state.py --- Agregasi MarketState**

Mengumpulkan semua output dari intelligence_layer menjadi satu objek MarketState yang siap dikonsumsi strategy_layer. Satu fungsi update dipanggil setiap tick.

**10.1 MarketState Dataclass --- Kontrak dengan Strategy Layer**

+----------------------------------------------------------------------------+
| \@dataclass                                                                |
|                                                                            |
| class MarketState:                                                         |
|                                                                            |
| \# ── Identitas ───────────────────────────────────────────                |
|                                                                            |
| symbol: str                                                                |
|                                                                            |
| timeframe: str                                                             |
|                                                                            |
| timestamp: datetime                                                        |
|                                                                            |
| \# ── Price & Market ──────────────────────────────────────                |
|                                                                            |
| last_price: float                                                          |
|                                                                            |
| bid: float                                                                 |
|                                                                            |
| ask: float                                                                 |
|                                                                            |
| mid: float                                                                 |
|                                                                            |
| spread_pct: float                                                          |
|                                                                            |
| volume_24h: float                                                          |
|                                                                            |
| orderbook_imbalance:float \# -1.0 to +1.0                                  |
|                                                                            |
| \# ── Candle Data ─────────────────────────────────────────                |
|                                                                            |
| latest_candle: Candle                                                      |
|                                                                            |
| candles_df: pd.DataFrame \# 200 candle historis untuk strategi             |
|                                                                            |
| \# ── Intelligence ────────────────────────────────────────                |
|                                                                            |
| regime: MarketRegime                                                       |
|                                                                            |
| regime_confidence: float                                                   |
|                                                                            |
| regime_stable: bool                                                        |
|                                                                            |
| vol_metrics: VolatilityMetrics                                             |
|                                                                            |
| \# Shortcut properties untuk kemudahan akses di strategi                   |
|                                                                            |
| \@property                                                                 |
|                                                                            |
| def atr(self) -\> float: return self.vol_metrics.atr                       |
|                                                                            |
| \@property                                                                 |
|                                                                            |
| def atr_percentile(self) -\> float: return self.vol_metrics.atr_percentile |
|                                                                            |
| \@property                                                                 |
|                                                                            |
| def vol_regime(self) -\> str: return self.vol_metrics.vol_regime           |
|                                                                            |
| \# ── Features (untuk model ML) ───────────────────────────                |
|                                                                            |
| features: pd.Series \# feature_names dari metadata.json                    |
|                                                                            |
| \# ── Portfolio Context ────────────────────────────────────               |
|                                                                            |
| open_positions: dict\[str, Position\] \# symbol → Position                 |
|                                                                            |
| available_equity: float                                                    |
|                                                                            |
| equity: float                                                              |
|                                                                            |
| daily_pnl: float                                                           |
|                                                                            |
| \# ── Anomaly & Safety ────────────────────────────────────                |
|                                                                            |
| active_anomalies: list\[AnomalyReport\]                                    |
|                                                                            |
| is_safe_to_trade: bool                                                     |
|                                                                            |
| \# ── Metadata ────────────────────────────────────────────                |
|                                                                            |
| data_quality: str \# \'good\' \| \'degraded\' \| \'bad\'                   |
|                                                                            |
| latency_ms: float \# waktu pembuatan state                                 |
+----------------------------------------------------------------------------+

**10.2 MarketStateBuilder --- Interface**

+---------------------------------------------------------------------------+
| class MarketStateBuilder:                                                 |
|                                                                           |
| def \_\_init\_\_(                                                         |
|                                                                           |
| self,                                                                     |
|                                                                           |
| regime_clf: RegimeClassifier,                                             |
|                                                                           |
| vol_calc: VolatilityCalculator,                                           |
|                                                                           |
| anomaly_det: AnomalyDetector,                                             |
|                                                                           |
| feature_eng: FeatureEngineering,                                          |
|                                                                           |
| latency_guard: LatencyGuard,                                              |
|                                                                           |
| ): \...                                                                   |
|                                                                           |
| async def build(                                                          |
|                                                                           |
| self,                                                                     |
|                                                                           |
| candle: Candle,                                                           |
|                                                                           |
| ticker: Ticker,                                                           |
|                                                                           |
| orderbook: Orderbook,                                                     |
|                                                                           |
| candles_df: pd.DataFrame,                                                 |
|                                                                           |
| portfolio: PortfolioState,                                                |
|                                                                           |
| ) -\> MarketState:                                                        |
|                                                                           |
| \"\"\"                                                                    |
|                                                                           |
| Dipanggil setiap tick oleh main_loop.                                     |
|                                                                           |
| Urutan build:                                                             |
|                                                                           |
| 1\. Validasi input (semua harus ada dan fresh)                            |
|                                                                           |
| 2\. Hitung volatility metrics                                             |
|                                                                           |
| 3\. Klasifikasi regime                                                    |
|                                                                           |
| 4\. Cek anomali                                                           |
|                                                                           |
| 5\. Hitung features (memanggil feature_engineering runtime version)       |
|                                                                           |
| 6\. Cek latency guard                                                     |
|                                                                           |
| 7\. Rakit MarketState                                                     |
|                                                                           |
| \"\"\"                                                                    |
|                                                                           |
| def get_last_state(self, symbol: str) -\> MarketState \| None:            |
|                                                                           |
| \"\"\"Return state terakhir yang berhasil dibuild.\"\"\"                  |
|                                                                           |
| def is_state_fresh(self, symbol: str, max_age_s: int = 5) -\> bool:       |
|                                                                           |
| \"\"\"Cek apakah state masih fresh. Jika tidak, gunakan state lama.\"\"\" |
+---------------------------------------------------------------------------+

**10.3 Data Quality Assessment**

  ------------------------------------------------------------------------------------------------------------------------------------
  **Quality Level**   **Kondisi**                                          **Aksi Strategi**
  ------------------- ---------------------------------------------------- -----------------------------------------------------------
  **good**            Semua data fresh, tidak ada anomali aktif            Trading normal

  **degraded**        Beberapa data stale ATAU ada anomali MEDIUM          Trading dengan position size dikurangi 50%

  **bad**             Data critical stale ATAU ada anomali HIGH/CRITICAL   is_safe_to_trade = False → strategi tidak generate signal
  ------------------------------------------------------------------------------------------------------------------------------------

**11. latency_guard.py --- Proteksi Eksekusi**

Mencegah eksekusi order saat kondisi latency tinggi yang bisa menyebabkan fill pada harga jauh dari yang diharapkan. Ini berbeda dari rate_limiter --- latency_guard mengukur kondisi jaringan, bukan API quota.

**11.1 Apa yang Diukur**

  ----------------------------------------------------------------------------------------------------------------------
  **Metrik**               **Cara Ukur**                                     **Threshold Alert**   **Threshold Block**
  ------------------------ ------------------------------------------------- --------------------- ---------------------
  WS message latency       timestamp Exchange vs timestamp terima            \> 500ms              \> 2000ms

  REST round-trip time     waktu antara request dan response                 \> 1000ms             \> 3000ms

  Order-to-fill latency    waktu antara submit order dan fill confirmation   \> 2000ms             \> 5000ms

  Tick-to-signal latency   waktu candle masuk sampai signal dihasilkan       \> 500ms              \> 1500ms
  ----------------------------------------------------------------------------------------------------------------------

**11.2 Interface Publik**

+-------------------------------------------------------------------------------------+
| \@dataclass                                                                         |
|                                                                                     |
| class LatencyStatus:                                                                |
|                                                                                     |
| ws_latency_ms: float                                                                |
|                                                                                     |
| rest_latency_ms: float                                                              |
|                                                                                     |
| order_latency_ms: float                                                             |
|                                                                                     |
| tick_latency_ms: float                                                              |
|                                                                                     |
| overall_status: str \# \'ok\' \| \'degraded\' \| \'high\' \| \'critical\'           |
|                                                                                     |
| should_block_trading: bool                                                          |
|                                                                                     |
| warnings: list\[str\]                                                               |
|                                                                                     |
| class LatencyGuard:                                                                 |
|                                                                                     |
| def record_ws_latency(self, exchange_ts: datetime, received_ts: datetime) -\> None: |
|                                                                                     |
| \"\"\"Dipanggil setiap kali pesan WebSocket diterima.\"\"\"                         |
|                                                                                     |
| def record_rest_latency(self, duration_ms: float, endpoint: str) -\> None:          |
|                                                                                     |
| \"\"\"Dipanggil setelah setiap REST request selesai.\"\"\"                          |
|                                                                                     |
| def record_order_latency(self, submit_ts: datetime, fill_ts: datetime) -\> None:    |
|                                                                                     |
| \"\"\"Dipanggil setelah order di-fill.\"\"\"                                        |
|                                                                                     |
| def get_status(self) -\> LatencyStatus:                                             |
|                                                                                     |
| \"\"\"Return status latency terkini.\"\"\"                                          |
|                                                                                     |
| def should_block_order(self) -\> tuple\[bool, str\]:                                |
|                                                                                     |
| \"\"\"                                                                              |
|                                                                                     |
| Return (True, alasan) jika order harus diblokir.                                    |
|                                                                                     |
| Dipanggil oleh main_loop sebelum submit order.                                      |
|                                                                                     |
| \"\"\"                                                                              |
|                                                                                     |
| def get_rolling_stats(self, window: int = 20) -\> dict:                             |
|                                                                                     |
| \"\"\"P50, P95, P99 latency untuk 20 pengukuran terakhir.\"\"\"                     |
+-------------------------------------------------------------------------------------+

**11.3 Config Keys Latency Guard**

  ----------------------------------------------------------------------------------------------------
  **Key**                 **Default**   **Keterangan**
  ----------------------- ------------- --------------------------------------------------------------
  WS_LATENCY_WARN_MS      500           WS latency di atas ini → warning, kurangi confidence signal.

  WS_LATENCY_BLOCK_MS     2000          WS latency di atas ini → blokir order baru.

  REST_LATENCY_WARN_MS    1000          REST round-trip di atas ini → warning.

  REST_LATENCY_BLOCK_MS   3000          REST round-trip di atas ini → blokir order.

  LATENCY_WINDOW          20            Jumlah pengukuran untuk rolling average.

  BLOCK_DURATION_S        60            Setelah block, tunggu 60 detik sebelum coba lagi.
  ----------------------------------------------------------------------------------------------------

**12. Integrasi & Dependency Map**

**12.1 Dependency Antar File**

  ----------------------------------------------------------------------------------------------------------------------------
  **File**              **Import Dari**                                    **Output Dikonsumsi Oleh**
  --------------------- -------------------------------------------------- ---------------------------------------------------
  market.py             security/ (key_mgr), rate_limiter, utils/helpers   orderbook.py, websocket_client, market_state

  orderbook.py          market.py (REST snapshot), websocket_client        market_state.py, execution_layer (slippage est.)

  websocket_client.py   security/ (key_mgr), market.py (listen key)        market.py (candle), orderbook.py (depth), sync/

  validator.py          --- (pure function)                                main_loop (gate pertama tiap tick)

  anomaly_detector.py   validator.py (ValidationResult)                    market_state.py (is_safe_to_trade)

  rate_limiter.py       --- (pure function)                                market.py, execution_layer (semua REST call)

  regime.py             volatility.py (atr_percentile)                     market_state.py

  volatility.py         --- (pure function)                                regime.py, market_state.py, exit_layer (trailing)

  market_state.py       regime, volatility, anomaly, feature_engineering   strategy_layer (dikonsumsi setiap tick)

  latency_guard.py      utils/time_utils                                   market_state.py, main_loop (gate sebelum order)
  ----------------------------------------------------------------------------------------------------------------------------

**12.2 Feature Engineering di Runtime**

market_state.py memanggil versi runtime dari feature_engineering yang identik dengan versi research. Ini adalah komponen yang paling kritis untuk konsistensi model.

  ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------
  **KONTRAK KRITIS:** FeatureConfig yang digunakan di runtime HARUS persis sama dengan FeatureConfig yang digunakan saat training di research layer. Jika ada perbedaan satu fitur saja, model akan menghasilkan prediksi tidak valid tanpa error eksplisit.

  ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------

+------------------------------------------------------------------------+
| \# Cara runtime memanggil feature engineering:                         |
|                                                                        |
| from research.pipeline.feature_engineering import FeatureEngineering   |
|                                                                        |
| \# ↑ Import langsung dari research/ untuk memastikan konsistensi       |
|                                                                        |
| \# Artinya: research/ dan runtime/ harus dalam satu Python package     |
|                                                                        |
| class RuntimeFeatureEngine:                                            |
|                                                                        |
| def \_\_init\_\_(self, metadata: dict):                                |
|                                                                        |
| self.config = FeatureConfig(\*\*metadata\[\'feature_config\'\])        |
|                                                                        |
| self.feature_names= metadata\[\'feature_names\'\]                      |
|                                                                        |
| self.\_engine = FeatureEngineering(self.config)                        |
|                                                                        |
| def compute(self, candles_df: pd.DataFrame) -\> pd.Series:             |
|                                                                        |
| \"\"\"                                                                 |
|                                                                        |
| Input: 200+ candle DataFrame                                           |
|                                                                        |
| Output: pd.Series dengan index = feature_names                         |
|                                                                        |
| Harus identik dengan apa yang model lihat saat training.               |
|                                                                        |
| \"\"\"                                                                 |
|                                                                        |
| features_df = self.\_engine.build_features(candles_df, self.config)    |
|                                                                        |
| latest = features_df.iloc\[-1\]                                        |
|                                                                        |
| \# Pastikan urutan dan nama kolom benar                                |
|                                                                        |
| assert list(latest.index) == self.feature_names, \'Feature mismatch!\' |
|                                                                        |
| return latest                                                          |
+------------------------------------------------------------------------+

**12.3 Checklist Implementasi Data & Intelligence Layer**

  -----------------------------------------------------------------------------------------------------------------------------------------------------------------
  **No**   **Item**                                                              **Verifikasi**                                                          **Done**
  -------- --------------------------------------------------------------------- ----------------------------------------------------------------------- ----------
  1        rate_limiter.acquire() dipanggil sebelum SETIAP REST request          grep -n \'await rate_limiter\' execution + market + sync files          ☐

  2        validator.validate_candle() dipanggil di awal setiap tick             Code review main_loop --- step 1 harus ada                              ☐

  3        anomaly_detector.is_safe_to_trade() block signal jika False           Unit test: inject CROSSED_BOOK → signal tidak dihasilkan                ☐

  4        WebSocket reconnect berjalan tanpa interrupt main_loop                Integration test: kill WS connection, pastikan loop tetap jalan         ☐

  5        Orderbook update sequence ID di-track, gap → snapshot ulang           Test dengan simulated gap dalam update ID                               ☐

  6        Listen key di-refresh setiap 30 menit                                 Mock time, pastikan refresh terpanggil sebelum 60 menit                 ☐

  7        MarketState.features identik dengan training features                 Test: compare output RuntimeFeatureEngine vs research FeatureEngine     ☐

  8        regime.classify() return UNDEFINED jika data \< MIN_HISTORY_CANDLES   Unit test dengan 50 baris DataFrame                                     ☐

  9        latency_guard.should_block_order() di-check sebelum submit            Code review main_loop step 8                                            ☐

  10       Semua anomali HIGH/CRITICAL di-publish ke event_bus (SYSTEM_ERROR)    Test: trigger anomali, cek event_bus menerima event                     ☐

  11       Cache invalidasi berjalan saat ada WebSocket update                   Test: WS order fill → balance cache di-clear                            ☐

  12       Tidak ada I/O di dalam intelligence_layer (pure computation)          grep -rn \'await\\\|async\\\|requests\\\|sqlite\' intelligence_layer/   ☐
  -----------------------------------------------------------------------------------------------------------------------------------------------------------------

+:---------------------------------------------------------------------------------------------------------------:+
| **Dokumen ini adalah kontrak implementasi Data Layer & Intelligence Layer.**                                    |
|                                                                                                                 |
| Perubahan pada dataclass output, config key default, atau aturan validasi WAJIB diupdate sebelum merge ke main. |
|                                                                                                                 |
| *Referensi terkait: agent_core_docs.docx • runtime_layer_docs.docx • research_layer_docs.docx*                  |
+-----------------------------------------------------------------------------------------------------------------+
