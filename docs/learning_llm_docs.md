**crypto_ai_agent**

**Dokumentasi: Learning Layer**

**&**

**LLM Layer**

*reflection · adaptation · drift_detection · experience_processor · strategy_killer*

*llm_client · llm_budget · llm_rate_limiter · trade_analyzer · strategy_feedback · llm_filter*

Versi 1.0 \| Referensi: agent_core_docs.docx · strategy_portfolio_docs.docx

  ------------------------------------------------

  ------------------------------------------------

**1. Overview --- Dua Layer Adaptif**

Learning Layer memungkinkan sistem belajar dari pengalamannya sendiri. LLM Layer menambahkan analisis berbasis AI sebagai advisor --- bukan pengambil keputusan. Keduanya opsional: tanpa keduanya sistem tetap berjalan penuh.

  --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------
  **PRINSIP UTAMA:** LLM TIDAK pernah mengeksekusi order. LLM hanya menghasilkan confidence_multiplier (0.0--1.5) yang dikalikan dengan signal confidence asli. Jika LLM gagal, sistem menggunakan multiplier default 1.0 dan melanjutkan seperti biasa.

  --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------

  ------------------------------------------------------------------------------------------------------------
  **Aspek**        **Learning Layer**                         **LLM Layer**
  ---------------- ------------------------------------------ ------------------------------------------------
  Tujuan           Adaptasi otomatis dari histori trading     Analisis kontekstual berbasis bahasa alami

  Input            experience.db, performance.db              MarketState, Signal, histori trade terakhir

  Output           Parameter diupdate, strategi di-disable    confidence_multiplier (float 0.0--1.5)

  Frekuensi        Harian (batch)                             Per signal (jika budget tersedia)

  Critical path?   Tidak                                      Tidak

  Bisa gagal?      Ya, skip --- tidak mempengaruhi eksekusi   Ya, fallback multiplier=1.0 --- trading lanjut
  ------------------------------------------------------------------------------------------------------------

**1.1 Alur Data**

+--------------------------------------------------------------+
| experience.db → experience_processor → performance.db        |
|                                                              |
| │                                                            |
|                                                              |
| ┌────────┴────────────┐                                      |
|                                                              |
| ▼ ▼                                                          |
|                                                              |
| reflection.py drift_detection.py                             |
|                                                              |
| │ │                                                          |
|                                                              |
| ▼ ▼                                                          |
|                                                              |
| adaptation.py retrain trigger                                |
|                                                              |
| strategy_killer.py                                           |
|                                                              |
| (opsional, per signal):                                      |
|                                                              |
| Signal + MarketState → llm_filter.py → confidence_multiplier |
|                                                              |
| │                                                            |
|                                                              |
| llm_budget.py (cost gate)                                    |
|                                                              |
| llm_rate_limiter.py (throttle)                               |
|                                                              |
| llm_client.py (Anthropic API)                                |
+--------------------------------------------------------------+

**BAGIAN A --- Learning Layer**

**2. experience_processor.py**

Memproses trade tertutup dari experience.db menjadi statistik terstruktur. Berjalan harian UTC 01:00 setelah reconciliation.

**2.1 StrategyStats Dataclass**

+----------------------------------------------------------+
| \@dataclass                                              |
|                                                          |
| class StrategyStats:                                     |
|                                                          |
| strategy_id: str                                         |
|                                                          |
| period: str \# \'7d\' \| \'30d\' \| \'90d\' \| \'all\'   |
|                                                          |
| computed_at: datetime                                    |
|                                                          |
| total_trades: int                                        |
|                                                          |
| winning_trades: int                                      |
|                                                          |
| win_rate: float                                          |
|                                                          |
| profit_factor: float \# gross_profit / gross_loss        |
|                                                          |
| total_pnl_usd: float                                     |
|                                                          |
| avg_win_usd: float                                       |
|                                                          |
| avg_loss_usd: float                                      |
|                                                          |
| avg_risk_reward: float                                   |
|                                                          |
| max_consecutive_wins: int                                |
|                                                          |
| max_consecutive_losses:int                               |
|                                                          |
| max_drawdown_pct: float                                  |
|                                                          |
| sharpe_ratio: float                                      |
|                                                          |
| sortino_ratio: float                                     |
|                                                          |
| avg_hold_candles: float                                  |
|                                                          |
| best_regime: str \# regime dengan win rate tertinggi     |
|                                                          |
| worst_regime: str \# regime dengan win rate terendah     |
|                                                          |
| high_conf_win_rate: float \# win rate confidence \> 0.75 |
|                                                          |
| low_conf_win_rate: float \# win rate confidence \<= 0.75 |
+----------------------------------------------------------+

**2.2 Interface Publik**

+---------------------------------------------------------------------------------------+
| class ExperienceProcessor:                                                            |
|                                                                                       |
| async def process_daily(self) -\> dict\[str, StrategyStats\]:                         |
|                                                                                       |
| \'\'\'Baca experience.db, hitung stats semua periode, simpan ke performance.db.\'\'\' |
|                                                                                       |
| def get_stats(self, strategy_id: str, period: str=\'30d\') -\> StrategyStats \| None: |
|                                                                                       |
| \'\'\'Ambil stats dari performance.db cache.\'\'\'                                    |
|                                                                                       |
| def get_regime_breakdown(self, strategy_id: str, period: str=\'30d\') -\> dict:       |
|                                                                                       |
| \'\'\'Win rate & avg PnL per regime. Input untuk reflection.py.\'\'\'                 |
|                                                                                       |
| def get_confidence_bins(self, strategy_id: str) -\> list\[\'ConfidenceBin\'\]:        |
|                                                                                       |
| \'\'\'                                                                                |
|                                                                                       |
| Bagi histori trade ke bin confidence:                                                 |
|                                                                                       |
| \[0.0-0.5), \[0.5-0.6), \[0.6-0.7), \[0.7-0.8), \[0.8-1.0\]                           |
|                                                                                       |
| Return win_rate & avg_pnl per bin.                                                    |
|                                                                                       |
| Digunakan untuk kalibrasi MIN_SIGNAL_CONFIDENCE.                                      |
|                                                                                       |
| \'\'\'                                                                                |
+---------------------------------------------------------------------------------------+

**2.3 Confidence Bin --- Contoh Output & Aksi**

+-----------------------------------------------------------------------+
| \# Contoh output get_confidence_bins() untuk SpotStrategyV1:          |
|                                                                       |
| \#                                                                    |
|                                                                       |
| \# Bin \| Trades \| Win Rate \| Avg PnL                               |
|                                                                       |
| \# \[0.0-0.5) \| 12 \| 33.3% \| -\$45                                 |
|                                                                       |
| \# \[0.5-0.6) \| 28 \| 46.4% \| -\$12                                 |
|                                                                       |
| \# \[0.6-0.7) \| 47 \| 55.3% \| +\$28 ← threshold optimal ada di sini |
|                                                                       |
| \# \[0.7-0.8) \| 31 \| 71.0% \| +\$67                                 |
|                                                                       |
| \# \[0.8-1.0\] \| 15 \| 80.0% \| +\$89                                |
|                                                                       |
| \#                                                                    |
|                                                                       |
| \# Insight: bin \< 0.65 konsisten negatif.                            |
|                                                                       |
| \# Aksi adaptation.py: naikkan min_signal_confidence ke 0.65          |
+-----------------------------------------------------------------------+

**3. reflection.py --- Analisis Kualitatif**

Menganalisis trade yang sudah ditutup untuk mengidentifikasi pola keberhasilan dan kegagalan. Output dari reflection menjadi masukan untuk adaptation.

**3.1 TradeReflection Dataclass**

+-----------------------------------------------------------------------+
| \@dataclass                                                           |
|                                                                       |
| class TradeReflection:                                                |
|                                                                       |
| trade_id: str                                                         |
|                                                                       |
| verdict: str \# \'good\' \| \'acceptable\' \| \'bad\' \| \'terrible\' |
|                                                                       |
| pnl_r: float \# PnL dalam unit risk (1R = entry ke SL)                |
|                                                                       |
| positives: list\[str\]                                                |
|                                                                       |
| negatives: list\[str\]                                                |
|                                                                       |
| process_followed: bool \# apakah SL, sizing, entry sesuai aturan?     |
|                                                                       |
| lessons: list\[str\]                                                  |
|                                                                       |
| regime_at_entry: str                                                  |
|                                                                       |
| confidence: float                                                     |
|                                                                       |
| hold_candles: int                                                     |
|                                                                       |
| exit_reason: str                                                      |
+-----------------------------------------------------------------------+

**3.2 Logika Penilaian Trade**

  -----------------------------------------------------------------------------------------------------
  **Kondisi**                    **Verdict**      **Penjelasan**
  ------------------------------ ---------------- -----------------------------------------------------
  PnL \>= +2R                    **good**         Trade ideal --- target lebih dari terpenuhi

  PnL \>= 0 ATAU loss \< -0.5R   **acceptable**   Dapat diterima --- proses mungkin sudah benar

  PnL dari -0.5R sampai -1.0R    **bad**          Perlu dikaji --- ada yang bisa diperbaiki

  PnL \< -1.0R                   **terrible**     Proses tidak diikuti --- SL gagal atau sizing salah
  -----------------------------------------------------------------------------------------------------

**3.3 Pola yang Dideteksi (Batch Reflection)**

  -----------------------------------------------------------------------------------------------------------------------------------------
  **Pola**                                      **Cara Deteksi**                                   **Aksi Rekomendasi**
  --------------------------------------------- -------------------------------------------------- ----------------------------------------
  Loss berulang di regime SIDEWAYS              exit_reason=SL & regime_at_entry=sideways \> 3x    Tambahkan regime filter untuk sideways

  Low confidence selalu loss                    bin \[0.0-0.6) win_rate \< 35%                     Naikkan min_signal_confidence

  Hold terlalu pendek, exit prematur            avg_hold \< 3 candle & exit_reason=TRAIL           Perbesar trail_atr_mult

  Profit tergerus oleh trailing terlalu ketat   avg exit_reason=TRAIL dengan PnL rendah            Perbesar trail_atr_mult

  Entry sering di akhir trend                   regime flip terjadi dalam 5 candle setelah entry   Tambahkan regime_stable filter
  -----------------------------------------------------------------------------------------------------------------------------------------

**4. drift_detection.py --- Deteksi Degradasi Model**

Mendeteksi apakah model ML mulai kehilangan kemampuan prediksinya. Early warning system paling kritis di learning layer --- jika diabaikan, sistem akan terus trading dengan model yang tidak relevan.

**4.1 Jenis Drift & Threshold**

  ------------------------------------------------------------------------------------------------------------------------------
  **Jenis Drift**    **Metrik**           **Cara Deteksi**                         **Threshold Alert**   **Threshold Retrain**
  ------------------ -------------------- ---------------------------------------- --------------------- -----------------------
  Feature drift      PSI per fitur        Distribusi live vs distribusi training   0.10--0.25            0.25+

  Prediction drift   KL divergence        Distribusi prediksi live vs baseline     0.30                  0.70

  IC degradation     Rolling IC 20 hari   Spearman(pred, actual_return)            \< 0.04               \< 0.02

  Win rate decline   Pct drop 30 hari     Win rate live vs baseline training       -10%                  -20%

  Regime shift       Cosine distance      Distribusi regime 30 hari vs 90 hari     0.20                  0.40
  ------------------------------------------------------------------------------------------------------------------------------

**4.2 DriftReport Dataclass**

+-----------------------------------------------------------------+
| \@dataclass                                                     |
|                                                                 |
| class DriftReport:                                              |
|                                                                 |
| strategy_id: str                                                |
|                                                                 |
| checked_at: datetime                                            |
|                                                                 |
| feature_drifts: list\[\'FeatureDriftResult\'\] \# PSI per fitur |
|                                                                 |
| max_psi: float                                                  |
|                                                                 |
| drifted_features: list\[str\] \# fitur dengan PSI \> threshold  |
|                                                                 |
| kl_divergence: float                                            |
|                                                                 |
| rolling_ic: float                                               |
|                                                                 |
| rolling_win_rate: float                                         |
|                                                                 |
| baseline_ic: float                                              |
|                                                                 |
| baseline_win_rate: float                                        |
|                                                                 |
| needs_retrain: bool                                             |
|                                                                 |
| needs_alert: bool                                               |
|                                                                 |
| severity: str \# \'none\'\|\'minor\'\|\'moderate\'\|\'major\'   |
|                                                                 |
| recommended_action: str                                         |
+-----------------------------------------------------------------+

**4.3 Interface Publik**

+-------------------------------------------------------------------------------+
| class DriftDetector:                                                          |
|                                                                               |
| def check(                                                                    |
|                                                                               |
| self,                                                                         |
|                                                                               |
| live_features: pd.DataFrame, \# fitur 30 hari terakhir                        |
|                                                                               |
| live_predictions: pd.Series, \# prediksi 30 hari terakhir                     |
|                                                                               |
| live_trades: list, \# closed trades 30 hari                                   |
|                                                                               |
| ) -\> DriftReport:                                                            |
|                                                                               |
| \'\'\'Dipanggil harian UTC 06:00. Jalankan semua check & return report.\'\'\' |
|                                                                               |
| def compute_psi(                                                              |
|                                                                               |
| self, expected: pd.Series, actual: pd.Series, bins: int = 10                  |
|                                                                               |
| ) -\> float:                                                                  |
|                                                                               |
| \'\'\'                                                                        |
|                                                                               |
| Population Stability Index.                                                   |
|                                                                               |
| PSI = sum((actual_pct - expected_pct) \* ln(actual_pct / expected_pct))       |
|                                                                               |
| \< 0.10 = stabil \| 0.10-0.25 = minor \| \> 0.25 = major                      |
|                                                                               |
| \'\'\'                                                                        |
|                                                                               |
| def compute_rolling_ic(                                                       |
|                                                                               |
| self, predictions: pd.Series, actual_returns: pd.Series, window: int = 20     |
|                                                                               |
| ) -\> float:                                                                  |
|                                                                               |
| \'\'\'Spearman correlation rolling window terakhir.\'\'\'                     |
|                                                                               |
| def get_retrain_recommendation(self, report: DriftReport) -\> str:            |
|                                                                               |
| if report.needs_retrain:                                                      |
|                                                                               |
| return \'RETRAIN --- jalankan automation/retrain.py\'                         |
|                                                                               |
| if report.needs_alert:                                                        |
|                                                                               |
| return \'MONITOR --- pantau 3 hari ke depan\'                                 |
|                                                                               |
| return \'OK\'                                                                 |
+-------------------------------------------------------------------------------+

**5. adaptation.py --- Update Parameter Otomatis**

Menyesuaikan parameter strategi berdasarkan hasil analisis. Hanya mengubah parameter dalam batas yang ditentukan --- tidak mengubah arsitektur atau fitur model.

  -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------
  **BATAS ADAPTATION:** Adaptation hanya boleh mengubah parameter dalam rentang MIN--MAX yang telah ditetapkan di config. Perubahan di luar ini tetap memerlukan review manual dan retrain.

  -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------

**5.1 Parameter yang Dapat Di-adapt**

  -----------------------------------------------------------------------------------------------
  **Parameter**           **Min**   **Max**   **Trigger Perubahan**                 **Arah**
  ----------------------- --------- --------- ------------------------------------- -------------
  min_signal_confidence   0.40      0.90      win_rate \< 45% selama 30 hari        Naik 0.05

  sl_atr_multiplier       1.0       3.5       avg loss \> 1.2R selama 20 trade      Naik 0.2

  default_tp_ratio        1.2       5.0       profit_factor \< 1.2 selama 30 hari   Naik 0.2

  signal_cooldown         1         10        3 loss berturut                       Naik 1

  trail_atr_mult          1.0       4.0       TRAIL exit dengan profit rendah       Naik 0.2

  risk_per_trade_pct      0.005     0.03      sharpe \< 0.5 selama 30 hari          Turun 0.002
  -----------------------------------------------------------------------------------------------

**5.2 Interface Publik**

+-----------------------------------------------------------------+
| class AdaptationEngine:                                         |
|                                                                 |
| def evaluate(                                                   |
|                                                                 |
| self, stats: StrategyStats, config: AgentConfig                 |
|                                                                 |
| ) -\> \'AdaptationResult\':                                     |
|                                                                 |
| \'\'\'Evaluasi & usulkan perubahan. TIDAK langsung apply.\'\'\' |
|                                                                 |
| def apply(                                                      |
|                                                                 |
| self, result: \'AdaptationResult\',                             |
|                                                                 |
| config: AgentConfig, dry_run: bool = False                      |
|                                                                 |
| ) -\> bool:                                                     |
|                                                                 |
| \'\'\'                                                          |
|                                                                 |
| Simpan config_backup SEBELUM apply.                             |
|                                                                 |
| dry_run=True → hanya log, tidak ubah.                           |
|                                                                 |
| \'\'\'                                                          |
|                                                                 |
| def rollback(                                                   |
|                                                                 |
| self, result: \'AdaptationResult\', config: AgentConfig         |
|                                                                 |
| ) -\> bool:                                                     |
|                                                                 |
| \'\'\'Kembalikan ke config_backup dari result.\'\'\'            |
|                                                                 |
| def get_adaptation_history(                                     |
|                                                                 |
| self, strategy_id: str, last_n: int = 10                        |
|                                                                 |
| ) -\> list\[\'AdaptationResult\'\]:                             |
|                                                                 |
| \'\'\'Riwayat adaptasi dari DB untuk audit.\'\'\'               |
+-----------------------------------------------------------------+

**6. strategy_killer.py --- Auto-Disable Strategi**

Menonaktifkan strategi underperform secara otomatis. Posisi yang sudah terbuka tetap dikelola sampai ditutup secara normal.

**6.1 Kriteria Kill**

  -------------------------------------------------------------------------------
  **Kriteria**         **Threshold**   **Periode**   **Logika**
  -------------------- --------------- ------------- ----------------------------
  Consecutive losses   \> 7 berturut   Real-time     ANY satu kondisi terpenuhi

  Sharpe ratio         \< -0.5         30 hari       ANY satu kondisi terpenuhi

  Win rate             \< 35%          30 hari       ANY satu kondisi terpenuhi

  Max drawdown         \> 15%          30 hari       ANY satu kondisi terpenuhi

  Profit factor        \< 0.7          30 hari       ANY satu kondisi terpenuhi
  -------------------------------------------------------------------------------

**6.2 Disable Flow**

+-----------------------------------------------------------------------------+
| async def \_kill(self, strategy_id: str, reasons: list\[str\]) -\> None:    |
|                                                                             |
| \# Urutan disable yang aman:                                                |
|                                                                             |
| \# 1. Stop terima signal baru dari strategi ini                             |
|                                                                             |
| self.\_registry.disable(strategy_id, \'; \'.join(reasons))                  |
|                                                                             |
| \# 2. Posisi yang sudah ada TETAP di-manage (tidak di-force close)          |
|                                                                             |
| \# 3. Catat ke disabled_strategies.json                                     |
|                                                                             |
| await self.\_save_disabled_record(strategy_id, reasons)                     |
|                                                                             |
| \# 4. Kirim alert                                                           |
|                                                                             |
| await self.\_alerts.send(Alert(                                             |
|                                                                             |
| severity = AlertSeverity.WARNING,                                           |
|                                                                             |
| title = f\'Strategi Dinonaktifkan: {strategy_id}\',                         |
|                                                                             |
| message = str(reasons),                                                     |
|                                                                             |
| component = \'strategy_killer\',                                            |
|                                                                             |
| ))                                                                          |
|                                                                             |
| \# 5. Jadwalkan re-evaluasi setelah REVIEW_AFTER_DAYS hari                  |
|                                                                             |
| await self.schedule_review(strategy_id, days=self.config.review_after_days) |
+-----------------------------------------------------------------------------+

**6.3 Config Keys Strategy Killer**

  --------------------------------------------------------------------------------------
  **Key**                   **Default**   **Keterangan**
  ------------------------- ------------- ----------------------------------------------
  KILL_SHARPE_THRESHOLD     -0.5          Sharpe di bawah ini → disable

  KILL_WIN_RATE_THRESHOLD   0.35          Win rate di bawah 35% → disable

  KILL_DD_THRESHOLD         0.15          Drawdown 30 hari \> 15% → disable

  KILL_CONSEC_LOSSES        7             7 loss berturut → disable

  KILL_PF_THRESHOLD         0.7           Profit factor \< 0.7 → disable

  MIN_TRADES_FOR_EVAL       20            Strategi dengan \< 20 trade tidak dievaluasi

  REVIEW_AFTER_DAYS         7             Re-evaluasi otomatis setelah 7 hari nonaktif
  --------------------------------------------------------------------------------------

**BAGIAN B --- LLM Layer**

**7. llm_client.py --- Koneksi Anthropic API**

**7.1 LLMRequest & LLMResponse**

+-----------------------------------------------------------------+
| \@dataclass                                                     |
|                                                                 |
| class LLMRequest:                                               |
|                                                                 |
| system_prompt: str                                              |
|                                                                 |
| user_message: str                                               |
|                                                                 |
| model: str = \'claude-haiku-4-5-20251001\'                      |
|                                                                 |
| max_tokens: int = 512                                           |
|                                                                 |
| temperature: float = 0.1 \# rendah untuk konsistensi            |
|                                                                 |
| timeout_s: int = 10 \# timeout ketat --- jangan block main loop |
|                                                                 |
| \@dataclass                                                     |
|                                                                 |
| class LLMResponse:                                              |
|                                                                 |
| content: str                                                    |
|                                                                 |
| input_tokens: int                                               |
|                                                                 |
| output_tokens: int                                              |
|                                                                 |
| model: str                                                      |
|                                                                 |
| latency_ms: float                                               |
|                                                                 |
| cost_usd: float                                                 |
|                                                                 |
| success: bool                                                   |
|                                                                 |
| error: str = \'\'                                               |
+-----------------------------------------------------------------+

**7.2 Interface & Error Handling**

+---------------------------------------------------------------------+
| class LLMClient:                                                    |
|                                                                     |
| PRICING = {                                                         |
|                                                                     |
| \'claude-haiku-4-5-20251001\': {\'input\': 0.25, \'output\': 1.25}, |
|                                                                     |
| \'claude-sonnet-4-6\': {\'input\': 3.00, \'output\': 15.00},        |
|                                                                     |
| \'claude-opus-4-6\': {\'input\': 15.00, \'output\': 75.00},         |
|                                                                     |
| }                                                                   |
|                                                                     |
| async def call(self, request: LLMRequest) -\> LLMResponse:          |
|                                                                     |
| try:                                                                |
|                                                                     |
| start = time.time()                                                 |
|                                                                     |
| resp = await asyncio.wait_for(                                      |
|                                                                     |
| self.\_api_call(request), timeout=request.timeout_s                 |
|                                                                     |
| )                                                                   |
|                                                                     |
| return LLMResponse(                                                 |
|                                                                     |
| content = resp.content\[0\].text,                                   |
|                                                                     |
| input_tokens = resp.usage.input_tokens,                             |
|                                                                     |
| output_tokens = resp.usage.output_tokens,                           |
|                                                                     |
| model = request.model,                                              |
|                                                                     |
| latency_ms = (time.time()-start)\*1000,                             |
|                                                                     |
| cost_usd = self.\_compute_cost(resp.usage, request.model),          |
|                                                                     |
| success = True,                                                     |
|                                                                     |
| )                                                                   |
|                                                                     |
| except asyncio.TimeoutError:                                        |
|                                                                     |
| return LLMResponse(content=\'\',input_tokens=0,output_tokens=0,     |
|                                                                     |
| model=request.model,latency_ms=10000,cost_usd=0,                    |
|                                                                     |
| success=False,error=\'Timeout 10s\')                                |
|                                                                     |
| except Exception as e:                                              |
|                                                                     |
| return LLMResponse(content=\'\',input_tokens=0,output_tokens=0,     |
|                                                                     |
| model=request.model,latency_ms=0,cost_usd=0,                        |
|                                                                     |
| success=False,error=str(e))                                         |
+---------------------------------------------------------------------+

**8. llm_budget.py & llm_rate_limiter.py**

**8.1 llm_budget.py --- Cost Control**

+---------------------------------------------------------------------------+
| class LLMBudget:                                                          |
|                                                                           |
| async def can_call(                                                       |
|                                                                           |
| self, estimated_tokens: int=500, model: str=\'claude-haiku-4-5-20251001\' |
|                                                                           |
| ) -\> bool:                                                               |
|                                                                           |
| \'\'\'Return False jika spent + estimated_cost \> daily_limit.\'\'\'      |
|                                                                           |
| pricing = LLMClient.PRICING\[model\]                                      |
|                                                                           |
| est_cost = (estimated_tokens\*pricing\[\'input\'\] +                      |
|                                                                           |
| estimated_tokens\*pricing\[\'output\'\]) / 1_000_000                      |
|                                                                           |
| return (self.\_spent_today + est_cost) \<= self.\_daily_limit             |
|                                                                           |
| async def record_usage(self, response: LLMResponse) -\> None:             |
|                                                                           |
| self.\_spent_today += response.cost_usd                                   |
|                                                                           |
| self.\_calls_today += 1                                                   |
|                                                                           |
| await self.\_persist() \# survive restart                                 |
|                                                                           |
| def reset_daily(self) -\> None:                                           |
|                                                                           |
| \'\'\'Dipanggil scheduler UTC 00:00.\'\'\'                                |
|                                                                           |
| self.\_spent_today = 0.0                                                  |
|                                                                           |
| self.\_calls_today = 0                                                    |
+---------------------------------------------------------------------------+

**8.2 Budget Allocation --- Rekomendasi**

  ---------------------------------------------------------------------------------------------------------------------------------
  **Model**                   **Input /1M tok**   **Output /1M tok**   **Rekomendasi**                        **Budget \$1/hari**
  --------------------------- ------------------- -------------------- -------------------------------------- ---------------------
  claude-haiku-4-5-20251001   \$0.25              \$1.25               Default untuk filter per signal        \~650 panggilan

  claude-sonnet-4-6           \$3.00              \$15.00              Analisis mendalam, batasi 5-10x/hari   \~50 panggilan

  claude-opus-4-6             \$15.00             \$75.00              Review mingguan saja                   \~10 panggilan
  ---------------------------------------------------------------------------------------------------------------------------------

**8.3 llm_rate_limiter.py --- Token Bucket**

+-------------------------------------------------------------------------------------------+
| class LLMRateLimiter:                                                                     |
|                                                                                           |
| \'\'\'Token bucket algorithm. Tidak memblokir --- return False jika limit tercapai.\'\'\' |
|                                                                                           |
| def \_\_init\_\_(                                                                         |
|                                                                                           |
| self,                                                                                     |
|                                                                                           |
| calls_per_minute: int = 5,                                                                |
|                                                                                           |
| tokens_per_minute: int = 50_000,                                                          |
|                                                                                           |
| ):                                                                                        |
|                                                                                           |
| self.\_call_bucket = TokenBucket(calls_per_minute)                                        |
|                                                                                           |
| self.\_token_bucket = TokenBucket(tokens_per_minute)                                      |
|                                                                                           |
| def acquire(self, estimated_tokens: int = 500) -\> bool:                                  |
|                                                                                           |
| \'\'\'Non-blocking. Return False jika rate limit tercapai.\'\'\'                          |
|                                                                                           |
| return (self.\_call_bucket.consume(1) and                                                 |
|                                                                                           |
| self.\_token_bucket.consume(estimated_tokens))                                            |
+-------------------------------------------------------------------------------------------+

**9. llm_filter.py --- Confidence Scoring**

Komponen utama LLM layer. Menghasilkan confidence_multiplier yang dikalikan dengan signal confidence asli sebelum dikirim ke risk layer.

**9.1 LLMScore Dataclass**

+---------------------------------------------------------------+
| \@dataclass                                                   |
|                                                               |
| class LLMScore:                                               |
|                                                               |
| confidence_multiplier: float \# 0.0--1.5                      |
|                                                               |
| proceed: bool \# True jika multiplier \>= 0.5                 |
|                                                               |
| reasoning: str                                                |
|                                                               |
| concerns: list\[str\]                                         |
|                                                               |
| model_used: str                                               |
|                                                               |
| cost_usd: float                                               |
|                                                               |
| latency_ms: float                                             |
|                                                               |
| fallback_used: bool \# True jika LLM gagal, pakai default 1.0 |
|                                                               |
| \@property                                                    |
|                                                               |
| def is_blocking(self) -\> bool:                               |
|                                                               |
| return self.confidence_multiplier \< 0.3                      |
+---------------------------------------------------------------+

**9.2 System Prompt Template**

+----------------------------------------------------------------------+
| SYSTEM_PROMPT = \'\'\'                                               |
|                                                                      |
| Kamu adalah analis trading cryptocurrency berpengalaman.             |
|                                                                      |
| Evaluasi signal trading dan berikan respons HANYA dalam format JSON: |
|                                                                      |
| {                                                                    |
|                                                                      |
| \"confidence_multiplier\": \<float 0.0-1.5\>,                        |
|                                                                      |
| \"proceed\": \<bool\>,                                               |
|                                                                      |
| \"reasoning\": \"\<satu kalimat\>\",                                 |
|                                                                      |
| \"concerns\": \[\"\<concern 1\>\", \"\<concern 2\>\"\]               |
|                                                                      |
| }                                                                    |
|                                                                      |
| Panduan confidence_multiplier:                                       |
|                                                                      |
| 1.5 = signal sangat kuat, kondisi ideal                              |
|                                                                      |
| 1.0 = signal normal, tidak ada kekhawatiran                          |
|                                                                      |
| 0.7 = ada kekhawatiran minor                                         |
|                                                                      |
| 0.3 = kekhawatiran serius, pertimbangkan skip                        |
|                                                                      |
| 0.0 = jangan trade                                                   |
|                                                                      |
| \'\'\'                                                               |
+----------------------------------------------------------------------+

**9.3 score_signal() --- Alur Lengkap**

+---------------------------------------------------------------------------+
| async def score_signal(                                                   |
|                                                                           |
| self, signal: Signal, state: MarketState, last_trades: list               |
|                                                                           |
| ) -\> LLMScore:                                                           |
|                                                                           |
| \# Step 1: Budget check                                                   |
|                                                                           |
| if not await self.\_budget.can_call():                                    |
|                                                                           |
| return self.\_fallback(\'Budget harian habis\')                           |
|                                                                           |
| \# Step 2: Rate limit check                                               |
|                                                                           |
| if not self.\_rate_limiter.acquire():                                     |
|                                                                           |
| return self.\_fallback(\'Rate limit tercapai\')                           |
|                                                                           |
| \# Step 3: Build & call                                                   |
|                                                                           |
| request = LLMRequest(                                                     |
|                                                                           |
| system_prompt = SYSTEM_PROMPT,                                            |
|                                                                           |
| user_message = self.\_build_message(signal, state, last_trades),          |
|                                                                           |
| model = self.\_config.llm_model,                                          |
|                                                                           |
| max_tokens = 256,                                                         |
|                                                                           |
| temperature = 0.1,                                                        |
|                                                                           |
| )                                                                         |
|                                                                           |
| response = await self.\_client.call(request)                              |
|                                                                           |
| if not response.success:                                                  |
|                                                                           |
| return self.\_fallback(f\'LLM error: {response.error}\')                  |
|                                                                           |
| \# Step 4: Parse JSON & clamp                                             |
|                                                                           |
| try:                                                                      |
|                                                                           |
| data = json.loads(response.content)                                       |
|                                                                           |
| multiplier = max(0.0, min(1.5, float(data\[\'confidence_multiplier\'\]))) |
|                                                                           |
| except (json.JSONDecodeError, KeyError, ValueError):                      |
|                                                                           |
| return self.\_fallback(\'Invalid JSON dari LLM\')                         |
|                                                                           |
| \# Step 5: Record usage                                                   |
|                                                                           |
| await self.\_budget.record_usage(response)                                |
|                                                                           |
| return LLMScore(                                                          |
|                                                                           |
| confidence_multiplier = multiplier,                                       |
|                                                                           |
| proceed = data.get(\'proceed\', multiplier \>= 0.5),                      |
|                                                                           |
| reasoning = data.get(\'reasoning\', \'\'),                                |
|                                                                           |
| concerns = data.get(\'concerns\', \[\]),                                  |
|                                                                           |
| model_used = response.model,                                              |
|                                                                           |
| cost_usd = response.cost_usd,                                             |
|                                                                           |
| latency_ms = response.latency_ms,                                         |
|                                                                           |
| fallback_used = False,                                                    |
|                                                                           |
| )                                                                         |
|                                                                           |
| def \_fallback(self, reason: str) -\> LLMScore:                           |
|                                                                           |
| log.warning(\'LLM fallback\', reason=reason)                              |
|                                                                           |
| return LLMScore(                                                          |
|                                                                           |
| confidence_multiplier = 1.0, \# default: tidak mengubah signal            |
|                                                                           |
| proceed=True, reasoning=f\'Fallback: {reason}\',                          |
|                                                                           |
| concerns=\[\], model_used=\'fallback\',                                   |
|                                                                           |
| cost_usd=0.0, latency_ms=0.0, fallback_used=True,                         |
|                                                                           |
| )                                                                         |
+---------------------------------------------------------------------------+

**10. trade_analyzer.py & strategy_feedback.py**

**10.1 trade_analyzer.py**

Analisis mendalam trade signifikan (\|pnl\| \> 2× risk) dan kondisi market tidak biasa. Menggunakan claude-sonnet untuk kualitas analisis yang lebih baik.

+------------------------------------------------------------------------+
| class TradeAnalyzer:                                                   |
|                                                                        |
| async def analyze_closed_trade(                                        |
|                                                                        |
| self, trade: \'ClosedTrade\'                                           |
|                                                                        |
| ) -\> \'TradeAnalysis\' \| None:                                       |
|                                                                        |
| \'\'\'Hanya untuk trade dengan \|pnl\| \> 2x risk_amount.\'\'\'        |
|                                                                        |
| if abs(trade.pnl_usd) \< trade.risk_amount_usd \* 2:                   |
|                                                                        |
| return None                                                            |
|                                                                        |
| \# Prompt meminta 3 insight: timing, SL placement, lesson              |
|                                                                        |
| \# Format: {\"timing\": str, \"sl_assessment\": str, \"lesson\": str}  |
|                                                                        |
| \...                                                                   |
|                                                                        |
| async def analyze_unusual_market(                                      |
|                                                                        |
| self, state: MarketState, anomalies: list\[str\]                       |
|                                                                        |
| ) -\> str:                                                             |
|                                                                        |
| \'\'\'Dipanggil saat anomaly_detector mendeteksi MEDIUM anomali.\'\'\' |
|                                                                        |
| \...                                                                   |
+------------------------------------------------------------------------+

**10.2 strategy_feedback.py --- Feedback Mingguan**

+--------------------------------------------------------------------------------------+
| class StrategyFeedback:                                                              |
|                                                                                      |
| \'\'\'Mengirim ringkasan performa ke LLM mingguan dan meminta saran perbaikan.\'\'\' |
|                                                                                      |
| async def weekly_feedback(                                                           |
|                                                                                      |
| self,                                                                                |
|                                                                                      |
| strategy_id: str,                                                                    |
|                                                                                      |
| stats: StrategyStats,                                                                |
|                                                                                      |
| reflections: list\[TradeReflection\],                                                |
|                                                                                      |
| ) -\> \'FeedbackReport\':                                                            |
|                                                                                      |
| \'\'\'                                                                               |
|                                                                                      |
| Dipanggil scheduler mingguan Minggu 03:00 UTC.                                       |
|                                                                                      |
| Menggunakan claude-sonnet (lebih dalam dari haiku).                                  |
|                                                                                      |
| Prompt mencakup: statistik 30 hari + pola refleksi berulang.                         |
|                                                                                      |
| Meminta 3 rekomendasi spesifik dalam format JSON.                                    |
|                                                                                      |
| \'\'\'                                                                               |
|                                                                                      |
| \@dataclass                                                                          |
|                                                                                      |
| class FeedbackReport:                                                                |
|                                                                                      |
| strategy_id: str                                                                     |
|                                                                                      |
| week_ending: datetime                                                                |
|                                                                                      |
| recommendations: list\[str\] \# 3 rekomendasi dari LLM                               |
|                                                                                      |
| model_used: str                                                                      |
|                                                                                      |
| cost_usd: float                                                                      |
+--------------------------------------------------------------------------------------+

**11. Integrasi --- Cara Main Loop Menggunakan Kedua Layer**

**11.1 LLM Filter dalam Main Loop**

+-------------------------------------------------------------------+
| \# Setelah risk_layer approve signal, sebelum trade_layer.open(): |
|                                                                   |
| async def apply_llm_filter(                                       |
|                                                                   |
| signal: Signal, state: MarketState,                               |
|                                                                   |
| llm: LLMFilter, config: AgentConfig,                              |
|                                                                   |
| ) -\> Signal \| None:                                             |
|                                                                   |
| if not config.llm_enabled:                                        |
|                                                                   |
| return signal \# disabled → lewati tanpa modifikasi               |
|                                                                   |
| last_trades = trade_store.get_closed_trades(                      |
|                                                                   |
| limit=5, strategy_id=signal.strategy_id)                          |
|                                                                   |
| score = await llm_filter.score_signal(signal, state, last_trades) |
|                                                                   |
| \# Update final_confidence dengan multiplier                      |
|                                                                   |
| signal.final_confidence = min(                                    |
|                                                                   |
| signal.final_confidence \* score.confidence_multiplier, 1.0)      |
|                                                                   |
| signal.metadata\[\'llm_score\'\] = {                              |
|                                                                   |
| \'multiplier\': score.confidence_multiplier,                      |
|                                                                   |
| \'reasoning\': score.reasoning,                                   |
|                                                                   |
| \'fallback\': score.fallback_used,                                |
|                                                                   |
| }                                                                 |
|                                                                   |
| \# Jika confidence setelah multiplier di bawah threshold → skip   |
|                                                                   |
| if signal.final_confidence \< config.min_signal_confidence:       |
|                                                                   |
| return None                                                       |
|                                                                   |
| return signal                                                     |
+-------------------------------------------------------------------+

**11.2 Schedule Learning Layer**

  -------------------------------------------------------------------------------------------------------------------
  **Komponen**                          **Waktu**                           **Output**
  ------------------------------------- ----------------------------------- -----------------------------------------
  experience_processor.process_daily    UTC 01:00                           StrategyStats ke performance.db

  reflection.reflect_batch              UTC 01:15                           BatchReflection --- pola berulang

  adaptation.evaluate + apply           UTC 01:30                           Config update (jika perlu)

  drift_detection.check                 UTC 06:00                           DriftReport, trigger retrain jika perlu

  strategy_killer.evaluate_all          UTC 07:00                           Disable strategi underperform

  strategy_feedback.weekly_feedback     Minggu 03:00                        FeedbackReport dengan rekomendasi LLM

  trade_analyzer.analyze_closed_trade   On-demand (setelah trade besar)     TradeAnalysis insight

  llm_filter.score_signal               Per signal (jika budget tersedia)   confidence_multiplier
  -------------------------------------------------------------------------------------------------------------------

**11.3 Dependency Map**

  ------------------------------------------------------------------------------------------------------------------------
  **File**                  **Import Dari**                                **Dikonsumsi Oleh**
  ------------------------- ---------------------------------------------- -----------------------------------------------
  experience_processor.py   experience.db, performance.db                  reflection, adaptation, strategy_killer

  reflection.py             experience_processor                           adaptation, strategy_feedback

  adaptation.py             reflection, config                             AgentConfig (langsung update)

  drift_detection.py        models/metadata.json, live features            automation/retrain.py, alerts

  strategy_killer.py        experience_processor, registry                 registry (disable), alerts

  llm_client.py             Anthropic API                                  llm_filter, trade_analyzer, strategy_feedback

  llm_budget.py             DB (budget persist)                            llm_filter, trade_analyzer, strategy_feedback

  llm_rate_limiter.py       --- (in-memory token bucket)                   llm_filter

  llm_filter.py             llm_client, llm_budget, llm_rate_limiter       main_loop apply_llm_filter()

  trade_analyzer.py         llm_client, llm_budget                         main_loop (after large trade close)

  strategy_feedback.py      llm_client, llm_budget, experience_processor   scheduler weekly
  ------------------------------------------------------------------------------------------------------------------------

**12. Checklist Implementasi**

**12.1 Checklist Learning Layer**

  -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------
  **No**   **Item**                                                                       **Verifikasi**                                                               **Done**
  -------- ------------------------------------------------------------------------------ ---------------------------------------------------------------------------- ----------
  1        experience_processor hanya membaca experience.db --- tidak write ke state.db   grep -n \'state.db\' experience_processor.py → kosong                        ☐

  2        adaptation.apply() simpan config_backup sebelum apply perubahan                Test rollback: apply → rollback → config kembali semula                      ☐

  3        Parameter adaptation tidak keluar batas MIN/MAX yang ditentukan                Test: inject stats sangat buruk → parameter clamp di batas                   ☐

  4        drift_detection menggunakan baseline dari models/metadata.json                 Unit test: mock metadata, pastikan PSI dihitung dari baseline training       ☐

  5        strategy_killer tidak menutup posisi yang ada saat disable strategi            Test: kill strategi dengan 2 posisi → posisi tetap open                      ☐

  6        Strategi dengan \< MIN_TRADES_FOR_EVAL trade tidak dievaluasi killer           Test: strategi 10 trade → tidak di-evaluate                                  ☐

  7        Semua learning task via scheduler, bukan di dalam main tick loop               grep \'experience_processor\\\|reflection\\\|adaptation\' main.py → kosong   ☐

  8        Riwayat adaptasi disimpan ke DB untuk audit dan rollback                       Cek performance.db ada tabel adaptation_history                              ☐
  -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------

**12.2 Checklist LLM Layer**

  --------------------------------------------------------------------------------------------------------------------------------------------------------------------
  **No**   **Item**                                                                  **Verifikasi**                                                         **Done**
  -------- ------------------------------------------------------------------------- ---------------------------------------------------------------------- ----------
  9        score_signal() tidak blocking main loop --- timeout 10 detik              Test: simulasi LLM lambat → fallback setelah 10 detik, signal lanjut   ☐

  10       LLM fallback (multiplier=1.0) saat LLM gagal --- trading tidak berhenti   Test: matikan network → fallback, signal tetap diproses                ☐

  11       confidence_multiplier di-clamp ke \[0.0, 1.5\] setelah parse JSON         Test: inject JSON dengan multiplier=5.0 → di-clamp ke 1.5              ☐

  12       llm_budget.can_call() dipanggil SEBELUM llm_client.call()                 Code review llm_filter.py --- budget check di baris pertama            ☐

  13       llm_budget.reset_daily() ada di scheduler UTC 00:00                       Code review scheduler.py --- llm_budget_reset task terdaftar           ☐

  14       LLM tidak bisa trigger order langsung                                     grep -rn \'place_order\\\|executor\' llm_layer/ → kosong               ☐

  15       JSON response diparse dengan try-except --- fallback jika invalid         Test: inject invalid JSON → fallback=True, tidak crash                 ☐
  --------------------------------------------------------------------------------------------------------------------------------------------------------------------

+:-----------------------------------------------------------------------------------------------------:+
| **Dokumen ini adalah kontrak implementasi Learning Layer & LLM Layer.**                               |
|                                                                                                       |
| LLM hanya sebagai advisor. Tanpa LLM pun sistem harus berjalan penuh. Fallback selalu multiplier=1.0. |
|                                                                                                       |
| *Referensi: agent_core_docs.docx • strategy_portfolio_docs.docx • exit_monitoring_sync_docs.docx*     |
|                                                                                                       |
| **Ini adalah dokumen terakhir dalam seri dokumentasi crypto_ai_agent.**                               |
+-------------------------------------------------------------------------------------------------------+
