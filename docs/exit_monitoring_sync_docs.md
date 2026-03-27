**crypto_ai_agent**

**Dokumentasi: Exit Layer**

**·**

**Monitoring Layer**

**·**

**Sync Layer**

*trailing · take_profit · break_even · exit_manager*

*heartbeat · connectivity · health_check · alerts*

*balance_sync · order_sync · position_sync · reconciliation*

Versi 1.0 \| Referensi: trade_execution_docs.docx · agent_core_docs.docx

  ------------------------------------------------

  ------------------------------------------------

**1. Overview --- Tiga Layer Pelengkap**

Tiga layer ini bekerja di belakang layar untuk memastikan sistem selalu dalam kondisi sehat, sinkron, dan keluar dari posisi dengan benar. Mereka tidak membuat keputusan entry --- hanya mengelola posisi aktif dan kesehatan sistem.

  -----------------------------------------------------------------------------------------------------------------------------------
  **Layer**        **Tanggung Jawab**                                 **Dipanggil Setiap**             **Output Utama**
  ---------------- -------------------------------------------------- -------------------------------- ------------------------------
  **Exit**         Tentukan kapan dan bagaimana keluar posisi         Setiap tick (per posisi aktif)   ExitDecision

  **Monitoring**   Deteksi masalah sistem sebelum jadi krisis         30 detik (background)            HealthStatus, Alert

  **Sync**         Pastikan state internal = state exchange (truth)   30--60 detik (scheduled)         SyncReport, DiscrepancyAlert
  -----------------------------------------------------------------------------------------------------------------------------------

**1.1 Interaksi Antar Layer**

+-----------------------------------------------------------+
| main_loop (setiap tick):                                  |
|                                                           |
| for position in trade_mgr.get_open_trades():              |
|                                                           |
| exit_decision = exit_mgr.evaluate(position, market_state) |
|                                                           |
| if exit_decision.action != \'HOLD\':                      |
|                                                           |
| await trade_mgr.close(position, exit_decision)            |
|                                                           |
| circuit_breaker.record_loss/win()                         |
|                                                           |
| scheduler (background):                                   |
|                                                           |
| every 30s → monitoring.health_check.run()                 |
|                                                           |
| every 30s → sync.order_sync.run()                         |
|                                                           |
| every 60s → sync.balance_sync.run()                       |
|                                                           |
| every 60s → sync.position_sync.run()                      |
|                                                           |
| every 30s → monitoring.heartbeat.pulse()                  |
|                                                           |
| daily → sync.reconciliation.run_daily()                   |
+-----------------------------------------------------------+

**BAGIAN A --- Exit Layer**

**2. exit_manager.py --- Koordinator Exit**

Dipanggil setiap tick untuk setiap posisi aktif. Mengevaluasi semua kondisi exit secara berurutan dan mengembalikan satu keputusan akhir.

**2.1 ExitDecision & ExitAction**

+--------------------------------------------------------------------------------+
| class ExitAction(str, Enum):                                                   |
|                                                                                |
| HOLD = \'hold\' \# jangan lakukan apa-apa                                      |
|                                                                                |
| EXIT_SL = \'exit_sl\' \# stop loss tercapai                                    |
|                                                                                |
| EXIT_TP = \'exit_tp\' \# take profit tercapai                                  |
|                                                                                |
| EXIT_TRAIL = \'exit_trail\' \# trailing stop tercapai                          |
|                                                                                |
| EXIT_SIGNAL = \'exit_signal\' \# strategi minta keluar                         |
|                                                                                |
| EXIT_TIMEOUT = \'exit_timeout\' \# posisi terlalu lama                         |
|                                                                                |
| EXIT_FORCED = \'exit_forced\' \# circuit breaker / safe mode                   |
|                                                                                |
| UPDATE_SL = \'update_sl\' \# geser SL (breakeven / trailing)                   |
|                                                                                |
| PARTIAL_CLOSE = \'partial_close\' \# tutup sebagian posisi                     |
|                                                                                |
| \@dataclass                                                                    |
|                                                                                |
| class ExitDecision:                                                            |
|                                                                                |
| action: ExitAction                                                             |
|                                                                                |
| exit_price: float \# 0.0 = gunakan market price                                |
|                                                                                |
| new_sl: float \# hanya untuk UPDATE_SL                                         |
|                                                                                |
| close_qty: float \# hanya untuk PARTIAL_CLOSE (0 = tutup semua)                |
|                                                                                |
| reason: str \# teks untuk logging & audit                                      |
|                                                                                |
| urgency: str \# \'normal\' \| \'urgent\'                                       |
|                                                                                |
| source: str \# \'sl\' \| \'tp\' \| \'trailing\' \| \'strategy\' \| \'timeout\' |
|                                                                                |
| confidence: float \# 0.0--1.0 seberapa yakin harus keluar                      |
|                                                                                |
| \@property                                                                     |
|                                                                                |
| def is_exit(self) -\> bool:                                                    |
|                                                                                |
| return self.action not in (ExitAction.HOLD, ExitAction.UPDATE_SL)              |
|                                                                                |
| \@property                                                                     |
|                                                                                |
| def is_urgent(self) -\> bool:                                                  |
|                                                                                |
| return self.urgency == \'urgent\'                                              |
+--------------------------------------------------------------------------------+

**2.2 Urutan Evaluasi --- 7 Langkah**

  ------------------------------------------------------------------------------------------------------------------------------
  **Prioritas**   **Check**         **Kondisi**                                           **Aksi**                 **Urgency**
  --------------- ----------------- ----------------------------------------------------- ------------------------ -------------
  1 (tertinggi)   **Hard SL**       low/high candle menembus sl_price                     EXIT_SL                  urgent

  2               **Hard TP**       low/high candle menembus tp_price                     EXIT_TP                  normal

  3               **Break-even**    profit \>= BREAKEVEN_TRIGGER_R × risk                 UPDATE_SL                normal

  4               **Trailing SL**   harga bergerak menguntungkan, update trail level      UPDATE_SL / EXIT_TRAIL   normal

  5               **Partial TP**    profit \>= PARTIAL_TP_TRIGGER, belum pernah partial   PARTIAL_CLOSE            normal

  6               **Strat Exit**    strategy.should_exit() return ExitSignal              EXIT_SIGNAL              normal

  7 (terendah)    **Timeout**       hold_candles \>= MAX_HOLD_CANDLES                     EXIT_TIMEOUT             normal
  ------------------------------------------------------------------------------------------------------------------------------

  ---------------------------------------------------------------------------------------------------------------------------------------------------------
  **PENTING:** Evaluasi berhenti di prioritas pertama yang terpenuhi. Jika SL tercapai, tidak perlu cek TP atau trailing. Ini mencegah konflik keputusan.

  ---------------------------------------------------------------------------------------------------------------------------------------------------------

**2.3 Interface Publik**

+---------------------------------------------------------------------+
| class ExitManager:                                                  |
|                                                                     |
| def \_\_init\_\_(                                                   |
|                                                                     |
| self,                                                               |
|                                                                     |
| config: AgentConfig,                                                |
|                                                                     |
| trailing: \'TrailingStopManager\',                                  |
|                                                                     |
| take_profit: \'TakeProfitManager\',                                 |
|                                                                     |
| break_even: \'BreakEvenManager\',                                   |
|                                                                     |
| strategy: \'BaseStrategy\',                                         |
|                                                                     |
| ): \...                                                             |
|                                                                     |
| def evaluate(                                                       |
|                                                                     |
| self,                                                               |
|                                                                     |
| position: \'Trade\',                                                |
|                                                                     |
| state: \'MarketState\',                                             |
|                                                                     |
| ) -\> ExitDecision:                                                 |
|                                                                     |
| \"\"\"                                                              |
|                                                                     |
| Evaluasi satu posisi. Pure function --- tidak ada I/O.              |
|                                                                     |
| Dipanggil dari main_loop setiap tick untuk setiap open trade.       |
|                                                                     |
| \"\"\"                                                              |
|                                                                     |
| high = state.latest_candle.high                                     |
|                                                                     |
| low = state.latest_candle.low                                       |
|                                                                     |
| close = state.latest_candle.close                                   |
|                                                                     |
| \# 1. Hard SL                                                       |
|                                                                     |
| sl_hit = self.\_check_sl(position, high, low)                       |
|                                                                     |
| if sl_hit: return sl_hit                                            |
|                                                                     |
| \# 2. Hard TP                                                       |
|                                                                     |
| tp_hit = self.\_check_tp(position, high, low)                       |
|                                                                     |
| if tp_hit: return tp_hit                                            |
|                                                                     |
| \# 3. Break-even                                                    |
|                                                                     |
| be_update = self.break_even.check(position, close)                  |
|                                                                     |
| if be_update: return be_update                                      |
|                                                                     |
| \# 4. Trailing SL                                                   |
|                                                                     |
| trail = self.trailing.update(position, high, low, close, state.atr) |
|                                                                     |
| if trail: return trail                                              |
|                                                                     |
| \# 5. Partial TP                                                    |
|                                                                     |
| partial = self.\_check_partial_tp(position, close)                  |
|                                                                     |
| if partial: return partial                                          |
|                                                                     |
| \# 6. Strategy exit                                                 |
|                                                                     |
| strat_exit = self.strategy.should_exit(position, state)             |
|                                                                     |
| if strat_exit:                                                      |
|                                                                     |
| return ExitDecision(                                                |
|                                                                     |
| action = ExitAction.EXIT_SIGNAL,                                    |
|                                                                     |
| exit_price = 0.0, new_sl=0.0, close_qty=0.0,                        |
|                                                                     |
| reason = strat_exit.reason,                                         |
|                                                                     |
| urgency = strat_exit.urgency,                                       |
|                                                                     |
| source = \'strategy\',                                              |
|                                                                     |
| confidence = strat_exit.confidence,                                 |
|                                                                     |
| )                                                                   |
|                                                                     |
| \# 7. Timeout                                                       |
|                                                                     |
| timeout = self.\_check_timeout(position, state)                     |
|                                                                     |
| if timeout: return timeout                                          |
|                                                                     |
| return ExitDecision(                                                |
|                                                                     |
| action=ExitAction.HOLD, exit_price=0.0, new_sl=0.0,                 |
|                                                                     |
| close_qty=0.0, reason=\'\', urgency=\'normal\',                     |
|                                                                     |
| source=\'hold\', confidence=0.0,                                    |
|                                                                     |
| )                                                                   |
|                                                                     |
| def register_open_positions(                                        |
|                                                                     |
| self, trades: list\[\'Trade\'\]                                     |
|                                                                     |
| ) -\> None:                                                         |
|                                                                     |
| \"\"\"                                                              |
|                                                                     |
| Inisialisasi trailing state untuk semua posisi saat startup.        |
|                                                                     |
| Dipanggil oleh main.py T-08.                                        |
|                                                                     |
| \"\"\"                                                              |
+---------------------------------------------------------------------+

**2.4 SL Check --- Menggunakan Candle High/Low**

+-----------------------------------------------------------------------+
| def \_check_sl(                                                       |
|                                                                       |
| self,                                                                 |
|                                                                       |
| position: \'Trade\',                                                  |
|                                                                       |
| high: float,                                                          |
|                                                                       |
| low: float,                                                           |
|                                                                       |
| ) -\> ExitDecision \| None:                                           |
|                                                                       |
| \"\"\"                                                                |
|                                                                       |
| Menggunakan high/low candle, bukan hanya close price.                 |
|                                                                       |
| Ini lebih akurat karena SL bisa di-hit di tengah candle.              |
|                                                                       |
| BUY position: SL di-hit jika low \<= sl_price                         |
|                                                                       |
| SELL position: SL di-hit jika high \>= sl_price                       |
|                                                                       |
| \"\"\"                                                                |
|                                                                       |
| if position.side == \'BUY\' and low \<= position.sl_price:            |
|                                                                       |
| return ExitDecision(                                                  |
|                                                                       |
| action = ExitAction.EXIT_SL,                                          |
|                                                                       |
| exit_price = position.sl_price, \# worst case: SL price               |
|                                                                       |
| new_sl = 0.0,                                                         |
|                                                                       |
| close_qty = 0.0,                                                      |
|                                                                       |
| reason = f\'SL hit: low {low:.2f} \<= sl {position.sl_price:.2f}\',   |
|                                                                       |
| urgency = \'urgent\',                                                 |
|                                                                       |
| source = \'sl\',                                                      |
|                                                                       |
| confidence = 1.0,                                                     |
|                                                                       |
| )                                                                     |
|                                                                       |
| if position.side == \'SELL\' and high \>= position.sl_price:          |
|                                                                       |
| return ExitDecision(                                                  |
|                                                                       |
| action = ExitAction.EXIT_SL,                                          |
|                                                                       |
| exit_price = position.sl_price,                                       |
|                                                                       |
| new_sl = 0.0, close_qty=0.0,                                          |
|                                                                       |
| reason = f\'SL hit: high {high:.2f} \>= sl {position.sl_price:.2f}\', |
|                                                                       |
| urgency = \'urgent\',                                                 |
|                                                                       |
| source = \'sl\',                                                      |
|                                                                       |
| confidence = 1.0,                                                     |
|                                                                       |
| )                                                                     |
|                                                                       |
| return None                                                           |
+-----------------------------------------------------------------------+

**3. trailing.py --- Trailing Stop**

Menggeser SL secara otomatis mengikuti pergerakan harga yang menguntungkan, mengunci profit sambil memberikan ruang untuk volatilitas normal.

**3.1 Tiga Metode Trailing**

  -----------------------------------------------------------------------------------------------------------------
  **Metode**       **Formula**                                  **Kelebihan**                  **Config Key**
  ---------------- -------------------------------------------- ------------------------------ --------------------
  **ATR-based**    trail = highest_high - (N × ATR) untuk BUY   Adaptif terhadap volatilitas   TRAIL_ATR_MULT

  **Percentage**   trail = highest_high × (1 - pct) untuk BUY   Simple, predictable            TRAIL_PCT

  **Chandelier**   trail = highest_high_N - (M × ATR)           Baik untuk trend panjang       TRAIL_CHANDELIER_N
  -----------------------------------------------------------------------------------------------------------------

**3.2 TrailingState --- Per Posisi**

+-----------------------------------------------------------------+
| \@dataclass                                                     |
|                                                                 |
| class TrailingState:                                            |
|                                                                 |
| trade_id: str                                                   |
|                                                                 |
| method: str \# \'atr\' \| \'percentage\' \| \'chandelier\'      |
|                                                                 |
| activated: bool \# True setelah profit \>= activation threshold |
|                                                                 |
| current_trail: float \# level trailing SL saat ini              |
|                                                                 |
| highest_high: float \# untuk BUY: high tertinggi sejak entry    |
|                                                                 |
| lowest_low: float \# untuk SELL: low terendah sejak entry       |
|                                                                 |
| last_updated: datetime                                          |
+-----------------------------------------------------------------+

**3.3 Interface Publik**

+---------------------------------------------------------------+
| class TrailingStopManager:                                    |
|                                                               |
| def \_\_init\_\_(self, config: AgentConfig):                  |
|                                                               |
| self.\_states: dict\[str, TrailingState\] = {}                |
|                                                               |
| self.method = config.trailing_method                          |
|                                                               |
| self.atr_mult = config.trail_atr_mult                         |
|                                                               |
| self.trail_pct = config.trail_pct                             |
|                                                               |
| self.activation_r = config.trail_activation_r                 |
|                                                               |
| def register(self, trade: \'Trade\') -\> None:                |
|                                                               |
| \"\"\"Inisialisasi TrailingState saat posisi dibuka.\"\"\"    |
|                                                               |
| self.\_states\[trade.trade_id\] = TrailingState(              |
|                                                               |
| trade_id = trade.trade_id,                                    |
|                                                               |
| method = self.method,                                         |
|                                                               |
| activated = False,                                            |
|                                                               |
| current_trail = trade.sl_price, \# mulai dari SL awal         |
|                                                               |
| highest_high = trade.avg_fill_price,                          |
|                                                               |
| lowest_low = trade.avg_fill_price,                            |
|                                                               |
| last_updated = utcnow(),                                      |
|                                                               |
| )                                                             |
|                                                               |
| def update(                                                   |
|                                                               |
| self,                                                         |
|                                                               |
| trade: \'Trade\',                                             |
|                                                               |
| high: float,                                                  |
|                                                               |
| low: float,                                                   |
|                                                               |
| close: float,                                                 |
|                                                               |
| atr: float,                                                   |
|                                                               |
| ) -\> ExitDecision \| None:                                   |
|                                                               |
| \"\"\"                                                        |
|                                                               |
| Update trailing state dan return ExitDecision jika:           |
|                                                               |
| \- Trail level diperbarui → UPDATE_SL                         |
|                                                               |
| \- Harga menembus trail → EXIT_TRAIL                          |
|                                                               |
| \- Trailing belum aktif → None                                |
|                                                               |
| \"\"\"                                                        |
|                                                               |
| state = self.\_states.get(trade.trade_id)                     |
|                                                               |
| if not state: return None                                     |
|                                                               |
| \# Cek aktivasi trail                                         |
|                                                               |
| if not state.activated:                                       |
|                                                               |
| profit = self.\_calc_profit_r(trade, close)                   |
|                                                               |
| if profit \>= self.activation_r:                              |
|                                                               |
| state.activated = True                                        |
|                                                               |
| log.info(\'Trailing activated\', trade_id=trade.trade_id,     |
|                                                               |
| profit_r=profit)                                              |
|                                                               |
| else:                                                         |
|                                                               |
| return None                                                   |
|                                                               |
| if trade.side == \'BUY\':                                     |
|                                                               |
| return self.\_update_buy_trail(trade, state, high, low, atr)  |
|                                                               |
| else:                                                         |
|                                                               |
| return self.\_update_sell_trail(trade, state, high, low, atr) |
|                                                               |
| def deregister(self, trade_id: str) -\> None:                 |
|                                                               |
| \"\"\"Hapus state saat posisi ditutup.\"\"\"                  |
|                                                               |
| self.\_states.pop(trade_id, None)                             |
|                                                               |
| def get_state(self, trade_id: str) -\> TrailingState \| None: |
|                                                               |
| return self.\_states.get(trade_id)                            |
+---------------------------------------------------------------+

**3.4 ATR-based Trail --- Implementasi**

+--------------------------------------------------------------------------------+
| def \_update_buy_trail(                                                        |
|                                                                                |
| self,                                                                          |
|                                                                                |
| trade: \'Trade\',                                                              |
|                                                                                |
| state: TrailingState,                                                          |
|                                                                                |
| high: float,                                                                   |
|                                                                                |
| low: float,                                                                    |
|                                                                                |
| atr: float,                                                                    |
|                                                                                |
| ) -\> ExitDecision \| None:                                                    |
|                                                                                |
| \# Update highest high                                                         |
|                                                                                |
| if high \> state.highest_high:                                                 |
|                                                                                |
| state.highest_high = high                                                      |
|                                                                                |
| \# Hitung trail level baru                                                     |
|                                                                                |
| if self.method == \'atr\':                                                     |
|                                                                                |
| new_trail = state.highest_high - (atr \* self.atr_mult)                        |
|                                                                                |
| elif self.method == \'percentage\':                                            |
|                                                                                |
| new_trail = state.highest_high \* (1 - self.trail_pct)                         |
|                                                                                |
| else: \# chandelier                                                            |
|                                                                                |
| new_trail = state.highest_high - (atr \* self.atr_mult)                        |
|                                                                                |
| \# Trail hanya boleh naik (ratchet effect)                                     |
|                                                                                |
| if new_trail \> state.current_trail:                                           |
|                                                                                |
| old_trail = state.current_trail                                                |
|                                                                                |
| state.current_trail = new_trail                                                |
|                                                                                |
| state.last_updated = utcnow()                                                  |
|                                                                                |
| return ExitDecision(                                                           |
|                                                                                |
| action = ExitAction.UPDATE_SL,                                                 |
|                                                                                |
| exit_price = 0.0,                                                              |
|                                                                                |
| new_sl = new_trail,                                                            |
|                                                                                |
| close_qty = 0.0,                                                               |
|                                                                                |
| reason = f\'Trailing SL naik: {old_trail:.2f} → {new_trail:.2f}\',             |
|                                                                                |
| urgency = \'normal\',                                                          |
|                                                                                |
| source = \'trailing\',                                                         |
|                                                                                |
| confidence = 1.0,                                                              |
|                                                                                |
| )                                                                              |
|                                                                                |
| \# Cek apakah harga menembus trail level                                       |
|                                                                                |
| if low \<= state.current_trail:                                                |
|                                                                                |
| return ExitDecision(                                                           |
|                                                                                |
| action = ExitAction.EXIT_TRAIL,                                                |
|                                                                                |
| exit_price = state.current_trail,                                              |
|                                                                                |
| new_sl=0.0, close_qty=0.0,                                                     |
|                                                                                |
| reason = f\'Trail SL hit: low {low:.2f} \<= trail {state.current_trail:.2f}\', |
|                                                                                |
| urgency = \'urgent\',                                                          |
|                                                                                |
| source = \'trailing\',                                                         |
|                                                                                |
| confidence = 1.0,                                                              |
|                                                                                |
| )                                                                              |
|                                                                                |
| return None                                                                    |
+--------------------------------------------------------------------------------+

**3.5 Config Keys Trailing**

  ------------------------------------------------------------------------------------------------------------------------
  **Key**              **Default**   **Valid Range**               **Keterangan**
  -------------------- ------------- ----------------------------- -------------------------------------------------------
  TRAILING_METHOD      atr           atr\|percentage\|chandelier   Metode trailing yang digunakan

  TRAIL_ATR_MULT       2.0           1.0--5.0                      Jarak trail = ATR × multiplier

  TRAIL_PCT            0.02          0.005--0.10                   Jarak trail 2% dari high/low untuk percentage method

  TRAIL_CHANDELIER_N   22            10--50                        Lookback untuk highest high / lowest low (chandelier)

  TRAIL_ACTIVATION_R   1.0           0.5--3.0                      Trailing aktif setelah profit = N × risk (1R = 1:1)
  ------------------------------------------------------------------------------------------------------------------------

**4. take_profit.py & break_even.py**

**4.1 take_profit.py --- Manajemen TP**

Mengelola dua mode take profit: single TP (tutup semua sekaligus) dan partial TP (tutup sebagian, sisa di-trailing).

+----------------------------------------------------------------------+
| class TakeProfitManager:                                             |
|                                                                      |
| \@dataclass                                                          |
|                                                                      |
| class TPState:                                                       |
|                                                                      |
| trade_id: str                                                        |
|                                                                      |
| tp1_hit: bool \# sudah partial close?                                |
|                                                                      |
| tp1_price: float \# harga TP pertama                                 |
|                                                                      |
| tp2_price: float \# TP kedua (atau 0 jika single TP)                 |
|                                                                      |
| partial_ratio: float \# berapa persen yang ditutup di TP1            |
|                                                                      |
| def check(                                                           |
|                                                                      |
| self,                                                                |
|                                                                      |
| trade: \'Trade\',                                                    |
|                                                                      |
| high: float,                                                         |
|                                                                      |
| low: float,                                                          |
|                                                                      |
| ) -\> ExitDecision \| None:                                          |
|                                                                      |
| \"\"\"                                                               |
|                                                                      |
| Single TP: jika tp_price \> 0 dan harga mencapainya → EXIT_TP        |
|                                                                      |
| Partial TP: jika belum pernah partial, tutup PARTIAL_TP_RATIO posisi |
|                                                                      |
| sisa posisi terus di-trailing                                        |
|                                                                      |
| \"\"\"                                                               |
|                                                                      |
| if trade.tp_price \<= 0:                                             |
|                                                                      |
| return None \# tidak ada TP fixed                                    |
|                                                                      |
| \# BUY: TP jika high \>= tp_price                                    |
|                                                                      |
| \# SELL: TP jika low \<= tp_price                                    |
|                                                                      |
| tp_hit = (                                                           |
|                                                                      |
| (trade.side == \'BUY\' and high \>= trade.tp_price) or               |
|                                                                      |
| (trade.side == \'SELL\' and low \<= trade.tp_price)                  |
|                                                                      |
| )                                                                    |
|                                                                      |
| if not tp_hit:                                                       |
|                                                                      |
| return None                                                          |
|                                                                      |
| state = self.\_states.get(trade.trade_id)                            |
|                                                                      |
| \# Partial TP: tutup sebagian, lanjut trailing                       |
|                                                                      |
| if self.config.partial_tp_enabled and state and not state.tp1_hit:   |
|                                                                      |
| state.tp1_hit = True                                                 |
|                                                                      |
| return ExitDecision(                                                 |
|                                                                      |
| action = ExitAction.PARTIAL_CLOSE,                                   |
|                                                                      |
| exit_price= trade.tp_price,                                          |
|                                                                      |
| new_sl = trade.avg_fill_price, \# geser SL ke entry (breakeven)      |
|                                                                      |
| close_qty = trade.filled_qty \* self.config.partial_tp_ratio,        |
|                                                                      |
| reason = f\'Partial TP: {self.config.partial_tp_ratio:.0%} closed\', |
|                                                                      |
| urgency = \'normal\',                                                |
|                                                                      |
| source = \'tp\',                                                     |
|                                                                      |
| confidence= 1.0,                                                     |
|                                                                      |
| )                                                                    |
|                                                                      |
| \# Full TP                                                           |
|                                                                      |
| return ExitDecision(                                                 |
|                                                                      |
| action = ExitAction.EXIT_TP,                                         |
|                                                                      |
| exit_price= trade.tp_price,                                          |
|                                                                      |
| new_sl=0.0, close_qty=0.0,                                           |
|                                                                      |
| reason = f\'TP hit: {trade.tp_price:.2f}\',                          |
|                                                                      |
| urgency = \'normal\',                                                |
|                                                                      |
| source = \'tp\',                                                     |
|                                                                      |
| confidence= 1.0,                                                     |
|                                                                      |
| )                                                                    |
+----------------------------------------------------------------------+

**4.2 break_even.py --- Geser SL ke Entry**

+------------------------------------------------------------------------------------------+
| class BreakEvenManager:                                                                  |
|                                                                                          |
| \"\"\"                                                                                   |
|                                                                                          |
| Menggeser SL ke harga entry saat profit mencapai threshold.                              |
|                                                                                          |
| Memastikan posisi tidak bisa rugi setelah threshold tercapai.                            |
|                                                                                          |
| \"\"\"                                                                                   |
|                                                                                          |
| def check(                                                                               |
|                                                                                          |
| self,                                                                                    |
|                                                                                          |
| trade: \'Trade\',                                                                        |
|                                                                                          |
| close: float,                                                                            |
|                                                                                          |
| ) -\> ExitDecision \| None:                                                              |
|                                                                                          |
| \# Sudah breakeven? Tidak perlu cek lagi.                                                |
|                                                                                          |
| if self.\_is_at_breakeven(trade): return None                                            |
|                                                                                          |
| \# Hitung profit dalam unit risk (R)                                                     |
|                                                                                          |
| risk = abs(trade.avg_fill_price - trade.sl_price)                                        |
|                                                                                          |
| if risk \<= 0: return None                                                               |
|                                                                                          |
| if trade.side == \'BUY\':                                                                |
|                                                                                          |
| profit = close - trade.avg_fill_price                                                    |
|                                                                                          |
| be_price = trade.avg_fill_price + self.config.breakeven_buffer_pct \* close              |
|                                                                                          |
| else:                                                                                    |
|                                                                                          |
| profit = trade.avg_fill_price - close                                                    |
|                                                                                          |
| be_price = trade.avg_fill_price - self.config.breakeven_buffer_pct \* close              |
|                                                                                          |
| profit_r = profit / risk                                                                 |
|                                                                                          |
| if profit_r \>= self.config.breakeven_trigger_r:                                         |
|                                                                                          |
| \# Jangan geser SL ke belakang                                                           |
|                                                                                          |
| if trade.side == \'BUY\' and be_price \<= trade.sl_price: return None                    |
|                                                                                          |
| if trade.side == \'SELL\' and be_price \>= trade.sl_price: return None                   |
|                                                                                          |
| return ExitDecision(                                                                     |
|                                                                                          |
| action = ExitAction.UPDATE_SL,                                                           |
|                                                                                          |
| exit_price = 0.0,                                                                        |
|                                                                                          |
| new_sl = be_price,                                                                       |
|                                                                                          |
| close_qty = 0.0,                                                                         |
|                                                                                          |
| reason = f\'Break-even: profit {profit_r:.1f}R \>= {self.config.breakeven_trigger_r}R\', |
|                                                                                          |
| urgency = \'normal\',                                                                    |
|                                                                                          |
| source = \'break_even\',                                                                 |
|                                                                                          |
| confidence = 1.0,                                                                        |
|                                                                                          |
| )                                                                                        |
|                                                                                          |
| return None                                                                              |
|                                                                                          |
| def \_is_at_breakeven(self, trade: \'Trade\') -\> bool:                                  |
|                                                                                          |
| \"\"\"SL sudah di atas entry (BUY) atau di bawah entry (SELL).\"\"\"                     |
|                                                                                          |
| if trade.side == \'BUY\': return trade.sl_price \>= trade.avg_fill_price                 |
|                                                                                          |
| if trade.side == \'SELL\': return trade.sl_price \<= trade.avg_fill_price                |
|                                                                                          |
| return False                                                                             |
+------------------------------------------------------------------------------------------+

**4.3 Config Keys Take Profit & Break Even**

  ------------------------------------------------------------------------------------------------
  **Key**                **Default**   **Keterangan**
  ---------------------- ------------- -----------------------------------------------------------
  PARTIAL_TP_ENABLED     True          True = tutup sebagian di TP, sisanya trailing

  PARTIAL_TP_RATIO       0.50          50% posisi ditutup saat TP pertama tercapai

  BREAKEVEN_TRIGGER_R    1.0           Aktifkan breakeven saat profit = 1R (1:1 risk)

  BREAKEVEN_BUFFER_PCT   0.001         Buffer 0.1% di atas entry untuk SL breakeven

  MAX_HOLD_CANDLES       48            Force exit jika posisi terbuka \> N candle tanpa progress
  ------------------------------------------------------------------------------------------------

**BAGIAN B --- Monitoring Layer**

**5. heartbeat.py --- Deteksi Agent Mati**

Pulse periodik yang membuktikan agent masih hidup. health_restart.py di automation/ memantau heartbeat ini dari luar process dan me-restart agent jika tidak ada tanda kehidupan.

**5.1 Cara Kerja**

+---------------------------------------------------------------------------+
| class Heartbeat:                                                          |
|                                                                           |
| PULSE_INTERVAL_S = 30 \# tulis ke DB setiap 30 detik                      |
|                                                                           |
| STALE_THRESHOLD_S = 90 \# STALE jika tidak ada pulse 90 detik             |
|                                                                           |
| DEAD_THRESHOLD_S = 180 \# DEAD jika tidak ada pulse 3 menit               |
|                                                                           |
| async def start(self) -\> None:                                           |
|                                                                           |
| \"\"\"Loop background yang berjalan seumur hidup agent.\"\"\"             |
|                                                                           |
| while True:                                                               |
|                                                                           |
| await self.\_pulse()                                                      |
|                                                                           |
| await asyncio.sleep(self.PULSE_INTERVAL_S)                                |
|                                                                           |
| async def \_pulse(self) -\> None:                                         |
|                                                                           |
| \"\"\"                                                                    |
|                                                                           |
| Tulis timestamp ke heartbeat.db.                                          |
|                                                                           |
| Update Prometheus gauge: agent_alive = 1.                                 |
|                                                                           |
| Cek semua asyncio tasks masih running.                                    |
|                                                                           |
| \"\"\"                                                                    |
|                                                                           |
| now = utcnow()                                                            |
|                                                                           |
| self.\_db.execute(                                                        |
|                                                                           |
| \'INSERT OR REPLACE INTO heartbeat (id, ts, tasks_ok) VALUES (1, ?, ?)\', |
|                                                                           |
| (now.isoformat(), self.\_check_tasks())                                   |
|                                                                           |
| )                                                                         |
|                                                                           |
| metrics.gauge(\'agent_alive\').set(1)                                     |
|                                                                           |
| metrics.gauge(\'agent_last_pulse_ts\').set(now.timestamp())               |
|                                                                           |
| def get_status(self) -\> \'HeartbeatStatus\':                             |
|                                                                           |
| \"\"\"Dibaca oleh health_check.py dan health_restart.py.\"\"\"            |
|                                                                           |
| last = self.\_db.fetchone(\'SELECT ts FROM heartbeat WHERE id = 1\')      |
|                                                                           |
| if not last: return HeartbeatStatus.DEAD                                  |
|                                                                           |
| elapsed = (utcnow() - datetime.fromisoformat(last\[0\])).total_seconds()  |
|                                                                           |
| if elapsed \> self.DEAD_THRESHOLD_S: return HeartbeatStatus.DEAD          |
|                                                                           |
| if elapsed \> self.STALE_THRESHOLD_S: return HeartbeatStatus.STALE        |
|                                                                           |
| return HeartbeatStatus.ALIVE                                              |
|                                                                           |
| class HeartbeatStatus(str, Enum):                                         |
|                                                                           |
| ALIVE = \'alive\'                                                         |
|                                                                           |
| STALE = \'stale\' \# pulse ada tapi sudah lama                            |
|                                                                           |
| DEAD = \'dead\' \# tidak ada pulse → trigger restart                      |
+---------------------------------------------------------------------------+

**5.2 health_restart.py --- Respon External**

+------------------------------------------------------------------------------+
| \# automation/health_restart.py                                              |
|                                                                              |
| \# Berjalan sebagai PROCESS TERPISAH dari agent.                             |
|                                                                              |
| \# Cek heartbeat setiap 60 detik.                                            |
|                                                                              |
| async def monitor_and_restart():                                             |
|                                                                              |
| while True:                                                                  |
|                                                                              |
| await asyncio.sleep(60)                                                      |
|                                                                              |
| status = read_heartbeat_from_db(DB_PATH)                                     |
|                                                                              |
| if status == HeartbeatStatus.DEAD:                                           |
|                                                                              |
| log.critical(\'Agent DEAD --- restarting\')                                  |
|                                                                              |
| send_alert(\'Agent tidak merespons, me-restart otomatis\')                   |
|                                                                              |
| await restart_agent() \# docker restart / systemctl restart                  |
|                                                                              |
| elif status == HeartbeatStatus.STALE:                                        |
|                                                                              |
| log.warning(\'Agent STALE --- memantau\')                                    |
|                                                                              |
| send_alert(\'Agent pulse lambat --- investigasi diperlukan\')                |
|                                                                              |
| async def restart_agent():                                                   |
|                                                                              |
| \# Gunakan subprocess untuk restart via Docker atau systemd                  |
|                                                                              |
| import subprocess                                                            |
|                                                                              |
| subprocess.run(\[\'docker\', \'restart\', \'crypto_ai_agent\'\], check=True) |
|                                                                              |
| await asyncio.sleep(30) \# beri waktu startup                                |
+------------------------------------------------------------------------------+

**6. health_check.py --- Kesehatan Komponen**

Mengecek setiap komponen sistem secara individual. Berbeda dari heartbeat yang hanya membuktikan agent hidup --- health_check memberikan detail tentang komponen mana yang bermasalah.

**6.1 Semua Check yang Dilakukan**

  -----------------------------------------------------------------------------------------------------------
  **Komponen**         **Cara Check**                     **Threshold DEGRADED**    **Threshold UNHEALTHY**
  -------------------- ---------------------------------- ------------------------- -------------------------
  WebSocket stream     Last message age                   \> 30 detik tanpa pesan   \> 60 detik

  Exchange REST ping   GET /api/v3/ping latency           \> 1000ms                 \> 3000ms

  Database state.db    SELECT 1 latency                   \> 100ms                  \> 500ms

  Memory (RSS)         process.memory_info().rss          \> 300 MB                 \> 500 MB

  Disk space           shutil.disk_usage(data_dir).free   \< 2 GB free              \< 500 MB free

  Open orders count    store.count_open()                 \> max_positions × 1.5    \> max_positions × 2

  Circuit breaker      circuit_breaker.state              WARNED                    HALTED

  Rate limit usage     rate_limiter.is_near_limit()       \> 70% weight             \> 90% weight
  -----------------------------------------------------------------------------------------------------------

**6.2 HealthReport Dataclass**

+-----------------------------------------------------------------+
| class ComponentStatus(str, Enum):                               |
|                                                                 |
| HEALTHY = \'healthy\'                                           |
|                                                                 |
| DEGRADED = \'degraded\' \# masih jalan tapi tidak optimal       |
|                                                                 |
| UNHEALTHY = \'unhealthy\' \# perlu intervensi                   |
|                                                                 |
| \@dataclass                                                     |
|                                                                 |
| class ComponentHealth:                                          |
|                                                                 |
| name: str                                                       |
|                                                                 |
| status: ComponentStatus                                         |
|                                                                 |
| value: float \| str \# nilai yang diukur                        |
|                                                                 |
| message: str                                                    |
|                                                                 |
| latency_ms: float = 0.0                                         |
|                                                                 |
| \@dataclass                                                     |
|                                                                 |
| class HealthReport:                                             |
|                                                                 |
| overall: ComponentStatus \# status terburuk dari semua komponen |
|                                                                 |
| components: list\[ComponentHealth\]                             |
|                                                                 |
| timestamp: datetime                                             |
|                                                                 |
| \@property                                                      |
|                                                                 |
| def is_healthy(self) -\> bool:                                  |
|                                                                 |
| return self.overall == ComponentStatus.HEALTHY                  |
|                                                                 |
| \@property                                                      |
|                                                                 |
| def unhealthy_components(self) -\> list\[str\]:                 |
|                                                                 |
| return \[c.name for c in self.components                        |
|                                                                 |
| if c.status == ComponentStatus.UNHEALTHY\]                      |
+-----------------------------------------------------------------+

**6.3 Interface Publik**

+----------------------------------------------------------------------+
| class HealthCheck:                                                   |
|                                                                      |
| async def run(self) -\> HealthReport:                                |
|                                                                      |
| \"\"\"                                                               |
|                                                                      |
| Jalankan semua check paralel (asyncio.gather).                       |
|                                                                      |
| Timeout per check: 5 detik.                                          |
|                                                                      |
| Overall status = status terburuk dari semua komponen.                |
|                                                                      |
| \"\"\"                                                               |
|                                                                      |
| checks = await asyncio.gather(                                       |
|                                                                      |
| self.\_check_websocket(),                                            |
|                                                                      |
| self.\_check_exchange_ping(),                                        |
|                                                                      |
| self.\_check_database(),                                             |
|                                                                      |
| self.\_check_memory(),                                               |
|                                                                      |
| self.\_check_disk(),                                                 |
|                                                                      |
| self.\_check_open_orders(),                                          |
|                                                                      |
| self.\_check_circuit_breaker(),                                      |
|                                                                      |
| self.\_check_rate_limit(),                                           |
|                                                                      |
| return_exceptions=True,                                              |
|                                                                      |
| )                                                                    |
|                                                                      |
| components = \[\]                                                    |
|                                                                      |
| for check in checks:                                                 |
|                                                                      |
| if isinstance(check, Exception):                                     |
|                                                                      |
| components.append(ComponentHealth(                                   |
|                                                                      |
| name=\'unknown\', status=ComponentStatus.UNHEALTHY,                  |
|                                                                      |
| value=\'error\', message=str(check)))                                |
|                                                                      |
| else:                                                                |
|                                                                      |
| components.append(check)                                             |
|                                                                      |
| statuses = \[c.status for c in components\]                          |
|                                                                      |
| if ComponentStatus.UNHEALTHY in statuses:                            |
|                                                                      |
| overall = ComponentStatus.UNHEALTHY                                  |
|                                                                      |
| elif ComponentStatus.DEGRADED in statuses:                           |
|                                                                      |
| overall = ComponentStatus.DEGRADED                                   |
|                                                                      |
| else:                                                                |
|                                                                      |
| overall = ComponentStatus.HEALTHY                                    |
|                                                                      |
| return HealthReport(overall=overall,                                 |
|                                                                      |
| components=components,                                               |
|                                                                      |
| timestamp=utcnow())                                                  |
|                                                                      |
| def get_last_report(self) -\> HealthReport \| None:                  |
|                                                                      |
| \"\"\"Return report terakhir dari cache (tanpa re-run checks).\"\"\" |
+----------------------------------------------------------------------+

**7. connectivity.py & alerts.py**

**7.1 connectivity.py --- Cek Jaringan**

+------------------------------------------------------------------------------+
| class ConnectivityChecker:                                                   |
|                                                                              |
| ENDPOINTS = {                                                                |
|                                                                              |
| \'binance_api\': \'https://api.binance.com/api/v3/ping\',                    |
|                                                                              |
| \'binance_fapi\': \'https://fapi.binance.com/fapi/v1/ping\',                 |
|                                                                              |
| \'internet\': \'https://httpbin.org/get\',                                   |
|                                                                              |
| }                                                                            |
|                                                                              |
| async def check_all(self) -\> dict\[str, ConnStatus\]:                       |
|                                                                              |
| \"\"\"                                                                       |
|                                                                              |
| Return dict status per endpoint.                                             |
|                                                                              |
| ConnStatus: UP \| SLOW \| DOWN                                               |
|                                                                              |
| SLOW = response \> 2000ms                                                    |
|                                                                              |
| DOWN = timeout atau error                                                    |
|                                                                              |
| \"\"\"                                                                       |
|                                                                              |
| async def is_exchange_reachable(self, exchange: str = \'binance\') -\> bool: |
|                                                                              |
| \"\"\"Quick check --- digunakan oleh health_check.\"\"\"                     |
|                                                                              |
| async def get_server_time_drift(self) -\> float:                             |
|                                                                              |
| \"\"\"                                                                       |
|                                                                              |
| Bandingkan waktu lokal dengan waktu server exchange.                         |
|                                                                              |
| Drift \> 1000ms bisa menyebabkan order ditolak (-1021).                      |
|                                                                              |
| Return drift dalam milidetik.                                                |
|                                                                              |
| \"\"\"                                                                       |
+------------------------------------------------------------------------------+

**7.2 alerts.py --- Pengiriman Alert**

Layer tipis yang memastikan setiap alert memiliki format standar dan di-route ke channel yang tepat sesuai severity.

+--------------------------------------------------------------------+
| class AlertSeverity(str, Enum):                                    |
|                                                                    |
| INFO = \'info\'                                                    |
|                                                                    |
| WARNING = \'warning\'                                              |
|                                                                    |
| CRITICAL = \'critical\' \# bangunkan operator                      |
|                                                                    |
| \@dataclass                                                        |
|                                                                    |
| class Alert:                                                       |
|                                                                    |
| severity: AlertSeverity                                            |
|                                                                    |
| title: str                                                         |
|                                                                    |
| message: str                                                       |
|                                                                    |
| component: str \# komponen yang mengirim                           |
|                                                                    |
| data: dict = None \# data tambahan (equity, trade_id, dll)         |
|                                                                    |
| timestamp: datetime = None                                         |
|                                                                    |
| class AlertManager:                                                |
|                                                                    |
| async def send(self, alert: Alert) -\> None:                       |
|                                                                    |
| \"\"\"                                                             |
|                                                                    |
| Route berdasarkan severity:                                        |
|                                                                    |
| INFO → Telegram saja                                               |
|                                                                    |
| WARNING → Telegram + Discord                                       |
|                                                                    |
| CRITICAL → Telegram + Discord + log ke errors.log                  |
|                                                                    |
| \"\"\"                                                             |
|                                                                    |
| async def send_circuit_breaker(                                    |
|                                                                    |
| self, state: \'CircuitState\', reasons: list\[str\], equity: float |
|                                                                    |
| ) -\> None:                                                        |
|                                                                    |
| \"\"\"Template untuk circuit breaker alert.\"\"\"                  |
|                                                                    |
| async def send_trade_opened(                                       |
|                                                                    |
| self, trade: \'Trade\'                                             |
|                                                                    |
| ) -\> None:                                                        |
|                                                                    |
| \"\"\"Template untuk notifikasi trade baru.\"\"\"                  |
|                                                                    |
| async def send_daily_summary(                                      |
|                                                                    |
| self, stats: dict                                                  |
|                                                                    |
| ) -\> None:                                                        |
|                                                                    |
| \"\"\"Ringkasan harian: PnL, win rate, jumlah trade, equity.\"\"\" |
+--------------------------------------------------------------------+

**7.3 Format Alert Message --- Template**

  ------------------------------------------------------------------------------------------------------------------------------------
  **Tipe Alert**        **Isi Wajib**                                         **Contoh Format**
  --------------------- ----------------------------------------------------- --------------------------------------------------------
  Trade Opened          Symbol, side, qty, entry, SL, TP, confidence          BUY BTCUSDT \| 0.001 @ 50000 \| SL: 49000 \| TP: 52000

  Trade Closed (win)    Symbol, PnL USD, PnL%, hold candles, exit reason      CLOSED BTCUSDT +\$150 (+3.00%) \| 12 candles \| TP

  Trade Closed (loss)   Symbol, PnL USD, PnL%, hold candles, exit reason      CLOSED BTCUSDT -\$100 (-2.00%) \| 8 candles \| SL

  Circuit Breaker       State, alasan, daily PnL, equity, waktu halt          HALT \| Daily loss -5.2% \| Equity: \$9,480 \| 60 min

  System Error          Komponen, error message, stack trace (shortened)      ERROR execution_layer \| TimeoutError \| retrying\...

  Daily Summary         Total trades, win rate, PnL, equity, open positions   Day +\$234 \| 4 trades \| 75% WR \| Eq: \$10,234
  ------------------------------------------------------------------------------------------------------------------------------------

**BAGIAN C --- Sync Layer**

Sync Layer adalah mekanisme yang menjaga internal state agent selalu konsisten dengan kondisi aktual di exchange. Exchange adalah source of truth --- internal state harus mengikuti exchange, bukan sebaliknya.

  -----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------
  **PRINSIP:** Jika ada konflik antara internal state dan exchange state, SELALU update internal mengikuti exchange. Jangan pernah kirim koreksi ke exchange berdasarkan asumsi internal.

  -----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------

**8. balance_sync.py --- Sinkronisasi Saldo**

**8.1 Cara Kerja**

+------------------------------------------------------------------------------------------+
| class BalanceSync:                                                                       |
|                                                                                          |
| MAX_DRIFT_PCT = 0.01 \# 1% perbedaan masih toleransi                                     |
|                                                                                          |
| ALERT_DRIFT_PCT= 0.05 \# 5% → alert kritis                                               |
|                                                                                          |
| async def run(self) -\> \'BalanceSyncReport\':                                           |
|                                                                                          |
| \"\"\"                                                                                   |
|                                                                                          |
| Dipanggil scheduler setiap 60 detik.                                                     |
|                                                                                          |
| 1\. Fetch balance aktual dari exchange REST API                                          |
|                                                                                          |
| 2\. Bandingkan dengan internal tracking di capital_manager                               |
|                                                                                          |
| 3\. Update capital_manager dengan nilai aktual                                           |
|                                                                                          |
| 4\. Hitung drift --- jika \> threshold → alert                                           |
|                                                                                          |
| \"\"\"                                                                                   |
|                                                                                          |
| actual = await self.\_exchange.get_balance(\'USDT\')                                     |
|                                                                                          |
| internal = self.\_capital.get_status().current_equity                                    |
|                                                                                          |
| drift_pct = abs(actual - internal) / max(internal, 1.0)                                  |
|                                                                                          |
| if drift_pct \> self.ALERT_DRIFT_PCT:                                                    |
|                                                                                          |
| await self.\_alerts.send(Alert(                                                          |
|                                                                                          |
| severity = AlertSeverity.CRITICAL,                                                       |
|                                                                                          |
| title = \'Balance drift signifikan\',                                                    |
|                                                                                          |
| message = f\'Internal: {internal:.2f}, Exchange: {actual:.2f}, Drift: {drift_pct:.1%}\', |
|                                                                                          |
| component = \'balance_sync\',                                                            |
|                                                                                          |
| ))                                                                                       |
|                                                                                          |
| \# Update internal dengan nilai aktual (exchange is truth)                               |
|                                                                                          |
| self.\_capital.update_from_exchange(actual)                                              |
|                                                                                          |
| return BalanceSyncReport(                                                                |
|                                                                                          |
| internal_before = internal,                                                              |
|                                                                                          |
| exchange_actual = actual,                                                                |
|                                                                                          |
| drift_pct = drift_pct,                                                                   |
|                                                                                          |
| updated = True,                                                                          |
|                                                                                          |
| timestamp = utcnow(),                                                                    |
|                                                                                          |
| )                                                                                        |
+------------------------------------------------------------------------------------------+

**8.2 BalanceSyncReport**

+--------------------------------------------------------+
| \@dataclass                                            |
|                                                        |
| class BalanceSyncReport:                               |
|                                                        |
| internal_before: float \# equity internal sebelum sync |
|                                                        |
| exchange_actual: float \# balance aktual dari exchange |
|                                                        |
| drift_pct: float \# persentase perbedaan               |
|                                                        |
| updated: bool \# True jika internal diupdate           |
|                                                        |
| alert_sent: bool \# True jika drift \> ALERT_DRIFT_PCT |
|                                                        |
| timestamp: datetime                                    |
+--------------------------------------------------------+

**9. order_sync.py --- Sinkronisasi Status Order**

Memastikan status semua open order di internal DB sesuai dengan status di exchange. Mendeteksi order yang filled, cancelled, atau rejected tanpa notifikasi WebSocket.

**9.1 Cara Kerja**

+-----------------------------------------------------------------------+
| class OrderSync:                                                      |
|                                                                       |
| async def run(self) -\> \'OrderSyncReport\':                          |
|                                                                       |
| \"\"\"                                                                |
|                                                                       |
| Dipanggil scheduler setiap 30 detik.                                  |
|                                                                       |
| Focus pada trade dengan status SUBMITTED atau PARTIAL.                |
|                                                                       |
| \"\"\"                                                                |
|                                                                       |
| pending_trades = self.\_store.get_all_active()                        |
|                                                                       |
| submitted = \[t for t in pending_trades                               |
|                                                                       |
| if t.status in (TradeStatus.SUBMITTED, TradeStatus.PARTIAL)\]         |
|                                                                       |
| updates = \[\]                                                        |
|                                                                       |
| conflicts = \[\]                                                      |
|                                                                       |
| for trade in submitted:                                               |
|                                                                       |
| try:                                                                  |
|                                                                       |
| ex_resp = await self.\_exchange.get_order_status(                     |
|                                                                       |
| trade.symbol, trade.client_order_id                                   |
|                                                                       |
| )                                                                     |
|                                                                       |
| except OrderNotFoundError:                                            |
|                                                                       |
| conflicts.append(f\'Order tidak ditemukan: {trade.client_order_id}\') |
|                                                                       |
| continue                                                              |
|                                                                       |
| \# Reconcile                                                          |
|                                                                       |
| if ex_resp.status == \'FILLED\' and trade.status != TradeStatus.OPEN: |
|                                                                       |
| self.\_manager.confirm(trade, ex_resp)                                |
|                                                                       |
| updates.append(f\'SYNCED FILLED: {trade.trade_id}\')                  |
|                                                                       |
| elif ex_resp.status == \'CANCELLED\':                                 |
|                                                                       |
| self.\_store.update_status(trade.trade_id, TradeStatus.CANCELLED)     |
|                                                                       |
| updates.append(f\'SYNCED CANCELLED: {trade.trade_id}\')               |
|                                                                       |
| elif ex_resp.status == \'PARTIALLY_FILLED\':                          |
|                                                                       |
| self.\_store.update_status(                                           |
|                                                                       |
| trade.trade_id, TradeStatus.PARTIAL,                                  |
|                                                                       |
| filled_qty=ex_resp.filled_qty,                                        |
|                                                                       |
| avg_fill_price=ex_resp.avg_price,                                     |
|                                                                       |
| )                                                                     |
|                                                                       |
| updates.append(f\'SYNCED PARTIAL: {trade.trade_id}\')                 |
|                                                                       |
| return OrderSyncReport(                                               |
|                                                                       |
| checked = len(submitted),                                             |
|                                                                       |
| updated = len(updates),                                               |
|                                                                       |
| conflicts = conflicts,                                                |
|                                                                       |
| timestamp = utcnow(),                                                 |
|                                                                       |
| )                                                                     |
+-----------------------------------------------------------------------+

**10. position_sync.py --- Rekonsiliasi Posisi**

Komponen paling kritis di sync layer. Membandingkan posisi aktual di exchange dengan posisi yang agent yakini terbuka. Ghost position (ada di exchange tapi tidak di internal) adalah kondisi berbahaya.

**10.1 Cara Kerja**

+------------------------------------------------------------------------+
| class PositionSync:                                                    |
|                                                                        |
| async def run(self) -\> \'PositionSyncReport\':                        |
|                                                                        |
| \"\"\"                                                                 |
|                                                                        |
| Dipanggil scheduler setiap 60 detik.                                   |
|                                                                        |
| Untuk futures: bisa fetch posisi real dari exchange.                   |
|                                                                        |
| Untuk spot: derive dari order history (tidak ada posisi API di spot).  |
|                                                                        |
| \"\"\"                                                                 |
|                                                                        |
| internal_positions = {                                                 |
|                                                                        |
| t.symbol: t                                                            |
|                                                                        |
| for t in self.\_store.get_open_trades()                                |
|                                                                        |
| }                                                                      |
|                                                                        |
| \# Fetch posisi dari exchange (futures only)                           |
|                                                                        |
| if self.\_config.market_type == \'futures\':                           |
|                                                                        |
| exchange_positions = await self.\_get_futures_positions()              |
|                                                                        |
| else:                                                                  |
|                                                                        |
| exchange_positions = await self.\_derive_spot_positions()              |
|                                                                        |
| discrepancies = \[\]                                                   |
|                                                                        |
| \# Cek ghost positions: ada di exchange, tidak di internal             |
|                                                                        |
| for symbol, ex_pos in exchange_positions.items():                      |
|                                                                        |
| if symbol not in internal_positions:                                   |
|                                                                        |
| discrepancies.append(Discrepancy(                                      |
|                                                                        |
| type = \'GHOST_POSITION\',                                             |
|                                                                        |
| symbol = symbol,                                                       |
|                                                                        |
| internal= None,                                                        |
|                                                                        |
| exchange= ex_pos,                                                      |
|                                                                        |
| severity= \'CRITICAL\',                                                |
|                                                                        |
| ))                                                                     |
|                                                                        |
| \# Cek zombie positions: ada di internal, tidak di exchange            |
|                                                                        |
| for symbol, int_pos in internal_positions.items():                     |
|                                                                        |
| if symbol not in exchange_positions:                                   |
|                                                                        |
| discrepancies.append(Discrepancy(                                      |
|                                                                        |
| type = \'ZOMBIE_POSITION\',                                            |
|                                                                        |
| symbol = symbol,                                                       |
|                                                                        |
| internal= int_pos,                                                     |
|                                                                        |
| exchange= None,                                                        |
|                                                                        |
| severity= \'HIGH\',                                                    |
|                                                                        |
| ))                                                                     |
|                                                                        |
| \# Cek qty mismatch                                                    |
|                                                                        |
| for symbol in (set(internal_positions) & set(exchange_positions)):     |
|                                                                        |
| int_qty = internal_positions\[symbol\].filled_qty                      |
|                                                                        |
| ex_qty = exchange_positions\[symbol\].qty                              |
|                                                                        |
| if abs(int_qty - ex_qty) / max(int_qty, 1e-8) \> 0.01: \# 1% tolerance |
|                                                                        |
| discrepancies.append(Discrepancy(                                      |
|                                                                        |
| type = \'QTY_MISMATCH\',                                               |
|                                                                        |
| symbol = symbol,                                                       |
|                                                                        |
| internal= int_pos,                                                     |
|                                                                        |
| exchange= ex_pos,                                                      |
|                                                                        |
| severity= \'MEDIUM\',                                                  |
|                                                                        |
| ))                                                                     |
|                                                                        |
| if discrepancies:                                                      |
|                                                                        |
| await self.\_handle_discrepancies(discrepancies)                       |
|                                                                        |
| return PositionSyncReport(                                             |
|                                                                        |
| internal_count = len(internal_positions),                              |
|                                                                        |
| exchange_count = len(exchange_positions),                              |
|                                                                        |
| discrepancies = discrepancies,                                         |
|                                                                        |
| timestamp = utcnow(),                                                  |
|                                                                        |
| )                                                                      |
+------------------------------------------------------------------------+

**10.2 Discrepancy Types & Handling**

  ----------------------------------------------------------------------------------------------------------------------------------------------------------------------
  **Tipe**              **Kondisi**                          **Severity**   **Aksi Otomatis**                         **Aksi Manual**
  --------------------- ------------------------------------ -------------- ----------------------------------------- --------------------------------------------------
  **GHOST_POSITION**    Ada di exchange, tidak di internal   CRITICAL       Alert + masuk cautious mode               Investigasi: close manual di exchange jika perlu

  **ZOMBIE_POSITION**   Ada di internal, tidak di exchange   HIGH           Alert + mark trade sebagai CLOSED         Cek apakah sudah di-close tanpa notifikasi

  **QTY_MISMATCH**      Qty berbeda \> 1%                    MEDIUM         Alert + update internal ke exchange qty   Investigasi partial fill yang tidak tercatat

  **SIDE_MISMATCH**     Sisi BUY/SELL berbeda                CRITICAL       Emergency stop                            Bug serius --- butuh review kode
  ----------------------------------------------------------------------------------------------------------------------------------------------------------------------

**11. reconciliation.py --- Audit Harian Penuh**

Full audit yang berjalan sekali sehari (UTC 00:30). Memeriksa konsistensi menyeluruh antara semua database internal dan exchange, lalu menghasilkan laporan yang disimpan ke disk.

**11.1 Ruang Lingkup Rekonsiliasi**

  ---------------------------------------------------------------------------------------------------------------------------
  **Yang Diperiksa**         **Sumber Internal**              **Sumber Exchange**         **Aksi Jika Berbeda**
  -------------------------- -------------------------------- --------------------------- -----------------------------------
  Saldo USDT                 capital_manager.current_equity   GET /api/v3/account         Update internal, alert jika \> 1%

  Jumlah posisi terbuka      store.get_open_trades()          GET open positions          Handle ghost/zombie position

  PnL hari ini               performance.db daily_pnl         Hitung dari closed trades   Alert jika berbeda \> 0.1%

  Total commission dibayar   experience.db sum(commission)    Trade history exchange      Log untuk reconciliation report

  Order yang menggantung     order_registry status=pending    GET open orders             Cancel jika \> 1 hari pending
  ---------------------------------------------------------------------------------------------------------------------------

**11.2 ReconciliationReport**

+-------------------------------------------------+
| \@dataclass                                     |
|                                                 |
| class ReconciliationReport:                     |
|                                                 |
| date: str \# tanggal rekonsiliasi (UTC)         |
|                                                 |
| \# Balance                                      |
|                                                 |
| balance_internal: float                         |
|                                                 |
| balance_exchange: float                         |
|                                                 |
| balance_drift_pct: float                        |
|                                                 |
| \# Positions                                    |
|                                                 |
| open_internal: int                              |
|                                                 |
| open_exchange: int                              |
|                                                 |
| position_discrepancies: list\[\'Discrepancy\'\] |
|                                                 |
| \# PnL                                          |
|                                                 |
| daily_pnl_internal: float                       |
|                                                 |
| daily_pnl_computed: float                       |
|                                                 |
| pnl_drift: float                                |
|                                                 |
| \# Commission                                   |
|                                                 |
| total_commission: float                         |
|                                                 |
| total_trades_today: int                         |
|                                                 |
| win_rate_today: float                           |
|                                                 |
| \# Status                                       |
|                                                 |
| all_ok: bool                                    |
|                                                 |
| issues: list\[str\]                             |
|                                                 |
| warnings: list\[str\]                           |
|                                                 |
| duration_s: float                               |
|                                                 |
| timestamp: datetime                             |
+-------------------------------------------------+

**11.3 Penyimpanan & Distribusi Report**

+-------------------------------------------------------------------+
| async def run_daily(self) -\> ReconciliationReport:               |
|                                                                   |
| \"\"\"                                                            |
|                                                                   |
| Dipanggil scheduler UTC 00:30 (30 menit setelah reset harian).    |
|                                                                   |
| Report disimpan ke tiga tempat:                                   |
|                                                                   |
| 1\. audit/trade_history.db --- untuk query jangka panjang         |
|                                                                   |
| 2\. logs/reconciliation\_{date}.json --- untuk arsip mudah dibaca |
|                                                                   |
| 3\. Dikirim via Telegram jika ada issues                          |
|                                                                   |
| \"\"\"                                                            |
|                                                                   |
| report = await self.\_run_all_checks()                            |
|                                                                   |
| \# Simpan ke audit DB                                             |
|                                                                   |
| self.\_audit_db.insert_reconciliation(report)                     |
|                                                                   |
| \# Simpan sebagai JSON                                            |
|                                                                   |
| path = f\'logs/reconciliation\_{report.date}.json\'               |
|                                                                   |
| with open(path, \'w\') as f:                                      |
|                                                                   |
| json.dump(dataclasses.asdict(report), f, default=str, indent=2)   |
|                                                                   |
| \# Alert jika ada masalah                                         |
|                                                                   |
| if not report.all_ok:                                             |
|                                                                   |
| await self.\_alerts.send(Alert(                                   |
|                                                                   |
| severity = AlertSeverity.WARNING,                                 |
|                                                                   |
| title = f\'Rekonsiliasi {report.date}: ada isu\',                 |
|                                                                   |
| message = \'\\n\'.join(report.issues),                            |
|                                                                   |
| component = \'reconciliation\',                                   |
|                                                                   |
| ))                                                                |
|                                                                   |
| return report                                                     |
+-------------------------------------------------------------------+

**12. notification/ --- Telegram & Discord**

**12.1 notifier.py --- Routing Terpusat**

+-----------------------------------------------------------------------+
| class Notifier:                                                       |
|                                                                       |
| \"\"\"                                                                |
|                                                                       |
| Abstraksi yang merutekan notifikasi ke channel yang tepat.            |
|                                                                       |
| Layer lain tidak perlu tahu apakah menggunakan Telegram atau Discord. |
|                                                                       |
| \"\"\"                                                                |
|                                                                       |
| def \_\_init\_\_(                                                     |
|                                                                       |
| self,                                                                 |
|                                                                       |
| telegram: \'TelegramNotifier\',                                       |
|                                                                       |
| discord: \'DiscordNotifier\',                                         |
|                                                                       |
| config: AgentConfig,                                                  |
|                                                                       |
| ): \...                                                               |
|                                                                       |
| async def notify(                                                     |
|                                                                       |
| self,                                                                 |
|                                                                       |
| event_type: str,                                                      |
|                                                                       |
| data: dict,                                                           |
|                                                                       |
| ) -\> None:                                                           |
|                                                                       |
| \"\"\"                                                                |
|                                                                       |
| Routing berdasarkan event_type dan config:                            |
|                                                                       |
| notify_trade_open → Telegram                                          |
|                                                                       |
| notify_trade_close → Telegram                                         |
|                                                                       |
| notify_circuit_break → Telegram + Discord                             |
|                                                                       |
| notify_daily_summary → Telegram                                       |
|                                                                       |
| CRITICAL severity → Telegram + Discord                                |
|                                                                       |
| \"\"\"                                                                |
+-----------------------------------------------------------------------+

**12.2 telegram.py --- Format Pesan**

+---------------------------------------------------------------------------+
| class TelegramNotifier:                                                   |
|                                                                           |
| MAX_MESSAGE_LEN = 4096 \# Telegram limit                                  |
|                                                                           |
| RETRY_ON_FAIL = 3                                                         |
|                                                                           |
| async def send(self, message: str, parse_mode: str = \'HTML\') -\> bool:  |
|                                                                           |
| \"\"\"                                                                    |
|                                                                           |
| Kirim pesan ke Telegram.                                                  |
|                                                                           |
| Jika gagal: retry 3x dengan delay 5s.                                     |
|                                                                           |
| Return True jika berhasil.                                                |
|                                                                           |
| \"\"\"                                                                    |
|                                                                           |
| \# Template untuk setiap event:                                           |
|                                                                           |
| def format_trade_opened(self, trade: \'Trade\') -\> str:                  |
|                                                                           |
| emoji = \'🟢\' if trade.side == \'BUY\' else \'🔴\'                       |
|                                                                           |
| return (                                                                  |
|                                                                           |
| f\'{emoji} \<b\>Trade Dibuka\</b\>\\n\'                                   |
|                                                                           |
| f\'Simbol: \<code\>{trade.symbol}\</code\>\\n\'                           |
|                                                                           |
| f\'Side: {trade.side} \| Qty: {trade.filled_qty}\\n\'                     |
|                                                                           |
| f\'Entry: {trade.avg_fill_price:,.2f} USDT\\n\'                           |
|                                                                           |
| f\'SL: {trade.sl_price:,.2f} \| TP: {trade.tp_price:,.2f}\\n\'            |
|                                                                           |
| f\'Risk: {trade.risk_amount_usd:.2f} USDT\\n\'                            |
|                                                                           |
| f\'Mode: \[{trade.mode.upper()}\]\'                                       |
|                                                                           |
| )                                                                         |
|                                                                           |
| def format_trade_closed(self, trade: \'Trade\') -\> str:                  |
|                                                                           |
| pnl_emoji = \'✅\' if trade.pnl_usd \>= 0 else \'❌\'                     |
|                                                                           |
| return (                                                                  |
|                                                                           |
| f\'{pnl_emoji} \<b\>Trade Ditutup\</b\>\\n\'                              |
|                                                                           |
| f\'Simbol: \<code\>{trade.symbol}\</code\>\\n\'                           |
|                                                                           |
| f\'PnL: \<b\>{trade.pnl_usd:+.2f} USDT ({trade.pnl_pct:+.2f}%)\</b\>\\n\' |
|                                                                           |
| f\'Exit: {trade.exit_reason} @ {trade.exit_price:,.2f}\\n\'               |
|                                                                           |
| f\'Equity: {self.\_equity:.2f} USDT\'                                     |
|                                                                           |
| )                                                                         |
+---------------------------------------------------------------------------+

**13. Integrasi --- Dependency Map Lengkap**

**13.1 Dependency Antar File**

  -----------------------------------------------------------------------------------------------------------------------------
  **File**            **Import Dari**                                     **Dipanggil Oleh**             **Frekuensi**
  ------------------- --------------------------------------------------- ------------------------------ ----------------------
  exit_manager.py     trailing, take_profit, break_even, strategy         main_loop per trade per tick   Setiap tick

  trailing.py         --- (pure state)                                    exit_manager.py                Setiap tick

  take_profit.py      --- (pure state)                                    exit_manager.py                Setiap tick

  break_even.py       --- (pure state)                                    exit_manager.py                Setiap tick

  heartbeat.py        DB (heartbeat.db), metrics                          main.py startup                Setiap 30 detik

  health_check.py     exchange, store, circuit_breaker, rate_limiter      scheduler setiap 30 detik      Setiap 30 detik

  connectivity.py     aiohttp                                             health_check.py                Per health_check run

  alerts.py           telegram.py, discord.py                             monitoring, sync, main         On-demand

  balance_sync.py     exchange.get_balance, capital_manager               scheduler setiap 60 detik      Setiap 60 detik

  order_sync.py       exchange.get_order_status, store                    scheduler setiap 30 detik      Setiap 30 detik

  position_sync.py    exchange.get_position, store                        scheduler setiap 60 detik      Setiap 60 detik

  reconciliation.py   balance_sync, order_sync, position_sync, audit_db   scheduler harian UTC 00:30     Harian
  -----------------------------------------------------------------------------------------------------------------------------

**14. Checklist Implementasi**

**14.1 Checklist Exit Layer**

  ---------------------------------------------------------------------------------------------------------------------------------------------------
  **No**   **Item**                                                          **Verifikasi**                                                **Done**
  -------- ----------------------------------------------------------------- ------------------------------------------------------------- ----------
  1        SL check menggunakan candle high/low, bukan hanya close price     Unit test: inject candle dengan low \< SL → EXIT_SL           ☐

  2        Trailing SL hanya naik --- tidak pernah turun (ratchet)           Test: harga naik lalu turun → trail tidak ikut turun          ☐

  3        Trailing tidak aktif sebelum TRAIL_ACTIVATION_R tercapai          Test: profit 0.5R, activation=1.0R → trailing belum aktif     ☐

  4        Partial TP: SL digeser ke entry setelah partial close             Test: TP1 hit → SL_UPDATED ke avg_fill_price                  ☐

  5        Breakeven tidak menggeser SL ke belakang (hanya maju)             Test: SL sudah di atas entry → breakeven tidak ubah SL        ☐

  6        Timeout exit berjalan setelah MAX_HOLD_CANDLES terlampaui         Test: mock hold_candles \> 48 → EXIT_TIMEOUT                  ☐

  7        evaluate() adalah pure function --- tidak ada I/O                 grep \'await\\\|open(\\\|sqlite\' exit_layer/\*.py → kosong   ☐

  8        register_open_positions() dipanggil saat startup untuk recovery   Code review main.py T-08 → exit_mgr.register(open_trades)     ☐
  ---------------------------------------------------------------------------------------------------------------------------------------------------

**14.2 Checklist Monitoring Layer**

  -------------------------------------------------------------------------------------------------------------------------------------------
  **No**   **Item**                                                        **Verifikasi**                                          **Done**
  -------- --------------------------------------------------------------- ------------------------------------------------------- ----------
  9        Heartbeat pulse berjalan setiap 30 detik tanpa interrupt        Log heartbeat.db timestamp --- interval konsisten       ☐

  10       health_restart.py berjalan sebagai process terpisah             ps aux \| grep health_restart → ada dua process         ☐

  11       health_check.run() paralel menggunakan asyncio.gather           Benchmark: semua 8 check selesai \< 5 detik total       ☐

  12       Alert CRITICAL selalu dikirim sebelum sys.exit()                Test safe_mode.emergency_stop → alert terpanggil dulu   ☐

  13       Server time drift dicek saat startup --- alert jika \> 1000ms   Test: mock server time +2000ms → alert dikirim          ☐
  -------------------------------------------------------------------------------------------------------------------------------------------

**14.3 Checklist Sync Layer**

  ----------------------------------------------------------------------------------------------------------------------------------------------------------------
  **No**   **Item**                                                               **Verifikasi**                                                        **Done**
  -------- ---------------------------------------------------------------------- --------------------------------------------------------------------- ----------
  14       balance_sync selalu update internal dari exchange (tidak sebaliknya)   Code review: capital.update_from_exchange(actual), bukan sebaliknya   ☐

  15       GHOST_POSITION terdeteksi dan trigger alert CRITICAL                   Test: buat posisi di exchange tanpa di internal → ghost terdeteksi    ☐

  16       ZOMBIE_POSITION di-mark CLOSED di internal                             Test: close trade tanpa notifikasi → zombie terdeteksi                ☐

  17       reconciliation.run_daily() menghasilkan JSON report ke disk            Cek file logs/reconciliation\_{date}.json setelah run                 ☐

  18       order_sync tidak retry order yang sudah CANCELLED                      Test: inject cancelled order → tidak re-submit                        ☐

  19       Semua sync task di-schedule oleh scheduler, bukan dipanggil langsung   Code review main.py --- sync dipanggil dari scheduler.is_due          ☐
  ----------------------------------------------------------------------------------------------------------------------------------------------------------------

+:-------------------------------------------------------------------------------------------------------------------:+
| **Dokumen ini adalah kontrak implementasi Exit Layer, Monitoring Layer & Sync Layer.**                              |
|                                                                                                                     |
| Perubahan ExitDecision dataclass, HealthReport format, atau Discrepancy types WAJIB diupdate sebelum merge ke main. |
|                                                                                                                     |
| *Referensi: trade_execution_docs.docx • risk_layer_docs.docx • agent_core_docs.docx*                                |
+---------------------------------------------------------------------------------------------------------------------+
