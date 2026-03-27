**crypto_ai_agent**

**Dokumentasi: Trade Layer**

**&**

**Execution Layer**

*trade · manager · store · audit_trail · trade_validator · idempotency · recovery*

*exchange · binance · spot_executor · futures_executor · order_splitter · smart_router · latency_tracker · execution_monitor*

Versi 1.0 \| Referensi: risk_layer_docs.docx · agent_core_docs.docx

  ------------------------------------------------

  ------------------------------------------------

**1. Overview --- Dua Layer, Satu Misi**

Trade Layer mengorkestrasi siklus hidup sebuah trade dari pembukaan sampai penutupan. Execution Layer mengeksekusi order ke exchange dengan aman, reliabel, dan idempoten. Keduanya bekerja bersama sebagai \'tangan\' sistem yang menyentuh uang sungguhan.

  -----------------------------------------------------------------------------------------------------------------
  **Aspek**        **Trade Layer**                                       **Execution Layer**
  ---------------- ----------------------------------------------------- ------------------------------------------
  Tanggung jawab   Orkestrasi: buka, update, tutup trade                 Eksekusi: kirim order ke exchange

  State            Stateful --- menyimpan semua trade ke DB              Minimal state --- cache retry per order

  I/O              DB read/write (state.db, experience.db)               Network --- REST API ke exchange

  Gagal →          Coba recovery, jangan tinggalkan posisi menggantung   Retry N kali lalu alert kritis

  Idempotency      Periksa order_registry sebelum buka trade             Gunakan client_order_id dari trade layer

  Dipanggil dari   main_loop setelah risk_layer approve                  trade_layer/manager.py
  -----------------------------------------------------------------------------------------------------------------

**1.1 Alur Trade Lengkap**

+---------------------------------------------------------+
| RiskResult (approved) dari risk_layer                   |
|                                                         |
| │                                                       |
|                                                         |
| ▼                                                       |
|                                                         |
| trade_layer/                                            |
|                                                         |
| ├── trade_validator.py ← validasi ulang sebelum buka    |
|                                                         |
| ├── idempotency.py ← cek duplikasi                      |
|                                                         |
| ├── manager.py ← buat Trade object, simpan ke DB        |
|                                                         |
| └── store.py ← persistensi ke state.db                  |
|                                                         |
| │                                                       |
|                                                         |
| ▼ Trade + OrderRequest                                  |
|                                                         |
| │                                                       |
|                                                         |
| execution_layer/                                        |
|                                                         |
| ├── smart_router.py ← pilih exchange terbaik            |
|                                                         |
| ├── order_splitter.py ← pecah order besar (opsional)    |
|                                                         |
| ├── spot_executor.py ← eksekusi spot order              |
|                                                         |
| ├── futures_executor.py ← eksekusi futures order        |
|                                                         |
| ├── latency_tracker.py ← catat latency                  |
|                                                         |
| └── execution_monitor.py ← pantau status setelah submit |
|                                                         |
| │                                                       |
|                                                         |
| ▼ OrderResponse                                         |
|                                                         |
| │                                                       |
|                                                         |
| trade_layer/manager.py ← konfirmasi fill, update state  |
|                                                         |
| trade_layer/audit_trail.py ← catat event ke audit log   |
|                                                         |
| │                                                       |
|                                                         |
| ▼                                                       |
|                                                         |
| exit_layer/ (untuk monitoring ongoing)                  |
+---------------------------------------------------------+

**BAGIAN A --- Trade Layer**

**2. trade.py --- Dataclass Inti**

Trade adalah objek yang merepresentasikan satu posisi dari pembukaan hingga penutupan. Setelah dibuat, field identitas (trade_id, client_order_id, symbol) tidak boleh diubah.

**2.1 Trade Dataclass Lengkap**

+----------------------------------------------------------------+
| from dataclasses import dataclass, field                       |
|                                                                |
| from datetime import datetime                                  |
|                                                                |
| from enum import Enum                                          |
|                                                                |
| class TradeStatus(str, Enum):                                  |
|                                                                |
| PENDING = \'pending\' \# dibuat, belum dikirim ke exchange     |
|                                                                |
| SUBMITTED = \'submitted\' \# dikirim, menunggu konfirmasi      |
|                                                                |
| OPEN = \'open\' \# terkonfirmasi, posisi aktif                 |
|                                                                |
| CLOSED = \'closed\' \# posisi ditutup                          |
|                                                                |
| CANCELLED = \'cancelled\' \# dibatalkan sebelum fill           |
|                                                                |
| FAILED = \'failed\' \# gagal total setelah retry               |
|                                                                |
| PARTIAL = \'partial\' \# partial fill, sisanya pending         |
|                                                                |
| \@dataclass                                                    |
|                                                                |
| class Trade:                                                   |
|                                                                |
| \# ── IMMUTABLE setelah dibuat ─────────────────────────────   |
|                                                                |
| trade_id: str \# UUID v4                                       |
|                                                                |
| client_order_id: str \# \'{strategy}\_{symbol}\_{ts_ms}\'      |
|                                                                |
| symbol: str                                                    |
|                                                                |
| side: str \# \'BUY\' \| \'SELL\'                               |
|                                                                |
| order_type: str \# \'market\' \| \'limit\' \| \'stop_market\'  |
|                                                                |
| strategy_id: str                                               |
|                                                                |
| mode: str \# \'paper\' \| \'shadow\' \| \'live\'               |
|                                                                |
| created_at: datetime                                           |
|                                                                |
| \# ── MUTABLE --- update seiring siklus hidup ──────────────── |
|                                                                |
| exchange_order_id: str = \'\'                                  |
|                                                                |
| status: TradeStatus = TradeStatus.PENDING                      |
|                                                                |
| \# Quantity & Price                                            |
|                                                                |
| requested_qty: float = 0.0                                     |
|                                                                |
| filled_qty: float = 0.0                                        |
|                                                                |
| avg_fill_price: float = 0.0                                    |
|                                                                |
| limit_price: float = 0.0 \# 0.0 = market                       |
|                                                                |
| \# Risk params                                                 |
|                                                                |
| sl_price: float = 0.0                                          |
|                                                                |
| tp_price: float = 0.0                                          |
|                                                                |
| risk_amount_usd: float = 0.0                                   |
|                                                                |
| leverage: int = 1                                              |
|                                                                |
| is_futures: bool = False                                       |
|                                                                |
| \# PnL (diisi saat close)                                      |
|                                                                |
| exit_price: float = 0.0                                        |
|                                                                |
| pnl_usd: float = 0.0                                           |
|                                                                |
| pnl_pct: float = 0.0                                           |
|                                                                |
| commission_usd: float = 0.0                                    |
|                                                                |
| exit_reason: str = \'\'                                        |
|                                                                |
| \# Timestamps                                                  |
|                                                                |
| submitted_at: datetime = None                                  |
|                                                                |
| opened_at: datetime = None                                     |
|                                                                |
| closed_at: datetime = None                                     |
|                                                                |
| updated_at: datetime = None                                    |
|                                                                |
| \# Metadata                                                    |
|                                                                |
| signal_confidence: float = 0.0                                 |
|                                                                |
| regime_at_entry: str = \'\'                                    |
|                                                                |
| vol_regime_entry: str = \'\'                                   |
|                                                                |
| metadata: dict = field(default_factory=dict)                   |
|                                                                |
| \# ── Computed properties ──────────────────────────────────   |
|                                                                |
| \@property                                                     |
|                                                                |
| def notional(self) -\> float:                                  |
|                                                                |
| return self.filled_qty \* self.avg_fill_price                  |
|                                                                |
| \@property                                                     |
|                                                                |
| def is_open(self) -\> bool:                                    |
|                                                                |
| return self.status in (TradeStatus.OPEN, TradeStatus.PARTIAL)  |
|                                                                |
| \@property                                                     |
|                                                                |
| def hold_duration_s(self) -\> float:                           |
|                                                                |
| if not self.opened_at: return 0.0                              |
|                                                                |
| end = self.closed_at or utcnow()                               |
|                                                                |
| return (end - self.opened_at).total_seconds()                  |
|                                                                |
| def to_order_request(self) -\> \'OrderRequest\':               |
|                                                                |
| \"\"\"Konversi ke OrderRequest untuk execution layer.\"\"\"    |
|                                                                |
| from execution_layer.exchange import OrderRequest              |
|                                                                |
| return OrderRequest(                                           |
|                                                                |
| symbol = self.symbol,                                          |
|                                                                |
| side = self.side,                                              |
|                                                                |
| order_type = self.order_type.upper(),                          |
|                                                                |
| quantity = self.requested_qty,                                 |
|                                                                |
| price = self.limit_price,                                      |
|                                                                |
| stop_price = 0.0,                                              |
|                                                                |
| client_order_id = self.client_order_id,                        |
|                                                                |
| is_futures = self.is_futures,                                  |
|                                                                |
| reduce_only = False,                                           |
|                                                                |
| )                                                              |
+----------------------------------------------------------------+

**2.2 Status Lifecycle**

  ------------------------------------------------------------------------------------------------------------------------------
  **Dari**        **Ke**          **Trigger**                                        **Siapa yang Ubah**
  --------------- --------------- -------------------------------------------------- -------------------------------------------
  **PENDING**     **SUBMITTED**   Order dikirim ke exchange                          manager.py setelah executor.place_order()

  **SUBMITTED**   **OPEN**        Exchange konfirmasi filled                         manager.py setelah OrderResponse

  **SUBMITTED**   **PARTIAL**     Partial fill diterima                              execution_monitor.py

  **SUBMITTED**   **CANCELLED**   Order di-cancel sebelum fill                       manager.py atau recovery.py

  **SUBMITTED**   **FAILED**      Max retry habis, tidak ada fill                    manager.py setelah retry exhausted

  **OPEN**        **CLOSED**      Exit order terkonfirmasi                           manager.py setelah close order filled

  **PARTIAL**     **OPEN**        Sisa order di-cancel, qty partial dianggap cukup   manager.py
  ------------------------------------------------------------------------------------------------------------------------------

**3. manager.py --- Orkestrasi Trade**

Koordinator utama trade layer. Mengelola seluruh siklus hidup trade dari open sampai close, termasuk update state, audit trail, dan komunikasi dengan execution layer.

**3.1 Interface Publik**

+-------------------------------------------------------------------------------+
| class TradeManager:                                                           |
|                                                                               |
| def \_\_init\_\_(                                                             |
|                                                                               |
| self,                                                                         |
|                                                                               |
| config: AgentConfig,                                                          |
|                                                                               |
| risk_mgr: RiskManager,                                                        |
|                                                                               |
| executor: \'ExecutionLayer\',                                                 |
|                                                                               |
| store: \'TradeStore\',                                                        |
|                                                                               |
| audit: \'AuditTrail\',                                                        |
|                                                                               |
| idempotency: \'IdempotencyManager\',                                          |
|                                                                               |
| ): \...                                                                       |
|                                                                               |
| async def open(                                                               |
|                                                                               |
| self,                                                                         |
|                                                                               |
| signal: Signal,                                                               |
|                                                                               |
| risk_result: RiskResult,                                                      |
|                                                                               |
| ) -\> Trade:                                                                  |
|                                                                               |
| \"\"\"                                                                        |
|                                                                               |
| Buka posisi baru. Urutan:                                                     |
|                                                                               |
| 1\. Validasi ulang (trade_validator)                                          |
|                                                                               |
| 2\. Buat Trade object dengan status PENDING                                   |
|                                                                               |
| 3\. Daftarkan ke idempotency registry                                         |
|                                                                               |
| 4\. Simpan ke store (state.db)                                                |
|                                                                               |
| 5\. Kirim ke execution layer                                                  |
|                                                                               |
| 6\. Update status berdasarkan response                                        |
|                                                                               |
| 7\. Catat ke audit trail                                                      |
|                                                                               |
| 8\. Publish TRADE_OPENED ke event_bus                                         |
|                                                                               |
| \"\"\"                                                                        |
|                                                                               |
| async def close(                                                              |
|                                                                               |
| self,                                                                         |
|                                                                               |
| trade: Trade,                                                                 |
|                                                                               |
| exit_decision: \'ExitDecision\',                                              |
|                                                                               |
| ) -\> Trade:                                                                  |
|                                                                               |
| \"\"\"                                                                        |
|                                                                               |
| Tutup posisi. Urutan:                                                         |
|                                                                               |
| 1\. Buat close OrderRequest (reduce_only=True untuk futures)                  |
|                                                                               |
| 2\. Kirim ke execution layer                                                  |
|                                                                               |
| 3\. Update Trade: status=CLOSED, pnl, exit_price                              |
|                                                                               |
| 4\. Pindahkan dari state.db ke experience.db                                  |
|                                                                               |
| 5\. Update performance.db                                                     |
|                                                                               |
| 6\. Update capital_manager & circuit_breaker                                  |
|                                                                               |
| 7\. Catat ke audit trail                                                      |
|                                                                               |
| 8\. Publish TRADE_CLOSED ke event_bus                                         |
|                                                                               |
| \"\"\"                                                                        |
|                                                                               |
| async def update_sl(                                                          |
|                                                                               |
| self,                                                                         |
|                                                                               |
| trade: Trade,                                                                 |
|                                                                               |
| new_sl: float,                                                                |
|                                                                               |
| reason: str,                                                                  |
|                                                                               |
| ) -\> bool:                                                                   |
|                                                                               |
| \"\"\"Update stop loss tanpa menutup posisi. Return True jika berhasil.\"\"\" |
|                                                                               |
| def get_open_trades(                                                          |
|                                                                               |
| self,                                                                         |
|                                                                               |
| symbol: str = None,                                                           |
|                                                                               |
| ) -\> list\[Trade\]:                                                          |
|                                                                               |
| \"\"\"Return semua trade dengan status OPEN atau PARTIAL.\"\"\"               |
|                                                                               |
| def confirm(                                                                  |
|                                                                               |
| self,                                                                         |
|                                                                               |
| trade: Trade,                                                                 |
|                                                                               |
| response: \'OrderResponse\',                                                  |
|                                                                               |
| ) -\> Trade:                                                                  |
|                                                                               |
| \"\"\"Update trade setelah menerima konfirmasi dari exchange.\"\"\"           |
|                                                                               |
| async def cancel(                                                             |
|                                                                               |
| self,                                                                         |
|                                                                               |
| trade: Trade,                                                                 |
|                                                                               |
| reason: str,                                                                  |
|                                                                               |
| ) -\> bool:                                                                   |
|                                                                               |
| \"\"\"Cancel order yang masih PENDING atau SUBMITTED.\"\"\"                   |
+-------------------------------------------------------------------------------+

**3.2 open() --- Implementasi Detail**

+----------------------------------------------------------------------------+
| async def open(self, signal: Signal, risk_result: RiskResult) -\> Trade:   |
|                                                                            |
| \# Step 1: Validasi ulang                                                  |
|                                                                            |
| ok, msg = self.\_validator.validate(signal, risk_result)                   |
|                                                                            |
| if not ok:                                                                 |
|                                                                            |
| raise TradeValidationError(msg)                                            |
|                                                                            |
| \# Step 2: Buat Trade object                                               |
|                                                                            |
| trade = Trade(                                                             |
|                                                                            |
| trade_id = str(uuid.uuid4()),                                              |
|                                                                            |
| client_order_id = generate_order_id(signal.strategy_id, signal.symbol),    |
|                                                                            |
| symbol = signal.symbol,                                                    |
|                                                                            |
| side = signal.side,                                                        |
|                                                                            |
| order_type = signal.signal_type,                                           |
|                                                                            |
| strategy_id = signal.strategy_id,                                          |
|                                                                            |
| mode = self.config.mode,                                                   |
|                                                                            |
| created_at = utcnow(),                                                     |
|                                                                            |
| requested_qty = risk_result.approved_quantity,                             |
|                                                                            |
| limit_price = signal.suggested_price,                                      |
|                                                                            |
| sl_price = risk_result.sl_price,                                           |
|                                                                            |
| tp_price = risk_result.tp_price,                                           |
|                                                                            |
| risk_amount_usd = risk_result.risk_amount_usd,                             |
|                                                                            |
| signal_confidence = signal.final_confidence,                               |
|                                                                            |
| regime_at_entry = signal.regime,                                           |
|                                                                            |
| vol_regime_entry = signal.vol_regime,                                      |
|                                                                            |
| )                                                                          |
|                                                                            |
| \# Step 3: Idempotency check & register                                    |
|                                                                            |
| idem_result = self.\_idempotency.check_or_register(trade.client_order_id)  |
|                                                                            |
| if idem_result == IdempotencyResult.DUPLICATE:                             |
|                                                                            |
| raise DuplicateOrderError(trade.client_order_id)                           |
|                                                                            |
| \# Step 4: Simpan ke state.db                                              |
|                                                                            |
| self.\_store.save(trade)                                                   |
|                                                                            |
| \# Step 5: Eksekusi (paper vs live berbeda)                                |
|                                                                            |
| if self.config.mode == \'paper\':                                          |
|                                                                            |
| response = self.\_simulate_fill(trade)                                     |
|                                                                            |
| else:                                                                      |
|                                                                            |
| response = await self.\_executor.place_order(trade.to_order_request())     |
|                                                                            |
| \# Step 6: Update status                                                   |
|                                                                            |
| trade = self.confirm(trade, response)                                      |
|                                                                            |
| \# Step 7: Audit trail                                                     |
|                                                                            |
| self.\_audit.record(trade, \'OPENED\', actor=\'manager\')                  |
|                                                                            |
| \# Step 8: Event bus                                                       |
|                                                                            |
| self.\_event_bus.publish(EventType.TRADE_OPENED, trade, \'trade_manager\') |
|                                                                            |
| log.info(\'Trade opened\', trade_id=trade.trade_id,                        |
|                                                                            |
| symbol=trade.symbol, side=trade.side,                                      |
|                                                                            |
| qty=trade.filled_qty, price=trade.avg_fill_price)                          |
|                                                                            |
| return trade                                                               |
+----------------------------------------------------------------------------+

**3.3 Paper Mode Fill Simulation**

+----------------------------------------------------------------+
| def \_simulate_fill(self, trade: Trade) -\> \'OrderResponse\': |
|                                                                |
| \"\"\"                                                         |
|                                                                |
| Simulasi fill untuk paper mode.                                |
|                                                                |
| Menggunakan harga terakhir dari portfolio_state.               |
|                                                                |
| Tidak ada slippage simulasi di sini --- gunakan backtest       |
|                                                                |
| untuk simulasi yang lebih akurat.                              |
|                                                                |
| \"\"\"                                                         |
|                                                                |
| last_price = self.\_portfolio_state.get(                       |
|                                                                |
| f\'last_price\_{trade.symbol}\', trade.limit_price or 50_000.0 |
|                                                                |
| )                                                              |
|                                                                |
| return OrderResponse(                                          |
|                                                                |
| exchange_order_id = f\'paper\_{trade.client_order_id}\',       |
|                                                                |
| client_order_id = trade.client_order_id,                       |
|                                                                |
| status = \'FILLED\',                                           |
|                                                                |
| filled_qty = trade.requested_qty,                              |
|                                                                |
| avg_price = last_price,                                        |
|                                                                |
| commission = last_price \* trade.requested_qty \* 0.001,       |
|                                                                |
| commission_asset = \'USDT\',                                   |
|                                                                |
| timestamp = utcnow(),                                          |
|                                                                |
| raw_response = {\'paper_mode\': True},                         |
|                                                                |
| )                                                              |
+----------------------------------------------------------------+

**4. store.py --- Persistensi Trade**

Satu-satunya komponen yang boleh read/write ke state.db dan experience.db. Semua akses database melalui TradeStore --- tidak ada raw SQL di luar file ini.

**4.1 Interface Publik**

+----------------------------------------------------------------------------+
| class TradeStore:                                                          |
|                                                                            |
| def save(self, trade: Trade) -\> None:                                     |
|                                                                            |
| \"\"\"INSERT atau UPDATE trade ke state.db.\"\"\"                          |
|                                                                            |
| def get(self, trade_id: str) -\> Trade \| None:                            |
|                                                                            |
| \"\"\"Ambil trade dari state.db by trade_id.\"\"\"                         |
|                                                                            |
| def get_by_order_id(self, client_order_id: str) -\> Trade \| None:         |
|                                                                            |
| \"\"\"Ambil trade by client_order_id (untuk idempotency recovery).\"\"\"   |
|                                                                            |
| def get_open_trades(self, symbol: str = None) -\> list\[Trade\]:           |
|                                                                            |
| \"\"\"Ambil semua trade OPEN/PARTIAL. Filter by symbol opsional.\"\"\"     |
|                                                                            |
| def get_all_active(self) -\> list\[Trade\]:                                |
|                                                                            |
| \"\"\"Semua trade yang bukan CLOSED/CANCELLED/FAILED.\"\"\"                |
|                                                                            |
| def update_status(                                                         |
|                                                                            |
| self,                                                                      |
|                                                                            |
| trade_id: str,                                                             |
|                                                                            |
| status: TradeStatus,                                                       |
|                                                                            |
| \*\*kwargs \# field tambahan yang diupdate bersamaan                       |
|                                                                            |
| ) -\> None:                                                                |
|                                                                            |
| \"\"\"Update status + field lain dalam satu transaksi.\"\"\"               |
|                                                                            |
| def archive(                                                               |
|                                                                            |
| self,                                                                      |
|                                                                            |
| trade: Trade,                                                              |
|                                                                            |
| ) -\> None:                                                                |
|                                                                            |
| \"\"\"                                                                     |
|                                                                            |
| Pindahkan trade CLOSED dari state.db ke experience.db.                     |
|                                                                            |
| Dipanggil oleh manager.close() setelah trade ditutup.                      |
|                                                                            |
| Gunakan transaksi DB: INSERT ke experience, DELETE dari state.             |
|                                                                            |
| Jika salah satu gagal, rollback keduanya.                                  |
|                                                                            |
| \"\"\"                                                                     |
|                                                                            |
| def count_open(self, symbol: str = None) -\> int:                          |
|                                                                            |
| \"\"\"Hitung jumlah posisi terbuka. Digunakan oleh exposure_control.\"\"\" |
+----------------------------------------------------------------------------+

**4.2 Aturan Transaksi DB**

  --------------------------------------------------------------------------------------------------------------------------
  **Operasi**                        **Transaksi?**   **Alasan**
  ---------------------------------- ---------------- ----------------------------------------------------------------------
  archive() --- move ke experience   Ya --- atomic    Jika INSERT berhasil tapi DELETE gagal, data ganda. Transaksi wajib.

  save() --- insert baru             Ya               Cegah partial write jika crash di tengah

  update_status()                    Ya               Update status + updated_at harus atomik

  get() / get_open_trades()          Tidak            Read-only, tidak perlu transaksi
  --------------------------------------------------------------------------------------------------------------------------

**5. audit_trail.py --- Log Immutable**

Mencatat setiap event yang terjadi pada sebuah trade. Audit trail adalah sumber kebenaran untuk investigasi dan compliance --- tidak pernah diedit atau dihapus.

**5.1 AuditEvent Dataclass**

+-----------------------------------------------------------------------+
| \@dataclass                                                           |
|                                                                       |
| class AuditEvent:                                                     |
|                                                                       |
| event_id: str \# UUID                                                 |
|                                                                       |
| trade_id: str                                                         |
|                                                                       |
| event_type: str \# lihat tabel di bawah                               |
|                                                                       |
| timestamp: datetime                                                   |
|                                                                       |
| actor: str \# \'manager\'\|\'exit_layer\'\|\'recovery\'\|\'manual\'   |
|                                                                       |
| equity_snapshot: float \# equity saat event terjadi                   |
|                                                                       |
| details: dict \# data spesifik per event type                         |
|                                                                       |
| checksum: str \# SHA-256 dari fields di atas (untuk tamper detection) |
+-----------------------------------------------------------------------+

**5.2 Event Types & Detail Format**

  ------------------------------------------------------------------------------------------------------------------
  **event_type**   **Kapan**                          **details fields wajib**
  ---------------- ---------------------------------- --------------------------------------------------------------
  CREATED          Trade object dibuat                symbol, side, requested_qty, sl_price, tp_price, strategy_id

  SUBMITTED        Order dikirim ke exchange          client_order_id, order_type, price, exchange

  FILLED           Konfirmasi fill dari exchange      exchange_order_id, filled_qty, avg_price, commission_usd

  PARTIAL_FILL     Partial fill diterima              filled_qty, remaining_qty, avg_price

  SL_UPDATED       SL diubah (trailing/breakeven)     old_sl, new_sl, reason

  TP_HIT           TP tercapai                        exit_price, pnl_usd, pnl_pct

  SL_HIT           SL tercapai                        exit_price, pnl_usd, pnl_pct

  STRATEGY_EXIT    Exit karena sinyal strategi        exit_price, reason, pnl_usd

  CLOSED           Trade ditutup (apapun alasannya)   exit_price, exit_reason, pnl_usd, pnl_pct, hold_candles

  CANCELLED        Order dibatalkan sebelum fill      reason, cancel_source

  FAILED           Trade gagal total                  reason, retry_count, last_error

  RECOVERED        State dipulihkan saat restart      recovery_source, old_status, new_status
  ------------------------------------------------------------------------------------------------------------------

  ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------
  **ATURAN IMMUTABILITY:** Tidak ada UPDATE atau DELETE pada tabel audit_events. Setiap koreksi ditambahkan sebagai event baru dengan event_type=\'CORRECTION\' yang mereferensikan event_id yang dikoreksi.

  ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------

**5.3 Interface Publik**

+---------------------------------------------------------------------------+
| class AuditTrail:                                                         |
|                                                                           |
| def record(                                                               |
|                                                                           |
| self,                                                                     |
|                                                                           |
| trade: Trade,                                                             |
|                                                                           |
| event_type: str,                                                          |
|                                                                           |
| actor: str,                                                               |
|                                                                           |
| details: dict = None,                                                     |
|                                                                           |
| equity: float = 0.0,                                                      |
|                                                                           |
| ) -\> AuditEvent:                                                         |
|                                                                           |
| \"\"\"                                                                    |
|                                                                           |
| Buat dan simpan AuditEvent.                                               |
|                                                                           |
| Checksum dihitung otomatis dari semua fields.                             |
|                                                                           |
| \"\"\"                                                                    |
|                                                                           |
| def get_history(self, trade_id: str) -\> list\[AuditEvent\]:              |
|                                                                           |
| \"\"\"Return semua events untuk satu trade, diurutkan by timestamp.\"\"\" |
|                                                                           |
| def verify_integrity(self, trade_id: str) -\> bool:                       |
|                                                                           |
| \"\"\"                                                                    |
|                                                                           |
| Verifikasi bahwa tidak ada event yang dimanipulasi.                       |
|                                                                           |
| Re-hitung checksum setiap event dan bandingkan.                           |
|                                                                           |
| \"\"\"                                                                    |
|                                                                           |
| def get_events_by_type(                                                   |
|                                                                           |
| self,                                                                     |
|                                                                           |
| event_type: str,                                                          |
|                                                                           |
| since: datetime = None,                                                   |
|                                                                           |
| ) -\> list\[AuditEvent\]:                                                 |
|                                                                           |
| \"\"\"Query events untuk reporting dan analytics.\"\"\"                   |
+---------------------------------------------------------------------------+

**6. idempotency.py --- Anti Double Order**

Mencegah order yang sama dikirim dua kali. Ini adalah komponen keamanan paling kritis di trade layer --- satu bug di sini bisa menghasilkan double position yang berlipat ganda kerugiannya.

**6.1 Cara Kerja**

> **1.** Sebelum order dikirim, hash client_order_id dan cek di order_registry.db.
>
> **2.** Jika record tidak ada → PROCEED: daftarkan dengan status=\'pending\', lanjut kirim.
>
> **3.** Jika record ada dengan status=\'pending\' → PENDING: mungkin sedang diproses, tunggu.
>
> **4.** Jika record ada dengan status=\'confirmed\' → DUPLICATE: jangan kirim ulang.
>
> **5.** Setelah konfirmasi fill dari exchange → update status ke \'confirmed\'.
>
> **6.** Jika order gagal total → update status ke \'failed\' (boleh retry dengan order ID baru).

**6.2 Interface Publik**

+-----------------------------------------------------------------------+
| class IdempotencyResult(str, Enum):                                   |
|                                                                       |
| PROCEED = \'proceed\' \# order baru, lanjutkan                        |
|                                                                       |
| DUPLICATE = \'duplicate\' \# sudah ada dan confirmed                  |
|                                                                       |
| PENDING = \'pending\' \# sedang diproses                              |
|                                                                       |
| class IdempotencyManager:                                             |
|                                                                       |
| def check_or_register(                                                |
|                                                                       |
| self,                                                                 |
|                                                                       |
| client_order_id: str,                                                 |
|                                                                       |
| ) -\> IdempotencyResult:                                              |
|                                                                       |
| \"\"\"                                                                |
|                                                                       |
| Atomic check dan register dalam satu transaksi DB.                    |
|                                                                       |
| Menggunakan INSERT OR IGNORE untuk mencegah race condition.           |
|                                                                       |
| \"\"\"                                                                |
|                                                                       |
| def confirm(                                                          |
|                                                                       |
| self,                                                                 |
|                                                                       |
| client_order_id: str,                                                 |
|                                                                       |
| exchange_order_id:str,                                                |
|                                                                       |
| ) -\> None:                                                           |
|                                                                       |
| \"\"\"Update status ke \'confirmed\' setelah exchange fill.\"\"\"     |
|                                                                       |
| def mark_failed(                                                      |
|                                                                       |
| self,                                                                 |
|                                                                       |
| client_order_id: str,                                                 |
|                                                                       |
| reason: str,                                                          |
|                                                                       |
| ) -\> None:                                                           |
|                                                                       |
| \"\"\"Mark sebagai failed --- boleh retry dengan order ID baru.\"\"\" |
|                                                                       |
| def get_status(                                                       |
|                                                                       |
| self,                                                                 |
|                                                                       |
| client_order_id: str,                                                 |
|                                                                       |
| ) -\> dict \| None:                                                   |
|                                                                       |
| \"\"\"Return record dari registry, None jika tidak ada.\"\"\"         |
|                                                                       |
| def cleanup_old(                                                      |
|                                                                       |
| self,                                                                 |
|                                                                       |
| older_than_days: int = 30,                                            |
|                                                                       |
| ) -\> int:                                                            |
|                                                                       |
| \"\"\"                                                                |
|                                                                       |
| Hapus record lama yang sudah confirmed/failed.                        |
|                                                                       |
| Dipanggil oleh scheduler db_vacuum task.                              |
|                                                                       |
| Return jumlah record yang dihapus.                                    |
|                                                                       |
| \"\"\"                                                                |
+-----------------------------------------------------------------------+

**6.3 Race Condition Prevention**

+-------------------------------------------------------------------------------------+
| \# SQL untuk atomic check-and-register:                                             |
|                                                                                     |
| \# INSERT OR IGNORE memastikan hanya satu record per client_order_id                |
|                                                                                     |
| INSERT OR IGNORE INTO order_registry (                                              |
|                                                                                     |
| client_order_id, status, strategy_id, symbol, created_at                            |
|                                                                                     |
| ) VALUES (?, \'pending\', ?, ?, ?)                                                  |
|                                                                                     |
| \# Setelah INSERT, SELECT untuk cek apakah kita yang insert:                        |
|                                                                                     |
| SELECT status FROM order_registry WHERE client_order_id = ?                         |
|                                                                                     |
| \# Jika status = \'pending\' DAN created_at = sekarang → kita yang insert → PROCEED |
|                                                                                     |
| \# Jika status = \'pending\' DAN created_at lama → orang lain → PENDING             |
|                                                                                     |
| \# Jika status = \'confirmed\' → DUPLICATE                                          |
|                                                                                     |
| \# Semua ini dalam satu koneksi DB dengan BEGIN IMMEDIATE transaction.              |
+-------------------------------------------------------------------------------------+

**7. recovery.py --- State Recovery Pasca Crash**

Dipanggil saat startup untuk memastikan internal state sinkron dengan kondisi exchange setelah crash, restart paksa, atau kehilangan koneksi panjang.

**7.1 RecoveryReport Dataclass**

+--------------------------------------------------------------+
| \@dataclass                                                  |
|                                                              |
| class RecoveryReport:                                        |
|                                                              |
| total_checked: int                                           |
|                                                              |
| resolved_count: int                                          |
|                                                              |
| unresolved_count: int                                        |
|                                                              |
| actions_taken: list\[str\] \# deskripsi setiap aksi recovery |
|                                                              |
| warnings: list\[str\]                                        |
|                                                              |
| success: bool \# True jika tidak ada unresolved              |
|                                                              |
| duration_s: float                                            |
|                                                              |
| timestamp: datetime                                          |
+--------------------------------------------------------------+

**7.2 Recovery Flow --- Detail**

+--------------------------------------------------------------------+
| class RecoveryManager:                                             |
|                                                                    |
| async def restore(self) -\> RecoveryReport:                        |
|                                                                    |
| \"\"\"                                                             |
|                                                                    |
| Dipanggil SEKALI saat startup (T-09 di main.py).                   |
|                                                                    |
| Harus selesai sebelum main loop mulai.                             |
|                                                                    |
| \"\"\"                                                             |
|                                                                    |
| start = utcnow()                                                   |
|                                                                    |
| actions = \[\]                                                     |
|                                                                    |
| warnings = \[\]                                                    |
|                                                                    |
| unresolved = \[\]                                                  |
|                                                                    |
| \# Step 1: Load semua trade non-closed dari state.db               |
|                                                                    |
| active_trades = self.\_store.get_all_active()                      |
|                                                                    |
| log.info(\'Recovery: found %d active trades\', len(active_trades)) |
|                                                                    |
| for trade in active_trades:                                        |
|                                                                    |
| \# Step 2: Query status dari exchange                              |
|                                                                    |
| try:                                                               |
|                                                                    |
| ex_status = await self.\_executor.get_order_status(                |
|                                                                    |
| trade.symbol, trade.client_order_id                                |
|                                                                    |
| )                                                                  |
|                                                                    |
| except OrderNotFoundError:                                         |
|                                                                    |
| \# Order tidak ada di exchange                                     |
|                                                                    |
| if trade.status == TradeStatus.PENDING:                            |
|                                                                    |
| \# Mungkin belum sempat dikirim --- cancel                         |
|                                                                    |
| self.\_store.update_status(trade.trade_id, TradeStatus.CANCELLED,  |
|                                                                    |
| updated_at=utcnow())                                               |
|                                                                    |
| actions.append(f\'PENDING→CANCELLED: {trade.trade_id}\')           |
|                                                                    |
| else:                                                              |
|                                                                    |
| \# SUBMITTED/OPEN tapi tidak ada di exchange --- aneh              |
|                                                                    |
| warnings.append(f\'Ghost trade: {trade.trade_id}\')                |
|                                                                    |
| unresolved.append(trade)                                           |
|                                                                    |
| continue                                                           |
|                                                                    |
| \# Step 3: Rekonsiliasi status                                     |
|                                                                    |
| if ex_status.status == \'FILLED\' and not trade.is_open:           |
|                                                                    |
| \# Trade sudah fill tapi DB belum update                           |
|                                                                    |
| self.\_store.update_status(                                        |
|                                                                    |
| trade.trade_id, TradeStatus.OPEN,                                  |
|                                                                    |
| filled_qty=ex_status.filled_qty,                                   |
|                                                                    |
| avg_fill_price=ex_status.avg_price,                                |
|                                                                    |
| exchange_order_id=ex_status.exchange_order_id,                     |
|                                                                    |
| opened_at=ex_status.timestamp,                                     |
|                                                                    |
| )                                                                  |
|                                                                    |
| actions.append(f\'UPDATED to OPEN: {trade.trade_id}\')             |
|                                                                    |
| elif ex_status.status == \'CANCELLED\':                            |
|                                                                    |
| self.\_store.update_status(                                        |
|                                                                    |
| trade.trade_id, TradeStatus.CANCELLED)                             |
|                                                                    |
| actions.append(f\'SYNCED CANCELLED: {trade.trade_id}\')            |
|                                                                    |
| \# Step 4: Pastikan setiap posisi OPEN punya SL di exchange        |
|                                                                    |
| open_trades = self.\_store.get_open_trades()                       |
|                                                                    |
| for trade in open_trades:                                          |
|                                                                    |
| await self.\_ensure_sl_exists(trade)                               |
|                                                                    |
| success = len(unresolved) == 0                                     |
|                                                                    |
| if not success:                                                    |
|                                                                    |
| log.error(\'Recovery: %d unresolved trades\', len(unresolved))     |
|                                                                    |
| \# Masuk safe_mode jika ada unresolved                             |
|                                                                    |
| await self.\_safe_mode.emergency_stop(\'Recovery failed\')         |
|                                                                    |
| return RecoveryReport(                                             |
|                                                                    |
| total_checked = len(active_trades),                                |
|                                                                    |
| resolved_count = len(active_trades) - len(unresolved),             |
|                                                                    |
| unresolved_count = len(unresolved),                                |
|                                                                    |
| actions_taken = actions,                                           |
|                                                                    |
| warnings = warnings,                                               |
|                                                                    |
| success = success,                                                 |
|                                                                    |
| duration_s = (utcnow()-start).total_seconds(),                     |
|                                                                    |
| timestamp = utcnow(),                                              |
|                                                                    |
| )                                                                  |
+--------------------------------------------------------------------+

**7.3 trade_validator.py --- Validasi Sebelum Open**

+-----------------------------------------------------------------------------------+
| class TradeValidator:                                                             |
|                                                                                   |
| def validate(                                                                     |
|                                                                                   |
| self,                                                                             |
|                                                                                   |
| signal: Signal,                                                                   |
|                                                                                   |
| risk_result: RiskResult,                                                          |
|                                                                                   |
| ) -\> tuple\[bool, str\]:                                                         |
|                                                                                   |
| \"\"\"                                                                            |
|                                                                                   |
| Validasi terakhir sebelum trade dibuat.                                           |
|                                                                                   |
| Cek konsistensi antara signal dan risk_result.                                    |
|                                                                                   |
| \"\"\"                                                                            |
|                                                                                   |
| \# Symbol harus sama                                                              |
|                                                                                   |
| if signal.symbol != risk_result.sl_price and risk_result.approved_quantity \<= 0: |
|                                                                                   |
| return False, \'approved_quantity harus \> 0\'                                    |
|                                                                                   |
| \# SL harus valid (tidak nol)                                                     |
|                                                                                   |
| if risk_result.sl_price \<= 0:                                                    |
|                                                                                   |
| return False, \'sl_price dari risk_result tidak valid\'                           |
|                                                                                   |
| \# Confidence masih di atas threshold                                             |
|                                                                                   |
| if signal.final_confidence \< self.config.min_signal_confidence:                  |
|                                                                                   |
| return False, (                                                                   |
|                                                                                   |
| f\'Confidence {signal.final_confidence:.2f} di bawah minimum\'                    |
|                                                                                   |
| f\' {self.config.min_signal_confidence:.2f}\'                                     |
|                                                                                   |
| )                                                                                 |
|                                                                                   |
| \# Mode consistency                                                               |
|                                                                                   |
| if self.config.mode == \'live\' and signal.mode == \'paper\':                     |
|                                                                                   |
| return False, \'Mode mismatch: live config tapi paper signal\'                    |
|                                                                                   |
| return True, \'\'                                                                 |
+-----------------------------------------------------------------------------------+

**BAGIAN B --- Execution Layer**

**8. exchange.py --- Abstract Base**

Mendefinisikan kontrak yang harus dipenuhi semua exchange connector. Memungkinkan smart_router untuk bekerja dengan multiple exchange tanpa tahu implementasi spesifik masing-masing.

**8.1 OrderRequest & OrderResponse**

+-------------------------------------------------------------------+
| \@dataclass                                                       |
|                                                                   |
| class OrderRequest:                                               |
|                                                                   |
| symbol: str                                                       |
|                                                                   |
| side: str \# \'BUY\' \| \'SELL\'                                  |
|                                                                   |
| order_type: str \# \'MARKET\' \| \'LIMIT\' \| \'STOP_MARKET\'     |
|                                                                   |
| quantity: float \# sudah di-round ke step_size                    |
|                                                                   |
| price: float \# 0.0 untuk market                                  |
|                                                                   |
| stop_price: float = 0.0 \# untuk STOP_MARKET                      |
|                                                                   |
| client_order_id: str = \'\'                                       |
|                                                                   |
| is_futures: bool = False                                          |
|                                                                   |
| reduce_only: bool = False \# True untuk close position            |
|                                                                   |
| time_in_force: str = \'GTC\' \# GTC \| IOC \| FOK                 |
|                                                                   |
| leverage: int = 1                                                 |
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
| commission_asset: str                                             |
|                                                                   |
| timestamp: datetime                                               |
|                                                                   |
| raw_response: dict                                                |
|                                                                   |
| \@property                                                        |
|                                                                   |
| def is_filled(self) -\> bool:                                     |
|                                                                   |
| return self.status in (\'FILLED\', \'PARTIALLY_FILLED\')          |
|                                                                   |
| \@property                                                        |
|                                                                   |
| def is_rejected(self) -\> bool:                                   |
|                                                                   |
| return self.status == \'REJECTED\'                                |
+-------------------------------------------------------------------+

**8.2 BaseExchange Abstract Class**

+---------------------------------------------------------------+
| from abc import ABC, abstractmethod                           |
|                                                               |
| class BaseExchange(ABC):                                      |
|                                                               |
| \@abstractmethod                                              |
|                                                               |
| async def place_order(                                        |
|                                                               |
| self, order: OrderRequest                                     |
|                                                               |
| ) -\> OrderResponse: \...                                     |
|                                                               |
| \@abstractmethod                                              |
|                                                               |
| async def cancel_order(                                       |
|                                                               |
| self, symbol: str, client_order_id: str                       |
|                                                               |
| ) -\> bool: \...                                              |
|                                                               |
| \@abstractmethod                                              |
|                                                               |
| async def get_order_status(                                   |
|                                                               |
| self, symbol: str, client_order_id: str                       |
|                                                               |
| ) -\> OrderResponse: \...                                     |
|                                                               |
| \@abstractmethod                                              |
|                                                               |
| async def get_balance(self, asset: str) -\> float: \...       |
|                                                               |
| \@abstractmethod                                              |
|                                                               |
| async def get_position(                                       |
|                                                               |
| self, symbol: str                                             |
|                                                               |
| ) -\> \'Position \| None\': \...                              |
|                                                               |
| \@abstractmethod                                              |
|                                                               |
| async def set_leverage(                                       |
|                                                               |
| self, symbol: str, leverage: int                              |
|                                                               |
| ) -\> bool: \...                                              |
|                                                               |
| \@abstractmethod                                              |
|                                                               |
| async def get_open_orders(                                    |
|                                                               |
| self, symbol: str = None                                      |
|                                                               |
| ) -\> list\[OrderResponse\]: \...                             |
|                                                               |
| \@abstractmethod                                              |
|                                                               |
| def get_lot_filter(self, symbol: str) -\> \'LotFilter\': \... |
|                                                               |
| \@abstractmethod                                              |
|                                                               |
| def get_exchange_name(self) -\> str: \...                     |
|                                                               |
| \# ── Shared utility (tidak abstract) ─────────────────────   |
|                                                               |
| def round_quantity(self, qty: float, symbol: str) -\> float:  |
|                                                               |
| lf = self.get_lot_filter(symbol)                              |
|                                                               |
| step = lf.step_size                                           |
|                                                               |
| import math                                                   |
|                                                               |
| return round(math.floor(qty / step) \* step,                  |
|                                                               |
| max(0, round(-math.log10(step))))                             |
|                                                               |
| def round_price(self, price: float, symbol: str) -\> float:   |
|                                                               |
| lf = self.get_lot_filter(symbol)                              |
|                                                               |
| tick = lf.tick_size                                           |
|                                                               |
| import math                                                   |
|                                                               |
| return round(round(price / tick) \* tick,                     |
|                                                               |
| max(0, round(-math.log10(tick))))                             |
+---------------------------------------------------------------+

**9. exchanges/binance.py --- Implementasi Binance**

Implementasi konkret BaseExchange untuk Binance Spot dan Futures (USDT-M). Semua detail API Binance dikapsulasi di sini --- layer lain tidak perlu tahu format request Binance.

**9.1 Konstruktor & Inisialisasi**

+----------------------------------------------------------------------+
| class BinanceExchange(BaseExchange):                                 |
|                                                                      |
| BASE_URL_SPOT = \'https://api.binance.com\'                          |
|                                                                      |
| BASE_URL_FUTURES = \'https://fapi.binance.com\'                      |
|                                                                      |
| BASE_URL_TESTNET = \'https://testnet.binancefutures.com\'            |
|                                                                      |
| def \_\_init\_\_(                                                    |
|                                                                      |
| self,                                                                |
|                                                                      |
| api_key: str,                                                        |
|                                                                      |
| api_secret: str,                                                     |
|                                                                      |
| testnet: bool = False,                                               |
|                                                                      |
| rate_limiter: \'BinanceRateLimiter\' = None,                         |
|                                                                      |
| ):                                                                   |
|                                                                      |
| self.\_key = api_key                                                 |
|                                                                      |
| self.\_secret = api_secret                                           |
|                                                                      |
| self.\_testnet = testnet                                             |
|                                                                      |
| self.\_rate_lim = rate_limiter                                       |
|                                                                      |
| self.\_session = None \# aiohttp.ClientSession, dibuat di connect()  |
|                                                                      |
| self.\_lot_cache = {} \# cache LotFilter per symbol                  |
|                                                                      |
| async def connect(self) -\> None:                                    |
|                                                                      |
| \"\"\"Buat session dan fetch exchange info untuk semua simbol.\"\"\" |
|                                                                      |
| import aiohttp                                                       |
|                                                                      |
| self.\_session = aiohttp.ClientSession()                             |
|                                                                      |
| await self.\_load_exchange_info()                                    |
|                                                                      |
| async def disconnect(self) -\> None:                                 |
|                                                                      |
| if self.\_session:                                                   |
|                                                                      |
| await self.\_session.close()                                         |
+----------------------------------------------------------------------+

**9.2 place_order() --- Implementasi**

+---------------------------------------------------------------------------+
| async def place_order(self, order: OrderRequest) -\> OrderResponse:       |
|                                                                           |
| \# Pilih base URL                                                         |
|                                                                           |
| base = self.BASE_URL_TESTNET if self.\_testnet else (                     |
|                                                                           |
| self.BASE_URL_FUTURES if order.is_futures                                 |
|                                                                           |
| else self.BASE_URL_SPOT                                                   |
|                                                                           |
| )                                                                         |
|                                                                           |
| endpoint = \'/fapi/v1/order\' if order.is_futures else \'/api/v3/order\'  |
|                                                                           |
| \# Rate limit check                                                       |
|                                                                           |
| if self.\_rate_lim:                                                       |
|                                                                           |
| await self.\_rate_lim.acquire(endpoint, is_order=True)                    |
|                                                                           |
| \# Build params                                                           |
|                                                                           |
| params = {                                                                |
|                                                                           |
| \'symbol\': order.symbol,                                                 |
|                                                                           |
| \'side\': order.side,                                                     |
|                                                                           |
| \'type\': order.order_type,                                               |
|                                                                           |
| \'quantity\': str(order.quantity),                                        |
|                                                                           |
| \'newClientOrderId\': order.client_order_id,                              |
|                                                                           |
| \'newOrderRespType\': \'FULL\',                                           |
|                                                                           |
| }                                                                         |
|                                                                           |
| if order.order_type == \'LIMIT\':                                         |
|                                                                           |
| params\[\'price\'\] = str(order.price)                                    |
|                                                                           |
| params\[\'timeInForce\'\] = order.time_in_force                           |
|                                                                           |
| if order.order_type == \'STOP_MARKET\':                                   |
|                                                                           |
| params\[\'stopPrice\'\] = str(order.stop_price)                           |
|                                                                           |
| if order.is_futures and order.reduce_only:                                |
|                                                                           |
| params\[\'reduceOnly\'\] = \'true\'                                       |
|                                                                           |
| \# Sign dan kirim                                                         |
|                                                                           |
| response = await self.\_signed_request(\'POST\', base + endpoint, params) |
|                                                                           |
| \# Update rate limiter dari headers                                       |
|                                                                           |
| if self.\_rate_lim:                                                       |
|                                                                           |
| self.\_rate_lim.update_from_headers(response.headers)                     |
|                                                                           |
| return self.\_parse_order_response(await response.json())                 |
+---------------------------------------------------------------------------+

**9.3 Error Mapping --- Binance Error Code**

  ------------------------------------------------------------------------------------------------------
  **Binance Error Code**   **Deskripsi**                    **Aksi Sistem**
  ------------------------ -------------------------------- --------------------------------------------
  -1013                    Invalid quantity                 BLOCKED --- cek lot filter

  -1021                    Timestamp out of sync            Sync server time, retry sekali

  -2010                    Insufficient balance             BLOCKED --- alert, jangan retry

  -2011                    Order not found (cancel)         Warning --- mungkin sudah ter-fill

  -1100                    Illegal characters in param      Bug --- log kritis, alert dev

  -1003                    Too many requests (rate limit)   Backoff sesuai Retry-After header

  HTTP 429                 Rate limit exceeded              Tunggu window reset

  HTTP 418                 IP banned                        Alert KRITIS --- butuh manual intervention
  ------------------------------------------------------------------------------------------------------

**10. spot_executor.py & futures_executor.py**

Executor spesifik untuk spot dan futures. Wrapper tipis di atas BinanceExchange yang menambahkan logic khas masing-masing market type.

**10.1 SpotExecutor**

+-------------------------------------------------------------------------------+
| class SpotExecutor:                                                           |
|                                                                               |
| def \_\_init\_\_(self, exchange: BaseExchange, config: AgentConfig):          |
|                                                                               |
| self.\_exchange = exchange                                                    |
|                                                                               |
| self.\_config = config                                                        |
|                                                                               |
| async def place_order(self, order: OrderRequest) -\> OrderResponse:           |
|                                                                               |
| \"\"\"                                                                        |
|                                                                               |
| Pra-proses khusus spot:                                                       |
|                                                                               |
| 1\. Pastikan quantity valid (lot filter)                                      |
|                                                                               |
| 2\. Pastikan USDT balance cukup                                               |
|                                                                               |
| 3\. Pastikan is_futures = False                                               |
|                                                                               |
| 4\. Delegasikan ke exchange.place_order()                                     |
|                                                                               |
| \"\"\"                                                                        |
|                                                                               |
| order.is_futures = False                                                      |
|                                                                               |
| order.quantity = self.\_exchange.round_quantity(order.quantity, order.symbol) |
|                                                                               |
| \# Check balance                                                              |
|                                                                               |
| balance = await self.\_exchange.get_balance(\'USDT\')                         |
|                                                                               |
| notional = order.quantity \* (order.price or                                  |
|                                                                               |
| await self.\_get_last_price(order.symbol))                                    |
|                                                                               |
| if balance \< notional \* 1.002: \# 0.2% buffer untuk commission              |
|                                                                               |
| raise InsufficientBalanceError(                                               |
|                                                                               |
| f\'Balance {balance:.2f} \< notional {notional:.2f}\')                        |
|                                                                               |
| return await self.\_exchange.place_order(order)                               |
|                                                                               |
| async def close_position(                                                     |
|                                                                               |
| self,                                                                         |
|                                                                               |
| symbol: str,                                                                  |
|                                                                               |
| qty: float,                                                                   |
|                                                                               |
| urgency: str = \'normal\',                                                    |
|                                                                               |
| ) -\> OrderResponse:                                                          |
|                                                                               |
| \"\"\"                                                                        |
|                                                                               |
| Tutup posisi spot dengan SELL market order.                                   |
|                                                                               |
| urgency=\'urgent\' → IOC order (immediate or cancel).                         |
|                                                                               |
| \"\"\"                                                                        |
|                                                                               |
| order = OrderRequest(                                                         |
|                                                                               |
| symbol = symbol,                                                              |
|                                                                               |
| side = \'SELL\',                                                              |
|                                                                               |
| order_type = \'MARKET\',                                                      |
|                                                                               |
| quantity = self.\_exchange.round_quantity(qty, symbol),                       |
|                                                                               |
| client_order_id = generate_order_id(\'close\', symbol),                       |
|                                                                               |
| time_in_force = \'IOC\' if urgency == \'urgent\' else \'GTC\',                |
|                                                                               |
| )                                                                             |
|                                                                               |
| return await self.\_exchange.place_order(order)                               |
+-------------------------------------------------------------------------------+

**10.2 FuturesExecutor --- Perbedaan dari Spot**

  ---------------------------------------------------------------------------------------------------
  **Aspek**             **SpotExecutor**        **FuturesExecutor**
  --------------------- ----------------------- -----------------------------------------------------
  Balance check         Cek USDT free balance   Cek available margin (USDT free / leverage)

  Set leverage          Tidak ada               set_leverage() sebelum place_order() jika perlu

  Close position        SELL dengan qty penuh   SELL dengan reduce_only=True

  Margin type check     Tidak relevan           Pastikan margin type = ISOLATED sebelum order

  Position mode check   Tidak relevan           Cek One-way vs Hedge mode dari exchange info

  Funding rate impact   Tidak ada               Log funding rate saat open dan close untuk tracking
  ---------------------------------------------------------------------------------------------------

**10.3 Set Leverage Sebelum Order (Futures)**

+----------------------------------------------------------------------------+
| async def \_ensure_leverage(                                               |
|                                                                            |
| self,                                                                      |
|                                                                            |
| symbol: str,                                                               |
|                                                                            |
| leverage: int,                                                             |
|                                                                            |
| ) -\> None:                                                                |
|                                                                            |
| \"\"\"                                                                     |
|                                                                            |
| Binance Futures: leverage harus di-set per simbol.                         |
|                                                                            |
| Jika leverage sama dengan yang sudah di-set → skip (hemat API call).       |
|                                                                            |
| \"\"\"                                                                     |
|                                                                            |
| current = self.\_leverage_cache.get(symbol, 0)                             |
|                                                                            |
| if current == leverage:                                                    |
|                                                                            |
| return                                                                     |
|                                                                            |
| ok = await self.\_exchange.set_leverage(symbol, leverage)                  |
|                                                                            |
| if ok:                                                                     |
|                                                                            |
| self.\_leverage_cache\[symbol\] = leverage                                 |
|                                                                            |
| log.info(\'Leverage set\', symbol=symbol, leverage=leverage)               |
|                                                                            |
| else:                                                                      |
|                                                                            |
| raise LeverageSetError(f\'Gagal set leverage {leverage}x untuk {symbol}\') |
+----------------------------------------------------------------------------+

**11. order_splitter.py --- Pemecahan Order Besar**

Memecah order besar menjadi beberapa sub-order kecil untuk mengurangi market impact. Digunakan saat notional order \> threshold persentase dari ADV (Average Daily Volume).

**11.1 Kapan Order Dipecah**

  ---------------------------------------------------------------------------------------------------------------------
  **Kondisi**                                 **Aksi**                                       **Config Key**
  ------------------------------------------- ---------------------------------------------- --------------------------
  notional \> ADV × SPLIT_ADV_THRESHOLD       Pecah menjadi sub-order menggunakan TWAP       SPLIT_ADV_THRESHOLD=0.01

  qty \> exchange max_qty                     Wajib dipecah oleh exchange --- selalu split   ---

  Urgency = \'urgent\' (exit karena SL hit)   Jangan split --- kirim satu market order       SPLIT_ON_URGENT=False
  ---------------------------------------------------------------------------------------------------------------------

**11.2 TWAP Splitter**

+-----------------------------------------------------------------------+
| class TWAPSplitter:                                                   |
|                                                                       |
| \"\"\"Time-Weighted Average Price order splitting.\"\"\"              |
|                                                                       |
| def split(                                                            |
|                                                                       |
| self,                                                                 |
|                                                                       |
| order: OrderRequest,                                                  |
|                                                                       |
| total_slices: int = 5,                                                |
|                                                                       |
| interval_s: int = 60,                                                 |
|                                                                       |
| ) -\> list\[OrderRequest\]:                                           |
|                                                                       |
| \"\"\"                                                                |
|                                                                       |
| Pecah order menjadi N slice yang dikirim setiap interval_s detik.     |
|                                                                       |
| Contoh: 0.1 BTC → 5 slice × 0.02 BTC, setiap 60 detik.                |
|                                                                       |
| Setiap slice punya client_order_id unik:                              |
|                                                                       |
| \'{original_id}\_twap\_{i}\'                                          |
|                                                                       |
| \"\"\"                                                                |
|                                                                       |
| base_qty = order.quantity / total_slices                              |
|                                                                       |
| remainder = order.quantity - (base_qty \* total_slices)               |
|                                                                       |
| slices = \[\]                                                         |
|                                                                       |
| for i in range(total_slices):                                         |
|                                                                       |
| qty = base_qty + (remainder if i == total_slices - 1 else 0)          |
|                                                                       |
| slice_order = OrderRequest(                                           |
|                                                                       |
| \*\*{k: v for k, v in vars(order).items()},                           |
|                                                                       |
| )                                                                     |
|                                                                       |
| slice_order.quantity = self.\_exchange.round_quantity(                |
|                                                                       |
| qty, order.symbol)                                                    |
|                                                                       |
| slice_order.client_order_id = f\'{order.client_order_id}\_twap\_{i}\' |
|                                                                       |
| slices.append(slice_order)                                            |
|                                                                       |
| return slices                                                         |
|                                                                       |
| async def execute(                                                    |
|                                                                       |
| self,                                                                 |
|                                                                       |
| slices: list\[OrderRequest\],                                         |
|                                                                       |
| executor: BaseExchange,                                               |
|                                                                       |
| interval_s: int,                                                      |
|                                                                       |
| ) -\> list\[OrderResponse\]:                                          |
|                                                                       |
| \"\"\"Kirim slice secara berurutan dengan delay interval_s.\"\"\"     |
|                                                                       |
| responses = \[\]                                                      |
|                                                                       |
| for i, slice_order in enumerate(slices):                              |
|                                                                       |
| if i \> 0:                                                            |
|                                                                       |
| await asyncio.sleep(interval_s)                                       |
|                                                                       |
| resp = await executor.place_order(slice_order)                        |
|                                                                       |
| responses.append(resp)                                                |
|                                                                       |
| log.info(\'TWAP slice sent\', slice=i+1, total=len(slices),           |
|                                                                       |
| qty=resp.filled_qty, price=resp.avg_price)                            |
|                                                                       |
| return responses                                                      |
+-----------------------------------------------------------------------+

**12. smart_router.py --- Routing Antar Exchange**

Memilih exchange terbaik untuk setiap order berdasarkan spread, likuiditas, dan availability. Untuk MVP dengan satu exchange, router selalu memilih Binance tapi tetap bisa fallback.

**12.1 Routing Strategies**

  ------------------------------------------------------------------------------------------------------------------
  **Strategy**       **Kapan Digunakan**          **Cara Kerja**
  ------------------ ---------------------------- ------------------------------------------------------------------
  **primary_only**   Default --- satu exchange    Selalu gunakan exchange utama (Binance). Paling simple.

  **best_price**     Multi-exchange aktif         Bandingkan ask/bid di semua exchange, pilih harga terbaik.

  **failover**       Primary down/rate limited    Coba exchange primary, jika 429/503 → failover ke secondary.

  **load_balance**   Rate limit mendekati batas   Distribusi order ke exchange yang rate limit-nya paling longgar.
  ------------------------------------------------------------------------------------------------------------------

**12.2 Interface Publik**

+---------------------------------------------------------------------------------+
| class SmartRouter:                                                              |
|                                                                                 |
| def \_\_init\_\_(                                                               |
|                                                                                 |
| self,                                                                           |
|                                                                                 |
| exchanges: dict\[str, BaseExchange\], \# {\'binance\': BinanceExchange(), \...} |
|                                                                                 |
| strategy: str = \'primary_only\',                                               |
|                                                                                 |
| primary: str = \'binance\',                                                     |
|                                                                                 |
| ):                                                                              |
|                                                                                 |
| self.\_exchanges = exchanges                                                    |
|                                                                                 |
| self.\_strategy = strategy                                                      |
|                                                                                 |
| self.\_primary = primary                                                        |
|                                                                                 |
| async def route(                                                                |
|                                                                                 |
| self,                                                                           |
|                                                                                 |
| order: OrderRequest,                                                            |
|                                                                                 |
| ) -\> tuple\[BaseExchange, str\]:                                               |
|                                                                                 |
| \"\"\"                                                                          |
|                                                                                 |
| Return (exchange_to_use, exchange_name).                                        |
|                                                                                 |
| Dipanggil oleh spot_executor dan futures_executor                               |
|                                                                                 |
| sebelum place_order().                                                          |
|                                                                                 |
| \"\"\"                                                                          |
|                                                                                 |
| if self.\_strategy == \'primary_only\':                                         |
|                                                                                 |
| return self.\_exchanges\[self.\_primary\], self.\_primary                       |
|                                                                                 |
| if self.\_strategy == \'best_price\':                                           |
|                                                                                 |
| return await self.\_find_best_price(order)                                      |
|                                                                                 |
| if self.\_strategy == \'failover\':                                             |
|                                                                                 |
| return await self.\_failover_route(order)                                       |
|                                                                                 |
| return self.\_exchanges\[self.\_primary\], self.\_primary                       |
|                                                                                 |
| async def \_find_best_price(                                                    |
|                                                                                 |
| self, order: OrderRequest                                                       |
|                                                                                 |
| ) -\> tuple\[BaseExchange, str\]:                                               |
|                                                                                 |
| \"\"\"                                                                          |
|                                                                                 |
| Fetch best ask/bid dari semua exchange paralel.                                 |
|                                                                                 |
| Pilih exchange dengan harga terbaik untuk sisi order.                           |
|                                                                                 |
| BUY → pilih exchange dengan ask terendah.                                       |
|                                                                                 |
| SELL → pilih exchange dengan bid tertinggi.                                     |
|                                                                                 |
| Jika selisih \< 0.05% → pakai primary (consistency).                            |
|                                                                                 |
| \"\"\"                                                                          |
|                                                                                 |
| \...                                                                            |
|                                                                                 |
| def get_fallback_order(                                                         |
|                                                                                 |
| self, failed_exchange: str                                                      |
|                                                                                 |
| ) -\> str \| None:                                                              |
|                                                                                 |
| \"\"\"Return nama exchange fallback, None jika tidak ada.\"\"\"                 |
|                                                                                 |
| \...                                                                            |
+---------------------------------------------------------------------------------+

**13. latency_tracker.py & execution_monitor.py**

**13.1 latency_tracker.py**

Mencatat dan menganalisis latency setiap interaksi dengan exchange. Digunakan oleh latency_guard di intelligence_layer untuk keputusan blokir order.

+---------------------------------------------------------------------------+
| \@dataclass                                                               |
|                                                                           |
| class LatencyRecord:                                                      |
|                                                                           |
| endpoint: str                                                             |
|                                                                           |
| latency_ms: float                                                         |
|                                                                           |
| timestamp: datetime                                                       |
|                                                                           |
| exchange: str                                                             |
|                                                                           |
| success: bool                                                             |
|                                                                           |
| class LatencyTracker:                                                     |
|                                                                           |
| WINDOW_SIZE = 50 \# track 50 request terakhir                             |
|                                                                           |
| def record(                                                               |
|                                                                           |
| self,                                                                     |
|                                                                           |
| endpoint: str,                                                            |
|                                                                           |
| latency_ms: float,                                                        |
|                                                                           |
| exchange: str = \'binance\',                                              |
|                                                                           |
| success: bool = True,                                                     |
|                                                                           |
| ) -\> None:                                                               |
|                                                                           |
| \"\"\"Catat latency. Digunakan sebagai decorator di setiap request.\"\"\" |
|                                                                           |
| def get_stats(                                                            |
|                                                                           |
| self,                                                                     |
|                                                                           |
| endpoint: str = None,                                                     |
|                                                                           |
| exchange: str = None,                                                     |
|                                                                           |
| ) -\> LatencyStats:                                                       |
|                                                                           |
| \"\"\"                                                                    |
|                                                                           |
| Return statistik P50, P95, P99 dari window terakhir.                      |
|                                                                           |
| Filter by endpoint atau exchange opsional.                                |
|                                                                           |
| \"\"\"                                                                    |
|                                                                           |
| def is_acceptable(self, threshold_ms: float = 2000.0) -\> bool:           |
|                                                                           |
| \"\"\"                                                                    |
|                                                                           |
| True jika P95 latency \< threshold.                                       |
|                                                                           |
| Dipanggil oleh latency_guard.should_block_order().                        |
|                                                                           |
| \"\"\"                                                                    |
+---------------------------------------------------------------------------+

**13.2 execution_monitor.py --- Monitor Post-Submit**

Memantau status order setelah dikirim. Mendeteksi order yang stuck dalam status SUBMITTED terlalu lama tanpa update.

+----------------------------------------------------------------------+
| class ExecutionMonitor:                                              |
|                                                                      |
| PENDING_TIMEOUT_S = 30 \# SUBMITTED \> 30s tanpa update → cek manual |
|                                                                      |
| PARTIAL_TIMEOUT_S = 300 \# PARTIAL \> 5 menit → cancel sisa          |
|                                                                      |
| async def monitor_loop(self) -\> None:                               |
|                                                                      |
| \"\"\"                                                               |
|                                                                      |
| Background loop yang jalan setiap 10 detik.                          |
|                                                                      |
| Cek semua trade SUBMITTED dan PARTIAL.                               |
|                                                                      |
| \"\"\"                                                               |
|                                                                      |
| while True:                                                          |
|                                                                      |
| await asyncio.sleep(10)                                              |
|                                                                      |
| await self.\_check_submitted_trades()                                |
|                                                                      |
| await self.\_check_partial_trades()                                  |
|                                                                      |
| async def \_check_submitted_trades(self) -\> None:                   |
|                                                                      |
| \"\"\"                                                               |
|                                                                      |
| Untuk setiap SUBMITTED trade yang sudah \> PENDING_TIMEOUT_S:        |
|                                                                      |
| 1\. Query exchange untuk status terbaru                              |
|                                                                      |
| 2\. Update trade sesuai status exchange                              |
|                                                                      |
| 3\. Jika tidak ada respons → retry query                             |
|                                                                      |
| 4\. Jika max retry habis → mark FAILED + alert                       |
|                                                                      |
| \"\"\"                                                               |
|                                                                      |
| async def \_check_partial_trades(self) -\> None:                     |
|                                                                      |
| \"\"\"                                                               |
|                                                                      |
| Untuk PARTIAL trade yang sudah \> PARTIAL_TIMEOUT_S:                 |
|                                                                      |
| 1\. Cancel sisa order yang belum fill                                |
|                                                                      |
| 2\. Anggap yang sudah fill sebagai posisi penuh                      |
|                                                                      |
| 3\. Update trade ke status OPEN dengan qty yang ter-fill             |
|                                                                      |
| 4\. Alert: partial fill terjadi                                      |
|                                                                      |
| \"\"\"                                                               |
+----------------------------------------------------------------------+

**13.3 Retry Policy --- Eksekusi**

  -----------------------------------------------------------------------------------------------------------------------------
  **Error**                 **Max Retry**   **Delay**                **Buat Order ID Baru?**   **Alert?**
  ------------------------- --------------- ------------------------ ------------------------- --------------------------------
  Network timeout           3x              1s, 2s, 4s               Tidak                     Setelah 3x gagal

  Rate limit 429            5x              5s, 10s, 20s, 40s, 80s   Tidak                     Setelah 2x

  Order rejected (-2010)    0x              ---                      ---                       Segera --- balance kurang

  Order not found (-2011)   1x              2s                       Tidak                     Jika masih gagal

  Invalid qty (-1013)       1x              0s                       Tidak                     Cek lot filter

  Server error 5xx          3x              5s, 10s, 20s             Tidak                     Setelah 2x

  IP banned 418             0x              ---                      ---                       KRITIS --- manual intervention
  -----------------------------------------------------------------------------------------------------------------------------

**14. Integrasi --- Dependency Map Lengkap**

**14.1 Dependency Antar File**

  ----------------------------------------------------------------------------------------------------------------------------------------------
  **File**               **Import Dari**                                                          **Dipanggil Oleh**
  ---------------------- ------------------------------------------------------------------------ ----------------------------------------------
  trade.py               utils/helpers, execution_layer (OrderRequest)                            manager.py

  manager.py             trade.py, store.py, audit_trail.py, idempotency.py, trade_validator.py   main_loop

  store.py               --- (raw DB queries via sqlite3)                                         manager.py, recovery.py, exit_layer

  audit_trail.py         --- (raw DB queries via sqlite3)                                         manager.py, exit_layer

  idempotency.py         --- (raw DB queries via sqlite3)                                         manager.py

  recovery.py            store.py, execution_layer (get_order_status)                             main.py startup

  trade_validator.py     --- (pure function)                                                      manager.py

  exchange.py            --- (abstract base)                                                      binance.py, smart_router.py

  binance.py             exchange.py, security/key_manager, data_layer/rate_limiter               spot_executor.py, futures_executor.py

  spot_executor.py       binance.py, smart_router.py                                              manager.py

  futures_executor.py    binance.py, smart_router.py                                              manager.py

  order_splitter.py      exchange.py (BaseExchange)                                               spot_executor.py, futures_executor.py

  smart_router.py        exchange.py (BaseExchange)                                               spot_executor.py, futures_executor.py

  latency_tracker.py     --- (pure state)                                                         binance.py, intelligence_layer/latency_guard

  execution_monitor.py   store.py, execution_layer (get_order_status)                             main.py (background task)
  ----------------------------------------------------------------------------------------------------------------------------------------------

**15. Checklist Implementasi Trade & Execution Layer**

**15.1 Checklist Trade Layer**

  -----------------------------------------------------------------------------------------------------------------------------------------------------------------
  **No**   **Item**                                                               **Verifikasi**                                                         **Done**
  -------- ---------------------------------------------------------------------- ---------------------------------------------------------------------- ----------
  1        Trade.client_order_id menggunakan format yang deterministik dan unik   Test: dua signal sama di waktu berbeda → ID berbeda                    ☐

  2        idempotency.check_or_register() atomic (INSERT OR IGNORE + SELECT)     Test concurrent: dua thread check_or_register ID sama → satu PROCEED   ☐

  3        manager.open() tidak kirim order jika idempotency return DUPLICATE     Test: kirim Signal dua kali → trade kedua tidak masuk exchange         ☐

  4        store.archive() transaksi atomik: INSERT+DELETE dalam satu BEGIN       Test: simulasi crash setelah INSERT → DELETE tidak jalan → rollback    ☐

  5        audit_trail record tidak pernah diupdate atau dihapus                  grep -n \'UPDATE\\\|DELETE\' audit_trail.py → harus kosong             ☐

  6        verify_integrity() mendeteksi checksum yang dimanipulasi               Test: edit record langsung di DB → verify_integrity() = False          ☐

  7        recovery.restore() selesai sebelum main loop mulai                     Code review main.py T-09 --- await restore() sebelum start loop        ☐

  8        recovery.restore() masuk safe_mode jika ada unresolved trade           Test: inject ghost trade → safe_mode.emergency_stop terpanggil         ☐

  9        Paper mode: status trade tetap update normal (PENDING→OPEN→CLOSED)     Test paper mode full cycle --- semua status transition normal          ☐
  -----------------------------------------------------------------------------------------------------------------------------------------------------------------

**15.2 Checklist Execution Layer**

  ------------------------------------------------------------------------------------------------------------------------------------------------------------------------
  **No**   **Item**                                                              **Verifikasi**                                                                 **Done**
  -------- --------------------------------------------------------------------- ------------------------------------------------------------------------------ ----------
  10       rate_limiter.acquire() dipanggil sebelum SETIAP request ke exchange   grep -n \'await self.\_exchange\' binance.py --- semua harus setelah acquire   ☐

  11       round_quantity() menggunakan floor, bukan round biasa                 Test: 0.1235 step 0.001 → 0.123 (bukan 0.124)                                  ☐

  12       Error code -2010 (balance kurang) tidak di-retry                      Test: inject -2010 → retry=0, alert langsung                                   ☐

  13       Error code 418 (IP banned) trigger alert KRITIS dan tidak di-retry    Test: inject 418 → emergency alert, no retry                                   ☐

  14       execution_monitor mendeteksi SUBMITTED trade yang stuck \> 30 detik   Test: submit order, jangan confirm → monitor cek dan query exchange            ☐

  15       FuturesExecutor: set_leverage dipanggil sebelum place_order           Test: buka futures trade tanpa cache leverage → set_leverage terpanggil        ☐

  16       SmartRouter selalu return exchange yang valid                         Test dengan semua exchanges down → raise ExchangeNotAvailableError             ☐

  17       latency_tracker mencatat setiap REST request (sukses maupun gagal)    Test: inject timeout → latency success=False tetap dicatat                     ☐
  ------------------------------------------------------------------------------------------------------------------------------------------------------------------------

+:--------------------------------------------------------------------------------------------------------------:+
| **Dokumen ini adalah kontrak implementasi Trade Layer & Execution Layer.**                                     |
|                                                                                                                |
| Perubahan Trade dataclass, AuditEvent format, atau OrderRequest/Response WAJIB diupdate sebelum merge ke main. |
|                                                                                                                |
| *Referensi: risk_layer_docs.docx • agent_core_docs.docx • data_intelligence_docs.docx*                         |
+----------------------------------------------------------------------------------------------------------------+
