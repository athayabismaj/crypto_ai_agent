**crypto_ai_agent**

**Dokumentasi: Strategy Layer**

**&**

**Portfolio Layer**

*base_strategy · spot_strategy · futures_strategy · strategy_utils*

*allocator · capital_manager · correlation · risk_budget*

Versi 1.0 \| Referensi: data_intelligence_docs.docx · agent_core_docs.docx

  ------------------------------------------------

  ------------------------------------------------

**1. Overview --- Dua Layer, Satu Tujuan**

Strategy Layer menghasilkan sinyal trading berdasarkan kondisi market. Portfolio Layer memastikan sinyal tersebut tidak merusak struktur modal secara keseluruhan. Keduanya bekerja sebelum Risk Layer mengeksekusi keputusan akhir.

  ------------------------------------------------------------------------------------------------------------------
  **Aspek**            **Strategy Layer**                           **Portfolio Layer**
  -------------------- -------------------------------------------- ------------------------------------------------
  Pertanyaan utama     Apakah ada peluang di market ini sekarang?   Apakah kita masih mampu mengambil peluang ini?

  Input                MarketState (dari intelligence layer)        PortfolioState, alokasi aktif, korelasi

  Output               Signal atau None                             Approved quota atau BLOCKED

  Tahu tentang uang?   Tidak --- hanya tahu soal market             Ya --- tahu semua posisi dan modal

  Stateful?            Minimal --- hanya cooldown per simbol        Ya --- melacak semua alokasi aktif

  I/O?                 Tidak --- pure computation                   Tidak --- pure computation

  Dipanggil dari       main_loop setelah intelligence update        risk_layer sebelum position sizing
  ------------------------------------------------------------------------------------------------------------------

**1.1 Posisi dalam Alur Trading**

+------------------------------------------------------------+
| MarketState (dari intelligence_layer)                      |
|                                                            |
| │                                                          |
|                                                            |
| ▼                                                          |
|                                                            |
| strategy_layer/                                            |
|                                                            |
| ├── base_strategy.py ← abstract contract                   |
|                                                            |
| ├── spot_strategy.py ← sinyal spot                         |
|                                                            |
| ├── futures_strategy.py ← sinyal futures + hedge           |
|                                                            |
| └── strategy_utils.py ← helper bersama                     |
|                                                            |
| │                                                          |
|                                                            |
| ▼ Signal                                                   |
|                                                            |
| │                                                          |
|                                                            |
| portfolio_layer/ ← validasi alokasi modal                  |
|                                                            |
| ├── allocator.py ← quota per strategi & simbol             |
|                                                            |
| ├── capital_manager.py ← equity tracking global            |
|                                                            |
| ├── correlation.py ← batas exposure antar aset berkorelasi |
|                                                            |
| └── risk_budget.py ← distribusi risiko per strategi        |
|                                                            |
| │                                                          |
|                                                            |
| ▼ Signal + approved_quota                                  |
|                                                            |
| │                                                          |
|                                                            |
| risk_layer/ ← position sizing + circuit breaker            |
+------------------------------------------------------------+

**BAGIAN A --- Strategy Layer**

**2. base_strategy.py --- Abstract Contract**

Mendefinisikan kontrak yang harus dipenuhi semua strategi. Setiap strategi baru WAJIB mengextend BaseStrategy dan mengimplementasikan semua method abstract.

  -----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------
  **ATURAN KERAS:** Tidak ada akses jaringan, tidak ada DB read/write, tidak ada side effect di dalam method strategy. generate_signal() dan should_exit() harus pure function yang bisa dipanggil 1000x tanpa masalah.

  -----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------

**2.1 Abstract Base Class**

+--------------------------------------------------------------------------------------------+
| from abc import ABC, abstractmethod                                                        |
|                                                                                            |
| from dataclasses import dataclass, field                                                   |
|                                                                                            |
| from typing import Optional                                                                |
|                                                                                            |
| class BaseStrategy(ABC):                                                                   |
|                                                                                            |
| def \_\_init\_\_(self, config: AgentConfig, model: TrainedModel):                          |
|                                                                                            |
| self.config = config                                                                       |
|                                                                                            |
| self.model = model                                                                         |
|                                                                                            |
| self.strategy_id = self.\_\_class\_\_.\_\_name\_\_                                         |
|                                                                                            |
| self.\_last_signal: dict\[str, datetime\] = {} \# cooldown tracker                         |
|                                                                                            |
| self.\_signal_count: dict\[str, int\] = {} \# burst limiter                                |
|                                                                                            |
| \# ── WAJIB diimplementasikan ──────────────────────────────                               |
|                                                                                            |
| \@abstractmethod                                                                           |
|                                                                                            |
| def generate_signal(                                                                       |
|                                                                                            |
| self,                                                                                      |
|                                                                                            |
| state: MarketState                                                                         |
|                                                                                            |
| ) -\> Optional\[\'Signal\'\]:                                                              |
|                                                                                            |
| \"\"\"                                                                                     |
|                                                                                            |
| Kembalikan Signal jika ada peluang, None jika tidak.                                       |
|                                                                                            |
| Dipanggil SETIAP TICK oleh main_loop.                                                      |
|                                                                                            |
| BOLEH: baca state, panggil model.predict(), hitung threshold                               |
|                                                                                            |
| DILARANG: akses exchange, tulis DB, kirim notifikasi, I/O apapun                           |
|                                                                                            |
| \"\"\"                                                                                     |
|                                                                                            |
| \@abstractmethod                                                                           |
|                                                                                            |
| def should_exit(                                                                           |
|                                                                                            |
| self,                                                                                      |
|                                                                                            |
| position: \'Position\',                                                                    |
|                                                                                            |
| state: MarketState                                                                         |
|                                                                                            |
| ) -\> Optional\[\'ExitSignal\'\]:                                                          |
|                                                                                            |
| \"\"\"                                                                                     |
|                                                                                            |
| Evaluasi apakah posisi yang ada harus ditutup berdasarkan                                  |
|                                                                                            |
| kondisi market saat ini (di luar SL/TP yang dikelola exit_layer).                          |
|                                                                                            |
| Contoh: exit karena regime berubah, atau model flip negatif.                               |
|                                                                                            |
| \"\"\"                                                                                     |
|                                                                                            |
| \@abstractmethod                                                                           |
|                                                                                            |
| def get_signal_metadata(self, state: MarketState) -\> dict:                                |
|                                                                                            |
| \"\"\"                                                                                     |
|                                                                                            |
| Return metadata tambahan untuk logging & LLM review.                                       |
|                                                                                            |
| Minimal: {\'reasoning\': str, \'model_output\': float}                                     |
|                                                                                            |
| \"\"\"                                                                                     |
|                                                                                            |
| \# ── BOLEH di-override (ada default) ─────────────────────                                |
|                                                                                            |
| def is_allowed_to_trade(                                                                   |
|                                                                                            |
| self,                                                                                      |
|                                                                                            |
| state: MarketState                                                                         |
|                                                                                            |
| ) -\> tuple\[bool, str\]:                                                                  |
|                                                                                            |
| \"\"\"                                                                                     |
|                                                                                            |
| Filter global sebelum generate_signal dipanggil.                                           |
|                                                                                            |
| Default checks (bisa di-override):                                                         |
|                                                                                            |
| 1\. Regime filter --- blokir jika HIGH_VOLATILITY                                          |
|                                                                                            |
| 2\. Spread filter --- blokir jika spread \> MAX_SPREAD                                     |
|                                                                                            |
| 3\. Cooldown filter --- blokir jika terlalu cepat dari signal terakhir                     |
|                                                                                            |
| 4\. Safety filter --- blokir jika is_safe_to_trade = False                                 |
|                                                                                            |
| \"\"\"                                                                                     |
|                                                                                            |
| \# Check 1: Safety                                                                         |
|                                                                                            |
| if not state.is_safe_to_trade:                                                             |
|                                                                                            |
| return False, \'Market not safe to trade (anomaly detected)\'                              |
|                                                                                            |
| \# Check 2: Regime filter                                                                  |
|                                                                                            |
| if self.config.regime_filter:                                                              |
|                                                                                            |
| if state.regime == MarketRegime.HIGH_VOLATILITY:                                           |
|                                                                                            |
| return False, f\'Blocked: regime is HIGH_VOLATILITY\'                                      |
|                                                                                            |
| \# Check 3: Spread filter                                                                  |
|                                                                                            |
| if state.spread_pct \> self.config.max_spread_pct:                                         |
|                                                                                            |
| return False, f\'Spread {state.spread_pct:.3f}% \> max {self.config.max_spread_pct:.3f}%\' |
|                                                                                            |
| \# Check 4: Cooldown                                                                       |
|                                                                                            |
| last = self.\_last_signal.get(state.symbol)                                                |
|                                                                                            |
| if last:                                                                                   |
|                                                                                            |
| elapsed = (utcnow() - last).total_seconds()                                                |
|                                                                                            |
| required = self.config.signal_cooldown \* tf_to_seconds(self.config.timeframe)             |
|                                                                                            |
| if elapsed \< required:                                                                    |
|                                                                                            |
| return False, f\'Cooldown: {elapsed:.0f}s \< {required:.0f}s required\'                    |
|                                                                                            |
| return True, \'\'                                                                          |
|                                                                                            |
| def get_confidence_multiplier(self, state: MarketState) -\> float:                         |
|                                                                                            |
| \"\"\"                                                                                     |
|                                                                                            |
| Modifier confidence berdasarkan kondisi tambahan.                                          |
|                                                                                            |
| Default: 1.0 (tidak ada modifikasi).                                                       |
|                                                                                            |
| Override untuk: scale up saat regime kuat, scale down saat mendekati resistance.           |
|                                                                                            |
| \"\"\"                                                                                     |
|                                                                                            |
| return 1.0                                                                                 |
+--------------------------------------------------------------------------------------------+

**2.2 Signal Dataclass --- Kontrak Output**

+-----------------------------------------------------------------------------------+
| \@dataclass                                                                       |
|                                                                                   |
| class Signal:                                                                     |
|                                                                                   |
| \# ── Identitas ────────────────────────────────────────────                      |
|                                                                                   |
| symbol: str                                                                       |
|                                                                                   |
| side: str \# \'BUY\' \| \'SELL\'                                                  |
|                                                                                   |
| strategy_id: str                                                                  |
|                                                                                   |
| timestamp: datetime                                                               |
|                                                                                   |
| \# ── Order parameters ─────────────────────────────────────                      |
|                                                                                   |
| signal_type: str \# \'market\' \| \'limit\' \| \'conditional\'                    |
|                                                                                   |
| suggested_price: float \# 0.0 = gunakan market price                              |
|                                                                                   |
| suggested_sl: float \# WAJIB diisi --- risk_layer butuh ini                       |
|                                                                                   |
| suggested_tp: float = 0.0 \# opsional (0.0 = tidak ada TP fixed)                  |
|                                                                                   |
| \# ── Confidence ───────────────────────────────────────────                      |
|                                                                                   |
| confidence: float \# 0.0 -- 1.0 (raw dari model)                                  |
|                                                                                   |
| final_confidence: float = 0.0 \# setelah multiplier & LLM filter                  |
|                                                                                   |
| \# ── Context ──────────────────────────────────────────────                      |
|                                                                                   |
| reasoning: str = \'\' \# teks penjelasan untuk logging                            |
|                                                                                   |
| model_output: float = 0.0 \# raw output model (return atau prob)                  |
|                                                                                   |
| regime: str = \'\' \# regime saat signal dibuat                                   |
|                                                                                   |
| vol_regime: str = \'\'                                                            |
|                                                                                   |
| metadata: dict = field(default_factory=dict)                                      |
|                                                                                   |
| def \_\_post_init\_\_(self):                                                      |
|                                                                                   |
| if self.final_confidence == 0.0:                                                  |
|                                                                                   |
| self.final_confidence = self.confidence                                           |
|                                                                                   |
| if self.suggested_sl \<= 0:                                                       |
|                                                                                   |
| raise ValueError(\'suggested_sl WAJIB \> 0\')                                     |
|                                                                                   |
| \@dataclass                                                                       |
|                                                                                   |
| class ExitSignal:                                                                 |
|                                                                                   |
| position_id: str                                                                  |
|                                                                                   |
| reason: str \# \'regime_change\' \| \'model_flip\' \| \'time_exit\' \| \'manual\' |
|                                                                                   |
| urgency: str \# \'normal\' \| \'urgent\'                                          |
|                                                                                   |
| exit_price: float \# 0.0 = market price                                           |
|                                                                                   |
| confidence: float \# seberapa yakin harus keluar (0.0--1.0)                       |
|                                                                                   |
| metadata: dict = field(default_factory=dict)                                      |
+-----------------------------------------------------------------------------------+

**3. spot_strategy.py --- Strategi Spot Trading**

Implementasi konkret untuk spot trading BTCUSDT (dan pair lain). Menggunakan model ML untuk prediksi arah return, dikombinasikan dengan filter teknikal dan regime.

**3.1 Logika Generate Signal**

+-----------------------------------------------------------------------+
| class SpotStrategy(BaseStrategy):                                     |
|                                                                       |
| def generate_signal(self, state: MarketState) -\> Optional\[Signal\]: |
|                                                                       |
| \# ── Step 1: Pre-filter ──────────────────────────────               |
|                                                                       |
| allowed, reason = self.is_allowed_to_trade(state)                     |
|                                                                       |
| if not allowed:                                                       |
|                                                                       |
| return None                                                           |
|                                                                       |
| \# ── Step 2: Model prediction ────────────────────────               |
|                                                                       |
| pred = self.model.predict(state.features.values.reshape(1, -1))\[0\]  |
|                                                                       |
| \# pred = prediksi return N candle ke depan (float)                   |
|                                                                       |
| \# ── Step 3: Entry threshold ─────────────────────────               |
|                                                                       |
| entry_threshold = self.\_get_dynamic_threshold(state)                 |
|                                                                       |
| if abs(pred) \< entry_threshold:                                      |
|                                                                       |
| return None \# prediksi terlalu kecil, tidak worth it                 |
|                                                                       |
| side = \'BUY\' if pred \> 0 else \'SELL\'                             |
|                                                                       |
| \# ── Step 4: Spot-specific filter ────────────────────               |
|                                                                       |
| \# Spot tidak bisa short --- hanya BUY yang diizinkan                 |
|                                                                       |
| if side == \'SELL\' and not self.config.allow_spot_short:             |
|                                                                       |
| return None                                                           |
|                                                                       |
| \# Jika sudah punya posisi di simbol ini, skip                        |
|                                                                       |
| if state.symbol in state.open_positions:                              |
|                                                                       |
| return None                                                           |
|                                                                       |
| \# ── Step 5: Confidence calculation ──────────────────               |
|                                                                       |
| confidence = self.\_calc_confidence(pred, state)                      |
|                                                                       |
| multiplier = self.get_confidence_multiplier(state)                    |
|                                                                       |
| final_conf = min(confidence \* multiplier, 1.0)                       |
|                                                                       |
| if final_conf \< self.config.min_signal_confidence:                   |
|                                                                       |
| return None                                                           |
|                                                                       |
| \# ── Step 6: SL / TP calculation ─────────────────────               |
|                                                                       |
| sl_price = self.\_calc_sl(state.last_price, side, state.atr)          |
|                                                                       |
| tp_price = self.\_calc_tp(state.last_price, sl_price, side)           |
|                                                                       |
| \# ── Step 7: Update cooldown & return signal ─────────               |
|                                                                       |
| self.\_last_signal\[state.symbol\] = utcnow()                         |
|                                                                       |
| return Signal(                                                        |
|                                                                       |
| symbol = state.symbol,                                                |
|                                                                       |
| side = side,                                                          |
|                                                                       |
| strategy_id = self.strategy_id,                                       |
|                                                                       |
| timestamp = utcnow(),                                                 |
|                                                                       |
| signal_type = \'market\',                                             |
|                                                                       |
| suggested_price = 0.0,                                                |
|                                                                       |
| suggested_sl = sl_price,                                              |
|                                                                       |
| suggested_tp = tp_price,                                              |
|                                                                       |
| confidence = confidence,                                              |
|                                                                       |
| final_confidence= final_conf,                                         |
|                                                                       |
| reasoning = self.\_build_reasoning(pred, state),                      |
|                                                                       |
| model_output = pred,                                                  |
|                                                                       |
| regime = state.regime.value,                                          |
|                                                                       |
| vol_regime = state.vol_regime,                                        |
|                                                                       |
| )                                                                     |
+-----------------------------------------------------------------------+

**3.2 Dynamic Entry Threshold**

+------------------------------------------------------------------+
| def \_get_dynamic_threshold(self, state: MarketState) -\> float: |
|                                                                  |
| \"\"\"                                                           |
|                                                                  |
| Threshold masuk disesuaikan dengan kondisi market.               |
|                                                                  |
| Semakin volatile / semakin kuat regime → threshold lebih tinggi. |
|                                                                  |
| \"\"\"                                                           |
|                                                                  |
| base = self.config.model_threshold \# default: 0.003 (0.3%)      |
|                                                                  |
| \# Scale berdasarkan volatility regime                           |
|                                                                  |
| vol_multiplier = {                                               |
|                                                                  |
| \'low\': 0.8, \# volatilitas rendah → threshold lebih rendah     |
|                                                                  |
| \'normal\': 1.0,                                                 |
|                                                                  |
| \'high\': 1.3, \# volatilitas tinggi → butuh sinyal lebih kuat   |
|                                                                  |
| \'extreme\': 2.0,                                                |
|                                                                  |
| }.get(state.vol_regime, 1.0)                                     |
|                                                                  |
| \# Scale berdasarkan spread                                      |
|                                                                  |
| spread_cost = state.spread_pct / 100 \# konversi ke decimal      |
|                                                                  |
| \# Threshold = base × vol_multiplier + biaya spread              |
|                                                                  |
| return base \* vol_multiplier + spread_cost                      |
+------------------------------------------------------------------+

**3.3 SL & TP Calculation**

+----------------------------------------------------------------------------+
| def \_calc_sl(self, price: float, side: str, atr: float) -\> float:        |
|                                                                            |
| \"\"\"                                                                     |
|                                                                            |
| SL berbasis ATR untuk menyesuaikan dengan volatilitas.                     |
|                                                                            |
| Jarak SL = atr × SL_ATR_MULTIPLIER                                         |
|                                                                            |
| \"\"\"                                                                     |
|                                                                            |
| distance = atr \* self.config.sl_atr_multiplier \# default: 1.5            |
|                                                                            |
| distance = max(distance, price \* self.config.default_sl_pct) \# floor: 2% |
|                                                                            |
| if side == \'BUY\':                                                        |
|                                                                            |
| return round_price(price - distance, tick_size)                            |
|                                                                            |
| else:                                                                      |
|                                                                            |
| return round_price(price + distance, tick_size)                            |
|                                                                            |
| def \_calc_tp(self, price: float, sl: float, side: str) -\> float:         |
|                                                                            |
| \"\"\"                                                                     |
|                                                                            |
| TP berbasis risk-reward ratio dari config.                                 |
|                                                                            |
| Risk = \|price - sl\|                                                      |
|                                                                            |
| Reward = risk × TP_RR_RATIO                                                |
|                                                                            |
| \"\"\"                                                                     |
|                                                                            |
| risk = abs(price - sl)                                                     |
|                                                                            |
| reward = risk \* self.config.default_tp_ratio \# default: 2.0 (1:2 RR)     |
|                                                                            |
| if side == \'BUY\':                                                        |
|                                                                            |
| return round_price(price + reward, tick_size)                              |
|                                                                            |
| else:                                                                      |
|                                                                            |
| return round_price(price - reward, tick_size)                              |
+----------------------------------------------------------------------------+

**3.4 should_exit --- Logika Keluar Berbasis Strategi**

+------------------------------------------------------------------------------------+
| def should_exit(                                                                   |
|                                                                                    |
| self,                                                                              |
|                                                                                    |
| position: Position,                                                                |
|                                                                                    |
| state: MarketState                                                                 |
|                                                                                    |
| ) -\> Optional\[ExitSignal\]:                                                      |
|                                                                                    |
| \"\"\"                                                                             |
|                                                                                    |
| Kondisi exit berbasis strategi (di luar SL/TP dari exit_layer):                    |
|                                                                                    |
| \"\"\"                                                                             |
|                                                                                    |
| \# Kondisi 1: Regime flip berlawanan dengan posisi                                 |
|                                                                                    |
| if position.side == \'BUY\':                                                       |
|                                                                                    |
| adverse_regimes = \[MarketRegime.STRONG_TREND_DOWN, MarketRegime.WEAK_TREND_DOWN\] |
|                                                                                    |
| else:                                                                              |
|                                                                                    |
| adverse_regimes = \[MarketRegime.STRONG_TREND_UP, MarketRegime.WEAK_TREND_UP\]     |
|                                                                                    |
| if state.regime in adverse_regimes and state.regime_stable:                        |
|                                                                                    |
| return ExitSignal(                                                                 |
|                                                                                    |
| position_id = position.trade_id,                                                   |
|                                                                                    |
| reason = \'regime_change\',                                                        |
|                                                                                    |
| urgency = \'normal\',                                                              |
|                                                                                    |
| exit_price = 0.0,                                                                  |
|                                                                                    |
| confidence = state.regime_confidence,                                              |
|                                                                                    |
| metadata = {\'regime\': state.regime.value}                                        |
|                                                                                    |
| )                                                                                  |
|                                                                                    |
| \# Kondisi 2: Model flip --- prediksi berubah arah signifikan                      |
|                                                                                    |
| pred = self.model.predict(state.features.values.reshape(1,-1))\[0\]                |
|                                                                                    |
| if position.side == \'BUY\' and pred \< -self.config.model_flip_threshold:         |
|                                                                                    |
| return ExitSignal(                                                                 |
|                                                                                    |
| position_id = position.trade_id,                                                   |
|                                                                                    |
| reason = \'model_flip\',                                                           |
|                                                                                    |
| urgency = \'normal\',                                                              |
|                                                                                    |
| exit_price = 0.0,                                                                  |
|                                                                                    |
| confidence = abs(pred) / self.config.model_flip_threshold,                         |
|                                                                                    |
| )                                                                                  |
|                                                                                    |
| return None                                                                        |
+------------------------------------------------------------------------------------+

**3.5 Config Keys Spot Strategy**

  -----------------------------------------------------------------------------------------------------------
  **Key**                 **Default**   **Valid Range**   **Keterangan**
  ----------------------- ------------- ----------------- ---------------------------------------------------
  MODEL_THRESHOLD         0.003         0.001--0.05       Prediksi return minimum untuk masuk

  SL_ATR_MULTIPLIER       1.5           1.0--3.0          Jarak SL = ATR × multiplier

  DEFAULT_TP_RATIO        2.0           1.0--5.0          Risk:Reward ratio untuk TP

  MIN_SIGNAL_CONFIDENCE   0.60          0.30--0.95        Confidence minimum lolos ke risk layer

  SIGNAL_COOLDOWN         3             1--20             Candle minimum antara dua signal di simbol sama

  REGIME_FILTER           True          bool              True = blokir signal saat HIGH_VOLATILITY

  MAX_SPREAD_PCT          0.5           0.1--2.0          Spread maksimum untuk izinkan signal (%)

  ALLOW_SPOT_SHORT        False         bool              False = hanya BUY untuk spot

  MODEL_FLIP_THRESHOLD    0.005         0.002--0.02       Model flip exit jika pred berlawanan \> threshold
  -----------------------------------------------------------------------------------------------------------

**4. futures_strategy.py --- Strategi Futures**

Extends SpotStrategy dengan kemampuan short selling dan manajemen leverage. Juga mencakup logika hedging untuk proteksi posisi spot yang sudah ada.

**4.1 Perbedaan dari SpotStrategy**

  ---------------------------------------------------------------------------------------------------------
  **Aspek**          **SpotStrategy**                         **FuturesStrategy**
  ------------------ ---------------------------------------- ---------------------------------------------
  Short selling      Tidak (ALLOW_SPOT_SHORT default False)   Ya --- BUY = long, SELL = short

  Leverage           Selalu 1x                                Configurable (max 10x, default 3x)

  Funding rate       Tidak relevan                            Diperhitungkan dalam entry decision

  Margin type        Tidak ada                                Isolated (default) atau Cross

  Hedge mode         Tidak ada                                Bisa buka LONG + SHORT bersamaan

  Basis dari spot    Tidak ada                                Monitor basis spot-futures untuk arb signal

  Max hold candles   48 (default)                             24 (lebih ketat karena funding cost)
  ---------------------------------------------------------------------------------------------------------

**4.2 Funding Rate Impact**

+-----------------------------------------------------------------------------------+
| def \_is_funding_cost_acceptable(                                                 |
|                                                                                   |
| self,                                                                             |
|                                                                                   |
| side: str,                                                                        |
|                                                                                   |
| funding_rate: float,                                                              |
|                                                                                   |
| pred_return: float                                                                |
|                                                                                   |
| ) -\> bool:                                                                       |
|                                                                                   |
| \"\"\"                                                                            |
|                                                                                   |
| Cek apakah funding rate tidak mengikis prediksi return.                           |
|                                                                                   |
| Funding cost per trade:                                                           |
|                                                                                   |
| \- Jika LONG dan funding_rate \> 0: kita BAYAR funding → cost positif             |
|                                                                                   |
| \- Jika SHORT dan funding_rate \> 0: kita TERIMA funding → cost negatif (benefit) |
|                                                                                   |
| \- Sebaliknya jika funding_rate \< 0                                              |
|                                                                                   |
| Formula:                                                                          |
|                                                                                   |
| expected_funding_cost = abs(funding_rate) × hold_candles / (8h_in_candles)        |
|                                                                                   |
| → Jika expected_cost \> pred_return × MAX_FUNDING_COST_RATIO → reject signal      |
|                                                                                   |
| \"\"\"                                                                            |
|                                                                                   |
| hold_estimate = self.config.avg_hold_candles \# estimasi hold                     |
|                                                                                   |
| funding_per_8h = abs(funding_rate) \# biaya per 8 jam                             |
|                                                                                   |
| tf_per_8h = 8 \* 3600 / tf_to_seconds(self.config.timeframe)                      |
|                                                                                   |
| funding_cost = funding_per_8h \* (hold_estimate / tf_per_8h)                      |
|                                                                                   |
| \# Jika LONG dan funding positif: kita bayar                                      |
|                                                                                   |
| paying_funding = (side == \'BUY\' and funding_rate \> 0) or \\                    |
|                                                                                   |
| (side == \'SELL\' and funding_rate \< 0)                                          |
|                                                                                   |
| if paying_funding:                                                                |
|                                                                                   |
| max_acceptable = abs(pred_return) \* self.config.max_funding_cost_ratio           |
|                                                                                   |
| return funding_cost \<= max_acceptable                                            |
|                                                                                   |
| return True \# menerima funding = bonus, selalu ok                                |
+-----------------------------------------------------------------------------------+

**4.3 Hedge Logic**

+---------------------------------------------------------------------------+
| def generate_hedge_signal(                                                |
|                                                                           |
| self,                                                                     |
|                                                                           |
| spot_position: Position,                                                  |
|                                                                           |
| state: MarketState,                                                       |
|                                                                           |
| ) -\> Optional\[Signal\]:                                                 |
|                                                                           |
| \"\"\"                                                                    |
|                                                                           |
| Buka posisi SHORT futures untuk hedge posisi LONG spot.                   |
|                                                                           |
| Dipanggil ketika:                                                         |
|                                                                           |
| 1\. Ada posisi LONG spot yang signifikan                                  |
|                                                                           |
| 2\. Model memprediksi penurunan jangka pendek                             |
|                                                                           |
| 3\. Tidak ingin jual spot (holding jangka panjang)                        |
|                                                                           |
| Hedge size = spot_qty × HEDGE_RATIO (default 0.5 = 50% hedge)             |
|                                                                           |
| \"\"\"                                                                    |
|                                                                           |
| pred = self.model.predict(state.features.values.reshape(1,-1))\[0\]       |
|                                                                           |
| \# Hanya hedge jika prediksi negatif kuat                                 |
|                                                                           |
| if pred \> -self.config.hedge_threshold:                                  |
|                                                                           |
| return None                                                               |
|                                                                           |
| hedge_qty = spot_position.filled_qty \* self.config.hedge_ratio           |
|                                                                           |
| sl_price = self.\_calc_sl(state.last_price, \'SELL\', state.atr)          |
|                                                                           |
| return Signal(                                                            |
|                                                                           |
| symbol = state.symbol,                                                    |
|                                                                           |
| side = \'SELL\',                                                          |
|                                                                           |
| strategy_id = f\'{self.strategy_id}\_hedge\',                             |
|                                                                           |
| signal_type = \'market\',                                                 |
|                                                                           |
| suggested_price = 0.0,                                                    |
|                                                                           |
| suggested_sl = sl_price,                                                  |
|                                                                           |
| confidence = abs(pred) / self.config.hedge_threshold,                     |
|                                                                           |
| reasoning = f\'Hedge spot long: pred={pred:.4f}\',                        |
|                                                                           |
| metadata = {\'is_hedge\': True, \'hedge_ratio\': self.config.hedge_ratio} |
|                                                                           |
| )                                                                         |
+---------------------------------------------------------------------------+

**4.4 Config Keys Futures Strategy**

  ------------------------------------------------------------------------------------------------------------
  **Key**                  **Default**   **Keterangan**
  ------------------------ ------------- ---------------------------------------------------------------------
  DEFAULT_LEVERAGE         3             Leverage default untuk semua futures position

  MAX_LEVERAGE             10            Batas atas leverage (override global_max_leverage jika lebih kecil)

  MARGIN_TYPE              isolated      isolated \| cross --- isolated lebih aman

  MAX_HOLD_CANDLES         24            Lebih ketat dari spot karena funding cost

  MAX_FUNDING_COST_RATIO   0.30          Funding cost max 30% dari prediksi return

  HEDGE_ENABLED            False         True = aktifkan logika hedge posisi spot

  HEDGE_RATIO              0.5           50% dari spot position di-hedge

  HEDGE_THRESHOLD          0.005         Prediksi harus \< -0.5% untuk trigger hedge
  ------------------------------------------------------------------------------------------------------------

**5. strategy_utils.py --- Helper Bersama**

Kumpulan fungsi utilitas yang digunakan oleh semua strategi. Tidak punya state --- semua fungsi pure.

**5.1 Signal Scoring & Filtering**

+-------------------------------------------------------------------------------------------+
| def score_signal(                                                                         |
|                                                                                           |
| pred: float,                                                                              |
|                                                                                           |
| regime: MarketRegime,                                                                     |
|                                                                                           |
| vol_regime: str,                                                                          |
|                                                                                           |
| spread_pct: float,                                                                        |
|                                                                                           |
| imbalance: float,                                                                         |
|                                                                                           |
| ) -\> float:                                                                              |
|                                                                                           |
| \"\"\"                                                                                    |
|                                                                                           |
| Composite score 0.0--1.0 dari berbagai faktor.                                            |
|                                                                                           |
| Digunakan untuk final_confidence sebelum MIN_SIGNAL_CONFIDENCE check.                     |
|                                                                                           |
| \"\"\"                                                                                    |
|                                                                                           |
| score = 0.0                                                                               |
|                                                                                           |
| \# Model prediction strength (40% weight)                                                 |
|                                                                                           |
| pred_score = min(abs(pred) / 0.01, 1.0) \# normalize: 1% pred = full score                |
|                                                                                           |
| score += pred_score \* 0.40                                                               |
|                                                                                           |
| \# Regime alignment (30% weight)                                                          |
|                                                                                           |
| regime_score = {                                                                          |
|                                                                                           |
| MarketRegime.STRONG_TREND_UP: 0.9,                                                        |
|                                                                                           |
| MarketRegime.WEAK_TREND_UP: 0.6,                                                          |
|                                                                                           |
| MarketRegime.SIDEWAYS: 0.3,                                                               |
|                                                                                           |
| MarketRegime.WEAK_TREND_DOWN: 0.6,                                                        |
|                                                                                           |
| MarketRegime.STRONG_TREND_DOWN: 0.9,                                                      |
|                                                                                           |
| MarketRegime.HIGH_VOLATILITY: 0.0, \# tidak pernah trade saat ini                         |
|                                                                                           |
| MarketRegime.UNDEFINED: 0.0,                                                              |
|                                                                                           |
| }.get(regime, 0.0)                                                                        |
|                                                                                           |
| score += regime_score \* 0.30                                                             |
|                                                                                           |
| \# Volatility (20% weight) --- low vol lebih baik untuk precision                         |
|                                                                                           |
| vol_score = {\'low\':1.0,\'normal\':0.7,\'high\':0.4,\'extreme\':0.0}.get(vol_regime,0.5) |
|                                                                                           |
| score += vol_score \* 0.20                                                                |
|                                                                                           |
| \# Orderbook imbalance (10% weight)                                                       |
|                                                                                           |
| \# Hanya jika arah imbalance sesuai prediksi                                              |
|                                                                                           |
| if (pred \> 0 and imbalance \> 0) or (pred \< 0 and imbalance \< 0):                      |
|                                                                                           |
| score += min(abs(imbalance), 1.0) \* 0.10                                                 |
|                                                                                           |
| return round(score, 4)                                                                    |
+-------------------------------------------------------------------------------------------+

**5.2 Fungsi Utilitas Lain**

  -------------------------------------------------------------------------------------------------------------
  **Fungsi**                **Parameter**            **Return**   **Keterangan**
  ------------------------- ------------------------ ------------ ---------------------------------------------
  calc_rr_ratio()           entry, sl, tp            float        Risk:Reward actual dari harga

  is_near_resistance()      price, df, margin=0.02   bool         Cek apakah harga dekat resistance key level

  is_near_support()         price, df, margin=0.02   bool         Cek apakah harga dekat support key level

  tf_to_seconds()           timeframe: str           int          \'1h\' → 3600, \'4h\' → 14400

  align_sl_to_structure()   price, side, df, atr     float        Geser SL ke swing low/high terdekat

  get_trend_strength()      df: pd.DataFrame         float 0--1   Kekuatan trend berdasarkan ADX + EMA slope

  build_reasoning()         pred, state, metadata    str          Generate teks reasoning untuk logging
  -------------------------------------------------------------------------------------------------------------

**BAGIAN B --- Portfolio Layer**

Portfolio Layer adalah lapisan kontrol modal yang berjalan di antara Strategy Layer dan Risk Layer. Ia tidak membuat keputusan soal market --- ia hanya memastikan tidak ada strategi yang menggunakan lebih dari jatah modalnya.

  ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------
  **PRINSIP UTAMA:** Portfolio Layer adalah \'portfolio manager\' yang melihat keseluruhan portofolio, bukan satu trade. Sebuah signal yang sempurna dari strategi bisa tetap ditolak jika portofolio sudah terlalu exposed.

  ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------

**6. allocator.py --- Alokasi Modal**

Mengontrol berapa banyak modal yang bisa digunakan oleh setiap kombinasi strategi + simbol. Ini adalah lapisan pertama portfolio layer yang dikonsultasikan sebelum sizing.

**6.1 Allocation Model**

+--------------------------------------------------------+
| \@dataclass                                            |
|                                                        |
| class AllocationBudget:                                |
|                                                        |
| strategy_id: str                                       |
|                                                        |
| symbol: str                                            |
|                                                        |
| max_usdt: float \# quota maksimum dalam USDT           |
|                                                        |
| used_usdt: float \# yang sudah digunakan               |
|                                                        |
| available_usdt: float \# max_usdt - used_usdt          |
|                                                        |
| pct_of_equity: float \# available / total_equity × 100 |
|                                                        |
| \@dataclass                                            |
|                                                        |
| class AllocationMatrix:                                |
|                                                        |
| \"\"\"Snapshot seluruh alokasi aktif.\"\"\"            |
|                                                        |
| total_equity: float                                    |
|                                                        |
| free_equity: float                                     |
|                                                        |
| reserved_equity: float \# MIN_CASH_RESERVE             |
|                                                        |
| allocations: dict\[str, AllocationBudget\]             |
|                                                        |
| \# key = f\'{strategy_id}:{symbol}\'                   |
|                                                        |
| total_used_pct: float                                  |
|                                                        |
| timestamp: datetime                                    |
+--------------------------------------------------------+

**6.2 Interface Publik**

+-----------------------------------------------------------------------------+
| class PortfolioAllocator:                                                   |
|                                                                             |
| def get_budget(                                                             |
|                                                                             |
| self,                                                                       |
|                                                                             |
| strategy_id: str,                                                           |
|                                                                             |
| symbol: str,                                                                |
|                                                                             |
| equity: float,                                                              |
|                                                                             |
| positions: dict,                                                            |
|                                                                             |
| ) -\> AllocationBudget:                                                     |
|                                                                             |
| \"\"\"                                                                      |
|                                                                             |
| Return berapa USDT yang tersedia untuk kombinasi strategy+symbol.           |
|                                                                             |
| Dipanggil oleh risk_layer sebelum position sizing.                          |
|                                                                             |
| \"\"\"                                                                      |
|                                                                             |
| def record_opened(                                                          |
|                                                                             |
| self,                                                                       |
|                                                                             |
| strategy_id: str,                                                           |
|                                                                             |
| symbol: str,                                                                |
|                                                                             |
| notional: float,                                                            |
|                                                                             |
| ) -\> None:                                                                 |
|                                                                             |
| \"\"\"Update internal tracking saat posisi dibuka.\"\"\"                    |
|                                                                             |
| def record_closed(                                                          |
|                                                                             |
| self,                                                                       |
|                                                                             |
| strategy_id: str,                                                           |
|                                                                             |
| symbol: str,                                                                |
|                                                                             |
| notional: float,                                                            |
|                                                                             |
| ) -\> None:                                                                 |
|                                                                             |
| \"\"\"Bebaskan alokasi saat posisi ditutup.\"\"\"                           |
|                                                                             |
| def get_matrix(self, equity: float, positions: dict) -\> AllocationMatrix:  |
|                                                                             |
| \"\"\"Snapshot seluruh situasi alokasi. Untuk monitoring & reporting.\"\"\" |
|                                                                             |
| def can_open(                                                               |
|                                                                             |
| self,                                                                       |
|                                                                             |
| strategy_id: str,                                                           |
|                                                                             |
| symbol: str,                                                                |
|                                                                             |
| notional: float,                                                            |
|                                                                             |
| equity: float,                                                              |
|                                                                             |
| positions: dict,                                                            |
|                                                                             |
| ) -\> tuple\[bool, str\]:                                                   |
|                                                                             |
| \"\"\"                                                                      |
|                                                                             |
| Cek semua batas sekaligus:                                                  |
|                                                                             |
| 1\. Batas per strategi                                                      |
|                                                                             |
| 2\. Batas per simbol                                                        |
|                                                                             |
| 3\. Cash reserve                                                            |
|                                                                             |
| 4\. Correlation budget (delegasi ke correlation.py)                         |
|                                                                             |
| Return (True, \'\') atau (False, alasan)                                    |
|                                                                             |
| \"\"\"                                                                      |
+-----------------------------------------------------------------------------+

**6.3 Aturan Alokasi --- Semua Batas**

  ----------------------------------------------------------------------------------------------------------------------
  **Batas**         **Formula**                               **Config Key**                **Default**   **Gagal →**
  ----------------- ----------------------------------------- ----------------------------- ------------- --------------
  Per strategi      notional ≤ equity × MAX_STRATEGY_PCT      MAX_STRATEGY_ALLOCATION_PCT   0.40          Block signal

  Per simbol        notional ≤ equity × MAX_SYMBOL_PCT        MAX_SYMBOL_ALLOCATION_PCT     0.20          Block signal

  Cash reserve      free_equity ≥ equity × MIN_CASH_RESERVE   MIN_CASH_RESERVE_PCT          0.10          Block signal

  Max posisi        count(open) \< MAX_OPEN_POSITIONS         MAX_OPEN_POSITIONS            5             Block signal

  Correlated pair   combined ≤ equity × MAX_CORRELATED_PCT    MAX_CORRELATED_PCT            0.35          Block signal
  ----------------------------------------------------------------------------------------------------------------------

**6.4 Contoh Perhitungan Alokasi**

+-------------------------------------------------------------------+
| \# Contoh: Equity = \$10,000                                      |
|                                                                   |
| \# Config: MAX_STRATEGY=40%, MAX_SYMBOL=20%, RESERVE=10%          |
|                                                                   |
| \# Situasi awal:                                                  |
|                                                                   |
| \# - SpotStrategy:BTCUSDT = \$1,500 aktif                         |
|                                                                   |
| \# - SpotStrategy:ETHUSDT = \$1,000 aktif                         |
|                                                                   |
| \# Total SpotStrategy used = \$2,500 (25% dari equity)            |
|                                                                   |
| \# Query: berapa budget untuk SpotStrategy:SOLUSDT?               |
|                                                                   |
| \# Step 1: Strategi budget = equity × 40% = \$4,000               |
|                                                                   |
| \# Used = \$2,500 → Available = \$1,500                           |
|                                                                   |
| \# Step 2: Simbol budget = equity × 20% = \$2,000                 |
|                                                                   |
| \# SOLUSDT belum ada → Available = \$2,000                        |
|                                                                   |
| \# Step 3: Cash reserve check                                     |
|                                                                   |
| \# Total used = \$2,500, reserve = \$1,000                        |
|                                                                   |
| \# Free = \$10,000 - \$2,500 - \$1,000 = \$6,500                  |
|                                                                   |
| \# Step 4: Correlation check                                      |
|                                                                   |
| \# SOLUSDT tidak berkorelasi kuat dengan BTC/ETH → pass           |
|                                                                   |
| \# Result: min(\$1,500, \$2,000) = \$1,500 tersedia untuk SOLUSDT |
+-------------------------------------------------------------------+

**7. capital_manager.py --- Manajemen Modal Global**

Melacak equity secara real-time dan mendeteksi kondisi berbahaya sebelum Risk Layer di-trigger. Capital Manager adalah \'akuntansi\' dari sistem.

**7.1 CapitalStatus Dataclass**

+-------------------------------------------------------------+
| \@dataclass                                                 |
|                                                             |
| class CapitalStatus:                                        |
|                                                             |
| \# ── Equity snapshot ───────────────────────────────────── |
|                                                             |
| current_equity: float                                       |
|                                                             |
| peak_equity: float                                          |
|                                                             |
| initial_equity: float                                       |
|                                                             |
| \# ── PnL ───────────────────────────────────────────────── |
|                                                             |
| daily_pnl: float                                            |
|                                                             |
| daily_pnl_pct: float                                        |
|                                                             |
| total_pnl: float                                            |
|                                                             |
| total_pnl_pct: float                                        |
|                                                             |
| \# ── Drawdown ──────────────────────────────────────────── |
|                                                             |
| current_drawdown: float \# dari peak (pct)                  |
|                                                             |
| max_drawdown_today: float                                   |
|                                                             |
| max_drawdown_ever: float                                    |
|                                                             |
| \# ── Exposure ──────────────────────────────────────────── |
|                                                             |
| open_notional: float \# total nilai posisi terbuka          |
|                                                             |
| exposure_pct: float \# open_notional / equity × 100         |
|                                                             |
| unrealized_pnl: float \# PnL posisi yang masih terbuka      |
|                                                             |
| \# ── Status flags ──────────────────────────────────────── |
|                                                             |
| safe_to_trade: bool                                         |
|                                                             |
| warnings: list\[str\]                                       |
|                                                             |
| timestamp: datetime                                         |
+-------------------------------------------------------------+

**7.2 Interface Publik**

+---------------------------------------------------------------+
| class CapitalManager:                                         |
|                                                               |
| def update(                                                   |
|                                                               |
| self,                                                         |
|                                                               |
| realized_equity: float, \# dari balance_sync.py               |
|                                                               |
| open_positions: dict, \# untuk hitung unrealized              |
|                                                               |
| current_prices: dict, \# symbol → last_price                  |
|                                                               |
| ) -\> CapitalStatus:                                          |
|                                                               |
| \"\"\"                                                        |
|                                                               |
| Dipanggil setiap tick (via scheduler sync interval).          |
|                                                               |
| Update semua metrik dan return status.                        |
|                                                               |
| Jika ada kondisi berbahaya, tambahkan ke warnings.            |
|                                                               |
| Jika critical, set safe_to_trade = False.                     |
|                                                               |
| \"\"\"                                                        |
|                                                               |
| def get_status(self) -\> CapitalStatus:                       |
|                                                               |
| \"\"\"Return status terakhir (cached, tidak recompute).\"\"\" |
|                                                               |
| def record_trade_open(                                        |
|                                                               |
| self,                                                         |
|                                                               |
| notional: float,                                              |
|                                                               |
| risk_usd: float,                                              |
|                                                               |
| ) -\> None:                                                   |
|                                                               |
| \"\"\"Update exposure saat posisi dibuka.\"\"\"               |
|                                                               |
| def record_trade_close(                                       |
|                                                               |
| self,                                                         |
|                                                               |
| pnl_usd: float,                                               |
|                                                               |
| commission: float,                                            |
|                                                               |
| ) -\> None:                                                   |
|                                                               |
| \"\"\"Update PnL dan equity saat posisi ditutup.\"\"\"        |
|                                                               |
| def get_compound_equity(self) -\> float:                      |
|                                                               |
| \"\"\"                                                        |
|                                                               |
| Equity untuk paper mode (simulated compounding).              |
|                                                               |
| Live mode: gunakan realized_equity dari balance_sync.         |
|                                                               |
| \"\"\"                                                        |
+---------------------------------------------------------------+

**7.3 Warning & Critical Thresholds**

  ------------------------------------------------------------------------------------------------------------
  **Kondisi**          **Warning Threshold**   **Critical (safe_to_trade=False)**   **Config Key**
  -------------------- ----------------------- ------------------------------------ --------------------------
  Daily PnL loss       \< -3% dari equity      \< -5% dari equity                   WARN/MAX_DAILY_LOSS_PCT

  Drawdown dari peak   \< -7% dari peak        \< -10% dari peak                    WARN/MAX_DRAWDOWN_PCT

  Total exposure       \> 70% equity           \> 85% equity                        WARN/MAX_EXPOSURE_PCT

  Unrealized loss      \< -3% dari equity      \< -6% dari equity                   WARN/MAX_UNREALIZED_LOSS
  ------------------------------------------------------------------------------------------------------------

**7.4 Equity Compounding (Paper Mode)**

+-------------------------------------------------------------------+
| \# Paper mode: equity tidak pernah berubah dari exchange balance. |
|                                                                   |
| \# Capital manager mensimulasikan compounding secara internal.    |
|                                                                   |
| class PaperEquityTracker:                                         |
|                                                                   |
| def \_\_init\_\_(self, initial: float):                           |
|                                                                   |
| self.\_equity = initial                                           |
|                                                                   |
| self.\_peak = initial                                             |
|                                                                   |
| def apply_trade(                                                  |
|                                                                   |
| self,                                                             |
|                                                                   |
| pnl_usd: float,                                                   |
|                                                                   |
| commission: float                                                 |
|                                                                   |
| ) -\> float:                                                      |
|                                                                   |
| net_pnl = pnl_usd - commission                                    |
|                                                                   |
| self.\_equity += net_pnl                                          |
|                                                                   |
| self.\_peak = max(self.\_peak, self.\_equity)                     |
|                                                                   |
| return self.\_equity                                              |
|                                                                   |
| \@property                                                        |
|                                                                   |
| def equity(self) -\> float: return self.\_equity                  |
|                                                                   |
| \@property                                                        |
|                                                                   |
| def peak(self) -\> float: return self.\_peak                      |
|                                                                   |
| \@property                                                        |
|                                                                   |
| def drawdown(self) -\> float:                                     |
|                                                                   |
| return (self.\_peak - self.\_equity) / self.\_peak                |
+-------------------------------------------------------------------+

**8. correlation.py --- Kontrol Korelasi**

Mencegah over-concentration pada aset yang bergerak bersama. Jika BTCUSDT dan ETHUSDT keduanya dalam posisi LONG, kerugian bisa berlipat ganda saat pasar turun bersamaan.

**8.1 Correlation Map --- Static**

+----------------------------------------------------------------------+
| \# Correlation pairs yang diperlakukan sebagai \'satu bucket\'       |
|                                                                      |
| \# Update berdasarkan observasi empiris rolling 90 hari              |
|                                                                      |
| CORRELATION_GROUPS: list\[dict\] = \[                                |
|                                                                      |
| {                                                                    |
|                                                                      |
| \'id\': \'btc_eth\',                                                 |
|                                                                      |
| \'symbols\': \[\'BTCUSDT\', \'ETHUSDT\'\],                           |
|                                                                      |
| \'corr\': 0.85,                                                      |
|                                                                      |
| \'max_combined_pct\': 0.35, \# max 35% equity kombinasi              |
|                                                                      |
| },                                                                   |
|                                                                      |
| {                                                                    |
|                                                                      |
| \'id\': \'eth_alts\',                                                |
|                                                                      |
| \'symbols\': \[\'ETHUSDT\', \'BNBUSDT\', \'SOLUSDT\'\],              |
|                                                                      |
| \'corr\': 0.75,                                                      |
|                                                                      |
| \'max_combined_pct\': 0.40,                                          |
|                                                                      |
| },                                                                   |
|                                                                      |
| {                                                                    |
|                                                                      |
| \'id\': \'large_caps\',                                              |
|                                                                      |
| \'symbols\': \[\'BTCUSDT\', \'ETHUSDT\', \'BNBUSDT\', \'SOLUSDT\'\], |
|                                                                      |
| \'corr\': 0.65,                                                      |
|                                                                      |
| \'max_combined_pct\': 0.60,                                          |
|                                                                      |
| },                                                                   |
|                                                                      |
| \]                                                                   |
|                                                                      |
| \# Config: CORRELATION_GROUPS bisa di-override via config YAML       |
|                                                                      |
| \# untuk update periodik tanpa perlu deploy ulang kode.              |
+----------------------------------------------------------------------+

**8.2 Interface Publik**

+---------------------------------------------------------------------------------+
| class CorrelationController:                                                    |
|                                                                                 |
| def check(                                                                      |
|                                                                                 |
| self,                                                                           |
|                                                                                 |
| new_symbol: str,                                                                |
|                                                                                 |
| new_side: str,                                                                  |
|                                                                                 |
| new_notional:float,                                                             |
|                                                                                 |
| positions: dict,                                                                |
|                                                                                 |
| equity: float,                                                                  |
|                                                                                 |
| ) -\> tuple\[bool, str\]:                                                       |
|                                                                                 |
| \"\"\"                                                                          |
|                                                                                 |
| Cek apakah membuka posisi baru akan melanggar batas korelasi.                   |
|                                                                                 |
| Return (True, \'\') = aman                                                      |
|                                                                                 |
| Return (False, alasan) = ditolak                                                |
|                                                                                 |
| \"\"\"                                                                          |
|                                                                                 |
| for group in self.\_groups:                                                     |
|                                                                                 |
| if new_symbol not in group\[\'symbols\'\]:                                      |
|                                                                                 |
| continue                                                                        |
|                                                                                 |
| \# Hitung combined exposure saat ini di group ini                               |
|                                                                                 |
| current_combined = sum(                                                         |
|                                                                                 |
| pos\[\'notional\'\]                                                             |
|                                                                                 |
| for sym, pos in positions.items()                                               |
|                                                                                 |
| if sym in group\[\'symbols\'\]                                                  |
|                                                                                 |
| )                                                                               |
|                                                                                 |
| if (current_combined + new_notional) / equity \> group\[\'max_combined_pct\'\]: |
|                                                                                 |
| return False, (                                                                 |
|                                                                                 |
| f\"Correlation group \'{group\[\'id\'\]}\' limit: \"                            |
|                                                                                 |
| f\"{group\[\'max_combined_pct\'\]:.0%} equity. \"                               |
|                                                                                 |
| f\"Current={current_combined/equity:.1%}, \"                                    |
|                                                                                 |
| f\"Would be={(current_combined+new_notional)/equity:.1%}\"                      |
|                                                                                 |
| )                                                                               |
|                                                                                 |
| return True, \'\'                                                               |
|                                                                                 |
| def get_group_exposure(                                                         |
|                                                                                 |
| self,                                                                           |
|                                                                                 |
| group_id: str,                                                                  |
|                                                                                 |
| positions: dict,                                                                |
|                                                                                 |
| equity: float,                                                                  |
|                                                                                 |
| ) -\> float:                                                                    |
|                                                                                 |
| \"\"\"Return % equity yang terexpose di group korelasi tertentu.\"\"\"          |
|                                                                                 |
| def update_groups(self, new_groups: list\[dict\]) -\> None:                     |
|                                                                                 |
| \"\"\"Update correlation groups dari config YAML (no restart needed).\"\"\"     |
+---------------------------------------------------------------------------------+

**8.3 Dynamic Correlation (Opsional)**

+----------------------------------------------------------------------------+
| \# Untuk implementasi lanjutan: hitung korelasi rolling dari data historis |
|                                                                            |
| \# Aktifkan dengan DYNAMIC_CORRELATION = True di config                    |
|                                                                            |
| def compute_rolling_correlation(                                           |
|                                                                            |
| returns_df: pd.DataFrame, \# kolom = symbol, baris = candle                |
|                                                                            |
| window: int = 90,                                                          |
|                                                                            |
| ) -\> pd.DataFrame:                                                        |
|                                                                            |
| \"\"\"                                                                     |
|                                                                            |
| Return correlation matrix rolling 90 candle.                               |
|                                                                            |
| Jika korelasi aktual \> threshold → perketat batas exposure.               |
|                                                                            |
| Jika korelasi aktual \< threshold → longgarkan batas.                      |
|                                                                            |
| \"\"\"                                                                     |
|                                                                            |
| return returns_df.rolling(window).corr().iloc\[-len(returns_df.columns):\] |
|                                                                            |
| \# Catatan: dynamic correlation lebih akurat tapi:                         |
|                                                                            |
| \# 1. Butuh data historis candle semua simbol                              |
|                                                                            |
| \# 2. Lebih expensive computationally                                      |
|                                                                            |
| \# 3. Correlation bisa berubah mendadak saat krisis (semua korelasi → 1)   |
|                                                                            |
| \# Untuk awal: gunakan static correlation groups yang sudah dikalibrasi.   |
+----------------------------------------------------------------------------+

**9. risk_budget.py --- Distribusi Risiko**

Mengontrol berapa banyak risiko (dalam USD) yang boleh diambil per strategi, per hari, dan secara keseluruhan. Berbeda dari allocator yang mengontrol notional --- risk_budget mengontrol nilai kerugian maksimum.

**9.1 Risk Budget Dataclass**

+------------------------------------------------------------------+
| \@dataclass                                                      |
|                                                                  |
| class RiskBudgetStatus:                                          |
|                                                                  |
| strategy_id: str                                                 |
|                                                                  |
| \# Harian                                                        |
|                                                                  |
| daily_risk_budget: float \# max loss per hari untuk strategi ini |
|                                                                  |
| daily_risk_used: float \# realized + unrealized loss hari ini    |
|                                                                  |
| daily_risk_pct: float \# used / budget × 100                     |
|                                                                  |
| daily_remaining: float \# budget - used                          |
|                                                                  |
| \# Total                                                         |
|                                                                  |
| total_risk_budget: float \# akumulasi dari initial equity        |
|                                                                  |
| total_risk_used: float                                           |
|                                                                  |
| \# Status                                                        |
|                                                                  |
| is_exhausted: bool \# True jika remaining ≤ 0                    |
|                                                                  |
| is_warning: bool \# True jika remaining \< 20% budget            |
|                                                                  |
| timestamp: datetime                                              |
+------------------------------------------------------------------+

**9.2 Interface Publik**

+-------------------------------------------------------------------------------------+
| class RiskBudgetManager:                                                            |
|                                                                                     |
| def get_status(                                                                     |
|                                                                                     |
| self,                                                                               |
|                                                                                     |
| strategy_id: str,                                                                   |
|                                                                                     |
| equity: float,                                                                      |
|                                                                                     |
| ) -\> RiskBudgetStatus:                                                             |
|                                                                                     |
| \"\"\"Return status risk budget untuk strategi tertentu.\"\"\"                      |
|                                                                                     |
| def can_take_risk(                                                                  |
|                                                                                     |
| self,                                                                               |
|                                                                                     |
| strategy_id: str,                                                                   |
|                                                                                     |
| risk_amount: float, \# USD yang akan di-risk pada trade ini                         |
|                                                                                     |
| equity: float,                                                                      |
|                                                                                     |
| ) -\> tuple\[bool, str\]:                                                           |
|                                                                                     |
| \"\"\"                                                                              |
|                                                                                     |
| Cek apakah masih ada budget untuk mengambil risiko sejumlah ini.                    |
|                                                                                     |
| Dipanggil oleh risk_layer/position_size.py setelah sizing.                          |
|                                                                                     |
| \"\"\"                                                                              |
|                                                                                     |
| def record_risk_taken(                                                              |
|                                                                                     |
| self,                                                                               |
|                                                                                     |
| strategy_id: str,                                                                   |
|                                                                                     |
| risk_amount: float,                                                                 |
|                                                                                     |
| ) -\> None:                                                                         |
|                                                                                     |
| \"\"\"Catat risiko yang diambil saat posisi dibuka.\"\"\"                           |
|                                                                                     |
| def record_risk_realized(                                                           |
|                                                                                     |
| self,                                                                               |
|                                                                                     |
| strategy_id: str,                                                                   |
|                                                                                     |
| actual_pnl: float, \# positif = win, negatif = loss                                 |
|                                                                                     |
| ) -\> None:                                                                         |
|                                                                                     |
| \"\"\"Update saat posisi ditutup --- free budget jika win, consume jika loss.\"\"\" |
|                                                                                     |
| def reset_daily(self) -\> None:                                                     |
|                                                                                     |
| \"\"\"Dipanggil scheduler UTC 00:00.\"\"\"                                          |
|                                                                                     |
| def get_portfolio_risk_summary(self, equity: float) -\> dict:                       |
|                                                                                     |
| \"\"\"Summary semua strategi untuk monitoring.\"\"\"                                |
+-------------------------------------------------------------------------------------+

**9.3 Budget Allocation Formula**

+----------------------------------------------------------------------+
| \# Risk budget dihitung dari equity saat awal hari                   |
|                                                                      |
| def compute_daily_budget(                                            |
|                                                                      |
| strategy_id: str,                                                    |
|                                                                      |
| equity: float,                                                       |
|                                                                      |
| config: AgentConfig,                                                 |
|                                                                      |
| strategy_weight: float = 1.0, \# bobot relatif antar strategi        |
|                                                                      |
| ) -\> float:                                                         |
|                                                                      |
| \"\"\"                                                               |
|                                                                      |
| Daily risk budget per strategi:                                      |
|                                                                      |
| 1\. Total daily budget = equity × max_daily_loss_pct                 |
|                                                                      |
| Contoh: \$10,000 × 5% = \$500/hari                                   |
|                                                                      |
| 2\. Per strategi = total_budget × strategy_weight / sum(all_weights) |
|                                                                      |
| Jika 2 strategi dengan bobot sama: masing-masing \$250/hari          |
|                                                                      |
| 3\. Per trade = risk_per_trade_pct × equity                          |
|                                                                      |
| Contoh: 1% × \$10,000 = \$100/trade                                  |
|                                                                      |
| 4\. Max trades per hari per strategi:                                |
|                                                                      |
| = daily_budget / risk_per_trade                                      |
|                                                                      |
| = \$250 / \$100 = 2.5 → max 2 trade/hari                             |
|                                                                      |
| \"\"\"                                                               |
|                                                                      |
| total_budget = equity \* config.max_daily_loss_pct                   |
|                                                                      |
| return total_budget \* strategy_weight                               |
+----------------------------------------------------------------------+

**9.4 Config Keys Risk Budget**

  ----------------------------------------------------------------------------------------------------------------
  **Key**                 **Default**         **Keterangan**
  ----------------------- ------------------- --------------------------------------------------------------------
  STRATEGY_WEIGHTS        {\'default\':1.0}   Dict bobot relatif antar strategi. Jumlah tidak harus 1.0.

  BUDGET_WARN_THRESHOLD   0.20                Warning jika sisa budget \< 20% dari daily budget.

  CARRY_OVER_WINS         False               True = profit hari ini bisa menambah budget besok (compound risk).

  RESET_ON_DAILY          True                True = budget reset setiap UTC 00:00.
  ----------------------------------------------------------------------------------------------------------------

**10. Integrasi --- Alur Signal Dari Strategy ke Risk**

**10.1 Sequence Lengkap Per Signal**

+--------------------------------------------------------------+
| \# Dipanggil dari main_loop setiap kali ada Signal baru:     |
|                                                              |
| async def process_signal(                                    |
|                                                              |
| signal: Signal,                                              |
|                                                              |
| state: MarketState,                                          |
|                                                              |
| portfolio: PortfolioAllocator,                               |
|                                                              |
| capital: CapitalManager,                                     |
|                                                              |
| corr: CorrelationController,                                 |
|                                                              |
| risk_bgt: RiskBudgetManager,                                 |
|                                                              |
| risk_mgr: RiskManager,                                       |
|                                                              |
| ) -\> ProcessResult:                                         |
|                                                              |
| \# ── LAYER 1: Portfolio checks ──────────────────────────── |
|                                                              |
| \# 1a. Capital check                                         |
|                                                              |
| cap_status = capital.get_status()                            |
|                                                              |
| if not cap_status.safe_to_trade:                             |
|                                                              |
| return ProcessResult.BLOCKED_CAPITAL                         |
|                                                              |
| \# 1b. Allocation check                                      |
|                                                              |
| ok, reason = portfolio.can_open(                             |
|                                                              |
| signal.strategy_id, signal.symbol,                           |
|                                                              |
| estimated_notional, state.equity, state.open_positions       |
|                                                              |
| )                                                            |
|                                                              |
| if not ok:                                                   |
|                                                              |
| return ProcessResult.BLOCKED_ALLOCATION                      |
|                                                              |
| \# 1c. Correlation check                                     |
|                                                              |
| ok, reason = corr.check(                                     |
|                                                              |
| signal.symbol, signal.side,                                  |
|                                                              |
| estimated_notional, state.open_positions, state.equity       |
|                                                              |
| )                                                            |
|                                                              |
| if not ok:                                                   |
|                                                              |
| return ProcessResult.BLOCKED_CORRELATION                     |
|                                                              |
| \# ── LAYER 2: Risk checks (risk_layer) ───────────────────  |
|                                                              |
| risk_result = risk_mgr.evaluate(                             |
|                                                              |
| TradeRequest.from_signal(signal),                            |
|                                                              |
| portfolio_state                                              |
|                                                              |
| )                                                            |
|                                                              |
| if not risk_result.is_approved:                              |
|                                                              |
| return ProcessResult.BLOCKED_RISK                            |
|                                                              |
| \# ── LAYER 3: Risk budget final check ───────────────────── |
|                                                              |
| ok, reason = risk_bgt.can_take_risk(                         |
|                                                              |
| signal.strategy_id,                                          |
|                                                              |
| risk_result.risk_amount_usd,                                 |
|                                                              |
| state.equity                                                 |
|                                                              |
| )                                                            |
|                                                              |
| if not ok:                                                   |
|                                                              |
| return ProcessResult.BLOCKED_BUDGET                          |
|                                                              |
| \# ── APPROVED: lanjut ke trade_layer ────────────────────── |
|                                                              |
| return ProcessResult.APPROVED                                |
+--------------------------------------------------------------+

**10.2 Dependency Map Antar File**

  ---------------------------------------------------------------------------------------------------------------------------------------
  **File**              **Import Dari**                                                         **Output Dikonsumsi Oleh**
  --------------------- ----------------------------------------------------------------------- -----------------------------------------
  base_strategy.py      intelligence_layer (MarketState, MarketRegime), models (TrainedModel)   spot_strategy, futures_strategy

  spot_strategy.py      base_strategy, strategy_utils                                           main_loop (via strategy registry)

  futures_strategy.py   base_strategy, spot_strategy, strategy_utils                            main_loop (via strategy registry)

  strategy_utils.py     intelligence_layer (MarketRegime), utils/helpers                        spot_strategy, futures_strategy

  allocator.py          correlation.py                                                          main_loop process_signal(), risk_layer

  capital_manager.py    sync/balance_sync (equity update)                                       main_loop, risk_layer (equity snapshot)

  correlation.py        --- (pure computation)                                                  allocator.py

  risk_budget.py        capital_manager.py (equity)                                             main_loop process_signal() final check
  ---------------------------------------------------------------------------------------------------------------------------------------

**11. Strategy Registry --- Manajemen Multi-Strategi**

Saat sistem berkembang, mungkin ada beberapa strategi berjalan bersamaan. Strategy Registry mengelola lifecycle semua strategi yang aktif.

**11.1 Interface StrategyRegistry**

+------------------------------------------------------------------------+
| class StrategyRegistry:                                                |
|                                                                        |
| def register(                                                          |
|                                                                        |
| self,                                                                  |
|                                                                        |
| strategy: BaseStrategy,                                                |
|                                                                        |
| symbols: list\[str\],                                                  |
|                                                                        |
| weight: float = 1.0,                                                   |
|                                                                        |
| ) -\> None:                                                            |
|                                                                        |
| \"\"\"Daftarkan strategi dan simbol yang di-handle.\"\"\"              |
|                                                                        |
| def disable(self, strategy_id: str, reason: str) -\> None:             |
|                                                                        |
| \"\"\"                                                                 |
|                                                                        |
| Non-aktifkan strategi. Dipanggil oleh strategy_killer.py.              |
|                                                                        |
| Posisi yang sudah terbuka tetap di-manage sampai close.                |
|                                                                        |
| \"\"\"                                                                 |
|                                                                        |
| def enable(self, strategy_id: str) -\> None:                           |
|                                                                        |
| \"\"\"Re-aktifkan setelah review.\"\"\"                                |
|                                                                        |
| def get_active_strategies(                                             |
|                                                                        |
| self,                                                                  |
|                                                                        |
| symbol: str = None,                                                    |
|                                                                        |
| ) -\> list\[BaseStrategy\]:                                            |
|                                                                        |
| \"\"\"Return semua strategi aktif, difilter per simbol jika ada.\"\"\" |
|                                                                        |
| def get_signals(                                                       |
|                                                                        |
| self,                                                                  |
|                                                                        |
| state: MarketState,                                                    |
|                                                                        |
| ) -\> list\[Signal\]:                                                  |
|                                                                        |
| \"\"\"                                                                 |
|                                                                        |
| Panggil generate_signal() untuk semua strategi aktif.                  |
|                                                                        |
| Return list signal (kosong jika tidak ada peluang).                    |
|                                                                        |
| Sudah di-filter MAX_SIGNALS_PER_TICK.                                  |
|                                                                        |
| \"\"\"                                                                 |
|                                                                        |
| def get_status(self) -\> dict:                                         |
|                                                                        |
| \"\"\"Status semua strategi: active, disabled, performance.\"\"\"      |
+------------------------------------------------------------------------+

**11.2 Signal Priority saat Multi-Strategi**

  -----------------------------------------------------------------------------------------------------------------
  **Kondisi**                               **Handling**
  ----------------------------------------- -----------------------------------------------------------------------
  Dua strategi signal di simbol yang sama   Ambil yang confidence lebih tinggi. Jika sama, ambil yang lebih dulu.

  Signal BUY dan SELL di simbol yang sama   Block keduanya --- sinyal konflik. Log sebagai CONFLICT.

  Jumlah signal \> MAX_SIGNALS_PER_TICK     Sort by confidence DESC, ambil N teratas. N = MAX_SIGNALS_PER_TICK.

  Satu strategi signal di banyak simbol     Proses semua, tapi portfolio layer akan block jika alokasi habis.
  -----------------------------------------------------------------------------------------------------------------

**12. Checklist Implementasi Strategy & Portfolio Layer**

**12.1 Checklist Strategy Layer**

  ---------------------------------------------------------------------------------------------------------------------------------------------------------------
  **No**   **Item**                                                             **Verifikasi**                                                         **Done**
  -------- -------------------------------------------------------------------- ---------------------------------------------------------------------- ----------
  1        BaseStrategy.generate_signal() tidak ada I/O atau side effect        grep -n \'await\\\|open(\\\|sqlite\\\|requests\' strategy_layer/       ☐

  2        Signal.suggested_sl selalu \> 0 --- enforced di \_\_post_init\_\_    Unit test: buat Signal dengan sl=0 → ValueError                        ☐

  3        is_allowed_to_trade() dijalankan sebelum generate_signal()           Code review SpotStrategy.generate_signal --- step 1                    ☐

  4        Cooldown per simbol berjalan benar setelah signal dikirim            Test: generate signal 2x dalam 3 candle → kedua signal harusnya None   ☐

  5        ALLOW_SPOT_SHORT=False benar-benar blokir sinyal SELL di spot        Unit test: SpotStrategy dengan side=SELL → None                        ☐

  6        Futures strategy check funding rate sebelum masuk                    Test: inject funding rate tinggi → signal diblokir                     ☐

  7        score_signal() menghasilkan nilai 0.0--1.0 untuk semua input valid   Property test dengan random input                                      ☐

  8        should_exit() return None jika tidak ada alasan kuat untuk keluar    Test: inject regime SIDEWAYS pada posisi BUY → return None             ☐
  ---------------------------------------------------------------------------------------------------------------------------------------------------------------

**12.2 Checklist Portfolio Layer**

  ---------------------------------------------------------------------------------------------------------------------------------------------------------
  **No**   **Item**                                                             **Verifikasi**                                                   **Done**
  -------- -------------------------------------------------------------------- ---------------------------------------------------------------- ----------
  9        allocator.can_open() menolak jika cash reserve terlampaui            Test: buka posisi sampai 90% equity → ditolak (reserve=10%)      ☐

  10       correlation.check() menolak saat BTCUSDT+ETHUSDT melebihi 35%        Test: buka BTC 20% + ETH 20% → coba ETH lagi → ditolak           ☐

  11       capital_manager.safe_to_trade=False saat daily loss \> threshold     Test: inject loss 6% → safe_to_trade=False                       ☐

  12       risk_budget.reset_daily() dipanggil tepat UTC 00:00 oleh scheduler   Test dengan mock datetime, cek daily_risk_used=0 setelah reset   ☐

  13       record_trade_open/close berpasangan --- tidak ada leak alokasi       Test: buka 5 posisi, tutup semua → free_equity kembali ke awal   ☐

  14       StrategyRegistry.disable() tidak menutup posisi yang sudah ada       Test: disable strategi → posisi existing tetap terbuka           ☐

  15       Signal konflik (BUY+SELL simbol sama) di-block keduanya              Test: inject dua strategi dengan signal berlawanan di BTCUSDT    ☐
  ---------------------------------------------------------------------------------------------------------------------------------------------------------

+:--------------------------------------------------------------------------------------------------------------:+
| **Dokumen ini adalah kontrak implementasi Strategy Layer & Portfolio Layer.**                                  |
|                                                                                                                |
| Perubahan pada Signal dataclass, config key default, atau aturan alokasi WAJIB diupdate sebelum merge ke main. |
|                                                                                                                |
| *Referensi terkait: data_intelligence_docs.docx • agent_core_docs.docx • risk_layer skeleton code*             |
+----------------------------------------------------------------------------------------------------------------+
