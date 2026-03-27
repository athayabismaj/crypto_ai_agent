**crypto_ai_agent**

**Dokumentasi: Risk Layer**

*risk_manager · position_size · stoploss · exposure_control*

*leverage_control · circuit_breaker · pre_trade_check*

Versi 1.0 \| Referensi: strategy_portfolio_docs.docx · agent_core_docs.docx

  ------------------------------------------------

  ------------------------------------------------

**1. Overview --- Risk Layer**

Risk Layer adalah gerbang tunggal sebelum order dikirim ke exchange. Setiap trade request WAJIB melewati RiskManager tanpa pengecualian. Tidak ada jalur bypass.

  ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------
  **PRINSIP UTAMA:** Risk Layer tidak membuat keputusan trading. Ia hanya bertanya: \'Apakah trade ini aman untuk dieksekusi sekarang?\' Jika tidak, ia menolak --- tanpa peduli seberapa bagus sinyalnya.

  ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------

**1.1 Filosofi Desain**

> **•** Conservative by default --- saat ragu, tolak.
>
> **•** Paper mode mencatat semua block tapi tidak menghentikan simulasi.
>
> **•** Live mode: block berarti order benar-benar tidak dikirim.
>
> **•** Setiap keputusan block harus bisa di-audit dengan alasan yang jelas.
>
> **•** Tidak ada magic number tersembunyi --- semua threshold di config.

**1.2 Alur Evaluasi --- 7 Lapis**

  -----------------------------------------------------------------------------------------------------------------
  **Lapis**   **Komponen**          **Pertanyaan**                                  **Gagal →**
  ----------- --------------------- ----------------------------------------------- -------------------------------
  L1          pre_trade_check.py    Data valid? Market normal? Symbol benar?        BLOCKED --- data error

  L2          circuit_breaker.py    Trading masih boleh? (daily loss / drawdown)?   BLOCKED --- circuit breaker

  L3          leverage_control.py   Leverage dalam batas yang diizinkan?            BLOCKED --- leverage too high

  L4          exposure_control.py   Portfolio tidak over-exposed?                   BLOCKED --- exposure limit

  L5          position_size.py      Berapa qty yang aman? (Kelly / Fixed / ATR)     qty=0 → BLOCKED

  L6          stoploss.py           SL valid dan dalam range yang wajar?            WARNED (live) / pass (paper)

  L7          risk_manager.py       Semua lulus? Rakit RiskResult.                  APPROVED dengan qty final
  -----------------------------------------------------------------------------------------------------------------

**1.3 RiskResult --- Output Akhir**

+-----------------------------------------------------------------------+
| from enum import Enum                                                 |
|                                                                       |
| from dataclasses import dataclass, field                              |
|                                                                       |
| class RiskVerdict(str, Enum):                                         |
|                                                                       |
| APPROVED = \'approved\'                                               |
|                                                                       |
| WARNED = \'warned\' \# boleh lanjut tapi ada catatan                  |
|                                                                       |
| BLOCKED = \'blocked\' \# order tidak boleh dikirim                    |
|                                                                       |
| \@dataclass                                                           |
|                                                                       |
| class RiskResult:                                                     |
|                                                                       |
| verdict: RiskVerdict                                                  |
|                                                                       |
| approved_quantity: float \# qty final setelah sizing (0 jika BLOCKED) |
|                                                                       |
| risk_amount_usd: float \# estimasi max loss dalam USD                 |
|                                                                       |
| sl_price: float \# SL yang divalidasi / dikalkulasi                   |
|                                                                       |
| tp_price: float \# TP yang divalidasi (0 = tidak ada)                 |
|                                                                       |
| reasons: list\[str\] \# alasan block (kosong jika APPROVED)           |
|                                                                       |
| warnings: list\[str\] \# peringatan (tetap bisa lanjut)               |
|                                                                       |
| sizing_method: str \# metode yang digunakan                           |
|                                                                       |
| timestamp: datetime                                                   |
|                                                                       |
| \@property                                                            |
|                                                                       |
| def is_approved(self) -\> bool:                                       |
|                                                                       |
| return self.verdict != RiskVerdict.BLOCKED                            |
|                                                                       |
| \@property                                                            |
|                                                                       |
| def has_warnings(self) -\> bool:                                      |
|                                                                       |
| return len(self.warnings) \> 0                                        |
+-----------------------------------------------------------------------+

**2. risk_manager.py --- Orchestrator Utama**

Satu titik masuk untuk semua evaluasi risk. Mendelegasikan ke komponen yang tepat dan merakit RiskResult akhir.

**2.1 TradeRequest --- Input**

+---------------------------------------------------------------------+
| \@dataclass                                                         |
|                                                                     |
| class TradeRequest:                                                 |
|                                                                     |
| symbol: str                                                         |
|                                                                     |
| side: str \# \'BUY\' \| \'SELL\'                                    |
|                                                                     |
| quantity: float \# qty yang diminta (bisa di-scale down)            |
|                                                                     |
| price: float \# 0.0 = market order                                  |
|                                                                     |
| suggested_sl: float \# dari Signal.suggested_sl                     |
|                                                                     |
| suggested_tp: float = 0.0                                           |
|                                                                     |
| leverage: int = 1 \# 1 untuk spot                                   |
|                                                                     |
| is_futures: bool = False                                            |
|                                                                     |
| strategy_id: str = \'\'                                             |
|                                                                     |
| client_order_id: str = \'\' \# diisi sebelum dikirim ke idempotency |
|                                                                     |
| signal_confidence:float = 0.0                                       |
|                                                                     |
| \@classmethod                                                       |
|                                                                     |
| def from_signal(                                                    |
|                                                                     |
| cls,                                                                |
|                                                                     |
| signal: \'Signal\',                                                 |
|                                                                     |
| quantity: float,                                                    |
|                                                                     |
| ) -\> \'TradeRequest\':                                             |
|                                                                     |
| return cls(                                                         |
|                                                                     |
| symbol = signal.symbol,                                             |
|                                                                     |
| side = signal.side,                                                 |
|                                                                     |
| quantity = quantity,                                                |
|                                                                     |
| price = signal.suggested_price,                                     |
|                                                                     |
| suggested_sl = signal.suggested_sl,                                 |
|                                                                     |
| suggested_tp = signal.suggested_tp,                                 |
|                                                                     |
| strategy_id = signal.strategy_id,                                   |
|                                                                     |
| signal_confidence= signal.final_confidence,                         |
|                                                                     |
| )                                                                   |
+---------------------------------------------------------------------+

**2.2 Interface Publik**

+---------------------------------------------------------------------+
| class RiskManager:                                                  |
|                                                                     |
| def \_\_init\_\_(self, config: AgentConfig):                        |
|                                                                     |
| self.paper_mode = config.mode != \'live\'                           |
|                                                                     |
| self.pre_trade_check = PreTradeCheck(config)                        |
|                                                                     |
| self.circuit_breaker = CircuitBreaker(config)                       |
|                                                                     |
| self.leverage_control = LeverageControl(config)                     |
|                                                                     |
| self.exposure_control = ExposureControl(config)                     |
|                                                                     |
| self.position_sizer = PositionSizer(config)                         |
|                                                                     |
| self.stoploss_manager = StopLossManager(config)                     |
|                                                                     |
| self.\_daily_pnl = 0.0                                              |
|                                                                     |
| self.\_peak_equity = config.initial_equity                          |
|                                                                     |
| self.\_current_equity = config.initial_equity                       |
|                                                                     |
| def evaluate(                                                       |
|                                                                     |
| self,                                                               |
|                                                                     |
| request: TradeRequest,                                              |
|                                                                     |
| portfolio_state: dict,                                              |
|                                                                     |
| ) -\> RiskResult:                                                   |
|                                                                     |
| \"\"\"                                                              |
|                                                                     |
| Evaluasi penuh satu trade request.                                  |
|                                                                     |
| Satu-satunya fungsi yang perlu dipanggil dari luar.                 |
|                                                                     |
| \"\"\"                                                              |
|                                                                     |
| def update_equity(self, current_equity: float) -\> None:            |
|                                                                     |
| \"\"\"                                                              |
|                                                                     |
| Dipanggil setelah setiap trade close atau sync periodik.            |
|                                                                     |
| Update internal state untuk circuit breaker & drawdown check.       |
|                                                                     |
| \"\"\"                                                              |
|                                                                     |
| def reset_daily_stats(self) -\> None:                               |
|                                                                     |
| \"\"\"Dipanggil scheduler UTC 00:00. Reset daily_pnl counter.\"\"\" |
|                                                                     |
| def get_risk_summary(self) -\> dict:                                |
|                                                                     |
| \"\"\"Snapshot current risk state untuk monitoring.\"\"\"           |
+---------------------------------------------------------------------+

**2.3 evaluate() --- Implementasi Lengkap**

+----------------------------------------------------------------------------------+
| def evaluate(self, request: TradeRequest, portfolio_state: dict) -\> RiskResult: |
|                                                                                  |
| reasons: list\[str\] = \[\]                                                      |
|                                                                                  |
| warnings: list\[str\] = \[\]                                                     |
|                                                                                  |
| \# ── L1: Pre-trade check ─────────────────────────────────                      |
|                                                                                  |
| ok, msg = self.pre_trade_check.validate(request, portfolio_state)                |
|                                                                                  |
| if not ok:                                                                       |
|                                                                                  |
| return self.\_make_result(RiskVerdict.BLOCKED, 0, \[msg\], \[\], request)        |
|                                                                                  |
| \# ── L2: Circuit breaker ─────────────────────────────────                      |
|                                                                                  |
| cb_state = self.circuit_breaker.check(                                           |
|                                                                                  |
| self.\_daily_pnl,                                                                |
|                                                                                  |
| self.\_current_equity,                                                           |
|                                                                                  |
| self.\_peak_equity                                                               |
|                                                                                  |
| )                                                                                |
|                                                                                  |
| if cb_state == CircuitState.HALTED:                                              |
|                                                                                  |
| return self.\_make_result(RiskVerdict.BLOCKED, 0,                                |
|                                                                                  |
| \[\'Circuit breaker HALTED\'\], \[\], request)                                   |
|                                                                                  |
| if cb_state == CircuitState.WARNED:                                              |
|                                                                                  |
| warnings.append(\'Circuit breaker WARNED --- mendekati batas loss\')             |
|                                                                                  |
| \# ── L3: Leverage check (futures only) ───────────────────                      |
|                                                                                  |
| if request.is_futures:                                                           |
|                                                                                  |
| lev_ok, lev_msg = self.leverage_control.validate(                                |
|                                                                                  |
| request.leverage, request.symbol)                                                |
|                                                                                  |
| if not lev_ok:                                                                   |
|                                                                                  |
| return self.\_make_result(RiskVerdict.BLOCKED, 0, \[lev_msg\], \[\], request)    |
|                                                                                  |
| \# ── L4: Exposure check ──────────────────────────────────                      |
|                                                                                  |
| exp_ok, exp_msg = self.exposure_control.validate(request, portfolio_state)       |
|                                                                                  |
| if not exp_ok:                                                                   |
|                                                                                  |
| return self.\_make_result(RiskVerdict.BLOCKED, 0, \[exp_msg\], \[\], request)    |
|                                                                                  |
| \# ── L5: Position sizing ─────────────────────────────────                      |
|                                                                                  |
| sized_qty, size_warnings, risk_usd = self.position_sizer.calculate(              |
|                                                                                  |
| request, self.\_current_equity, portfolio_state)                                 |
|                                                                                  |
| warnings.extend(size_warnings)                                                   |
|                                                                                  |
| if sized_qty \<= 0:                                                              |
|                                                                                  |
| return self.\_make_result(RiskVerdict.BLOCKED, 0,                                |
|                                                                                  |
| \[\'PositionSizer: qty=0 (notional di bawah minimum)\'\], warnings, request)     |
|                                                                                  |
| \# ── L6: Stop loss validation ────────────────────────────                      |
|                                                                                  |
| sl_ok, sl_msg, final_sl = self.stoploss_manager.validate_and_compute(            |
|                                                                                  |
| request, portfolio_state)                                                        |
|                                                                                  |
| if not sl_ok and not self.paper_mode:                                            |
|                                                                                  |
| return self.\_make_result(RiskVerdict.BLOCKED, 0, \[sl_msg\], warnings, request) |
|                                                                                  |
| if not sl_ok:                                                                    |
|                                                                                  |
| warnings.append(sl_msg)                                                          |
|                                                                                  |
| \# ── L7: Rakit RiskResult ────────────────────────────────                      |
|                                                                                  |
| verdict = RiskVerdict.WARNED if warnings else RiskVerdict.APPROVED               |
|                                                                                  |
| result = RiskResult(                                                             |
|                                                                                  |
| verdict = verdict,                                                               |
|                                                                                  |
| approved_quantity = sized_qty,                                                   |
|                                                                                  |
| risk_amount_usd = risk_usd,                                                      |
|                                                                                  |
| sl_price = final_sl,                                                             |
|                                                                                  |
| tp_price = request.suggested_tp,                                                 |
|                                                                                  |
| reasons = reasons,                                                               |
|                                                                                  |
| warnings = warnings,                                                             |
|                                                                                  |
| sizing_method = self.position_sizer.last_method,                                 |
|                                                                                  |
| timestamp = utcnow(),                                                            |
|                                                                                  |
| )                                                                                |
|                                                                                  |
| \# Paper mode: BLOCKED tetap dicatat tapi verdict diubah ke WARNED               |
|                                                                                  |
| if self.paper_mode and verdict == RiskVerdict.BLOCKED:                           |
|                                                                                  |
| result.verdict = RiskVerdict.WARNED                                              |
|                                                                                  |
| result.approved_quantity = sized_qty or request.quantity                         |
|                                                                                  |
| result.warnings = result.reasons + warnings                                      |
|                                                                                  |
| result.reasons = \[\]                                                            |
|                                                                                  |
| self.\_log_result(request, result)                                               |
|                                                                                  |
| return result                                                                    |
+----------------------------------------------------------------------------------+

**3. pre_trade_check.py --- Validasi Data & Market**

Gate paling pertama. Jika data tidak valid atau market dalam kondisi abnormal, tidak perlu melanjutkan ke risk check yang lebih dalam.

**3.1 Semua Validasi yang Dilakukan**

  -------------------------------------------------------------------------------------------------------------------
  **Check**           **Kondisi Valid**                                 **Aksi Jika Gagal**   **Error Code**
  ------------------- ------------------------------------------------- --------------------- -----------------------
  Symbol format       len \> 0 dan len ≤ 20 karakter                    BLOCKED               INVALID_SYMBOL

  Side                \'BUY\' atau \'SELL\' (uppercase)                 BLOCKED               INVALID_SIDE

  Quantity            qty ≥ MIN_QTY (1e-8)                              BLOCKED               INVALID_QTY

  Price               price ≥ 0 (0 = market ok jika diizinkan)          BLOCKED               INVALID_PRICE

  Market order flag   allow_market_order = True jika price = 0          BLOCKED               MARKET_ORDER_DISABLED

  Client order ID     format valid (alphanumeric, 1-36 char)            BLOCKED               INVALID_ORDER_ID

  Exchange status     portfolio_state\[\'exchange_status\'\] = normal   BLOCKED               EXCHANGE_MAINTENANCE

  Market halted       portfolio_state\[\'market_halted\'\] = False      BLOCKED               MARKET_HALTED

  Harga tersedia      last_price\_{symbol} ada di portfolio_state       BLOCKED               PRICE_UNAVAILABLE

  Spread wajar        spread_pct ≤ MAX_SPREAD_PCT                       WARNED                WIDE_SPREAD
  -------------------------------------------------------------------------------------------------------------------

**3.2 Interface Publik**

+--------------------------------------------------------------+
| class PreTradeCheck:                                         |
|                                                              |
| def validate(                                                |
|                                                              |
| self,                                                        |
|                                                              |
| request: TradeRequest,                                       |
|                                                              |
| portfolio_state: dict,                                       |
|                                                              |
| ) -\> tuple\[bool, str\]:                                    |
|                                                              |
| \"\"\"                                                       |
|                                                              |
| Return (True, \'\') jika semua check lulus.                  |
|                                                              |
| Return (False, error_message) pada check pertama yang gagal. |
|                                                              |
| Fast-fail: berhenti di check pertama yang gagal.             |
|                                                              |
| \"\"\"                                                       |
|                                                              |
| def validate_all(                                            |
|                                                              |
| self,                                                        |
|                                                              |
| request: TradeRequest,                                       |
|                                                              |
| portfolio_state: dict,                                       |
|                                                              |
| ) -\> list\[tuple\[str, bool, str\]\]:                       |
|                                                              |
| \"\"\"                                                       |
|                                                              |
| Jalankan SEMUA check tanpa fast-fail.                        |
|                                                              |
| Return list (check_name, passed, message).                   |
|                                                              |
| Berguna untuk debugging dan testing.                         |
|                                                              |
| \"\"\"                                                       |
+--------------------------------------------------------------+

**3.3 Client Order ID --- Format & Validasi**

+---------------------------------------------------------------------------+
| import re                                                                 |
|                                                                           |
| \# Format yang valid:                                                     |
|                                                                           |
| \# {strategy_id}\_{symbol}\_{timestamp_ms}                                |
|                                                                           |
| \# Contoh: spot_strategy_v1_BTCUSDT_1731658200000                         |
|                                                                           |
| ORDER_ID_PATTERN = re.compile(r\'\^\[a-zA-Z0-9\_\\-\]{1,36}\$\')          |
|                                                                           |
| def validate_client_order_id(cid: str) -\> tuple\[bool, str\]:            |
|                                                                           |
| if not cid:                                                               |
|                                                                           |
| return False, \'client_order_id kosong\'                                  |
|                                                                           |
| if len(cid) \> 36:                                                        |
|                                                                           |
| return False, f\'client_order_id terlalu panjang: {len(cid)} \> 36\'      |
|                                                                           |
| if not ORDER_ID_PATTERN.match(cid):                                       |
|                                                                           |
| return False, f\'client_order_id mengandung karakter tidak valid: {cid}\' |
|                                                                           |
| return True, \'\'                                                         |
|                                                                           |
| \# Helper untuk generate client_order_id yang valid:                      |
|                                                                           |
| def generate_order_id(strategy_id: str, symbol: str) -\> str:             |
|                                                                           |
| ts = int(utcnow().timestamp() \* 1000)                                    |
|                                                                           |
| raw = f\'{strategy_id}\_{symbol}\_{ts}\'                                  |
|                                                                           |
| \# Truncate dan sanitize jika perlu                                       |
|                                                                           |
| return raw\[:36\].replace(\' \', \'\_\')                                  |
+---------------------------------------------------------------------------+

**3.4 Config Keys Pre-Trade Check**

  -------------------------------------------------------------------------------------------------------
  **Key**                   **Default**   **Keterangan**
  ------------------------- ------------- ---------------------------------------------------------------
  ALLOW_MARKET_ORDER        True          False = wajib limit order. Lebih aman tapi bisa missed entry.

  REQUIRE_CLIENT_ORDER_ID   True          False hanya untuk testing. Live WAJIB True untuk idempotency.

  MAX_SPREAD_PCT            1.0           Spread di atas ini → WARNED (tidak BLOCKED, tapi dicatat).

  MIN_QTY                   1e-8          Quantity minimum absolut (sebelum lot filter exchange).
  -------------------------------------------------------------------------------------------------------

**4. circuit_breaker.py --- Penghenti Otomatis**

State machine yang menghentikan trading saat kondisi berbahaya terdeteksi. Ini adalah mekanisme self-preservation paling penting dalam sistem.

**4.1 State Machine**

+------------------------------------------------------------------------------+
| class CircuitState(str, Enum):                                               |
|                                                                              |
| NORMAL = \'normal\'                                                          |
|                                                                              |
| WARNED = \'warned\' \# mendekati batas --- trading masih boleh               |
|                                                                              |
| HALTED = \'halted\' \# trading STOP sampai kondisi membaik                   |
|                                                                              |
| \# Transisi yang valid:                                                      |
|                                                                              |
| \# NORMAL → WARNED → HALTED → NORMAL (setelah auto-resume atau manual reset) |
|                                                                              |
| \# NORMAL → HALTED (jika kondisi langsung kritis tanpa warning dulu)         |
|                                                                              |
| \# HALTED → NORMAL (hanya via auto-resume timeout atau manual_resume())      |
+------------------------------------------------------------------------------+

**4.2 Trigger Conditions**

  -----------------------------------------------------------------------------------------------------------------------
  **Trigger**                 **Threshold WARNED**   **Threshold HALTED**   **Auto-resume?**    **Config Key**
  --------------------------- ---------------------- ---------------------- ------------------- -------------------------
  Daily loss dari equity      \< -3%                 \< -5%                 Ya (reset harian)   WARN/MAX_DAILY_LOSS_PCT

  Drawdown dari peak          \< -7%                 \< -10%                Ya (auto timer)     WARN/MAX_DRAWDOWN_PCT

  Consecutive losses          ≥ 4                    ≥ 5                    Ya (reset harian)   MAX_CONSECUTIVE_LOSSES

  Manual halt (via gateway)   ---                    Segera                 Tidak (manual)      ---
  -----------------------------------------------------------------------------------------------------------------------

**4.3 Interface Publik Lengkap**

+----------------------------------------------------------------------+
| class CircuitBreaker:                                                |
|                                                                      |
| def \_\_init\_\_(self, config: AgentConfig):                         |
|                                                                      |
| self.max_daily_loss_pct = config.max_daily_loss_pct \# 0.05          |
|                                                                      |
| self.max_drawdown_pct = config.max_drawdown_pct \# 0.10              |
|                                                                      |
| self.max_consecutive_losses = config.max_consecutive_loss \# 5       |
|                                                                      |
| self.halt_duration_minutes = config.halt_duration_minutes \# 60      |
|                                                                      |
| self.warn_daily_loss_pct = config.warn_daily_loss_pct \# 0.03        |
|                                                                      |
| self.\_state = CircuitState.NORMAL                                   |
|                                                                      |
| self.\_halted_at = None                                              |
|                                                                      |
| self.\_consecutive_losses = 0                                        |
|                                                                      |
| self.\_manual_halt = False                                           |
|                                                                      |
| self.\_halt_reasons = \[\]                                           |
|                                                                      |
| def check(                                                           |
|                                                                      |
| self,                                                                |
|                                                                      |
| daily_pnl: float,                                                    |
|                                                                      |
| current_equity: float,                                               |
|                                                                      |
| peak_equity: float,                                                  |
|                                                                      |
| ) -\> CircuitState:                                                  |
|                                                                      |
| \"\"\"                                                               |
|                                                                      |
| Evaluasi semua kondisi dan return state terkini.                     |
|                                                                      |
| Dipanggil di setiap tick oleh RiskManager.evaluate().                |
|                                                                      |
| Side effect: bisa mengubah self.\_state.                             |
|                                                                      |
| \"\"\"                                                               |
|                                                                      |
| def record_loss(self) -\> None:                                      |
|                                                                      |
| \"\"\"Dipanggil setelah setiap trade yang merugi.\"\"\"              |
|                                                                      |
| def record_win(self) -\> None:                                       |
|                                                                      |
| \"\"\"Reset consecutive loss counter setelah win.\"\"\"              |
|                                                                      |
| def manual_halt(self, reason: str) -\> None:                         |
|                                                                      |
| \"\"\"Halt permanen --- hanya bisa resume via manual_resume().\"\"\" |
|                                                                      |
| def manual_resume(self) -\> None:                                    |
|                                                                      |
| \"\"\"Resume dari manual halt. Butuh konfirmasi dari operator.\"\"\" |
|                                                                      |
| def reset_daily(self) -\> None:                                      |
|                                                                      |
| \"\"\"Reset counter harian. Dipanggil scheduler UTC 00:00.\"\"\"     |
|                                                                      |
| \@property                                                           |
|                                                                      |
| def state(self) -\> CircuitState:                                    |
|                                                                      |
| return self.\_state                                                  |
|                                                                      |
| def status(self) -\> dict:                                           |
|                                                                      |
| \"\"\"Snapshot state untuk monitoring/healthcheck.\"\"\"             |
|                                                                      |
| return {                                                             |
|                                                                      |
| \'state\': self.\_state,                                             |
|                                                                      |
| \'consecutive_losses\': self.\_consecutive_losses,                   |
|                                                                      |
| \'halted_at\': self.\_halted_at,                                     |
|                                                                      |
| \'manual_halt\': self.\_manual_halt,                                 |
|                                                                      |
| \'halt_reasons\': self.\_halt_reasons,                               |
|                                                                      |
| }                                                                    |
+----------------------------------------------------------------------+

**4.4 Auto-Resume Logic**

+-------------------------------------------------------------------------+
| def \_auto_resume_due(self) -\> bool:                                   |
|                                                                         |
| \"\"\"                                                                  |
|                                                                         |
| Auto-resume hanya berlaku untuk non-manual halt.                        |
|                                                                         |
| Resume setelah HALT_DURATION_MINUTES menit.                             |
|                                                                         |
| \"\"\"                                                                  |
|                                                                         |
| if self.\_manual_halt:                                                  |
|                                                                         |
| return False \# manual halt tidak auto-resume                           |
|                                                                         |
| if not self.\_halted_at:                                                |
|                                                                         |
| return False                                                            |
|                                                                         |
| elapsed = datetime.utcnow() - self.\_halted_at                          |
|                                                                         |
| return elapsed \>= timedelta(minutes=self.halt_duration_minutes)        |
|                                                                         |
| \# Logika di dalam check():                                             |
|                                                                         |
| \# if self.\_state == CircuitState.HALTED and self.\_auto_resume_due(): |
|                                                                         |
| \# self.\_resume() \# kembali ke NORMAL                                 |
|                                                                         |
| \# return CircuitState.NORMAL                                           |
|                                                                         |
| \# Reset harian (scheduler UTC 00:00):                                  |
|                                                                         |
| \# - Reset consecutive_losses → 0                                       |
|                                                                         |
| \# - Jika HALTED karena daily_loss: resume (bukan manual halt)          |
|                                                                         |
| \# - TIDAK reset drawdown --- drawdown dari peak tetap dihitung         |
+-------------------------------------------------------------------------+

**4.5 Config Keys Circuit Breaker**

  ------------------------------------------------------------------------------------------------------------
  **Key**                  **Default**   **Valid Range**   **Keterangan**
  ------------------------ ------------- ----------------- ---------------------------------------------------
  MAX_DAILY_LOSS_PCT       0.05          0.01--0.15        5% loss dari equity dalam satu hari → HALT

  WARN_DAILY_LOSS_PCT      0.03          0.01--0.10        3% loss → WARNED (masih boleh trade)

  MAX_DRAWDOWN_PCT         0.10          0.05--0.30        10% drawdown dari peak → HALT

  MAX_CONSECUTIVE_LOSSES   5             3--15             5 loss berturut-turut → HALT

  HALT_DURATION_MINUTES    60            15--480           Auto-resume setelah 60 menit saat non-manual halt
  ------------------------------------------------------------------------------------------------------------

**5. position_size.py --- Kalkulasi Ukuran Posisi**

Menentukan berapa quantity yang tepat untuk setiap trade. Tiga metode tersedia, semua menghasilkan output yang di-clamp ke Binance lot filter secara otomatis.

**5.1 Tiga Metode Sizing**

  -----------------------------------------------------------------------------------------------------------------------------------------------------------------
  **Metode**              **Filosofi**                                        **Kapan Gunakan**           **Kelebihan**            **Kekurangan**
  ----------------------- --------------------------------------------------- --------------------------- ------------------------ --------------------------------
  **fixed_fractional**    Risiko X% dari equity per trade                     Default --- semua kondisi   Simple, predictable      Tidak menyesuaikan volatilitas

  **kelly**               Optimal berdasarkan win rate & RR                   Setelah 50+ trade histori   Mathematically optimal   Over-fit ke histori pendek

  **volatility_scaled**   Lebih kecil saat volatil, lebih besar saat tenang   Market yang berubah-ubah    Adaptif                  Butuh ATR yang akurat
  -----------------------------------------------------------------------------------------------------------------------------------------------------------------

**5.2 Fixed Fractional --- Detail**

+--------------------------------------------------------+
| def \_fixed_fractional(                                |
|                                                        |
| self,                                                  |
|                                                        |
| request: TradeRequest,                                 |
|                                                        |
| equity: float,                                         |
|                                                        |
| price: float,                                          |
|                                                        |
| ) -\> tuple\[float, list\[str\], float\]:              |
|                                                        |
| \"\"\"                                                 |
|                                                        |
| Return: (quantity, warnings, risk_usd)                 |
|                                                        |
| Formula:                                               |
|                                                        |
| 1\. risk_amount = equity × risk_per_trade_pct          |
|                                                        |
| Contoh: \$10,000 × 1% = \$100                          |
|                                                        |
| 2\. stop_distance = \|entry_price - sl_price\|         |
|                                                        |
| Contoh: \|50,000 - 49,000\| = \$1,000                  |
|                                                        |
| 3\. quantity = risk_amount / stop_distance             |
|                                                        |
| Contoh: \$100 / \$1,000 = 0.1 BTC                      |
|                                                        |
| 4\. notional = quantity × price                        |
|                                                        |
| Contoh: 0.1 × 50,000 = \$5,000                         |
|                                                        |
| \"\"\"                                                 |
|                                                        |
| risk_amount = equity \* self.risk_per_trade            |
|                                                        |
| stop_distance = abs(price - request.suggested_sl)      |
|                                                        |
| if stop_distance \<= 0:                                |
|                                                        |
| \# Fallback: gunakan default SL pct                    |
|                                                        |
| stop_distance = price \* self.config.default_sl_pct    |
|                                                        |
| quantity = risk_amount / stop_distance                 |
|                                                        |
| risk_usd = min(quantity \* stop_distance, risk_amount) |
|                                                        |
| return quantity, \[\], risk_usd                        |
+--------------------------------------------------------+

**5.3 Kelly Criterion --- Detail**

+------------------------------------------------------------------+
| def \_kelly(                                                     |
|                                                                  |
| self,                                                            |
|                                                                  |
| request: TradeRequest,                                           |
|                                                                  |
| equity: float,                                                   |
|                                                                  |
| price: float,                                                    |
|                                                                  |
| portfolio_state: dict,                                           |
|                                                                  |
| ) -\> tuple\[float, list\[str\], float\]:                        |
|                                                                  |
| \"\"\"                                                           |
|                                                                  |
| Kelly Criterion: f\* = (b×p - q) / b                             |
|                                                                  |
| b = avg_win / avg_loss (risk-reward ratio)                       |
|                                                                  |
| p = win rate                                                     |
|                                                                  |
| q = 1 - p = loss rate                                            |
|                                                                  |
| Half-Kelly digunakan untuk keamanan (× kelly_fraction = 0.5)     |
|                                                                  |
| Kelly penuh sering terlalu agresif untuk trading.                |
|                                                                  |
| \"\"\"                                                           |
|                                                                  |
| warnings = \[\]                                                  |
|                                                                  |
| stats = portfolio_state.get(\'strategy_stats\', {}).get(         |
|                                                                  |
| request.strategy_id, {})                                         |
|                                                                  |
| if not stats:                                                    |
|                                                                  |
| warnings.append(                                                 |
|                                                                  |
| f\'Tidak ada stats untuk {request.strategy_id}, pakai default\') |
|                                                                  |
| win_rate = 0.50                                                  |
|                                                                  |
| avg_rr = 1.50                                                    |
|                                                                  |
| else:                                                            |
|                                                                  |
| win_rate = stats.get(\'win_rate\', 0.50)                         |
|                                                                  |
| avg_rr = stats.get(\'avg_risk_reward\', 1.50)                    |
|                                                                  |
| \# Validasi: win_rate & rr harus masuk akal                      |
|                                                                  |
| if win_rate \<= 0 or win_rate \>= 1:                             |
|                                                                  |
| win_rate = 0.50                                                  |
|                                                                  |
| warnings.append(\'win_rate tidak valid, reset ke 0.50\')         |
|                                                                  |
| b = avg_rr                                                       |
|                                                                  |
| p = win_rate                                                     |
|                                                                  |
| q = 1.0 - p                                                      |
|                                                                  |
| kelly_pct = max(0.0, (b \* p - q) / b) \* self.kelly_fraction    |
|                                                                  |
| \# Clamp: jangan melebihi 3× fixed fractional                    |
|                                                                  |
| max_kelly = self.risk_per_trade \* 3                             |
|                                                                  |
| kelly_pct = min(kelly_pct, max_kelly)                            |
|                                                                  |
| risk_amount = equity \* kelly_pct                                |
|                                                                  |
| stop_dist = abs(price - request.suggested_sl) or price \* 0.02   |
|                                                                  |
| quantity = risk_amount / stop_dist                               |
|                                                                  |
| return quantity, warnings, risk_amount                           |
+------------------------------------------------------------------+

**5.4 Volatility Scaled --- Detail**

+---------------------------------------------------------------------------------------+
| def \_volatility_scaled(                                                              |
|                                                                                       |
| self,                                                                                 |
|                                                                                       |
| request: TradeRequest,                                                                |
|                                                                                       |
| equity: float,                                                                        |
|                                                                                       |
| price: float,                                                                         |
|                                                                                       |
| portfolio_state: dict,                                                                |
|                                                                                       |
| ) -\> tuple\[float, list\[str\], float\]:                                             |
|                                                                                       |
| \"\"\"                                                                                |
|                                                                                       |
| Sizing berbasis ATR: semakin volatile → posisi lebih kecil.                           |
|                                                                                       |
| Stop distance = ATR × atr_multiplier (default 2.0)                                    |
|                                                                                       |
| \"\"\"                                                                                |
|                                                                                       |
| warnings = \[\]                                                                       |
|                                                                                       |
| atr_key = f\'atr\_{request.symbol}\'                                                  |
|                                                                                       |
| if atr_key not in portfolio_state:                                                    |
|                                                                                       |
| warnings.append(f\'ATR tidak ditemukan untuk {request.symbol}, estimasi 1.5% harga\') |
|                                                                                       |
| atr = price \* 0.015                                                                  |
|                                                                                       |
| else:                                                                                 |
|                                                                                       |
| atr = portfolio_state\[atr_key\]                                                      |
|                                                                                       |
| stop_distance = atr \* self.atr_multiplier                                            |
|                                                                                       |
| risk_amount = equity \* self.risk_per_trade                                           |
|                                                                                       |
| quantity = risk_amount / stop_distance                                                |
|                                                                                       |
| return quantity, warnings, risk_amount                                                |
+---------------------------------------------------------------------------------------+

**5.5 Binance Lot Filter --- Wajib Diaplikasikan**

+-----------------------------------------------------------------------------+
| \# Semua sizing method wajib melewati lot filter sebelum return             |
|                                                                             |
| BINANCE_LOT_DEFAULTS = {                                                    |
|                                                                             |
| \'spot\': {                                                                 |
|                                                                             |
| \'min_qty\': 0.00001,                                                       |
|                                                                             |
| \'step_size\': 0.00001,                                                     |
|                                                                             |
| \'min_notional\':10.0,                                                      |
|                                                                             |
| },                                                                          |
|                                                                             |
| \'futures\': {                                                              |
|                                                                             |
| \'min_qty\': 0.001,                                                         |
|                                                                             |
| \'step_size\': 0.001,                                                       |
|                                                                             |
| \'min_notional\':5.0,                                                       |
|                                                                             |
| \'max_notional\':1_000_000.0,                                               |
|                                                                             |
| }                                                                           |
|                                                                             |
| }                                                                           |
|                                                                             |
| def \_apply_lot_filter(self, qty: float, is_futures: bool) -\> float:       |
|                                                                             |
| \"\"\"                                                                      |
|                                                                             |
| Floor quantity ke step_size (BUKAN round --- untuk menghindari over-order). |
|                                                                             |
| Menggunakan math.floor, bukan round.                                        |
|                                                                             |
| Contoh:                                                                     |
|                                                                             |
| qty = 0.123456, step_size = 0.001                                           |
|                                                                             |
| floor(0.123456 / 0.001) × 0.001 = 0.123                                     |
|                                                                             |
| \"\"\"                                                                      |
|                                                                             |
| import math                                                                 |
|                                                                             |
| market = \'futures\' if is_futures else \'spot\'                            |
|                                                                             |
| step = BINANCE_LOT_DEFAULTS\[market\]\[\'step_size\'\]                      |
|                                                                             |
| \# Presisi dari step_size                                                   |
|                                                                             |
| precision = max(0, round(-math.log10(step)))                                |
|                                                                             |
| floored = math.floor(qty / step) \* step                                    |
|                                                                             |
| return round(floored, precision)                                            |
|                                                                             |
| \# Setelah lot filter, cek notional minimum:                                |
|                                                                             |
| \# notional = qty × price                                                   |
|                                                                             |
| \# if notional \< MIN_NOTIONAL: return 0 → BLOCKED                          |
+-----------------------------------------------------------------------------+

**5.6 Config Keys Position Sizer**

  -------------------------------------------------------------------------------------------------------------------------------------------
  **Key**              **Default**        **Valid Range**                              **Keterangan**
  -------------------- ------------------ -------------------------------------------- ------------------------------------------------------
  SIZING_METHOD        fixed_fractional   fixed_fractional\|kelly\|volatility_scaled   Metode default

  RISK_PER_TRADE_PCT   0.01               0.001--0.05                                  Persentase equity yang di-risk per trade

  MAX_POSITION_PCT     0.10               0.01--0.30                                   Max notional per posisi sebagai % equity

  KELLY_FRACTION       0.5                0.1--1.0                                     Half-Kelly. Nilai ≤ 0.5 lebih konservatif.

  ATR_MULTIPLIER       2.0                1.0--5.0                                     Stop distance = ATR × multiplier (volatility_scaled)

  MIN_NOTIONAL_USD     10.0               5.0--100.0                                   Minimal notional dalam USDT (Binance minimum)
  -------------------------------------------------------------------------------------------------------------------------------------------

**6. stoploss.py --- Validasi & Kalkulasi Stop Loss**

Memastikan setiap trade memiliki stop loss yang valid sebelum masuk. SL adalah komponen risk management paling fundamental --- tanpa SL, kerugian bisa tak terbatas.

**6.1 Validasi SL --- Aturan Lengkap**

  -------------------------------------------------------------------------------------------------------------------------------
  **Rule**          **Check**                                      **Aksi Jika Gagal**                      **Config Key**
  ----------------- ---------------------------------------------- ---------------------------------------- ---------------------
  SL harus ada      suggested_sl \> 0                              BLOCKED (live) / WARNED (paper)          REQUIRE_SL

  Jarak minimum     \|price - sl\| / price ≥ MIN_SL_DISTANCE_PCT   BLOCKED --- SL terlalu dekat             MIN_SL_DISTANCE_PCT

  Jarak maksimum    \|price - sl\| / price ≤ MAX_SL_DISTANCE_PCT   WARNED --- risk per trade sangat besar   MAX_SL_DISTANCE_PCT

  Arah SL benar     BUY: sl \< price, SELL: sl \> price            BLOCKED --- SL di sisi yang salah        ---

  SL tidak di nol   sl \> 0 untuk BUY                              BLOCKED --- invalid price                ---
  -------------------------------------------------------------------------------------------------------------------------------

**6.2 Interface Publik**

+------------------------------------------------------------------+
| class StopLossManager:                                           |
|                                                                  |
| def validate_and_compute(                                        |
|                                                                  |
| self,                                                            |
|                                                                  |
| request: TradeRequest,                                           |
|                                                                  |
| portfolio_state: dict,                                           |
|                                                                  |
| ) -\> tuple\[bool, str, float\]:                                 |
|                                                                  |
| \"\"\"                                                           |
|                                                                  |
| Return: (ok, message, final_sl_price)                            |
|                                                                  |
| Jika ok=True: final_sl adalah SL yang sudah divalidasi.          |
|                                                                  |
| Jika ok=False:                                                   |
|                                                                  |
| \- Live mode: BLOCKED                                            |
|                                                                  |
| \- Paper mode: final_sl = default_sl yang dikalkulasi            |
|                                                                  |
| \"\"\"                                                           |
|                                                                  |
| def compute_default_sl(                                          |
|                                                                  |
| self,                                                            |
|                                                                  |
| price: float,                                                    |
|                                                                  |
| side: str,                                                       |
|                                                                  |
| atr: float = 0.0,                                                |
|                                                                  |
| ) -\> float:                                                     |
|                                                                  |
| \"\"\"                                                           |
|                                                                  |
| Hitung default SL jika tidak di-set.                             |
|                                                                  |
| Prioritas:                                                       |
|                                                                  |
| 1\. Jika ATR tersedia: SL = price ± (ATR × SL_ATR_MULT)          |
|                                                                  |
| 2\. Fallback: SL = price × (1 ± DEFAULT_SL_PCT)                  |
|                                                                  |
| \"\"\"                                                           |
|                                                                  |
| def is_sl_tight_enough(                                          |
|                                                                  |
| self,                                                            |
|                                                                  |
| price: float,                                                    |
|                                                                  |
| sl: float,                                                       |
|                                                                  |
| atr: float,                                                      |
|                                                                  |
| ) -\> tuple\[bool, str\]:                                        |
|                                                                  |
| \"\"\"                                                           |
|                                                                  |
| Cek apakah SL tidak terlalu jauh (risk terlalu besar per trade). |
|                                                                  |
| SL dikatakan terlalu jauh jika:                                  |
|                                                                  |
| \|price - sl\| \> ATR × MAX_SL_ATR_MULT                          |
|                                                                  |
| \"\"\"                                                           |
+------------------------------------------------------------------+

**6.3 Config Keys Stop Loss**

  --------------------------------------------------------------------------------------------------------------------------
  **Key**               **Default**   **Valid Range**   **Keterangan**
  --------------------- ------------- ----------------- --------------------------------------------------------------------
  REQUIRE_SL            True          bool              True di live mode. False hanya untuk testing.

  DEFAULT_SL_PCT        0.02          0.005--0.10       Default SL 2% dari harga jika tidak ada ATR.

  MIN_SL_DISTANCE_PCT   0.003         0.001--0.01       SL minimum 0.3% dari harga. Di bawah ini terlalu rawan kena noise.

  MAX_SL_DISTANCE_PCT   0.15          0.05--0.30        SL maksimum 15% dari harga. Di atas ini risk terlalu besar.

  SL_ATR_MULT           1.5           1.0--3.0          Default SL = ATR × multiplier (jika ATR tersedia).

  MAX_SL_ATR_MULT       4.0           2.0--8.0          SL dianggap terlalu jauh jika \> ATR × max_mult.
  --------------------------------------------------------------------------------------------------------------------------

**7. exposure_control.py --- Kontrol Exposure Portfolio**

Mencegah portfolio over-leveraged atau terlalu terkonsentrasi. Berjalan setelah portfolio_layer/allocator.py tapi dengan perspektif yang berbeda --- allocator melihat USDT, exposure_control melihat persentase equity.

**7.1 Semua Check yang Dilakukan**

  ------------------------------------------------------------------------------------------------------------------------
  **Check**             **Formula**                                            **Config Key**                **Default**
  --------------------- ------------------------------------------------------ ----------------------------- -------------
  Max posisi terbuka    count(open_positions) \< MAX_OPEN_POSITIONS            MAX_OPEN_POSITIONS            5

  Max per simbol        notional_symbol / equity ≤ MAX_PER_SYMBOL_PCT          MAX_EXPOSURE_PER_SYMBOL_PCT   0.20

  Max total exposure    Σ(notional) / equity ≤ MAX_TOTAL_PCT                   MAX_TOTAL_EXPOSURE_PCT        0.80

  Correlated exposure   Σ(notional corr group) / equity ≤ MAX_CORRELATED_PCT   MAX_CORRELATED_EXPOSURE_PCT   0.35
  ------------------------------------------------------------------------------------------------------------------------

**7.2 Interface Publik**

+------------------------------------------------------------------------+
| class ExposureControl:                                                 |
|                                                                        |
| def validate(                                                          |
|                                                                        |
| self,                                                                  |
|                                                                        |
| request: TradeRequest,                                                 |
|                                                                        |
| portfolio_state: dict,                                                 |
|                                                                        |
| ) -\> tuple\[bool, str\]:                                              |
|                                                                        |
| \"\"\"                                                                 |
|                                                                        |
| Return (True, \'\') jika exposure dalam batas.                         |
|                                                                        |
| Return (False, alasan) jika melebihi batas.                            |
|                                                                        |
| \"\"\"                                                                 |
|                                                                        |
| equity = portfolio_state.get(\'equity\', 1.0)                          |
|                                                                        |
| positions = portfolio_state.get(\'open_positions\', {})                |
|                                                                        |
| price = request.price or portfolio_state.get(                          |
|                                                                        |
| f\'last_price\_{request.symbol}\', 0.0)                                |
|                                                                        |
| new_notional = request.quantity \* price                               |
|                                                                        |
| \# Check 1: max posisi                                                 |
|                                                                        |
| if len(positions) \>= self.max_positions:                              |
|                                                                        |
| if request.symbol not in positions: \# bukan tambah ke posisi existing |
|                                                                        |
| return False, f\'Max posisi ({self.max_positions}) sudah tercapai\'    |
|                                                                        |
| \# Check 2: max per simbol                                             |
|                                                                        |
| existing = positions.get(request.symbol, {}).get(\'notional\', 0.0)    |
|                                                                        |
| if (existing + new_notional) / equity \> self.max_per_symbol:          |
|                                                                        |
| return False, (                                                        |
|                                                                        |
| f\'{request.symbol} exposure {(existing+new_notional)/equity:.1%} \'   |
|                                                                        |
| f\'\> max {self.max_per_symbol:.1%}\'                                  |
|                                                                        |
| )                                                                      |
|                                                                        |
| \# Check 3: max total                                                  |
|                                                                        |
| total_current = sum(p.get(\'notional\',0) for p in positions.values()) |
|                                                                        |
| if (total_current + new_notional) / equity \> self.max_total:          |
|                                                                        |
| return False, (                                                        |
|                                                                        |
| f\'Total exposure {(total_current+new_notional)/equity:.1%} \'         |
|                                                                        |
| f\'\> max {self.max_total:.1%}\'                                       |
|                                                                        |
| )                                                                      |
|                                                                        |
| \# Check 4: correlated exposure                                        |
|                                                                        |
| corr_result = self.\_check_correlation(                                |
|                                                                        |
| request.symbol, new_notional, positions, equity)                       |
|                                                                        |
| if corr_result:                                                        |
|                                                                        |
| return False, corr_result                                              |
|                                                                        |
| return True, \'\'                                                      |
+------------------------------------------------------------------------+

**7.3 Correlation Pairs --- Default**

+-------------------------------------------------------------------+
| \# Pair yang dianggap sangat berkorelasi                          |
|                                                                   |
| \# Update via config jika ada pair baru yang ditambahkan          |
|                                                                   |
| CORRELATED_PAIRS: list\[set\[str\]\] = \[                         |
|                                                                   |
| {\'BTCUSDT\', \'ETHUSDT\'},                                       |
|                                                                   |
| {\'ETHUSDT\', \'BNBUSDT\'},                                       |
|                                                                   |
| {\'BTCUSDT\', \'ETHUSDT\', \'BNBUSDT\', \'SOLUSDT\'},             |
|                                                                   |
| \]                                                                |
|                                                                   |
| def \_check_correlation(                                          |
|                                                                   |
| self,                                                             |
|                                                                   |
| symbol: str,                                                      |
|                                                                   |
| new_notional: float,                                              |
|                                                                   |
| positions: dict,                                                  |
|                                                                   |
| equity: float,                                                    |
|                                                                   |
| ) -\> str:                                                        |
|                                                                   |
| \"\"\"Return error string jika melanggar, kosong jika aman.\"\"\" |
|                                                                   |
| for pair in CORRELATED_PAIRS:                                     |
|                                                                   |
| if symbol not in pair:                                            |
|                                                                   |
| continue                                                          |
|                                                                   |
| partner_notional = sum(                                           |
|                                                                   |
| positions.get(s, {}).get(\'notional\', 0)                         |
|                                                                   |
| for s in pair if s != symbol                                      |
|                                                                   |
| )                                                                 |
|                                                                   |
| existing = positions.get(symbol, {}).get(\'notional\', 0)         |
|                                                                   |
| total = partner_notional + existing + new_notional                |
|                                                                   |
| if total / equity \> self.max_correlated:                         |
|                                                                   |
| return (                                                          |
|                                                                   |
| f\'Correlated exposure ({\'+\'.join(pair)}) \'                    |
|                                                                   |
| f\'{total/equity:.1%} \> max {self.max_correlated:.1%}\'          |
|                                                                   |
| )                                                                 |
|                                                                   |
| return \'\'                                                       |
+-------------------------------------------------------------------+

**8. leverage_control.py --- Kontrol Leverage Futures**

Khusus untuk futures trading. Mencegah penggunaan leverage berlebihan yang bisa memperbesar kerugian secara drastis.

**8.1 Leverage Limits per Simbol**

  -------------------------------------------------------------------------------------------------------------------------------
  **Symbol**   **Max Leverage (Agent)**   **Max Leverage (Binance)**   **Catatan**
  ------------ -------------------------- ---------------------------- ----------------------------------------------------------
  BTCUSDT      10x                        125x                         Agent batasi 10x --- aman untuk strategi trend following

  ETHUSDT      10x                        100x                         Sama dengan BTC

  BNBUSDT      5x                         75x                          Lebih volatile --- batas lebih ketat

  SOLUSDT      5x                         50x                          Altcoin --- batas lebih ketat

  DEFAULT      3x                         Varies                       Untuk simbol yang tidak terdaftar --- konservatif
  -------------------------------------------------------------------------------------------------------------------------------

**8.2 Interface Publik**

+----------------------------------------------------------------------------------+
| class LeverageControl:                                                           |
|                                                                                  |
| def validate(                                                                    |
|                                                                                  |
| self,                                                                            |
|                                                                                  |
| leverage: int,                                                                   |
|                                                                                  |
| symbol: str,                                                                     |
|                                                                                  |
| ) -\> tuple\[bool, str\]:                                                        |
|                                                                                  |
| \"\"\"                                                                           |
|                                                                                  |
| Return (True, \'\') jika leverage valid.                                         |
|                                                                                  |
| Return (False, alasan) jika melebihi batas.                                      |
|                                                                                  |
| \"\"\"                                                                           |
|                                                                                  |
| if leverage \<= 0:                                                               |
|                                                                                  |
| return False, f\'Leverage tidak valid: {leverage}\'                              |
|                                                                                  |
| symbol_max = SYMBOL_MAX_LEVERAGE.get(symbol, SYMBOL_MAX_LEVERAGE\[\'DEFAULT\'\]) |
|                                                                                  |
| effective_max = min(self.global_max, symbol_max)                                 |
|                                                                                  |
| if leverage \> effective_max:                                                    |
|                                                                                  |
| return False, (                                                                  |
|                                                                                  |
| f\'Leverage {leverage}x melebihi batas {effective_max}x \'                       |
|                                                                                  |
| f\'untuk {symbol} (global={self.global_max}, symbol={symbol_max})\'              |
|                                                                                  |
| )                                                                                |
|                                                                                  |
| if leverage \> 3:                                                                |
|                                                                                  |
| \# Warning untuk leverage tinggi meski masih dalam batas                         |
|                                                                                  |
| log.warning(\'High leverage\', leverage=leverage, symbol=symbol)                 |
|                                                                                  |
| return True, \'\'                                                                |
|                                                                                  |
| def get_max_leverage(self, symbol: str) -\> int:                                 |
|                                                                                  |
| \"\"\"Return max leverage yang diizinkan untuk simbol ini.\"\"\"                 |
|                                                                                  |
| symbol_max = SYMBOL_MAX_LEVERAGE.get(symbol, SYMBOL_MAX_LEVERAGE\[\'DEFAULT\'\]) |
|                                                                                  |
| return min(self.global_max, symbol_max)                                          |
|                                                                                  |
| def get_effective_leverage(                                                      |
|                                                                                  |
| self,                                                                            |
|                                                                                  |
| notional: float,                                                                 |
|                                                                                  |
| margin: float,                                                                   |
|                                                                                  |
| ) -\> float:                                                                     |
|                                                                                  |
| \"\"\"Hitung leverage aktual dari notional dan margin yang digunakan.\"\"\"      |
|                                                                                  |
| if margin \<= 0: return 0.0                                                      |
|                                                                                  |
| return notional / margin                                                         |
+----------------------------------------------------------------------------------+

**8.3 Config Keys Leverage Control**

  ---------------------------------------------------------------------------------------------------------------
  **Key**               **Default**   **Keterangan**
  --------------------- ------------- ---------------------------------------------------------------------------
  GLOBAL_MAX_LEVERAGE   5             Batas atas untuk SEMUA simbol. Override symbol-specific jika lebih kecil.

  ALLOW_CROSS_MARGIN    False         False = hanya isolated margin. Cross margin risiko lebih tinggi.

  SYMBOL_MAX_LEVERAGE   lihat tabel   Dict per simbol, bisa di-override via config YAML.
  ---------------------------------------------------------------------------------------------------------------

**9. Integrasi --- portfolio_state Format Lengkap**

portfolio_state adalah dict yang dikirim ke risk_manager.evaluate(). Ini adalah kontrak antara main_loop dan risk_layer. Semua komponen risk layer membaca dari dict ini.

**9.1 Format Lengkap portfolio_state**

+-------------------------------------------------------------------+
| \# Dict yang dikirim ke risk_manager.evaluate()                   |
|                                                                   |
| \# Semua field wajib kecuali yang diberi komentar \'opsional\'    |
|                                                                   |
| portfolio_state: dict = {                                         |
|                                                                   |
| \# ── WAJIB ───────────────────────────────────────────────       |
|                                                                   |
| \'equity\': 10_000.0,                                             |
|                                                                   |
| \'open_positions\': {                                             |
|                                                                   |
| \'BTCUSDT\': {                                                    |
|                                                                   |
| \'trade_id\': \'uuid-\...\',                                      |
|                                                                   |
| \'side\': \'BUY\',                                                |
|                                                                   |
| \'qty\': 0.01,                                                    |
|                                                                   |
| \'notional\': 500.0, \# qty × entry_price                         |
|                                                                   |
| \'entry_price\':50_000.0,                                         |
|                                                                   |
| \'unrealized_pnl\': 50.0,                                         |
|                                                                   |
| }                                                                 |
|                                                                   |
| },                                                                |
|                                                                   |
| \'exchange_status\': \'normal\', \# \'normal\' \| \'maintenance\' |
|                                                                   |
| \'market_halted\': False,                                         |
|                                                                   |
| \# ── HARGA (wajib untuk setiap simbol yang diperdagangkan) ─     |
|                                                                   |
| \'last_price_BTCUSDT\': 50_000.0,                                 |
|                                                                   |
| \'last_price_ETHUSDT\': 3_000.0,                                  |
|                                                                   |
| \# ── UNTUK VOLATILITY SCALED SIZING (opsional) ────────────      |
|                                                                   |
| \'atr_BTCUSDT\': 800.0,                                           |
|                                                                   |
| \'atr_ETHUSDT\': 60.0,                                            |
|                                                                   |
| \# ── UNTUK KELLY SIZING (opsional) ────────────────────────      |
|                                                                   |
| \'strategy_stats\': {                                             |
|                                                                   |
| \'spot_strategy_v1\': {                                           |
|                                                                   |
| \'win_rate\': 0.55,                                               |
|                                                                   |
| \'avg_risk_reward\': 1.80,                                        |
|                                                                   |
| \'total_trades\': 47,                                             |
|                                                                   |
| }                                                                 |
|                                                                   |
| },                                                                |
|                                                                   |
| \# ── UNTUK SPREAD CHECK (opsional, dari ticker) ───────────      |
|                                                                   |
| \'spread_pct_BTCUSDT\': 0.012,                                    |
|                                                                   |
| }                                                                 |
+-------------------------------------------------------------------+

**9.2 Siapa yang Mengisi portfolio_state**

  --------------------------------------------------------------------------------------------------------
  **Field**         **Diisi Oleh**                               **Diupdate Setiap**
  ----------------- -------------------------------------------- -----------------------------------------
  equity            capital_manager.py (via sync/balance_sync)   Setiap sync periodik (60 detik)

  open_positions    trade_layer/store.py + sync/position_sync    Setiap trade open/close + sync periodik

  exchange_status   data_layer/market.py (dari exchange info)    Setiap healthcheck (30 detik)

  last_price\_\*    data_layer/market.py (dari ticker cache)     Setiap tick (dari WebSocket)

  atr\_\*           intelligence_layer/volatility.py             Setiap tick

  strategy_stats    learning_layer/performance.db                Harian (setelah daily_eval)

  spread_pct\_\*    data_layer/market.py (dari ticker)           Setiap tick
  --------------------------------------------------------------------------------------------------------

**9.3 Dependency Map Antar File**

  -----------------------------------------------------------------------------------------------------------------------------------------------------------------------------
  **File**              **Import Dari**                                                                                 **Dipanggil Oleh**
  --------------------- ----------------------------------------------------------------------------------------------- -------------------------------------------------------
  risk_manager.py       pre_trade_check, circuit_breaker, leverage_control, exposure_control, position_size, stoploss   main_loop process_signal()

  pre_trade_check.py    utils/helpers (validate_order_id)                                                               risk_manager.evaluate()

  circuit_breaker.py    --- (pure state machine)                                                                        risk_manager.evaluate(), exit_layer (record_win/loss)

  position_size.py      utils/helpers (round_qty, round_price)                                                          risk_manager.evaluate()

  stoploss.py           utils/helpers (round_price)                                                                     risk_manager.evaluate()

  exposure_control.py   --- (pure computation)                                                                          risk_manager.evaluate()

  leverage_control.py   --- (pure computation)                                                                          risk_manager.evaluate()
  -----------------------------------------------------------------------------------------------------------------------------------------------------------------------------

**10. Skenario Pengujian --- Test Cases Kritis**

Risk Layer harus menanggung 100% branch coverage. Berikut skenario pengujian yang wajib ada karena paling sering jadi sumber bug tersembunyi.

**10.1 Skenario Happy Path**

  --------------------------------------------------------------------------------------------------------------------------------------------------------
  **Skenario**                    **Setup**                                               **Expected Result**
  ------------------------------- ------------------------------------------------------- ----------------------------------------------------------------
  Trade normal disetujui          Equity \$10k, posisi kosong, sinyal valid, paper mode   verdict=APPROVED, qty\>0

  WARNED tetapi lanjut            ATR tidak tersedia → default digunakan                  verdict=WARNED, qty\>0, warning berisi pesan ATR

  Scale-down quantity             qty diminta 0.1 BTC tapi melebihi max_position_pct      verdict=APPROVED, qty \< 0.1 (di-clamp), warning berisi alasan

  Paper mode: block tetap jalan   Circuit breaker HALTED, paper_mode=True                 verdict=WARNED (bukan BLOCKED), qty\>0
  --------------------------------------------------------------------------------------------------------------------------------------------------------

**10.2 Skenario Error & Block**

  ---------------------------------------------------------------------------------------------------------------------------------------------------
  **Skenario**                       **Setup**                                                **Expected Result**
  ---------------------------------- -------------------------------------------------------- -------------------------------------------------------
  Symbol kosong                      request.symbol = \'\'                                    verdict=BLOCKED, reason=\'Symbol tidak valid\'

  Quantity nol                       request.quantity = 0.0                                   verdict=BLOCKED, reason=\'Quantity terlalu kecil\'

  SL di sisi salah (BUY+SL\>entry)   side=BUY, sl=51000, price=50000                          verdict=BLOCKED, reason=\'SL di sisi yang salah\'

  Circuit breaker HALTED             daily_pnl = -6% dari equity, live mode                   verdict=BLOCKED, reason=\'Circuit breaker HALTED\'

  Max posisi tercapai                5 posisi terbuka, coba buka simbol baru                  verdict=BLOCKED, reason=\'Max posisi\'

  Exposure per simbol terlampaui     BTCUSDT sudah 20% equity, coba tambah lagi               verdict=BLOCKED, reason=\'Exposure BTCUSDT\'

  Leverage terlalu tinggi            leverage=20, symbol=SOLUSDT (max=5)                      verdict=BLOCKED, reason=\'Leverage 20x \> max 5x\'

  Notional di bawah minimum          qty sangat kecil → notional \< MIN_NOTIONAL (\$10)       verdict=BLOCKED, reason=\'Notional di bawah minimum\'

  Exchange maintenance               portfolio_state\[\'exchange_status\'\]=\'maintenance\'   verdict=BLOCKED, reason=\'Exchange maintenance\'
  ---------------------------------------------------------------------------------------------------------------------------------------------------

**10.3 Skenario Edge Case**

  ------------------------------------------------------------------------------------------------------------------------------
  **Skenario**                                        **Expected Behavior**
  --------------------------------------------------- --------------------------------------------------------------------------
  equity = 0                                          Pre-trade check: BLOCKED --- tidak bisa sizing dari equity 0

  SL = 0 (tidak di-set)                               StopLoss: hitung default SL, return WARNED (paper) / BLOCKED (live)

  ATR = 0 (data tidak tersedia)                       PositionSizer: fallback ke default_sl_pct, tambahkan warning

  Kelly dengan win_rate = 0                           Kelly: clamp ke 0, fallback ke fixed_fractional

  Dua request bersamaan untuk simbol yang sama        Idempotency check di trade_layer menangani ini --- risk_layer tidak tahu

  consecutive_losses = 5, lalu win                    circuit_breaker.record_win() reset consecutive=0

  Manual halt → manual_resume() → check() dipanggil   Setelah resume: state = NORMAL, semua check normal
  ------------------------------------------------------------------------------------------------------------------------------

**11. Checklist Implementasi Risk Layer**

**11.1 Checklist Fungsional**

  ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------
  **No**   **Item**                                                                       **Verifikasi**                                                                  **Done**
  -------- ------------------------------------------------------------------------------ ------------------------------------------------------------------------------- ----------
  1        risk_manager.evaluate() adalah satu-satunya titik masuk --- tidak ada bypass   grep -rn \'place_order\\\|executor\' \| grep -v \'risk_manager\' harus kosong   ☐

  2        Paper mode: BLOCKED diubah ke WARNED dengan qty tetap diisi                    Unit test: CB halted + paper_mode → verdict=WARNED, qty\>0                      ☐

  3        Semua komponen menerima portfolio_state dict dengan format yang sama           Unit test setiap komponen dengan portfolio_state yang valid                     ☐

  4        circuit_breaker.record_loss/win() dipanggil setelah setiap trade close         Code review exit_layer --- setelah close, record dipanggil                      ☐

  5        circuit_breaker.reset_daily() dipanggil tepat UTC 00:00 oleh scheduler         Mock datetime, verifikasi daily_pnl=0 setelah reset                             ☐

  6        \_apply_lot_filter() menggunakan floor, bukan round                            Test: qty=0.1235, step=0.001 → 0.123 (bukan 0.124)                              ☐

  7        SL di sisi yang salah (BUY + sl \> price) → BLOCKED                            Unit test case ini                                                              ☐

  8        Correlated exposure check berfungsi untuk grup 3+ simbol                       Test: BTC+ETH+BNB semua terbuka, coba buka SOL → check group                    ☐

  9        Manual halt tidak auto-resume (butuh manual_resume())                          Inject manual_halt, tunggu \> halt_duration → masih HALTED                      ☐

  10       leverage_control hanya aktif jika is_futures=True                              Test: leverage=20, is_futures=False → tidak diblokir                            ☐
  ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------

**11.2 Checklist Performa**

  -------------------------------------------------------------------------------------------------------------------------------------------------------------------
  **No**   **Item**                                                                **Verifikasi**                                                          **Done**
  -------- ----------------------------------------------------------------------- ----------------------------------------------------------------------- ----------
  11       evaluate() selesai dalam \< 30ms (tidak ada I/O di dalamnya)            Benchmark test: 1000 evaluasi, avg \< 30ms                              ☐

  12       Tidak ada query DB di dalam evaluate() --- semua dari portfolio_state   grep -n \'sqlite\\\|db\\\|await\' risk_layer/risk_manager.py → kosong   ☐

  13       Tidak ada network call di dalam evaluate()                              grep -n \'requests\\\|aiohttp\\\|await\' risk_layer/\*.py → kosong      ☐
  -------------------------------------------------------------------------------------------------------------------------------------------------------------------

**11.3 Checklist Config**

  -------------------------------------------------------------------------------------------------------------------------------------------------------------------
  **No**   **Item**                                                         **Verifikasi**                                                                 **Done**
  -------- ---------------------------------------------------------------- ------------------------------------------------------------------------------ ----------
  14       Semua threshold risk ada di AgentConfig --- tidak ada hardcode   grep -rn \'0\\.05\\\|0\\.10\\\|0\\.01\' risk_layer/\*.py → hanya dari config   ☐

  15       Config change log diisi setiap kali threshold diubah             Cek agent_core_docs.docx Section 3.4 Config Change Log                         ☐
  -------------------------------------------------------------------------------------------------------------------------------------------------------------------

+:----------------------------------------------------------------------------------------------------------:+
| **Dokumen ini adalah kontrak implementasi Risk Layer.**                                                    |
|                                                                                                            |
| **Tidak ada order yang boleh dikirim ke exchange tanpa melewati risk_manager.evaluate() terlebih dahulu.** |
|                                                                                                            |
| *Referensi: strategy_portfolio_docs.docx • agent_core_docs.docx • risk_layer skeleton code*                |
+------------------------------------------------------------------------------------------------------------+
