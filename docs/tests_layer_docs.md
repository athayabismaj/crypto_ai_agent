**crypto_ai_agent**

**Dokumentasi: Tests Layer**

*unit/ · integration/ · mocks/*

*Panduan lengkap: apa yang ditest, cara test, coverage requirement*

Versi 1.0 \| Referensi: semua dokumentasi layer sebelumnya

  ------------------------------------------------

  ------------------------------------------------

**1. Overview --- Filosofi Testing**

Tests Layer bukan formalitas --- ini adalah jaring pengaman yang mencegah bug masuk ke sistem yang mengelola uang nyata. Setiap bug yang lolos ke live mode bisa menghasilkan kerugian finansial yang langsung.

  ---------------------------------------------------------------------------------------------------------------------------------------------
  **PRINSIP:** Test harus bisa menjawab: \'Apakah sistem ini aman untuk dijalankan dengan uang nyata?\' Bukan hanya \'Apakah kodenya jalan?\'

  ---------------------------------------------------------------------------------------------------------------------------------------------

**1.1 Tiga Jenis Test**

  --------------------------------------------------------------------------------------------------------------------------------------
  **Jenis**         **Tujuan**                                          **Scope**              **Kecepatan**   **Coverage Target**
  ----------------- --------------------------------------------------- ---------------------- --------------- -------------------------
  **Unit**          Validasi satu fungsi atau class secara isolasi      Satu file/class        \< 50ms         100% critical paths

  **Integration**   Validasi interaksi antar dua atau lebih komponen    2--5 komponen          \< 5s           Happy path + error path

  **Mock-based**    Test komponen dengan dependency diganti mock/stub   Satu komponen + deps   \< 100ms        Semua edge case
  --------------------------------------------------------------------------------------------------------------------------------------

**1.2 Coverage Requirements per Layer**

  ---------------------------------------------------------------------------------------------------------------------------------
  **Layer**             **Target Coverage**   **Alasan**                             **File Kritis (100% branch)**
  --------------------- --------------------- -------------------------------------- ----------------------------------------------
  risk_layer/           100%                  Langsung mempengaruhi uang             risk_manager, circuit_breaker, position_size

  trade_layer/          100%                  Idempotency & recovery kritis          idempotency, recovery, audit_trail

  execution_layer/      95%                   Error handling exchange wajib ditest   binance (error codes), smart_router

  exit_layer/           100%                  Kalkulasi SL/TP harus presisi          exit_manager, trailing, break_even

  strategy_layer/       90%                   Logic bisnis utama                     base_strategy (filters), spot_strategy

  portfolio_layer/      95%                   Allocation & correlation checks        allocator, correlation

  data_layer/           85%                   Reconnect & anomaly handling           validator, anomaly_detector

  intelligence_layer/   85%                   Regime classification accuracy         regime, volatility

  core/                 95%                   Startup & event bus                    config_schema, safe_mode

  utils/                95%                   Dipakai semua layer                    helpers (round_qty), time_utils

  sync/                 90%                   Discrepancy detection                  position_sync

  learning_layer/       80%                   Adaptasi tidak langsung ke uang        strategy_killer
  ---------------------------------------------------------------------------------------------------------------------------------

**1.3 Perintah Menjalankan Test**

+----------------------------------------------------------------------------------+
| \# Jalankan semua test                                                           |
|                                                                                  |
| pytest tests/ -v                                                                 |
|                                                                                  |
| \# Hanya unit test (cepat, tanpa I/O)                                            |
|                                                                                  |
| pytest tests/unit/ -v                                                            |
|                                                                                  |
| \# Hanya integration test                                                        |
|                                                                                  |
| pytest tests/integration/ -v                                                     |
|                                                                                  |
| \# Dengan coverage report                                                        |
|                                                                                  |
| pytest tests/ \--cov=runtime/agent \--cov-report=html \--cov-report=term-missing |
|                                                                                  |
| \# Test satu layer spesifik                                                      |
|                                                                                  |
| pytest tests/unit/test_risk.py -v                                                |
|                                                                                  |
| \# Test dengan output verbose + stop on first failure                            |
|                                                                                  |
| pytest tests/ -v -x                                                              |
|                                                                                  |
| \# Jalankan hanya test yang ditandai \'critical\'                                |
|                                                                                  |
| pytest tests/ -m critical -v                                                     |
+----------------------------------------------------------------------------------+

**2. Struktur Folder & Konvensi Penamaan**

**2.1 Hierarki Folder**

+------------------------------------------------------------------+
| tests/                                                           |
|                                                                  |
| ├── unit/                                                        |
|                                                                  |
| │ ├── test_risk.py ← risk_layer/ (sudah ada skeleton)            |
|                                                                  |
| │ ├── test_strategy.py ← strategy_layer/                         |
|                                                                  |
| │ ├── test_portfolio.py ← portfolio_layer/                       |
|                                                                  |
| │ ├── test_idempotency.py ← trade_layer/idempotency.py           |
|                                                                  |
| │ ├── test_execution.py ← execution_layer/                       |
|                                                                  |
| │ ├── test_exit.py ← exit_layer/                                 |
|                                                                  |
| │ ├── test_data_layer.py ← data_layer/                           |
|                                                                  |
| │ ├── test_intelligence.py ← intelligence_layer/                 |
|                                                                  |
| │ ├── test_helpers.py ← utils/helpers.py                         |
|                                                                  |
| │ ├── test_time_utils.py ← utils/time_utils.py                   |
|                                                                  |
| │ ├── test_trade.py ← trade_layer/trade.py + manager.py          |
|                                                                  |
| │ ├── test_sync.py ← sync/                                       |
|                                                                  |
| │ └── test_learning.py ← learning_layer/                         |
|                                                                  |
| │                                                                |
|                                                                  |
| ├── integration/                                                 |
|                                                                  |
| │ ├── test_full_flow.py ← signal → risk → trade → exit           |
|                                                                  |
| │ ├── test_exchange_flow.py ← executor → mock exchange → confirm |
|                                                                  |
| │ ├── test_recovery_flow.py ← crash → startup → recovery         |
|                                                                  |
| │ └── test_paper_mode_flow.py ← full paper trading cycle         |
|                                                                  |
| │                                                                |
|                                                                  |
| ├── mocks/                                                       |
|                                                                  |
| │ ├── mock_exchange.py ← simulasi Binance API                    |
|                                                                  |
| │ ├── mock_data.py ← fixture candle, ticker, orderbook           |
|                                                                  |
| │ ├── mock_db.py ← in-memory SQLite untuk test                   |
|                                                                  |
| │ └── mock_notifier.py ← notifier yang tidak kirim pesan nyata   |
|                                                                  |
| │                                                                |
|                                                                  |
| └── conftest.py ← shared fixtures untuk semua test               |
+------------------------------------------------------------------+

**2.2 Konvensi Penamaan**

  ------------------------------------------------------------------------------------------------------------
  **Konvensi**             **Contoh**                             **Keterangan**
  ------------------------ -------------------------------------- --------------------------------------------
  File test                test\_{nama_modul}.py                  Selalu prefix \'test\_\'

  Class test               class TestRiskManager:                 PascalCase, prefix \'Test\'

  Fungsi test happy path   def test_approve_normal_trade():       Deskriptif, jelaskan kondisi

  Fungsi test error path   def test_block_zero_quantity():        Sebutkan kondisi yang ditest

  Fungsi test edge case    def test_kelly_with_zero_win_rate():   Sebutkan edge case-nya

  Fixture shared           \@pytest.fixture def config():         Di conftest.py jika dipakai \> 1 file

  Marker critical          \@pytest.mark.critical                 Untuk test yang wajib lulus sebelum deploy
  ------------------------------------------------------------------------------------------------------------

**3. mocks/ --- Komponen Tiruan**

**3.1 mock_exchange.py --- Simulasi Binance**

Menggantikan koneksi nyata ke Binance API. Semua test yang melibatkan execution layer harus menggunakan mock ini, bukan exchange sungguhan.

+------------------------------------------------------------------------------+
| class MockBinanceExchange:                                                   |
|                                                                              |
| \"\"\"                                                                       |
|                                                                              |
| Simulasi Binance API untuk testing.                                          |
|                                                                              |
| Bisa dikonfigurasi untuk mensimulasikan berbagai skenario:                   |
|                                                                              |
| \- Order berhasil (default)                                                  |
|                                                                              |
| \- Rate limit 429                                                            |
|                                                                              |
| \- Insufficient balance -2010                                                |
|                                                                              |
| \- Network timeout                                                           |
|                                                                              |
| \- Partial fill                                                              |
|                                                                              |
| \"\"\"                                                                       |
|                                                                              |
| def \_\_init\_\_(                                                            |
|                                                                              |
| self,                                                                        |
|                                                                              |
| scenario: str = \'success\',                                                 |
|                                                                              |
| fill_delay_ms: int = 0,                                                      |
|                                                                              |
| partial_ratio: float = 1.0, \# 1.0 = full fill                               |
|                                                                              |
| initial_balance: float = 10_000.0,                                           |
|                                                                              |
| ):                                                                           |
|                                                                              |
| self.scenario = scenario                                                     |
|                                                                              |
| self.fill_delay_ms = fill_delay_ms                                           |
|                                                                              |
| self.partial_ratio = partial_ratio                                           |
|                                                                              |
| self.\_balance = initial_balance                                             |
|                                                                              |
| self.\_orders: dict = {}                                                     |
|                                                                              |
| self.\_positions: dict= {}                                                   |
|                                                                              |
| self.\_call_count = 0                                                        |
|                                                                              |
| self.\_call_log: list = \[\]                                                 |
|                                                                              |
| async def place_order(self, order: OrderRequest) -\> OrderResponse:          |
|                                                                              |
| self.\_call_count += 1                                                       |
|                                                                              |
| self.\_call_log.append((\'place_order\', order))                             |
|                                                                              |
| if self.fill_delay_ms \> 0:                                                  |
|                                                                              |
| await asyncio.sleep(self.fill_delay_ms / 1000)                               |
|                                                                              |
| if self.scenario == \'rate_limit\':                                          |
|                                                                              |
| raise RateLimitError(\'429 Too Many Requests\')                              |
|                                                                              |
| if self.scenario == \'insufficient_balance\':                                |
|                                                                              |
| raise InsufficientBalanceError(\'-2010\')                                    |
|                                                                              |
| if self.scenario == \'network_timeout\':                                     |
|                                                                              |
| raise asyncio.TimeoutError()                                                 |
|                                                                              |
| if self.scenario == \'rejected\':                                            |
|                                                                              |
| return OrderResponse(                                                        |
|                                                                              |
| exchange_order_id = \'\',                                                    |
|                                                                              |
| client_order_id = order.client_order_id,                                     |
|                                                                              |
| status = \'REJECTED\',                                                       |
|                                                                              |
| filled_qty=0, avg_price=0, commission=0,                                     |
|                                                                              |
| commission_asset=\'USDT\', timestamp=utcnow(),                               |
|                                                                              |
| raw_response={\'code\': -1100, \'msg\': \'Bad parameter\'},                  |
|                                                                              |
| )                                                                            |
|                                                                              |
| \# Default: success                                                          |
|                                                                              |
| filled_qty = order.quantity \* self.partial_ratio                            |
|                                                                              |
| price = order.price or self.\_get_mock_price(order.symbol)                   |
|                                                                              |
| commission = filled_qty \* price \* 0.001                                    |
|                                                                              |
| resp = OrderResponse(                                                        |
|                                                                              |
| exchange_order_id = f\'mock\_{order.client_order_id}\',                      |
|                                                                              |
| client_order_id = order.client_order_id,                                     |
|                                                                              |
| status = \'FILLED\' if self.partial_ratio == 1.0 else \'PARTIALLY_FILLED\',  |
|                                                                              |
| filled_qty = filled_qty,                                                     |
|                                                                              |
| avg_price = price,                                                           |
|                                                                              |
| commission = commission,                                                     |
|                                                                              |
| commission_asset = \'USDT\',                                                 |
|                                                                              |
| timestamp = utcnow(),                                                        |
|                                                                              |
| raw_response = {\'mock\': True},                                             |
|                                                                              |
| )                                                                            |
|                                                                              |
| self.\_orders\[order.client_order_id\] = resp                                |
|                                                                              |
| return resp                                                                  |
|                                                                              |
| async def get_balance(self, asset: str = \'USDT\') -\> float:                |
|                                                                              |
| return self.\_balance                                                        |
|                                                                              |
| async def get_order_status(                                                  |
|                                                                              |
| self, symbol: str, client_order_id: str                                      |
|                                                                              |
| ) -\> OrderResponse:                                                         |
|                                                                              |
| if client_order_id not in self.\_orders:                                     |
|                                                                              |
| raise OrderNotFoundError(client_order_id)                                    |
|                                                                              |
| return self.\_orders\[client_order_id\]                                      |
|                                                                              |
| def assert_called_once(self) -\> None:                                       |
|                                                                              |
| assert self.\_call_count == 1, f\'Expected 1 call, got {self.\_call_count}\' |
|                                                                              |
| def assert_order_sent(self, client_order_id: str) -\> None:                  |
|                                                                              |
| ids = \[o.client_order_id for \_, o in self.\_call_log                       |
|                                                                              |
| if \_ == \'place_order\'\]                                                   |
|                                                                              |
| assert client_order_id in ids, f\'{client_order_id} not in {ids}\'           |
|                                                                              |
| def set_scenario(self, scenario: str) -\> None:                              |
|                                                                              |
| self.scenario = scenario                                                     |
|                                                                              |
| def \_get_mock_price(self, symbol: str) -\> float:                           |
|                                                                              |
| prices = {\'BTCUSDT\': 50_000.0, \'ETHUSDT\': 3_000.0,                       |
|                                                                              |
| \'BNBUSDT\': 300.0, \'SOLUSDT\': 100.0}                                      |
|                                                                              |
| return prices.get(symbol, 1_000.0)                                           |
+------------------------------------------------------------------------------+

**3.2 mock_data.py --- Fixture Market Data**

+-----------------------------------------------------------------------------------------+
| from datetime import datetime, timezone                                                 |
|                                                                                         |
| import pandas as pd                                                                     |
|                                                                                         |
| import numpy as np                                                                      |
|                                                                                         |
| def make_candle(                                                                        |
|                                                                                         |
| symbol: str = \'BTCUSDT\',                                                              |
|                                                                                         |
| tf: str = \'1h\',                                                                       |
|                                                                                         |
| close: float = 50_000.0,                                                                |
|                                                                                         |
| pct_move: float = 0.0, \# persentase perubahan dari close                               |
|                                                                                         |
| volume: float = 100.0,                                                                  |
|                                                                                         |
| is_closed: bool = True,                                                                 |
|                                                                                         |
| ) -\> \'Candle\':                                                                       |
|                                                                                         |
| \"\"\"Buat satu candle untuk testing.\"\"\"                                             |
|                                                                                         |
| price = close \* (1 + pct_move)                                                         |
|                                                                                         |
| return Candle(                                                                          |
|                                                                                         |
| symbol=symbol, timeframe=tf,                                                            |
|                                                                                         |
| timestamp=utcnow(),                                                                     |
|                                                                                         |
| open=price \* 0.999,                                                                    |
|                                                                                         |
| high=price \* 1.002,                                                                    |
|                                                                                         |
| low=price \* 0.997,                                                                     |
|                                                                                         |
| close=price,                                                                            |
|                                                                                         |
| volume=volume,                                                                          |
|                                                                                         |
| is_closed=is_closed,                                                                    |
|                                                                                         |
| source=\'mock\',                                                                        |
|                                                                                         |
| )                                                                                       |
|                                                                                         |
| def make_candles_df(                                                                    |
|                                                                                         |
| n: int = 200,                                                                           |
|                                                                                         |
| symbol: str = \'BTCUSDT\',                                                              |
|                                                                                         |
| base_price: float = 50_000.0,                                                           |
|                                                                                         |
| trend: float = 0.0001, \# drift per candle                                              |
|                                                                                         |
| noise: float = 0.005, \# std volatilitas                                                |
|                                                                                         |
| ) -\> pd.DataFrame:                                                                     |
|                                                                                         |
| \"\"\"                                                                                  |
|                                                                                         |
| Buat DataFrame OHLCV sintetis untuk testing.                                            |
|                                                                                         |
| trend=0.001 = trending up kira-kira 0.1% per candle                                     |
|                                                                                         |
| \"\"\"                                                                                  |
|                                                                                         |
| np.random.seed(42) \# reproducible                                                      |
|                                                                                         |
| returns = np.random.normal(trend, noise, n)                                             |
|                                                                                         |
| closes = base_price \* np.cumprod(1 + returns)                                          |
|                                                                                         |
| df = pd.DataFrame({                                                                     |
|                                                                                         |
| \'timestamp\': pd.date_range(start=\'2024-01-01\', periods=n, freq=\'1H\', tz=\'UTC\'), |
|                                                                                         |
| \'open\': closes \* (1 + np.random.normal(0, 0.001, n)),                                |
|                                                                                         |
| \'high\': closes \* (1 + np.abs(np.random.normal(0, 0.003, n))),                        |
|                                                                                         |
| \'low\': closes \* (1 - np.abs(np.random.normal(0, 0.003, n))),                         |
|                                                                                         |
| \'close\': closes,                                                                      |
|                                                                                         |
| \'volume\': np.random.uniform(50, 200, n),                                              |
|                                                                                         |
| })                                                                                      |
|                                                                                         |
| \# Pastikan OHLC logic: high \>= max(open,close), low \<= min                           |
|                                                                                         |
| df\[\'high\'\] = df\[\[\'open\',\'close\',\'high\'\]\].max(axis=1)                      |
|                                                                                         |
| df\[\'low\'\] = df\[\[\'open\',\'close\',\'low\'\]\].min(axis=1)                        |
|                                                                                         |
| return df                                                                               |
|                                                                                         |
| def make_ticker(                                                                        |
|                                                                                         |
| symbol: str = \'BTCUSDT\',                                                              |
|                                                                                         |
| price: float = 50_000.0,                                                                |
|                                                                                         |
| spread_pct: float = 0.01,                                                               |
|                                                                                         |
| ) -\> \'Ticker\':                                                                       |
|                                                                                         |
| half_spread = price \* spread_pct / 100 / 2                                             |
|                                                                                         |
| return Ticker(                                                                          |
|                                                                                         |
| symbol=symbol,                                                                          |
|                                                                                         |
| bid=price - half_spread,                                                                |
|                                                                                         |
| ask=price + half_spread,                                                                |
|                                                                                         |
| last=price,                                                                             |
|                                                                                         |
| mid=price,                                                                              |
|                                                                                         |
| spread=price \* spread_pct / 100,                                                       |
|                                                                                         |
| spread_pct=spread_pct,                                                                  |
|                                                                                         |
| volume_24h=1_000_000.0,                                                                 |
|                                                                                         |
| change_24h=0.5,                                                                         |
|                                                                                         |
| timestamp=utcnow(),                                                                     |
|                                                                                         |
| )                                                                                       |
|                                                                                         |
| def make_portfolio_state(                                                               |
|                                                                                         |
| equity: float = 10_000.0,                                                               |
|                                                                                         |
| positions: dict = None,                                                                 |
|                                                                                         |
| last_price: float = 50_000.0,                                                           |
|                                                                                         |
| symbol: str = \'BTCUSDT\',                                                              |
|                                                                                         |
| ) -\> dict:                                                                             |
|                                                                                         |
| \"\"\"Buat portfolio_state dict untuk test risk_layer.\"\"\"                            |
|                                                                                         |
| return {                                                                                |
|                                                                                         |
| \'equity\': equity,                                                                     |
|                                                                                         |
| \'open_positions\': positions or {},                                                    |
|                                                                                         |
| \'exchange_status\': \'normal\',                                                        |
|                                                                                         |
| \'market_halted\': False,                                                               |
|                                                                                         |
| f\'last_price\_{symbol}\': last_price,                                                  |
|                                                                                         |
| f\'atr\_{symbol}\': last_price \* 0.015,                                                |
|                                                                                         |
| \'strategy_stats\': {                                                                   |
|                                                                                         |
| \'spot_strategy_v1\': {                                                                 |
|                                                                                         |
| \'win_rate\': 0.55,                                                                     |
|                                                                                         |
| \'avg_risk_reward\': 1.8,                                                               |
|                                                                                         |
| }                                                                                       |
|                                                                                         |
| },                                                                                      |
|                                                                                         |
| }                                                                                       |
|                                                                                         |
| def make_signal(                                                                        |
|                                                                                         |
| symbol: str = \'BTCUSDT\',                                                              |
|                                                                                         |
| side: str = \'BUY\',                                                                    |
|                                                                                         |
| confidence: float = 0.75,                                                               |
|                                                                                         |
| price: float = 50_000.0,                                                                |
|                                                                                         |
| sl_pct: float = 0.02,                                                                   |
|                                                                                         |
| ) -\> \'Signal\':                                                                       |
|                                                                                         |
| sl = price \* (1 - sl_pct) if side == \'BUY\' else price \* (1 + sl_pct)                |
|                                                                                         |
| tp = price \* (1 + sl_pct \* 2) if side == \'BUY\' else price \* (1 - sl_pct \* 2)      |
|                                                                                         |
| return Signal(                                                                          |
|                                                                                         |
| symbol=symbol, side=side,                                                               |
|                                                                                         |
| strategy_id=\'spot_strategy_v1\',                                                       |
|                                                                                         |
| timestamp=utcnow(),                                                                     |
|                                                                                         |
| signal_type=\'market\',                                                                 |
|                                                                                         |
| suggested_price=0.0,                                                                    |
|                                                                                         |
| suggested_sl=sl,                                                                        |
|                                                                                         |
| suggested_tp=tp,                                                                        |
|                                                                                         |
| confidence=confidence,                                                                  |
|                                                                                         |
| final_confidence=confidence,                                                            |
|                                                                                         |
| reasoning=\'Mock signal for testing\',                                                  |
|                                                                                         |
| model_output=0.005,                                                                     |
|                                                                                         |
| )                                                                                       |
+-----------------------------------------------------------------------------------------+

**3.3 mock_db.py & mock_notifier.py**

+--------------------------------------------------------------------------------+
| \# mock_db.py --- in-memory SQLite untuk test tanpa file system                |
|                                                                                |
| import sqlite3                                                                 |
|                                                                                |
| class MockDatabase:                                                            |
|                                                                                |
| \"\"\"SQLite in-memory. Reset otomatis setelah setiap test.\"\"\"              |
|                                                                                |
| def \_\_init\_\_(self):                                                        |
|                                                                                |
| self.conn = sqlite3.connect(\':memory:\')                                      |
|                                                                                |
| self.\_run_migrations()                                                        |
|                                                                                |
| def \_run_migrations(self):                                                    |
|                                                                                |
| \"\"\"Jalankan migration yang sama dengan production DB.\"\"\"                 |
|                                                                                |
| from migration.versions import all_migrations                                  |
|                                                                                |
| cursor = self.conn.cursor()                                                    |
|                                                                                |
| for migration in all_migrations:                                               |
|                                                                                |
| migration.up(cursor)                                                           |
|                                                                                |
| self.conn.commit()                                                             |
|                                                                                |
| def cursor(self): return self.conn.cursor()                                    |
|                                                                                |
| def commit(self): self.conn.commit()                                           |
|                                                                                |
| \# mock_notifier.py --- notifier yang tidak kirim pesan nyata                  |
|                                                                                |
| class MockNotifier:                                                            |
|                                                                                |
| \"\"\"Catat semua notifikasi yang dikirim untuk di-assert di test.\"\"\"       |
|                                                                                |
| def \_\_init\_\_(self):                                                        |
|                                                                                |
| self.sent: list\[dict\] = \[\]                                                 |
|                                                                                |
| async def notify(self, event_type: str, data: dict, \*\*kwargs) -\> None:      |
|                                                                                |
| self.sent.append({\'event_type\': event_type, \'data\': data, \*\*kwargs})     |
|                                                                                |
| async def send_emergency(self, title: str, message: str, \*\*kwargs) -\> None: |
|                                                                                |
| self.sent.append({\'event_type\': \'emergency\', \'title\': title,             |
|                                                                                |
| \'message\': message})                                                         |
|                                                                                |
| def assert_sent(self, event_type: str) -\> None:                               |
|                                                                                |
| types = \[m\[\'event_type\'\] for m in self.sent\]                             |
|                                                                                |
| assert event_type in types, f\'{event_type} tidak ditemukan di {types}\'       |
|                                                                                |
| def assert_not_sent(self, event_type: str) -\> None:                           |
|                                                                                |
| types = \[m\[\'event_type\'\] for m in self.sent\]                             |
|                                                                                |
| assert event_type not in types, f\'{event_type} tidak seharusnya dikirim\'     |
|                                                                                |
| def reset(self) -\> None:                                                      |
|                                                                                |
| self.sent.clear()                                                              |
+--------------------------------------------------------------------------------+

**4. conftest.py --- Shared Fixtures**

File ini berisi semua pytest fixture yang digunakan di lebih dari satu test file. Ditempatkan di root tests/ agar otomatis di-discover oleh pytest.

+------------------------------------------------------------------------+
| \# tests/conftest.py                                                   |
|                                                                        |
| import pytest                                                          |
|                                                                        |
| from unittest.mock import AsyncMock, MagicMock                         |
|                                                                        |
| from mocks.mock_exchange import MockBinanceExchange                    |
|                                                                        |
| from mocks.mock_data import make_portfolio_state, make_candles_df      |
|                                                                        |
| from mocks.mock_db import MockDatabase                                 |
|                                                                        |
| from mocks.mock_notifier import MockNotifier                           |
|                                                                        |
| \# ── Config fixtures ──────────────────────────────────────────       |
|                                                                        |
| \@pytest.fixture                                                       |
|                                                                        |
| def base_config():                                                     |
|                                                                        |
| \"\"\"Config dengan paper mode, nilai minimal untuk test.\"\"\"        |
|                                                                        |
| return AgentConfig(                                                    |
|                                                                        |
| mode = \'paper\',                                                      |
|                                                                        |
| initial_equity = 10_000.0,                                             |
|                                                                        |
| risk_per_trade_pct = 0.01,                                             |
|                                                                        |
| max_daily_loss_pct = 0.05,                                             |
|                                                                        |
| max_drawdown_pct = 0.10,                                               |
|                                                                        |
| max_open_positions = 5,                                                |
|                                                                        |
| symbols = \[\'BTCUSDT\'\],                                             |
|                                                                        |
| timeframe = \'1h\',                                                    |
|                                                                        |
| )                                                                      |
|                                                                        |
| \@pytest.fixture                                                       |
|                                                                        |
| def live_config(base_config):                                          |
|                                                                        |
| \"\"\"Config live mode untuk test yang butuh strict enforcement.\"\"\" |
|                                                                        |
| base_config.mode = \'live\'                                            |
|                                                                        |
| return base_config                                                     |
|                                                                        |
| \# ── Exchange fixtures ────────────────────────────────────────       |
|                                                                        |
| \@pytest.fixture                                                       |
|                                                                        |
| def mock_exchange():                                                   |
|                                                                        |
| return MockBinanceExchange(scenario=\'success\')                       |
|                                                                        |
| \@pytest.fixture                                                       |
|                                                                        |
| def mock_exchange_timeout():                                           |
|                                                                        |
| return MockBinanceExchange(scenario=\'network_timeout\')               |
|                                                                        |
| \@pytest.fixture                                                       |
|                                                                        |
| def mock_exchange_rate_limit():                                        |
|                                                                        |
| return MockBinanceExchange(scenario=\'rate_limit\')                    |
|                                                                        |
| \# ── Data fixtures ────────────────────────────────────────────       |
|                                                                        |
| \@pytest.fixture                                                       |
|                                                                        |
| def portfolio_state():                                                 |
|                                                                        |
| return make_portfolio_state(equity=10_000.0)                           |
|                                                                        |
| \@pytest.fixture                                                       |
|                                                                        |
| def candles_df():                                                      |
|                                                                        |
| return make_candles_df(n=200)                                          |
|                                                                        |
| \@pytest.fixture                                                       |
|                                                                        |
| def candles_trending_up():                                             |
|                                                                        |
| return make_candles_df(n=200, trend=0.002) \# strong uptrend           |
|                                                                        |
| \@pytest.fixture                                                       |
|                                                                        |
| def candles_sideways():                                                |
|                                                                        |
| return make_candles_df(n=200, trend=0.0, noise=0.002)                  |
|                                                                        |
| \# ── DB fixtures ──────────────────────────────────────────────       |
|                                                                        |
| \@pytest.fixture                                                       |
|                                                                        |
| def mock_db():                                                         |
|                                                                        |
| db = MockDatabase()                                                    |
|                                                                        |
| yield db                                                               |
|                                                                        |
| db.conn.close()                                                        |
|                                                                        |
| \# ── Notifier fixtures ────────────────────────────────────────       |
|                                                                        |
| \@pytest.fixture                                                       |
|                                                                        |
| def mock_notifier():                                                   |
|                                                                        |
| return MockNotifier()                                                  |
|                                                                        |
| \# ── Composite fixtures ───────────────────────────────────────       |
|                                                                        |
| \@pytest.fixture                                                       |
|                                                                        |
| def risk_manager(base_config):                                         |
|                                                                        |
| from risk_layer import RiskManager                                     |
|                                                                        |
| return RiskManager(base_config)                                        |
|                                                                        |
| \@pytest.fixture                                                       |
|                                                                        |
| def trade_store(mock_db):                                              |
|                                                                        |
| from trade_layer.store import TradeStore                               |
|                                                                        |
| return TradeStore(mock_db)                                             |
+------------------------------------------------------------------------+

**5. unit/ --- Unit Test per Layer**

**5.1 test_risk.py --- Risk Layer (sudah ada skeleton)**

Referensi ke skeleton yang sudah dibuat sebelumnya. File ini berisi 20+ test case yang sudah didokumentasikan di risk_layer_docs.docx Section 10.

  ------------------------------------------------------------------------------------------------------------------
  **Test Class**            **Yang Ditest**           **Test Case Penting**
  ------------------------- ------------------------- --------------------------------------------------------------
  **TestRiskManager**       Orchestrator evaluate()   approve normal, paper mode tidak block, block invalid symbol

  **TestCircuitBreaker**    State machine HALT        halt daily loss, warn 3%, consecutive loss, manual halt

  **TestPositionSizer**     Tiga metode sizing        fixed fractional, kelly dengan win_rate=0, lot filter floor

  **TestStopLoss**          Validasi SL               SL terlalu dekat, SL di sisi salah, default SL calculation

  **TestExposureControl**   Batas exposure            max per symbol, max total, correlation pair
  ------------------------------------------------------------------------------------------------------------------

**5.2 test_strategy.py --- Strategy Layer**

+-------------------------------------------------------------------------------+
| \# tests/unit/test_strategy.py                                                |
|                                                                               |
| import pytest                                                                 |
|                                                                               |
| from unittest.mock import MagicMock                                           |
|                                                                               |
| from mocks.mock_data import make_candles_df, make_portfolio_state             |
|                                                                               |
| class TestSpotStrategy:                                                       |
|                                                                               |
| \@pytest.fixture                                                              |
|                                                                               |
| def strategy(self, base_config):                                              |
|                                                                               |
| model = MagicMock()                                                           |
|                                                                               |
| model.predict.return_value = \[0.006\] \# prediksi return 0.6%                |
|                                                                               |
| return SpotStrategy(config=base_config, model=model)                          |
|                                                                               |
| \@pytest.fixture                                                              |
|                                                                               |
| def market_state(self, candles_df):                                           |
|                                                                               |
| return MarketState(                                                           |
|                                                                               |
| symbol=\'BTCUSDT\', timeframe=\'1h\',                                         |
|                                                                               |
| timestamp=utcnow(),                                                           |
|                                                                               |
| last_price=50_000.0, bid=49_995.0, ask=50_005.0,                              |
|                                                                               |
| mid=50_000.0, spread_pct=0.01,                                                |
|                                                                               |
| volume_24h=1_000_000.0, orderbook_imbalance=0.2,                              |
|                                                                               |
| latest_candle=make_candle(),                                                  |
|                                                                               |
| candles_df=candles_df,                                                        |
|                                                                               |
| regime=MarketRegime.STRONG_TREND_UP,                                          |
|                                                                               |
| regime_confidence=0.85,                                                       |
|                                                                               |
| regime_stable=True,                                                           |
|                                                                               |
| vol_metrics=MagicMock(atr=750.0, atr_percentile=45.0, vol_regime=\'normal\'), |
|                                                                               |
| features=MagicMock(),                                                         |
|                                                                               |
| open_positions={},                                                            |
|                                                                               |
| available_equity=10_000.0, equity=10_000.0, daily_pnl=0.0,                    |
|                                                                               |
| active_anomalies=\[\], is_safe_to_trade=True,                                 |
|                                                                               |
| data_quality=\'good\', latency_ms=50.0,                                       |
|                                                                               |
| )                                                                             |
|                                                                               |
| def test_generate_signal_above_threshold(self, strategy, market_state):       |
|                                                                               |
| signal = strategy.generate_signal(market_state)                               |
|                                                                               |
| assert signal is not None                                                     |
|                                                                               |
| assert signal.side == \'BUY\'                                                 |
|                                                                               |
| assert signal.suggested_sl \> 0                                               |
|                                                                               |
| assert signal.confidence \> 0                                                 |
|                                                                               |
| def test_no_signal_below_threshold(self, strategy, market_state):             |
|                                                                               |
| strategy.model.predict.return_value = \[0.001\] \# di bawah threshold         |
|                                                                               |
| signal = strategy.generate_signal(market_state)                               |
|                                                                               |
| assert signal is None                                                         |
|                                                                               |
| def test_no_signal_high_volatility_regime(self, strategy, market_state):      |
|                                                                               |
| market_state.regime = MarketRegime.HIGH_VOLATILITY                            |
|                                                                               |
| signal = strategy.generate_signal(market_state)                               |
|                                                                               |
| assert signal is None                                                         |
|                                                                               |
| def test_no_signal_when_position_open(self, strategy, market_state):          |
|                                                                               |
| market_state.open_positions = {\'BTCUSDT\': {\'notional\': 500.0}}            |
|                                                                               |
| signal = strategy.generate_signal(market_state)                               |
|                                                                               |
| assert signal is None                                                         |
|                                                                               |
| def test_no_signal_when_unsafe(self, strategy, market_state):                 |
|                                                                               |
| market_state.is_safe_to_trade = False                                         |
|                                                                               |
| signal = strategy.generate_signal(market_state)                               |
|                                                                               |
| assert signal is None                                                         |
|                                                                               |
| def test_no_short_in_spot_mode(self, strategy, market_state):                 |
|                                                                               |
| strategy.model.predict.return_value = \[-0.006\] \# bearish                   |
|                                                                               |
| signal = strategy.generate_signal(market_state)                               |
|                                                                               |
| assert signal is None \# ALLOW_SPOT_SHORT = False by default                  |
|                                                                               |
| def test_cooldown_between_signals(self, strategy, market_state):              |
|                                                                               |
| \# Pertama kali: hasilkan signal                                              |
|                                                                               |
| sig1 = strategy.generate_signal(market_state)                                 |
|                                                                               |
| assert sig1 is not None                                                       |
|                                                                               |
| \# Langsung setelah itu: harus None (cooldown)                                |
|                                                                               |
| sig2 = strategy.generate_signal(market_state)                                 |
|                                                                               |
| assert sig2 is None                                                           |
|                                                                               |
| def test_sl_below_entry_for_buy(self, strategy, market_state):                |
|                                                                               |
| signal = strategy.generate_signal(market_state)                               |
|                                                                               |
| assert signal is not None                                                     |
|                                                                               |
| assert signal.suggested_sl \< market_state.last_price                         |
|                                                                               |
| def test_confidence_between_0_and_1(self, strategy, market_state):            |
|                                                                               |
| signal = strategy.generate_signal(market_state)                               |
|                                                                               |
| if signal:                                                                    |
|                                                                               |
| assert 0.0 \<= signal.confidence \<= 1.0                                      |
|                                                                               |
| assert 0.0 \<= signal.final_confidence \<= 1.0                                |
+-------------------------------------------------------------------------------+

**5.3 test_idempotency.py --- Anti Double Order**

+-------------------------------------------------------------------------+
| \# tests/unit/test_idempotency.py                                       |
|                                                                         |
| import pytest                                                           |
|                                                                         |
| import threading                                                        |
|                                                                         |
| class TestIdempotencyManager:                                           |
|                                                                         |
| \@pytest.fixture                                                        |
|                                                                         |
| def idempotency(self, mock_db):                                         |
|                                                                         |
| return IdempotencyManager(db=mock_db)                                   |
|                                                                         |
| def test_first_call_returns_proceed(self, idempotency):                 |
|                                                                         |
| result = idempotency.check_or_register(\'order-001\')                   |
|                                                                         |
| assert result == IdempotencyResult.PROCEED                              |
|                                                                         |
| def test_second_call_returns_duplicate(self, idempotency):              |
|                                                                         |
| idempotency.check_or_register(\'order-001\')                            |
|                                                                         |
| idempotency.confirm(\'order-001\', \'exchange-001\')                    |
|                                                                         |
| result = idempotency.check_or_register(\'order-001\')                   |
|                                                                         |
| assert result == IdempotencyResult.DUPLICATE                            |
|                                                                         |
| def test_pending_returns_pending(self, idempotency):                    |
|                                                                         |
| idempotency.check_or_register(\'order-002\')                            |
|                                                                         |
| \# Belum confirm → masih pending                                        |
|                                                                         |
| result = idempotency.check_or_register(\'order-002\')                   |
|                                                                         |
| assert result == IdempotencyResult.PENDING                              |
|                                                                         |
| def test_failed_order_can_be_retried_with_new_id(self, idempotency):    |
|                                                                         |
| idempotency.check_or_register(\'order-003\')                            |
|                                                                         |
| idempotency.mark_failed(\'order-003\', \'network timeout\')             |
|                                                                         |
| \# Order ID baru: PROCEED                                               |
|                                                                         |
| result = idempotency.check_or_register(\'order-004\')                   |
|                                                                         |
| assert result == IdempotencyResult.PROCEED                              |
|                                                                         |
| def test_concurrent_same_id_only_one_proceeds(self, idempotency):       |
|                                                                         |
| \"\"\"                                                                  |
|                                                                         |
| Simulasi race condition: dua thread check_or_register                   |
|                                                                         |
| dengan ID yang sama secara bersamaan.                                   |
|                                                                         |
| Hanya satu yang boleh PROCEED.                                          |
|                                                                         |
| \"\"\"                                                                  |
|                                                                         |
| results = \[\]                                                          |
|                                                                         |
| def worker():                                                           |
|                                                                         |
| r = idempotency.check_or_register(\'order-race\')                       |
|                                                                         |
| results.append(r)                                                       |
|                                                                         |
| threads = \[threading.Thread(target=worker) for \_ in range(5)\]        |
|                                                                         |
| for t in threads: t.start()                                             |
|                                                                         |
| for t in threads: t.join()                                              |
|                                                                         |
| proceed_count = results.count(IdempotencyResult.PROCEED)                |
|                                                                         |
| assert proceed_count == 1, f\'Expected 1 PROCEED, got {proceed_count}\' |
|                                                                         |
| def test_cleanup_removes_old_confirmed(self, idempotency):              |
|                                                                         |
| idempotency.check_or_register(\'old-order\')                            |
|                                                                         |
| idempotency.confirm(\'old-order\', \'ex-001\')                          |
|                                                                         |
| \# Manipulate timestamp ke 31 hari lalu                                 |
|                                                                         |
| idempotency.\_backdate(\'old-order\', days=31)                          |
|                                                                         |
| deleted = idempotency.cleanup_old(older_than_days=30)                   |
|                                                                         |
| assert deleted \>= 1                                                    |
+-------------------------------------------------------------------------+

**5.4 test_exit.py --- Exit Layer**

+---------------------------------------------------------------------------------------------+
| \# tests/unit/test_exit.py                                                                  |
|                                                                                             |
| import pytest                                                                               |
|                                                                                             |
| from mocks.mock_data import make_candle                                                     |
|                                                                                             |
| class TestExitManager:                                                                      |
|                                                                                             |
| \@pytest.fixture                                                                            |
|                                                                                             |
| def exit_mgr(self, base_config):                                                            |
|                                                                                             |
| return ExitManager(config=base_config, \...)                                                |
|                                                                                             |
| \@pytest.fixture                                                                            |
|                                                                                             |
| def open_trade(self):                                                                       |
|                                                                                             |
| return Trade(                                                                               |
|                                                                                             |
| trade_id=\'test-001\', client_order_id=\'test_btc_123\',                                    |
|                                                                                             |
| symbol=\'BTCUSDT\', side=\'BUY\', order_type=\'market\',                                    |
|                                                                                             |
| strategy_id=\'spot_v1\', mode=\'paper\', created_at=utcnow(),                               |
|                                                                                             |
| status=TradeStatus.OPEN,                                                                    |
|                                                                                             |
| filled_qty=0.001, avg_fill_price=50_000.0,                                                  |
|                                                                                             |
| sl_price=49_000.0, tp_price=52_000.0,                                                       |
|                                                                                             |
| risk_amount_usd=50.0,                                                                       |
|                                                                                             |
| )                                                                                           |
|                                                                                             |
| def test_sl_hit_by_candle_low(self, exit_mgr, open_trade, market_state):                    |
|                                                                                             |
| market_state.latest_candle = make_candle(close=49_500.0) \# low akan \< SL                  |
|                                                                                             |
| market_state.latest_candle.low = 48_900.0 \# di bawah sl_price=49000                        |
|                                                                                             |
| decision = exit_mgr.evaluate(open_trade, market_state)                                      |
|                                                                                             |
| assert decision.action == ExitAction.EXIT_SL                                                |
|                                                                                             |
| assert decision.urgency == \'urgent\'                                                       |
|                                                                                             |
| def test_sl_not_hit_if_low_above_sl(self, exit_mgr, open_trade, market_state):              |
|                                                                                             |
| market_state.latest_candle.low = 49_200.0 \# di atas sl_price=49000                         |
|                                                                                             |
| decision = exit_mgr.evaluate(open_trade, market_state)                                      |
|                                                                                             |
| assert decision.action != ExitAction.EXIT_SL                                                |
|                                                                                             |
| def test_tp_hit_by_candle_high(self, exit_mgr, open_trade, market_state):                   |
|                                                                                             |
| market_state.latest_candle.high = 52_100.0 \# di atas tp_price=52000                        |
|                                                                                             |
| decision = exit_mgr.evaluate(open_trade, market_state)                                      |
|                                                                                             |
| assert decision.action == ExitAction.EXIT_TP                                                |
|                                                                                             |
| def test_sl_priority_over_tp(self, exit_mgr, open_trade, market_state):                     |
|                                                                                             |
| \"\"\"SL harus dicek duluan dari TP --- prioritas lebih tinggi.\"\"\"                       |
|                                                                                             |
| market_state.latest_candle.low = 48_900.0 \# SL hit                                         |
|                                                                                             |
| market_state.latest_candle.high = 52_100.0 \# TP juga hit (candle lebar)                    |
|                                                                                             |
| decision = exit_mgr.evaluate(open_trade, market_state)                                      |
|                                                                                             |
| assert decision.action == ExitAction.EXIT_SL \# bukan EXIT_TP                               |
|                                                                                             |
| def test_trailing_only_activates_after_trigger_r(self, exit_mgr, open_trade, market_state): |
|                                                                                             |
| \# Profit 0.5R, activation = 1.0R → trailing belum aktif                                    |
|                                                                                             |
| market_state.latest_candle.close = 50_500.0 \# profit = \$500 = 0.5R                        |
|                                                                                             |
| decision = exit_mgr.evaluate(open_trade, market_state)                                      |
|                                                                                             |
| assert decision.action == ExitAction.HOLD                                                   |
|                                                                                             |
| def test_timeout_exit_after_max_hold_candles(self, exit_mgr, open_trade, market_state):     |
|                                                                                             |
| from utils.time_utils import mock_time, reset_mock_time                                     |
|                                                                                             |
| \# Simulasi posisi sudah terbuka 49 jam (max = 48 candle 1H)                                |
|                                                                                             |
| mock_time(open_trade.opened_at + timedelta(hours=49))                                       |
|                                                                                             |
| decision = exit_mgr.evaluate(open_trade, market_state)                                      |
|                                                                                             |
| assert decision.action == ExitAction.EXIT_TIMEOUT                                           |
|                                                                                             |
| reset_mock_time()                                                                           |
|                                                                                             |
| class TestTrailingStop:                                                                     |
|                                                                                             |
| def test_trail_only_moves_up_for_buy(self):                                                 |
|                                                                                             |
| trailing = TrailingStopManager(config=\...)                                                 |
|                                                                                             |
| trade = make_open_trade(side=\'BUY\', entry=50_000, sl=49_000)                              |
|                                                                                             |
| trailing.register(trade)                                                                    |
|                                                                                             |
| \# Aktifkan trailing (profit 1R)                                                            |
|                                                                                             |
| trailing.update(trade, high=51_000, low=50_500, close=51_000, atr=750)                      |
|                                                                                             |
| state1 = trailing.get_state(trade.trade_id)                                                 |
|                                                                                             |
| trail1 = state1.current_trail                                                               |
|                                                                                             |
| \# Harga naik lagi → trail naik                                                             |
|                                                                                             |
| trailing.update(trade, high=52_000, low=51_500, close=52_000, atr=750)                      |
|                                                                                             |
| state2 = trailing.get_state(trade.trade_id)                                                 |
|                                                                                             |
| assert state2.current_trail \> trail1                                                       |
|                                                                                             |
| \# Harga turun → trail TIDAK ikut turun                                                     |
|                                                                                             |
| trailing.update(trade, high=51_500, low=50_500, close=51_000, atr=750)                      |
|                                                                                             |
| state3 = trailing.get_state(trade.trade_id)                                                 |
|                                                                                             |
| assert state3.current_trail == state2.current_trail \# tidak berubah                        |
|                                                                                             |
| def test_breakeven_updates_sl_to_entry(self):                                               |
|                                                                                             |
| be = BreakEvenManager(config=\...)                                                          |
|                                                                                             |
| trade = make_open_trade(side=\'BUY\', entry=50_000, sl=49_000)                              |
|                                                                                             |
| \# Profit = 1R → trigger breakeven                                                          |
|                                                                                             |
| \# risk = 50000 - 49000 = 1000, profit 1R = 1000                                            |
|                                                                                             |
| decision = be.check(trade, close=51_000.0)                                                  |
|                                                                                             |
| assert decision is not None                                                                 |
|                                                                                             |
| assert decision.action == ExitAction.UPDATE_SL                                              |
|                                                                                             |
| assert decision.new_sl \>= trade.avg_fill_price                                             |
+---------------------------------------------------------------------------------------------+

**5.5 test_helpers.py --- Utils**

+-----------------------------------------------------------------------------------------+
| \# tests/unit/test_helpers.py                                                           |
|                                                                                         |
| import pytest                                                                           |
|                                                                                         |
| from utils.helpers import round_qty, round_price, safe_div, calc_pnl, generate_order_id |
|                                                                                         |
| class TestRoundQty:                                                                     |
|                                                                                         |
| def test_floor_not_round(self):                                                         |
|                                                                                         |
| \# 0.1235 harus → 0.123, bukan 0.124                                                    |
|                                                                                         |
| assert round_qty(0.1235, 0.001) == pytest.approx(0.123)                                 |
|                                                                                         |
| def test_exact_step_unchanged(self):                                                    |
|                                                                                         |
| assert round_qty(0.100, 0.001) == pytest.approx(0.100)                                  |
|                                                                                         |
| def test_very_small_qty(self):                                                          |
|                                                                                         |
| assert round_qty(0.0009, 0.001) == 0.0                                                  |
|                                                                                         |
| def test_different_step_sizes(self):                                                    |
|                                                                                         |
| assert round_qty(5.678, 0.1) == pytest.approx(5.6)                                      |
|                                                                                         |
| assert round_qty(5.678, 0.01) == pytest.approx(5.67)                                    |
|                                                                                         |
| assert round_qty(5.678, 1.0) == pytest.approx(5.0)                                      |
|                                                                                         |
| def test_invalid_step_raises(self):                                                     |
|                                                                                         |
| with pytest.raises(ValueError):                                                         |
|                                                                                         |
| round_qty(1.0, 0.0)                                                                     |
|                                                                                         |
| class TestCalcPnl:                                                                      |
|                                                                                         |
| def test_buy_win(self):                                                                 |
|                                                                                         |
| pnl_usd, pnl_pct = calc_pnl(\'BUY\', 50_000, 52_000, 0.001)                             |
|                                                                                         |
| assert pnl_usd == pytest.approx(2.0) \# \$2                                             |
|                                                                                         |
| assert pnl_pct == pytest.approx(4.0) \# 4%                                              |
|                                                                                         |
| def test_buy_loss(self):                                                                |
|                                                                                         |
| pnl_usd, pnl_pct = calc_pnl(\'BUY\', 50_000, 49_000, 0.001)                             |
|                                                                                         |
| assert pnl_usd == pytest.approx(-1.0) \# -\$1                                           |
|                                                                                         |
| assert pnl_pct == pytest.approx(-2.0) \# -2%                                            |
|                                                                                         |
| def test_sell_win(self):                                                                |
|                                                                                         |
| pnl_usd, pnl_pct = calc_pnl(\'SELL\', 50_000, 48_000, 0.001)                            |
|                                                                                         |
| assert pnl_usd == pytest.approx(2.0) \# short profit                                    |
|                                                                                         |
| def test_commission_deducted(self):                                                     |
|                                                                                         |
| pnl_usd, \_ = calc_pnl(\'BUY\', 50_000, 52_000, 0.001, commission=0.05)                 |
|                                                                                         |
| assert pnl_usd == pytest.approx(1.95) \# 2.0 - 0.05                                     |
|                                                                                         |
| class TestGenerateOrderId:                                                              |
|                                                                                         |
| def test_unique_per_call(self):                                                         |
|                                                                                         |
| id1 = generate_order_id(\'strategy_v1\', \'BTCUSDT\')                                   |
|                                                                                         |
| id2 = generate_order_id(\'strategy_v1\', \'BTCUSDT\')                                   |
|                                                                                         |
| assert id1 != id2 \# timestamp berbeda                                                  |
|                                                                                         |
| def test_max_36_chars(self):                                                            |
|                                                                                         |
| very_long = \'a\' \* 50                                                                 |
|                                                                                         |
| oid = generate_order_id(very_long, \'BTCUSDT\')                                         |
|                                                                                         |
| assert len(oid) \<= 36                                                                  |
|                                                                                         |
| def test_valid_characters(self):                                                        |
|                                                                                         |
| import re                                                                               |
|                                                                                         |
| oid = generate_order_id(\'spot_v1\', \'BTCUSDT\')                                       |
|                                                                                         |
| assert re.match(r\'\^\[a-zA-Z0-9\_\\-\]+\$\', oid)                                      |
+-----------------------------------------------------------------------------------------+

**6. integration/ --- Test Alur Antar Layer**

**6.1 test_full_flow.py --- Signal ke Close**

Test end-to-end yang paling penting. Memastikan seluruh pipeline dari signal generation sampai trade close bekerja dengan benar secara bersamaan.

+-----------------------------------------------------------------------------+
| \# tests/integration/test_full_flow.py                                      |
|                                                                             |
| import pytest                                                               |
|                                                                             |
| import asyncio                                                              |
|                                                                             |
| class TestFullTradingFlow:                                                  |
|                                                                             |
| \@pytest.fixture                                                            |
|                                                                             |
| async def system(self, base_config, mock_exchange, mock_db, mock_notifier): |
|                                                                             |
| \"\"\"Inisialisasi seluruh layer dalam kondisi paper mode.\"\"\"            |
|                                                                             |
| risk_mgr = RiskManager(base_config)                                         |
|                                                                             |
| store = TradeStore(mock_db)                                                 |
|                                                                             |
| audit = AuditTrail(mock_db)                                                 |
|                                                                             |
| idem = IdempotencyManager(mock_db)                                          |
|                                                                             |
| executor = SpotExecutor(exchange=mock_exchange, config=base_config)         |
|                                                                             |
| trade_mgr = TradeManager(                                                   |
|                                                                             |
| config=base_config, risk_mgr=risk_mgr,                                      |
|                                                                             |
| executor=executor, store=store,                                             |
|                                                                             |
| audit=audit, idempotency=idem,                                              |
|                                                                             |
| )                                                                           |
|                                                                             |
| exit_mgr = ExitManager(config=base_config, \...)                            |
|                                                                             |
| return {\'risk\': risk_mgr, \'trade\': trade_mgr,                           |
|                                                                             |
| \'exit\': exit_mgr, \'notifier\': mock_notifier}                            |
|                                                                             |
| \@pytest.mark.asyncio                                                       |
|                                                                             |
| \@pytest.mark.critical                                                      |
|                                                                             |
| async def test_open_and_close_trade(self, system, portfolio_state):         |
|                                                                             |
| signal = make_signal(symbol=\'BTCUSDT\', side=\'BUY\', confidence=0.80)     |
|                                                                             |
| \# 1. Risk evaluation                                                       |
|                                                                             |
| request = TradeRequest.from_signal(signal, quantity=0.001)                  |
|                                                                             |
| risk_result = system\[\'risk\'\].evaluate(request, portfolio_state)         |
|                                                                             |
| assert risk_result.is_approved                                              |
|                                                                             |
| \# 2. Buka trade                                                            |
|                                                                             |
| trade = await system\[\'trade\'\].open(signal, risk_result)                 |
|                                                                             |
| assert trade.status == TradeStatus.OPEN                                     |
|                                                                             |
| assert trade.filled_qty \> 0                                                |
|                                                                             |
| assert trade.sl_price \> 0                                                  |
|                                                                             |
| \# 3. Simulasi SL hit                                                       |
|                                                                             |
| sl_candle = make_candle(close=49_000.0)                                     |
|                                                                             |
| sl_candle.low = 48_900.0 \# bawah sl_price                                  |
|                                                                             |
| state = make_market_state(candle=sl_candle)                                 |
|                                                                             |
| decision = system\[\'exit\'\].evaluate(trade, state)                        |
|                                                                             |
| assert decision.action == ExitAction.EXIT_SL                                |
|                                                                             |
| \# 4. Tutup trade                                                           |
|                                                                             |
| closed = await system\[\'trade\'\].close(trade, decision)                   |
|                                                                             |
| assert closed.status == TradeStatus.CLOSED                                  |
|                                                                             |
| assert closed.exit_reason == \'sl\'                                         |
|                                                                             |
| assert closed.pnl_usd \< 0 \# loss karena SL                                |
|                                                                             |
| \@pytest.mark.asyncio                                                       |
|                                                                             |
| \@pytest.mark.critical                                                      |
|                                                                             |
| async def test_idempotency_prevents_double_open(                            |
|                                                                             |
| self, system, portfolio_state                                               |
|                                                                             |
| ):                                                                          |
|                                                                             |
| signal = make_signal(symbol=\'BTCUSDT\', side=\'BUY\', confidence=0.80)     |
|                                                                             |
| request = TradeRequest.from_signal(signal, quantity=0.001)                  |
|                                                                             |
| risk_result = system\[\'risk\'\].evaluate(request, portfolio_state)         |
|                                                                             |
| \# Buka pertama kali                                                        |
|                                                                             |
| trade1 = await system\[\'trade\'\].open(signal, risk_result)                |
|                                                                             |
| assert trade1.status == TradeStatus.OPEN                                    |
|                                                                             |
| \# Coba buka dengan signal yang sama (ID sama)                              |
|                                                                             |
| with pytest.raises(DuplicateOrderError):                                    |
|                                                                             |
| await system\[\'trade\'\].open(signal, risk_result)                         |
|                                                                             |
| \@pytest.mark.asyncio                                                       |
|                                                                             |
| async def test_paper_mode_does_not_send_real_order(                         |
|                                                                             |
| self, system, portfolio_state, mock_exchange                                |
|                                                                             |
| ):                                                                          |
|                                                                             |
| signal = make_signal(symbol=\'BTCUSDT\', side=\'BUY\', confidence=0.80)     |
|                                                                             |
| request = TradeRequest.from_signal(signal, quantity=0.001)                  |
|                                                                             |
| risk_result = system\[\'risk\'\].evaluate(request, portfolio_state)         |
|                                                                             |
| await system\[\'trade\'\].open(signal, risk_result)                         |
|                                                                             |
| \# Paper mode: tidak ada call ke exchange                                   |
|                                                                             |
| assert mock_exchange.\_call_count == 0                                      |
+-----------------------------------------------------------------------------+

**6.2 test_recovery_flow.py --- Crash & Recovery**

+---------------------------------------------------------------------------+
| \# tests/integration/test_recovery_flow.py                                |
|                                                                           |
| class TestRecoveryFlow:                                                   |
|                                                                           |
| \@pytest.mark.asyncio                                                     |
|                                                                           |
| \@pytest.mark.critical                                                    |
|                                                                           |
| async def test_recovery_syncs_filled_order(self, mock_db, mock_exchange): |
|                                                                           |
| \"\"\"                                                                    |
|                                                                           |
| Skenario: order dikirim ke exchange, agent crash sebelum                  |
|                                                                           |
| menerima konfirmasi. Saat restart, recovery harus mendeteksi              |
|                                                                           |
| bahwa order sudah ter-fill dan update DB.                                 |
|                                                                           |
| \"\"\"                                                                    |
|                                                                           |
| store = TradeStore(mock_db)                                               |
|                                                                           |
| \# Simpan trade dalam status SUBMITTED (belum konfirmasi)                 |
|                                                                           |
| trade = make_trade(status=TradeStatus.SUBMITTED,                          |
|                                                                           |
| client_order_id=\'recover-001\')                                          |
|                                                                           |
| store.save(trade)                                                         |
|                                                                           |
| \# Exchange tahu order sudah FILLED                                       |
|                                                                           |
| mock_exchange.\_orders\[\'recover-001\'\] = OrderResponse(                |
|                                                                           |
| exchange_order_id=\'ex-001\', client_order_id=\'recover-001\',            |
|                                                                           |
| status=\'FILLED\', filled_qty=0.001, avg_price=50_000.0,                  |
|                                                                           |
| commission=0.05, commission_asset=\'USDT\', timestamp=utcnow(),           |
|                                                                           |
| raw_response={},                                                          |
|                                                                           |
| )                                                                         |
|                                                                           |
| recovery = RecoveryManager(store=store, executor=mock_exchange, \...)     |
|                                                                           |
| report = await recovery.restore()                                         |
|                                                                           |
| assert report.success                                                     |
|                                                                           |
| assert \'UPDATED to OPEN: \' in str(report.actions_taken)                 |
|                                                                           |
| updated = store.get(trade.trade_id)                                       |
|                                                                           |
| assert updated.status == TradeStatus.OPEN                                 |
|                                                                           |
| assert updated.filled_qty == 0.001                                        |
|                                                                           |
| \@pytest.mark.asyncio                                                     |
|                                                                           |
| \@pytest.mark.critical                                                    |
|                                                                           |
| async def test_recovery_enters_safe_mode_on_ghost_trade(                  |
|                                                                           |
| self, mock_db, mock_exchange, mock_notifier                               |
|                                                                           |
| ):                                                                        |
|                                                                           |
| \"\"\"                                                                    |
|                                                                           |
| Ghost trade: ada di internal (OPEN) tapi tidak di exchange.               |
|                                                                           |
| Recovery harus masuk safe_mode.                                           |
|                                                                           |
| \"\"\"                                                                    |
|                                                                           |
| store = TradeStore(mock_db)                                               |
|                                                                           |
| \# Trade OPEN di internal                                                 |
|                                                                           |
| trade = make_trade(status=TradeStatus.OPEN,                               |
|                                                                           |
| client_order_id=\'ghost-001\')                                            |
|                                                                           |
| store.save(trade)                                                         |
|                                                                           |
| \# Exchange: OrderNotFoundError                                           |
|                                                                           |
| mock_exchange.set_scenario(\'order_not_found\')                           |
|                                                                           |
| safe_mode = MagicMock()                                                   |
|                                                                           |
| recovery = RecoveryManager(store=store, executor=mock_exchange,           |
|                                                                           |
| safe_mode=safe_mode, \...)                                                |
|                                                                           |
| report = await recovery.restore()                                         |
|                                                                           |
| assert not report.success                                                 |
|                                                                           |
| safe_mode.emergency_stop.assert_called_once()                             |
+---------------------------------------------------------------------------+

**6.3 test_exchange_flow.py --- Execution Layer**

+---------------------------------------------------------------------+
| \# tests/integration/test_exchange_flow.py                          |
|                                                                     |
| class TestExchangeFlow:                                             |
|                                                                     |
| \@pytest.mark.asyncio                                               |
|                                                                     |
| async def test_spot_executor_places_order(                          |
|                                                                     |
| self, base_config, mock_exchange                                    |
|                                                                     |
| ):                                                                  |
|                                                                     |
| executor = SpotExecutor(exchange=mock_exchange, config=base_config) |
|                                                                     |
| order = OrderRequest(                                               |
|                                                                     |
| symbol=\'BTCUSDT\', side=\'BUY\', order_type=\'MARKET\',            |
|                                                                     |
| quantity=0.001, price=0.0,                                          |
|                                                                     |
| client_order_id=\'test-ex-001\',                                    |
|                                                                     |
| )                                                                   |
|                                                                     |
| response = await executor.place_order(order)                        |
|                                                                     |
| assert response.status == \'FILLED\'                                |
|                                                                     |
| assert response.filled_qty == pytest.approx(0.001)                  |
|                                                                     |
| \@pytest.mark.asyncio                                               |
|                                                                     |
| async def test_retry_on_network_timeout(                            |
|                                                                     |
| self, base_config, mock_exchange_timeout                            |
|                                                                     |
| ):                                                                  |
|                                                                     |
| executor = SpotExecutor(exchange=mock_exchange_timeout,             |
|                                                                     |
| config=base_config)                                                 |
|                                                                     |
| order = make_order_request()                                        |
|                                                                     |
| with pytest.raises(asyncio.TimeoutError):                           |
|                                                                     |
| await executor.place_order(order)                                   |
|                                                                     |
| \# Harus ada 3 attempt (max_retry=3)                                |
|                                                                     |
| assert mock_exchange_timeout.\_call_count == 3                      |
|                                                                     |
| \@pytest.mark.asyncio                                               |
|                                                                     |
| async def test_insufficient_balance_no_retry(                       |
|                                                                     |
| self, base_config, mock_exchange                                    |
|                                                                     |
| ):                                                                  |
|                                                                     |
| mock_exchange.set_scenario(\'insufficient_balance\')                |
|                                                                     |
| executor = SpotExecutor(exchange=mock_exchange, config=base_config) |
|                                                                     |
| order = make_order_request()                                        |
|                                                                     |
| with pytest.raises(InsufficientBalanceError):                       |
|                                                                     |
| await executor.place_order(order)                                   |
|                                                                     |
| \# Hanya 1 attempt --- tidak di-retry                               |
|                                                                     |
| assert mock_exchange.\_call_count == 1                              |
|                                                                     |
| \@pytest.mark.asyncio                                               |
|                                                                     |
| async def test_smart_router_uses_primary_exchange(                  |
|                                                                     |
| self, base_config, mock_exchange                                    |
|                                                                     |
| ):                                                                  |
|                                                                     |
| router = SmartRouter(                                               |
|                                                                     |
| exchanges={\'binance\': mock_exchange},                             |
|                                                                     |
| strategy=\'primary_only\',                                          |
|                                                                     |
| primary=\'binance\',                                                |
|                                                                     |
| )                                                                   |
|                                                                     |
| exchange, name = await router.route(make_order_request())           |
|                                                                     |
| assert name == \'binance\'                                          |
|                                                                     |
| assert exchange is mock_exchange                                    |
+---------------------------------------------------------------------+

**7. Pytest Markers & CI Pipeline**

**7.1 Custom Markers**

  -------------------------------------------------------------------------------------------------------------------------------
  **Marker**                  **Digunakan Untuk**                                   **Contoh**
  --------------------------- ----------------------------------------------------- ---------------------------------------------
  \@pytest.mark.critical      Test wajib lulus sebelum deploy apapun                Test idempotency, recovery, circuit breaker

  \@pytest.mark.slow          Test yang butuh \> 1 detik (skip di quick run)        Integration test dengan DB

  \@pytest.mark.live_only     Test yang butuh koneksi exchange nyata (skip di CI)   Test koneksi Binance testnet

  \@pytest.mark.parametrize   Test dengan multiple input values                     Test round_qty dengan berbagai step_size
  -------------------------------------------------------------------------------------------------------------------------------

**7.2 pytest.ini --- Konfigurasi**

+-----------------------------------------------------------+
| \# pytest.ini                                             |
|                                                           |
| \[pytest\]                                                |
|                                                           |
| testpaths = tests                                         |
|                                                           |
| python_files = test\_\*.py                                |
|                                                           |
| python_classes = Test\*                                   |
|                                                           |
| python_functions= test\_\*                                |
|                                                           |
| asyncio_mode = auto                                       |
|                                                           |
| markers =                                                 |
|                                                           |
| critical: Test wajib lulus sebelum deploy                 |
|                                                           |
| slow: Test lambat, skip dengan -m \'not slow\'            |
|                                                           |
| live_only: Butuh koneksi exchange nyata                   |
|                                                           |
| \# Jalankan hanya critical test (untuk pre-deploy check): |
|                                                           |
| \# pytest -m critical -v                                  |
|                                                           |
| \# Jalankan semua kecuali live_only:                      |
|                                                           |
| \# pytest -m \'not live_only\' -v                         |
|                                                           |
| \# Quick run (tanpa slow test):                           |
|                                                           |
| \# pytest -m \'not slow and not live_only\' -v            |
+-----------------------------------------------------------+

**7.3 Pipeline CI/CD --- Gate per Stage**

  ---------------------------------------------------------------------------------------------------------------
  **Stage**     **Trigger**               **Test yang Dijalankan**                 **Syarat Lulus**
  ------------- ------------------------- ---------------------------------------- ------------------------------
  Pre-commit    git commit                pytest -m critical (\< 30 detik)         100% critical tests pass

  CI Build      push ke branch            pytest tests/unit/ (\< 5 menit)          100% unit tests pass

  CI Full       merge ke main             pytest tests/ -m \'not live_only\'       100% tests + coverage target

  Pre-deploy    tag release               pytest -m critical + coverage check      Coverage sesuai requirements

  Post-deploy   setelah deploy ke paper   pytest tests/integration/ (smoke test)   Semua integration tests pass
  ---------------------------------------------------------------------------------------------------------------

**8. Coverage Report --- Cara Membaca & Target**

**8.1 Menjalankan Coverage Report**

+-------------------------------------------------------------------------------------------+
| \# Generate HTML report (buka htmlcov/index.html)                                         |
|                                                                                           |
| pytest tests/ \--cov=runtime/agent \--cov-report=html                                     |
|                                                                                           |
| \# Terminal report dengan missing lines                                                   |
|                                                                                           |
| pytest tests/ \--cov=runtime/agent \--cov-report=term-missing                             |
|                                                                                           |
| \# Coverage per layer (fokus ke layer tertentu)                                           |
|                                                                                           |
| pytest tests/unit/test_risk.py \--cov=runtime/agent/risk_layer \--cov-report=term-missing |
|                                                                                           |
| \# Fail jika coverage di bawah threshold                                                  |
|                                                                                           |
| pytest tests/ \--cov=runtime/agent \--cov-fail-under=90                                   |
|                                                                                           |
| \# .coveragerc --- konfigurasi apa yang dikecualikan                                      |
+-------------------------------------------------------------------------------------------+

**8.2 .coveragerc --- Konfigurasi**

+-------------------------------------------------+
| \# .coveragerc                                  |
|                                                 |
| \[run\]                                         |
|                                                 |
| source = runtime/agent                          |
|                                                 |
| omit =                                          |
|                                                 |
| \*/tests/\*                                     |
|                                                 |
| \*/mocks/\*                                     |
|                                                 |
| \*/\_\_init\_\_.py                              |
|                                                 |
| \*/conftest.py                                  |
|                                                 |
| \# File yang tidak perlu di-cover:              |
|                                                 |
| \*/agent/logs/\*                                |
|                                                 |
| \*/agent/memory/\*                              |
|                                                 |
| \*/agent/models/\*.pkl                          |
|                                                 |
| \[report\]                                      |
|                                                 |
| exclude_lines =                                 |
|                                                 |
| \# Baris yang boleh dikecualikan dari coverage: |
|                                                 |
| pragma: no cover                                |
|                                                 |
| def \_\_repr\_\_                                |
|                                                 |
| if \_\_name\_\_ == .\_\_main\_\_.:              |
|                                                 |
| raise NotImplementedError                       |
|                                                 |
| pass                                            |
|                                                 |
| \\.\\.\\. \# abstract method body               |
|                                                 |
| \[html\]                                        |
|                                                 |
| directory = htmlcov                             |
|                                                 |
| title = crypto_ai_agent Coverage Report         |
+-------------------------------------------------+

**8.3 Branch Coverage vs Line Coverage**

  --------------------------------------------------------------------------------------------------------------------------------------
  **Metrik**        **Artinya**                                  **Contoh**                             **Target**
  ----------------- -------------------------------------------- -------------------------------------- --------------------------------
  Line coverage     Berapa % baris yang dieksekusi saat test     70% = 7 dari 10 baris dijalankan       ≥ 85% semua layer

  Branch coverage   Berapa % percabangan (if/else) yang ditest   if x \> 0: harus test x\>0 DAN x\<=0   ≥ 80% critical layers

  Missing lines     Baris yang tidak pernah dieksekusi           \`if self.paper_mode:\` tidak ditest   Harus nol untuk critical paths
  --------------------------------------------------------------------------------------------------------------------------------------

**9. Panduan Menulis Test yang Baik**

**9.1 Prinsip AAA --- Arrange, Act, Assert**

+---------------------------------------------------------------------------------+
| def test_position_sizer_fixed_fractional(config, portfolio_state):              |
|                                                                                 |
| \# ── ARRANGE ─────────────────────────────────────────────                     |
|                                                                                 |
| \# Siapkan semua yang dibutuhkan sebelum aksi                                   |
|                                                                                 |
| sizer = PositionSizer(config)                                                   |
|                                                                                 |
| request = make_trade_request(symbol=\'BTCUSDT\', qty=999) \# qty diabaikan      |
|                                                                                 |
| equity = 10_000.0                                                               |
|                                                                                 |
| \# ── ACT ──────────────────────────────────────────────────                    |
|                                                                                 |
| \# Jalankan satu aksi yang ditest                                               |
|                                                                                 |
| qty, warnings, risk_usd = sizer.calculate(request, equity, portfolio_state)     |
|                                                                                 |
| \# ── ASSERT ───────────────────────────────────────────────                    |
|                                                                                 |
| \# Verifikasi hasilnya                                                          |
|                                                                                 |
| assert qty \> 0                                                                 |
|                                                                                 |
| assert risk_usd \<= equity \* config.risk_per_trade_pct \* 1.01 \# toleransi 1% |
|                                                                                 |
| assert len(warnings) == 0                                                       |
+---------------------------------------------------------------------------------+

**9.2 Satu Test --- Satu Hal**

  ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------
  **Buruk**                                                                     **Baik**                                                                   **Alasan**
  ----------------------------------------------------------------------------- -------------------------------------------------------------------------- ------------------------------------------------------
  test_risk_manager() yang test 10 hal sekaligus                                test_approve_normal(), test_block_zero_qty(), test_paper_mode_override()   Jika satu gagal, langsung tahu persis apa yang salah

  assert result.is_approved and result.qty \> 0 and len(result.warnings) == 0   Tiga assert terpisah dengan pesan error berbeda                            Satu assert gagal tidak menyembunyikan yang lain
  ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------

**9.3 Test Nama yang Mendeskripsikan Kegagalan**

+---------------------------------------------------------------------+
| \# BURUK --- tidak jelas apa yang ditest                            |
|                                                                     |
| def test_risk(): \...                                               |
|                                                                     |
| def test_case_1(): \...                                             |
|                                                                     |
| def test_circuit_breaker_2(): \...                                  |
|                                                                     |
| \# BAIK --- nama = kondisi + expected behavior                      |
|                                                                     |
| def test_circuit_breaker_halts_when_daily_loss_exceeds_5pct(): \... |
|                                                                     |
| def test_position_sizer_returns_zero_when_equity_is_zero(): \...    |
|                                                                     |
| def test_idempotency_blocks_duplicate_order_after_confirm(): \...   |
|                                                                     |
| \# PATTERN: test\_{komponen}\_{kondisi}\_{expected}                 |
|                                                                     |
| \# Saat test gagal, nama langsung menjelaskan apa yang rusak.       |
+---------------------------------------------------------------------+

**9.4 Hindari Test yang Bergantung pada Urutan**

+---------------------------------------------------------------------+
| \# BURUK --- test B bergantung pada state yang dibuat test A        |
|                                                                     |
| def test_a_open_trade(): \...                                       |
|                                                                     |
| def test_b_close_trade(): \... \# butuh trade yang dibuka di test_a |
|                                                                     |
| \# BAIK --- setiap test independent                                 |
|                                                                     |
| def test_open_trade(trade_store, mock_exchange): \...               |
|                                                                     |
| def test_close_trade(trade_store, mock_exchange):                   |
|                                                                     |
| \# Buat trade baru di sini, tidak bergantung test lain              |
|                                                                     |
| trade = make_trade(status=TradeStatus.OPEN, \...)                   |
|                                                                     |
| trade_store.save(trade)                                             |
|                                                                     |
| \...                                                                |
+---------------------------------------------------------------------+

**10. Checklist Implementasi Tests Layer**

**10.1 Checklist Setup**

  ------------------------------------------------------------------------------------------------------------------------------
  **No**   **Item**                                                 **Verifikasi**                                    **Done**
  -------- -------------------------------------------------------- ------------------------------------------------- ----------
  1        pytest, pytest-asyncio, pytest-cov, coverage terpasang   pip install pytest pytest-asyncio pytest-cov      ☐

  2        pytest.ini dikonfigurasi dengan asyncio_mode=auto        pytest \--co -q tidak ada error asyncio           ☐

  3        .coveragerc mengecualikan file yang tidak relevan        coverage html → htmlcov/index.html bisa dibuka    ☐

  4        conftest.py berisi semua shared fixture                  pytest \--fixtures \| grep base_config → ada      ☐

  5        MockBinanceExchange bisa dikonfigurasi per skenario      test mock: scenario=rate_limit → RateLimitError   ☐
  ------------------------------------------------------------------------------------------------------------------------------

**10.2 Checklist Coverage**

  ----------------------------------------------------------------------------------------------------------------------------------------------------
  **No**   **Item**                                               **Verifikasi**                                                            **Done**
  -------- ------------------------------------------------------ ------------------------------------------------------------------------- ----------
  6        risk_layer/ coverage 100%                              pytest \--cov=risk_layer \--cov-fail-under=100                            ☐

  7        trade_layer/idempotency.py coverage 100%               pytest test_idempotency.py \--cov=\...idempotency \--cov-fail-under=100   ☐

  8        exit_layer/ coverage 100% (SL/TP/trailing/breakeven)   pytest test_exit.py \--cov=exit_layer \--cov-fail-under=100               ☐

  9        utils/helpers.py coverage 95%+                         pytest test_helpers.py \--cov=utils/helpers                               ☐

  10       Semua \@pytest.mark.critical pass                      pytest -m critical -v → 0 failures                                        ☐
  ----------------------------------------------------------------------------------------------------------------------------------------------------

**10.3 Checklist Test Quality**

  -------------------------------------------------------------------------------------------------------------------------------------------------
  **No**   **Item**                                                         **Verifikasi**                                               **Done**
  -------- ---------------------------------------------------------------- ------------------------------------------------------------ ----------
  11       Setiap test independent --- tidak bergantung urutan              pytest \--randomly-seed=random -v → tetap lulus              ☐

  12       Tidak ada sleep() atau time.sleep() di unit test                 grep -rn \'sleep\' tests/unit/ → kosong (kecuali mock)       ☐

  13       Mock exchange tidak pernah buat koneksi nyata                    Jalankan test tanpa internet → semua lulus                   ☐

  14       Test idempotency mengtest concurrent race condition              test_concurrent_same_id_only_one_proceeds ada dan pass       ☐

  15       Recovery test verifikasi safe_mode dipanggil saat unresolved     test_recovery_enters_safe_mode_on_ghost_trade ada dan pass   ☐

  16       Integration test verifikasi paper mode tidak kirim ke exchange   test_paper_mode_does_not_send_real_order ada dan pass        ☐
  -------------------------------------------------------------------------------------------------------------------------------------------------

+:--------------------------------------------------------------------------------------------------------------------:+
| **Dokumen ini adalah panduan implementasi Tests Layer.**                                                             |
|                                                                                                                      |
| Setiap layer baru yang diimplementasikan WAJIB punya test yang menutup semua critical path sebelum dianggap selesai. |
|                                                                                                                      |
| *Referensi: semua dokumentasi layer • risk_layer skeleton code (sudah ada test_risk.py)*                             |
+----------------------------------------------------------------------------------------------------------------------+
