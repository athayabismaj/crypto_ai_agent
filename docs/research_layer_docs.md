**crypto_ai_agent**

**Dokumentasi Layer: Research**

*Panduan implementasi lengkap --- interface contract, config, data flow, keputusan desain*

Versi 1.0 \| Referensi: struktur final crypto_ai_agent

  ------------------------------------------------

  ------------------------------------------------

**1. Overview --- Research Layer**

Research Layer adalah zona eksperimen offline yang sepenuhnya terpisah dari sistem live. Tidak ada kode di sini yang bisa mengeksekusi order sungguhan. Output layer ini adalah:

> **•** Model ML terlatih (.pkl) yang siap di-deploy ke runtime/agent/models/
>
> **•** Laporan backtest lengkap: ROI, drawdown, Sharpe, win rate
>
> **•** Config optimal dari proses walk-forward & optimasi
>
> **•** Dataset bersih & fitur yang sudah divalidasi

  --------------------------------------------------------------------------------------------------------------------------------------------------------------
  **ATURAN KERAS:** Tidak ada impor dari runtime/ di dalam research/. Dependency harus satu arah: research menghasilkan artefak → runtime mengonsumsi artefak.

  --------------------------------------------------------------------------------------------------------------------------------------------------------------

**1.1 Struktur Folder**

+---------------------------------------------------------------+
| research/                                                     |
|                                                               |
| ├── data/                                                     |
|                                                               |
| │ ├── raw/ ← hasil fetch_data.py (immutable, jangan edit)     |
|                                                               |
| │ ├── processed/ ← hasil clean_data.py                        |
|                                                               |
| │ ├── features/ ← hasil feature_engineering.py                |
|                                                               |
| │ └── orderbook_samples/ ← depth snapshot untuk simulasi fill |
|                                                               |
| ├── pipeline/                                                 |
|                                                               |
| │ ├── fetch_data.py                                           |
|                                                               |
| │ ├── clean_data.py                                           |
|                                                               |
| │ ├── resample_data.py                                        |
|                                                               |
| │ └── feature_engineering.py                                  |
|                                                               |
| ├── validation/                                               |
|                                                               |
| │ ├── data_quality.py                                         |
|                                                               |
| │ ├── leakage_check.py                                        |
|                                                               |
| │ └── consistency_check.py                                    |
|                                                               |
| ├── modeling/                                                 |
|                                                               |
| │ ├── train.py                                                |
|                                                               |
| │ ├── walk_forward.py                                         |
|                                                               |
| │ ├── evaluate.py                                             |
|                                                               |
| │ ├── model_registry.py                                       |
|                                                               |
| │ └── feature_importance.py                                   |
|                                                               |
| ├── backtest/                                                 |
|                                                               |
| │ ├── engine.py                                               |
|                                                               |
| │ ├── simulator.py                                            |
|                                                               |
| │ ├── portfolio_simulator.py                                  |
|                                                               |
| │ ├── orderbook_simulator.py                                  |
|                                                               |
| │ ├── liquidity_model.py                                      |
|                                                               |
| │ ├── execution_emulator.py                                   |
|                                                               |
| │ ├── metrics.py                                              |
|                                                               |
| │ └── report.py                                               |
|                                                               |
| ├── optimization/                                             |
|                                                               |
| │ ├── tuner.py                                                |
|                                                               |
| │ ├── grid_search.py                                          |
|                                                               |
| │ └── bayesian_opt.py                                         |
|                                                               |
| └── experiments/ ← notebook & catatan eksperimen              |
+---------------------------------------------------------------+

**1.2 Data Flow Antar Sub-layer**

  -------------------------------------------------------------------------------------------------------------------------------------------
  **Tahap**      **Input**                          **Proses**                   **Output**                    **Validasi wajib**
  -------------- ---------------------------------- ---------------------------- ----------------------------- ------------------------------
  1\. Fetch      Exchange API / CSV                 fetch_data.py                raw/\*.parquet                data_quality.py

  2\. Clean      raw/\*.parquet                     clean_data.py                processed/\*.parquet          consistency_check.py

  3\. Resample   processed/\*.parquet               resample_data.py             processed/\*\_Xm.parquet      consistency_check.py

  4\. Feature    processed/\*.parquet               feature_engineering.py       features/\*.parquet           leakage_check.py

  5\. Train      features/\*.parquet                train.py + walk_forward.py   models/\*.pkl + metadata      evaluate.py

  6\. Backtest   features/ + orderbook_samples/     engine.py + simulator.py     results/\*.json + report      metrics.py (threshold check)

  7\. Optimize   results/\*.json                    tuner.py / bayesian_opt.py   best_params.json              walk_forward ulang

  8\. Deploy     models/\*.pkl + best_params.json   deploy.py (automation/)      runtime/agent/models/\*.pkl   metadata.json update
  -------------------------------------------------------------------------------------------------------------------------------------------

**2. Pipeline --- Akuisisi & Preprocessing Data**

**2.1 fetch_data.py**

Mengambil data OHLCV, funding rate, dan open interest dari exchange. Data disimpan dalam format Parquet partisi per simbol per timeframe.

**Interface Publik**

  -----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------
  **Fungsi**               **Parameter**                                                                     **Return**                                              **Exception**
  ------------------------ --------------------------------------------------------------------------------- ------------------------------------------------------- ----------------------------
  fetch_ohlcv()            symbol: str, tf: str, start: datetime, end: datetime, exchange: str=\'binance\'   pd.DataFrame \[open,high,low,close,volume,timestamp\]   FetchError, RateLimitError

  fetch_funding_rate()     symbol: str, start: datetime, end: datetime                                       pd.DataFrame \[timestamp, rate, next_time\]             FetchError

  fetch_orderbook_snap()   symbol: str, depth: int=20, n_samples: int=500                                    list\[dict\] --- simpan ke orderbook_samples/           FetchError

  save_raw()               df: pd.DataFrame, symbol: str, tf: str                                            Path --- lokasi file .parquet                           IOError
  -----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------

**Config Keys**

  ---------------------------------------------------------------------------------------------------
  **Key**            **Default**         **Valid Range**           **Keterangan**
  ------------------ ------------------- ------------------------- ----------------------------------
  EXCHANGE           binance             binance \| bybit \| okx   Exchange sumber data

  SYMBOLS            \[\'BTCUSDT\'\]     list of str               Simbol yang difetch

  TIMEFRAMES         \[\'1h\',\'4h\'\]   1m\|5m\|15m\|1h\|4h\|1d   Timeframe yang diambil

  START_DATE         2020-01-01          datetime ISO              Awal periode fetch

  RATE_LIMIT_SLEEP   0.2                 0.1 -- 2.0 (detik)        Jeda antar request (hindari ban)

  MAX_RETRY          3                   1 -- 10                   Retry saat request gagal

  RAW_DIR            data/raw/           str path                  Direktori simpan data mentah
  ---------------------------------------------------------------------------------------------------

**Keputusan Desain**

> **•** Format Parquet bukan CSV --- ukuran 5--10x lebih kecil, load lebih cepat, schema enforced.
>
> **•** File raw TIDAK PERNAH diedit setelah disimpan. Jika ada koreksi, simpan versi baru.
>
> **•** Naming convention: BTCUSDT_1h_20200101_20241231.parquet --- deterministic, easy glob.
>
> **•** Rate limit sleep dikonfigurasi, bukan hardcode --- tiap exchange punya limit berbeda.

**2.2 clean_data.py**

Membersihkan data mentah: handle missing candle, outlier harga, duplikat timestamp, dan normalisasi timezone ke UTC.

**Interface Publik**

  -----------------------------------------------------------------------------------------------------------------------------------------------
  **Fungsi**              **Parameter**                            **Return**                 **Catatan**
  ----------------------- ---------------------------------------- -------------------------- ---------------------------------------------------
  clean_ohlcv()           df: pd.DataFrame, symbol: str, tf: str   pd.DataFrame bersih        Forward-fill candle kosong max 3 berturut

  remove_outliers()       df, z_thresh: float=4.0                  pd.DataFrame               Z-score pada log-return, bukan harga absolut

  fix_timestamps()        df, tz: str=\'UTC\'                      pd.DataFrame               Konversi ke UTC, hapus duplikat

  validate_ohlc_logic()   df: pd.DataFrame                         bool, list\[str\] errors   High \>= max(Open,Close), Low \<= min(Open,Close)
  -----------------------------------------------------------------------------------------------------------------------------------------------

**Config Keys**

  --------------------------------------------------------------------------------------------------------------------------
  **Key**             **Default**       **Keterangan**
  ------------------- ----------------- ------------------------------------------------------------------------------------
  MAX_FFILL_CANDLES   3                 Max candle kosong yang di-forward-fill. Lebih dari ini → baris dihapus.

  OUTLIER_Z_THRESH    4.0               Threshold Z-score log-return. Z \> threshold → outlier. Default 4 = \~0.003% data.

  MIN_CANDLE_VOLUME   0.0               Volume 0 diizinkan (bisa terjadi di pair illiquid). Set \> 0 untuk filter.

  PROCESSED_DIR       data/processed/   Output direktori.
  --------------------------------------------------------------------------------------------------------------------------

**Aturan Cleaning yang Harus Konsisten**

  -------------------------------------------------------------------------------------------------------------------------------------------------------------------------
  **PENTING:** Aturan cleaning HARUS identik antara training dan runtime. Jika runtime melakukan normalisasi berbeda, model akan menerima distribusi yang tidak dikenali.

  -------------------------------------------------------------------------------------------------------------------------------------------------------------------------

> **•** Log-return dihitung di sini sebagai cross-check outlier, tapi TIDAK disimpan --- feature_engineering.py yang menghitung ulang.
>
> **•** Duplikat timestamp: ambil yang pertama (bukan last) --- konsisten dengan exchange behavior.
>
> **•** Gap lebih dari MAX_FFILL_CANDLES diisi dengan NaN lalu row dihapus, bukan di-interpolate.

**2.3 resample_data.py**

Mengubah timeframe data: 1m → 5m, 1h → 4h, dst. Menggunakan OHLC resampling yang benar (bukan sekedar downsample).

**Interface Publik**

  ---------------------------------------------------------------------------------------------------------------------------------------
  **Fungsi**         **Parameter**                                      **Return**
  ------------------ -------------------------------------------------- -----------------------------------------------------------------
  resample_ohlcv()   df: pd.DataFrame, source_tf: str, target_tf: str   pd.DataFrame pada target timeframe

  align_multi_tf()   dfs: dict\[str, pd.DataFrame\]                     dict\[str, pd.DataFrame\] --- semua di-align ke index yang sama
  ---------------------------------------------------------------------------------------------------------------------------------------

**Aturan Resampling OHLC**

+-----------------------------------------------------+
| Open = first(open) \# candle pertama dalam window   |
|                                                     |
| High = max(high) \# tertinggi dalam window          |
|                                                     |
| Low = min(low) \# terendah dalam window             |
|                                                     |
| Close = last(close) \# candle terakhir dalam window |
|                                                     |
| Volume = sum(volume) \# total volume                |
|                                                     |
| \# Label timestamp: AWAL window, bukan akhir        |
|                                                     |
| \# Contoh: 4H candle jam 08:00 = data 08:00--11:59  |
+-----------------------------------------------------+

**2.4 feature_engineering.py**

Menghitung semua fitur yang dibutuhkan model ML. Output adalah DataFrame dengan kolom fitur + label target (return N candle ke depan).

**Kategori Fitur**

  ------------------------------------------------------------------------------------------------------
  **Kategori**   **Fitur**                     **Formula / Library**              **Catatan**
  -------------- ----------------------------- ---------------------------------- ----------------------
  Price action   log_return, log_return_N      np.log(close/close.shift(N))       N = 1, 5, 20

  Momentum       RSI, MOM, ROC                 ta-lib / pandas-ta                 Period: 7, 14, 21

  Trend          EMA_fast, EMA_slow, MACD      ta-lib                             EMA 9/21/50/200

  Volatility     ATR, BB_width, realized_vol   ta-lib / rolling std log_return    ATR period 14

  Volume         volume_ratio, OBV, VWAP       volume/volume.rolling(20).mean()   VWAP per hari

  Regime         adx, trend_strength           ADX ta-lib period 14               ADX \> 25 = trending

  Funding        funding_rate, funding_cum8h   merge dari fetch_funding_rate()    Futures only

  Target         target_return_Nh              log_return.shift(-N)               N = 1, 4, 8 candle
  ------------------------------------------------------------------------------------------------------

**Interface Publik**

  -----------------------------------------------------------------------------------------------------------------------
  **Fungsi**              **Parameter**                             **Return**                    **Exception**
  ----------------------- ----------------------------------------- ----------------------------- -----------------------
  build_features()        df: pd.DataFrame, config: FeatureConfig   pd.DataFrame semua fitur      InsufficientDataError

  add_target()            df, horizon: int, col: str=\'close\'      pd.DataFrame + kolom target   ---

  validate_no_leakage()   df: pd.DataFrame                          bool                          DataLeakageError

  get_feature_names()     config: FeatureConfig                     list\[str\]                   ---
  -----------------------------------------------------------------------------------------------------------------------

**FeatureConfig --- Dataclass**

+-------------------------------------------------------------+
| \@dataclass                                                 |
|                                                             |
| class FeatureConfig:                                        |
|                                                             |
| rsi_periods: list\[int\] = (7, 14, 21)                      |
|                                                             |
| ema_periods: list\[int\] = (9, 21, 50, 200)                 |
|                                                             |
| atr_period: int = 14                                        |
|                                                             |
| bb_period: int = 20                                         |
|                                                             |
| volume_ma_period: int = 20                                  |
|                                                             |
| target_horizons: list\[int\] = (1, 4, 8) \# candle ke depan |
|                                                             |
| include_funding: bool = False \# True untuk futures         |
|                                                             |
| drop_na: bool = True                                        |
+-------------------------------------------------------------+

  ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------
  **KRITIS --- Anti Leakage:** Target label (target_return_Nh) wajib menggunakan shift(-N) dan harus divalidasi SETELAH semua fitur dihitung. Jangan pernah menggunakan future data di dalam fitur.

  ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------

**3. Validation --- Jaminan Kualitas Data**

**3.1 data_quality.py**

Lapisan pertama validasi: cek kelengkapan, range nilai, dan statistik dasar sebelum data diproses lebih lanjut.

**Interface Publik**

  --------------------------------------------------------------------------------------------------------------------
  **Fungsi**             **Parameter**                            **Return**
  ---------------------- ---------------------------------------- ----------------------------------------------------
  run_quality_report()   df: pd.DataFrame, symbol: str, tf: str   QualityReport dataclass (lihat di bawah)

  check_missing()        df: pd.DataFrame                         dict\[col, missing_pct\] --- pct missing per kolom

  check_price_range()    df: pd.DataFrame, symbol: str            bool, list\[str\] anomalies

  check_candle_gaps()    df: pd.DataFrame, tf: str                list\[datetime\] --- timestamp gap yang ditemukan
  --------------------------------------------------------------------------------------------------------------------

**QualityReport Dataclass**

+-----------------------------------------------------+
| \@dataclass                                         |
|                                                     |
| class QualityReport:                                |
|                                                     |
| symbol: str                                         |
|                                                     |
| timeframe: str                                      |
|                                                     |
| total_rows: int                                     |
|                                                     |
| missing_pct: dict\[str, float\] \# per kolom        |
|                                                     |
| gap_count: int \# jumlah gap candle                 |
|                                                     |
| outlier_count: int                                  |
|                                                     |
| ohlc_logic_errors: int \# High \< Low, dll          |
|                                                     |
| passed: bool \# True jika semua threshold terpenuhi |
|                                                     |
| warnings: list\[str\]                               |
+-----------------------------------------------------+

**Threshold yang Harus Dipenuhi (passed=True)**

  -----------------------------------------------------------------------------------------------------------
  **Check**          **Threshold**               **Aksi jika gagal**
  ------------------ --------------------------- ------------------------------------------------------------
  Missing candle     \< 1% dari total candle     Log warning, lanjutkan. Jika \> 5%, raise DataQualityError

  OHLC logic error   = 0                         Raise DataQualityError --- data corrupt

  Gap \> MAX_FFILL   \< 10 gap per 1000 candle   Log warning

  Volume = 0         \< 0.5% dari candle         Log warning --- bisa pair illiquid
  -----------------------------------------------------------------------------------------------------------

**3.2 leakage_check.py**

Validasi kritis untuk memastikan tidak ada informasi masa depan yang bocor ke fitur. Data leakage adalah penyebab utama backtest terlalu optimis.

**Interface Publik**

  -----------------------------------------------------------------------------------------------------------------------------
  **Fungsi**                 **Parameter**                                           **Return**
  -------------------------- ------------------------------------------------------- ------------------------------------------
  check_temporal_leakage()   df: pd.DataFrame, feature_cols: list, target_col: str   bool --- True = aman (tidak ada leakage)

  check_lookahead_bias()     df, window: int                                         dict\[col, correlation_with_future\]

  check_index_alignment()    features: pd.DataFrame, target: pd.Series               bool
  -----------------------------------------------------------------------------------------------------------------------------

**Cara Kerja Temporal Leakage Check**

> **1.** Hitung korelasi setiap kolom fitur dengan target masa depan (shift -1, -2, -5).
>
> **2.** Korelasi \> 0.8 dengan masa depan → indikasi kuat leakage.
>
> **3.** Rolling window forward correlation: korelasi fitur\[t\] dengan close\[t+N\] tidak boleh konsisten tinggi.
>
> **4.** Khusus fitur yang menggunakan .shift(): pastikan shift positif (bukan negatif) untuk data historis.

  ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------
  **ATURAN:** leakage_check.py WAJIB dijalankan setelah feature_engineering.py dan SEBELUM train.py. Pipeline tidak boleh dilanjutkan jika check_temporal_leakage() mengembalikan False.

  ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------

**3.3 consistency_check.py**

Memvalidasi konsistensi data lintas timeframe dan lintas simbol. Memastikan data 1H bisa di-derive dari data 15m, dan BTCUSDT spot konsisten dengan BTCUSDT futures.

**Interface Publik**

  --------------------------------------------------------------------------------------------------------------------------------------------------------
  **Fungsi**               **Parameter**                                                           **Return**
  ------------------------ ----------------------------------------------------------------------- -------------------------------------------------------
  check_tf_consistency()   df_low: pd.DataFrame, df_high: pd.DataFrame, src_tf: str, tgt_tf: str   bool, list\[str\] discrepancies

  check_spot_futures()     df_spot: pd.DataFrame, df_futures: pd.DataFrame                         bool, max_basis_pct: float

  check_feature_drift()    df_old: pd.DataFrame, df_new: pd.DataFrame                              dict\[col, psi_score\] --- Population Stability Index
  --------------------------------------------------------------------------------------------------------------------------------------------------------

**PSI Threshold untuk Feature Drift**

  ---------------------------------------------------------------------------
  **PSI Score**   **Interpretasi**   **Aksi**
  --------------- ------------------ ----------------------------------------
  \< 0.1          Tidak ada drift    Aman, lanjutkan training

  0.1 -- 0.2      Drift minor        Log warning, monitor

  \> 0.2          Drift signifikan   Stop training, investigasi sumber data
  ---------------------------------------------------------------------------

**4. Modeling --- Training & Evaluasi**

**4.1 train.py**

Melatih model ML dari fitur yang sudah divalidasi. Mendukung LightGBM (default), XGBoost, dan sklearn estimator lainnya melalui interface yang seragam.

**Interface Publik**

  ----------------------------------------------------------------------------------------------------------------------------
  **Fungsi**           **Parameter**                           **Return**                             **Side Effect**
  -------------------- --------------------------------------- -------------------------------------- ------------------------
  train_model()        df: pd.DataFrame, config: TrainConfig   TrainedModel dataclass                 Simpan .pkl ke models/

  load_model()         path: str \| Path                       TrainedModel                           ---

  predict()            model: TrainedModel, X: pd.DataFrame    np.ndarray --- probabilitas / return   ---

  get_feature_list()   model: TrainedModel                     list\[str\]                            ---
  ----------------------------------------------------------------------------------------------------------------------------

**TrainConfig --- Dataclass**

+------------------------------------------------------------------------+
| \@dataclass                                                            |
|                                                                        |
| class TrainConfig:                                                     |
|                                                                        |
| model_type: str = \'lightgbm\' \# lightgbm \| xgboost \| random_forest |
|                                                                        |
| target_col: str = \'target_return_4h\'                                 |
|                                                                        |
| task: str = \'regression\' \# regression \| classification             |
|                                                                        |
| test_size: float = 0.2                                                 |
|                                                                        |
| n_splits: int = 5 \# untuk walk_forward                                |
|                                                                        |
| early_stopping: int = 50                                               |
|                                                                        |
| verbose: int = 100                                                     |
|                                                                        |
| \# LightGBM params (override jika perlu)                               |
|                                                                        |
| lgbm_params: dict = field(default_factory=lambda: {                    |
|                                                                        |
| \'n_estimators\': 1000,                                                |
|                                                                        |
| \'learning_rate\': 0.05,                                               |
|                                                                        |
| \'num_leaves\': 31,                                                    |
|                                                                        |
| \'subsample\': 0.8,                                                    |
|                                                                        |
| \'colsample_bytree\': 0.8,                                             |
|                                                                        |
| \'reg_alpha\': 0.1,                                                    |
|                                                                        |
| \'reg_lambda\': 0.1,                                                   |
|                                                                        |
| })                                                                     |
+------------------------------------------------------------------------+

**TrainedModel --- Dataclass (artefak output)**

+-----------------------------------------------------------+
| \@dataclass                                               |
|                                                           |
| class TrainedModel:                                       |
|                                                           |
| model: Any \# estimator object                            |
|                                                           |
| feature_names: list\[str\] \# WAJIB --- runtime butuh ini |
|                                                           |
| target_col: str                                           |
|                                                           |
| model_type: str                                           |
|                                                           |
| train_date_range: tuple\[str, str\]                       |
|                                                           |
| metrics: dict \# val_score, ic, dll                       |
|                                                           |
| config: TrainConfig                                       |
|                                                           |
| version: str \# semver: \'1.0.0\'                         |
+-----------------------------------------------------------+

  ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------
  **KRITIS:** feature_names di dalam TrainedModel adalah kontrak antara Research dan Runtime. Runtime WAJIB menyiapkan fitur dengan urutan dan nama yang persis sama. Jika berbeda, model akan menghasilkan prediksi sampah tanpa error.

  ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------

**4.2 walk_forward.py**

Validasi model menggunakan walk-forward (expanding window atau rolling window). Ini adalah metode validasi paling realistis untuk data time series karena tidak menggunakan data masa depan untuk training.

**Skema Walk-Forward**

+--------------------------------------------------------+
| Expanding Window (default):                            |
|                                                        |
| Fold 1: Train \[Jan--Jun\] → Val \[Jul\]               |
|                                                        |
| Fold 2: Train \[Jan--Jul\] → Val \[Aug\]               |
|                                                        |
| Fold 3: Train \[Jan--Aug\] → Val \[Sep\]               |
|                                                        |
| \...                                                   |
|                                                        |
| Rolling Window (set rolling=True):                     |
|                                                        |
| Fold 1: Train \[Jan--Jun\] → Val \[Jul\]               |
|                                                        |
| Fold 2: Train \[Feb--Jul\] → Val \[Aug\] (Jan dibuang) |
|                                                        |
| \...                                                   |
|                                                        |
| Gap wajib antara train end dan val start:              |
|                                                        |
| → Minimal sama dengan target horizon                   |
|                                                        |
| → Jika target = 4H, gap = 4 candle = 16 jam            |
+--------------------------------------------------------+

**Interface Publik**

  -----------------------------------------------------------------------------------------------------------------
  **Fungsi**            **Parameter**                                 **Return**
  --------------------- --------------------------------------------- ---------------------------------------------
  walk_forward_cv()     df, config: WalkForwardConfig                 list\[FoldResult\] --- metrics per fold

  aggregate_results()   results: list\[FoldResult\]                   WalkForwardSummary --- mean, std, stability

  plot_equity_curve()   results: list\[FoldResult\], save_path: str   None --- simpan gambar
  -----------------------------------------------------------------------------------------------------------------

**WalkForwardConfig**

+-------------------------------------------------------------+
| \@dataclass                                                 |
|                                                             |
| class WalkForwardConfig:                                    |
|                                                             |
| n_splits: int = 5                                           |
|                                                             |
| gap_periods: int = 4 \# candle gap antara train & val       |
|                                                             |
| rolling: bool = False \# False = expanding window           |
|                                                             |
| min_train_size: int = 1000 \# minimum candle untuk training |
+-------------------------------------------------------------+

**4.3 evaluate.py**

Menghitung semua metrik evaluasi model. Memiliki threshold minimum yang harus dipenuhi sebelum model diizinkan lanjut ke backtest.

**Metrik yang Dihitung**

  --------------------------------------------------------------------------------------------------------------------------
  **Metrik**               **Formula**                          **Threshold Minimum**   **Catatan**
  ------------------------ ------------------------------------ ----------------------- ------------------------------------
  IC (Information Coef.)   spearman(pred, actual_return)        \> 0.05                 IC \< 0.03 = model tidak prediktif

  ICIR                     IC.mean() / IC.std()                 \> 0.5                  Stabilitas IC antar periode

  Directional Accuracy     sign(pred) == sign(actual)           \> 52%                  Di atas 50% berarti ada edge

  Sharpe (signal)          mean(ret\*signal)/std(ret\*signal)   ≥ 0.8                   Sebelum biaya transaksi

  Max DD (signal)          max drawdown equity curve            \< 30%                  Drawdown sinyal, bukan trade
  --------------------------------------------------------------------------------------------------------------------------

  ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------
  **ATURAN PIPELINE:** Jika salah satu threshold tidak terpenuhi, evaluate.py WAJIB raise ModelNotReadyError dan pipeline dihentikan. Model tidak boleh di-deploy ke backtest apalagi runtime.

  ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------

**4.4 model_registry.py**

Versioning dan tracking semua model yang pernah ditraining. Setiap model punya metadata lengkap sehingga bisa di-rollback kapan saja.

**metadata.json --- Format**

+--------------------------------------------------------------------------+
| {                                                                        |
|                                                                          |
| \"model_id\": \"spot_lgbm_v2_3_0\",                                      |
|                                                                          |
| \"version\": \"2.3.0\",                                                  |
|                                                                          |
| \"created_at\": \"2024-11-15T08:30:00Z\",                                |
|                                                                          |
| \"model_type\": \"lightgbm\",                                            |
|                                                                          |
| \"target_col\": \"target_return_4h\",                                    |
|                                                                          |
| \"symbol\": \"BTCUSDT\",                                                 |
|                                                                          |
| \"timeframe\": \"1h\",                                                   |
|                                                                          |
| \"train_period\": \[\"2020-01-01\", \"2024-10-31\"\],                    |
|                                                                          |
| \"feature_names\": \[\"rsi_14\", \"ema_ratio_9_21\", \"atr_14\", \...\], |
|                                                                          |
| \"feature_count\": 47,                                                   |
|                                                                          |
| \"metrics\": {                                                           |
|                                                                          |
| \"ic_mean\": 0.078, \"ic_std\": 0.031, \"icir\": 1.85,                   |
|                                                                          |
| \"dir_accuracy\": 0.543, \"sharpe_signal\": 1.12                         |
|                                                                          |
| },                                                                       |
|                                                                          |
| \"status\": \"production\",                                              |
|                                                                          |
| \"replaces\": \"spot_lgbm_v2_2_1\"                                       |
|                                                                          |
| }                                                                        |
+--------------------------------------------------------------------------+

**Status Lifecycle Model**

  ------------------------------------------------------------------------------------------------------------
  **Status**       **Artinya**                                         **Transisi ke**
  ---------------- --------------------------------------------------- ---------------------------------------
  **candidate**    Baru selesai training, belum divalidasi             validated (setelah evaluate.py lulus)

  **validated**    Lulus semua threshold evaluate.py                   production (setelah backtest lulus)

  **production**   Aktif digunakan di runtime                          deprecated (saat diganti versi baru)

  **deprecated**   Diganti versi baru, masih disimpan untuk rollback   archived (setelah 90 hari)

  **failed**       Tidak lulus threshold, tidak boleh deploy           --- (hanya untuk referensi)
  ------------------------------------------------------------------------------------------------------------

**5. Backtest --- Simulasi Trading**

Backtest layer adalah yang paling kritikal untuk menilai apakah model dan strategi layak di-deploy. Keakuratan simulasi sangat menentukan apakah live performance akan konsisten dengan backtest.

  ------------------------------------------------------------------------------------------------------------------------------------------------------
  **PRINSIP:** Backtest yang baik lebih baik menunjukkan hasil konservatif daripada optimis. Setiap asumsi yang memperbagus hasil harus dipertanyakan.

  ------------------------------------------------------------------------------------------------------------------------------------------------------

**5.1 engine.py --- Backtest Engine**

Event-driven backtest engine. Memproses candle satu per satu secara kronologis untuk menghindari lookahead bias.

**Interface Publik**

  ----------------------------------------------------------------------------------------
  **Fungsi / Class**   **Parameter**                                    **Return**
  -------------------- ------------------------------------------------ ------------------
  **BacktestEngine**   config: BacktestConfig                           ---

  .run()               df: pd.DataFrame, strategy: BaseStrategy         BacktestResult

  .run_portfolio()     dfs: dict\[str, pd.DataFrame\], strategy: \...   PortfolioResult
  ----------------------------------------------------------------------------------------

**BacktestConfig**

+-------------------------------------------------------------------------+
| \@dataclass                                                             |
|                                                                         |
| class BacktestConfig:                                                   |
|                                                                         |
| initial_capital: float = 10_000.0 \# USDT                               |
|                                                                         |
| commission_pct: float = 0.001 \# 0.1% taker fee Binance                 |
|                                                                         |
| slippage_model: str = \'liquidity\' \# fixed \| percentage \| liquidity |
|                                                                         |
| slippage_pct: float = 0.0005 \# 0.05% jika model=\'percentage\'         |
|                                                                         |
| use_orderbook: bool = True \# pakai orderbook_simulator                 |
|                                                                         |
| execution_delay: int = 1 \# candle delay sebelum fill                   |
|                                                                         |
| max_position_pct: float = 0.1 \# max 10% per posisi                     |
|                                                                         |
| allow_short: bool = False \# spot: False, futures: True                 |
|                                                                         |
| funding_rate: bool = False \# True untuk futures                        |
+-------------------------------------------------------------------------+

**Urutan Eksekusi Per Candle (WAJIB DIIKUTI)**

> **1.** Terima candle baru (OHLCV).
>
> **2.** Update posisi yang ada: cek SL, TP, trailing stop menggunakan high/low candle.
>
> **3.** Panggil strategi: strategy.on_candle(candle, portfolio_state) → Signal.
>
> **4.** Proses signal melalui risk check (position sizing, exposure).
>
> **5.** Simulasi eksekusi order pada candle BERIKUTNYA (execution_delay=1).
>
> **6.** Catat trade & update equity.

  -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------
  **LOOKAHEAD PROTECTION:** Signal pada candle T tidak boleh di-fill pada harga candle T. Minimal fill pada open candle T+1. Ini meniru kondisi live trading yang sesungguhnya.

  -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------

**5.2 orderbook_simulator.py & liquidity_model.py**

Dua komponen ini bekerja bersama untuk mensimulasikan fill order secara realistis berdasarkan depth orderbook yang diambil dari fetch_orderbook_snap().

**Interface --- orderbook_simulator.py**

  -----------------------------------------------------------------------------------------------------------------------
  **Fungsi**                 **Parameter**                                 **Return**
  -------------------------- --------------------------------------------- ----------------------------------------------
  simulate_fill()            order: Order, orderbook: Orderbook            FillResult (avg_price, filled_qty, slippage)

  load_orderbook()           symbol: str, timestamp: datetime              Orderbook --- snapshot terdekat dari data

  estimate_market_impact()   qty: float, orderbook: Orderbook, side: str   float --- estimasi impact dalam persen
  -----------------------------------------------------------------------------------------------------------------------

**FillResult Dataclass**

+------------------------------------------------------------+
| \@dataclass                                                |
|                                                            |
| class FillResult:                                          |
|                                                            |
| avg_price: float \# harga rata-rata fill                   |
|                                                            |
| filled_qty: float \# qty yang berhasil di-fill             |
|                                                            |
| slippage_pct: float \# (avg_price - mid_price) / mid_price |
|                                                            |
| market_impact:float \# estimasi impact ke harga            |
|                                                            |
| partial: bool \# True jika tidak fully filled              |
|                                                            |
| unfilled_qty: float \# qty yang tidak ter-fill             |
+------------------------------------------------------------+

**Model Slippage --- liquidity_model.py**

  -----------------------------------------------------------------------------------------------------
  **Model**    **Formula**                                             **Kapan Digunakan**
  ------------ ------------------------------------------------------- --------------------------------
  fixed        slippage = slippage_pct × price                         Testing cepat, tidak realistis

  percentage   slippage = order_value / ADV × impact_factor            Jika tidak ada orderbook data

  liquidity    Walk through orderbook bids/asks sampai qty terpenuhi   Default --- paling realistis
  -----------------------------------------------------------------------------------------------------

**5.3 execution_emulator.py**

Mengemulasi ketidakpastian eksekusi: delay jaringan, partial fill, order rejection, dan retry logic.

**Skenario yang Diemulasi**

  ------------------------------------------------------------------------------------------------------------------
  **Skenario**               **Probabilitas Default**   **Dampak**                           **Config Key**
  -------------------------- -------------------------- ------------------------------------ -----------------------
  Execution delay 1 candle   100%                       Fill pada open candle berikutnya     execution_delay

  Partial fill               5%                         Hanya 70--99% qty yang ter-fill      partial_fill_prob

  Order rejection            0.5%                       Order tidak masuk, perlu retry       rejection_prob

  Price gap / slippage       100%                       Harga berbeda dari expected          slippage_model

  High volatility spread     ATR-based                  Spread lebih lebar saat ATR tinggi   vol_spread_multiplier
  ------------------------------------------------------------------------------------------------------------------

**5.4 metrics.py**

Menghitung seluruh metrik performa dari hasil backtest. Semua metrik harus dihitung secara konsisten menggunakan satu sumber kebenaran.

**Semua Metrik yang Dihitung**

  ---------------------------------------------------------------------------------------------------
  **Metrik**      **Formula**                                          **Threshold Minimum Deploy**
  --------------- ---------------------------------------------------- ------------------------------
  Total ROI       ((final_equity - initial) / initial) × 100           Positif setelah biaya

  CAGR            (final/initial)\^(365/days) - 1                      \> 20% per tahun

  Max Drawdown    max(peak - trough) / peak                            \< 20%

  Sharpe Ratio    annualized(mean_return) / annualized(std_return)     ≥ 1.0

  Sortino Ratio   annualized(mean_return) / annualized(downside_std)   ≥ 1.5

  Calmar Ratio    CAGR / max_drawdown                                  ≥ 1.0

  Win Rate        winning_trades / total_trades                        \> 45%

  Profit Factor   gross_profit / gross_loss                            \> 1.3

  Avg R:R         avg_win / avg_loss                                   \> 1.2

  Total Trades    count                                                \> 30 (statistik valid)
  ---------------------------------------------------------------------------------------------------

  ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------
  **PENTING:** Sharpe Ratio harus dihitung menggunakan daily return (bukan trade return) dan dibandingkan dengan risk-free rate 0 (karena kita memegang USDT, bukan obligasi).

  ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------

**6. Optimization --- Tuning Parameter**

Optimization layer mencari kombinasi parameter terbaik untuk strategi. Wajib menggunakan out-of-sample validation untuk menghindari overfitting.

  --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------
  **PERINGATAN KERAS:** Jangan pernah optimize pada seluruh dataset lalu test pada data yang sama. Selalu sisihkan out-of-sample period SEBELUM optimasi dimulai. Periode ini tidak boleh dilihat sampai final evaluation.

  --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------

**6.1 Protokol Optimasi yang Benar**

+---------------------------------------------------------------------+
| Split data SEBELUM optimasi:                                        |
|                                                                     |
| Total data: Jan 2020 -- Des 2024 (5 tahun)                          |
|                                                                     |
| In-sample: Jan 2020 -- Des 2023 (4 tahun) ← untuk optimize          |
|                                                                     |
| Out-of-sample: Jan 2024 -- Des 2024 (1 tahun) ← TIDAK BOLEH DILIHAT |
|                                                                     |
| Alur:                                                               |
|                                                                     |
| 1\. Optimize parameter menggunakan in-sample walk-forward           |
|                                                                     |
| 2\. Pilih best_params berdasarkan Sharpe in-sample                  |
|                                                                     |
| 3\. Jalankan backtest SEKALI di out-of-sample                       |
|                                                                     |
| 4\. Bandingkan IS vs OOS --- degradasi \> 40% = overfitting         |
|                                                                     |
| 5\. Jika lulus, lanjut ke deploy. Jika tidak, kembali ke step 1     |
+---------------------------------------------------------------------+

**6.2 tuner.py --- Runner**

**Interface Publik**

  ----------------------------------------------------------------------------------------------------------------
  **Fungsi**           **Parameter**                                       **Return**
  -------------------- --------------------------------------------------- ---------------------------------------
  run_optimization()   param_space: dict, config: OptConfig, method: str   OptResult --- best_params, all_trials

  load_best_params()   symbol: str, strategy: str                          dict --- best params dari file

  save_best_params()   params: dict, symbol: str, strategy: str            Path
  ----------------------------------------------------------------------------------------------------------------

**6.3 bayesian_opt.py vs grid_search.py**

  ----------------------------------------------------------------------------------------------------------------------
  **Metode**         **Kapan Digunakan**                     **Kelebihan**                    **Kekurangan**
  ------------------ --------------------------------------- -------------------------------- --------------------------
  **grid_search**    Parameter sedikit (\< 3), range kecil   Exhaustive, mudah debug          Exponential complexity

  **bayesian_opt**   Parameter banyak (\> 3), range besar    Efisien, lebih cepat konvergen   Perlu lebih banyak setup
  ----------------------------------------------------------------------------------------------------------------------

**OptConfig**

+-----------------------------------------------------------------------------+
| \@dataclass                                                                 |
|                                                                             |
| class OptConfig:                                                            |
|                                                                             |
| n_trials: int = 100 \# untuk bayesian                                       |
|                                                                             |
| n_jobs: int = -1 \# parallel (-1 = semua core)                              |
|                                                                             |
| objective: str = \'sharpe\' \# sharpe \| sortino \| calmar \| profit_factor |
|                                                                             |
| min_trades: int = 30 \# abaikan trial dengan trade \< N                     |
|                                                                             |
| timeout_seconds: int = 3600 \# max waktu optimasi                           |
|                                                                             |
| sampler: str = \'tpe\' \# tpe \| random \| cma-es                           |
+-----------------------------------------------------------------------------+

**7. Dependency & Integrasi dengan Runtime**

**7.1 Dependency Antar File**

  -------------------------------------------------------------------------------------------------------------------
  **File**                 **Depends On**                                  **Output Dikonsumsi Oleh**
  ------------------------ ----------------------------------------------- ------------------------------------------
  fetch_data.py            Exchange API (ccxt)                             clean_data.py

  clean_data.py            fetch_data.py output                            resample_data.py, feature_engineering.py

  resample_data.py         clean_data.py output                            feature_engineering.py

  feature_engineering.py   clean/resample output                           train.py, engine.py, leakage_check.py

  leakage_check.py         feature_engineering.py output                   train.py (gate)

  train.py                 features/\*.parquet + leakage_check lulus       evaluate.py, model_registry.py

  walk_forward.py          features/\*.parquet + TrainConfig               evaluate.py

  evaluate.py              TrainedModel + WalkForwardSummary               engine.py (gate), model_registry.py

  engine.py                features/ + orderbook_samples/ + TrainedModel   metrics.py, report.py

  metrics.py               BacktestResult                                  report.py, tuner.py

  model_registry.py        TrainedModel + metadata.json                    automation/deploy.py
  -------------------------------------------------------------------------------------------------------------------

**7.2 Kontrak Interface dengan Runtime**

Ini adalah interface yang harus dijaga kompatibilitasnya. Perubahan di sini membutuhkan koordinasi dengan runtime.

  -------------------------------------------------------------------------------------------------------------------
  **Artefak**        **Format**         **Field WAJIB**                                     **Yang Mengkonsumsi**
  ------------------ ------------------ --------------------------------------------------- -------------------------
  \*.pkl             joblib/pickle      model object + predict(X) method + feature_names    runtime/agent/models/

  metadata.json      JSON               feature_names (ordered list), version, threshold    runtime core/config.py

  best_params.json   JSON               strategy params yang match dengan strategy_layer/   runtime strategy_layer/

  FeatureConfig      Python dataclass   Sama persis yang dipakai feature_engineering.py     runtime data_layer/
  -------------------------------------------------------------------------------------------------------------------

  ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------
  **ATURAN VERSIONING:** Setiap kali feature_names berubah (tambah/hapus/ganti urutan), versi model WAJIB naik major version (1.x.x → 2.0.0). Runtime tidak boleh menggunakan model baru tanpa update feature pipeline-nya.

  ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------

**7.3 Python Dependencies**

  ------------------------------------------------------------------------------------------------------------
  **Library**    **Versi Min**   **Digunakan di**                      **Catatan**
  -------------- --------------- ------------------------------------- ---------------------------------------
  pandas         2.0+            Semua file                            Gunakan pyarrow backend untuk Parquet

  numpy          1.24+           Semua file                            ---

  lightgbm       4.0+            train.py                              Model utama

  scikit-learn   1.3+            train.py, evaluate.py, walk_forward   Metrics, pipeline

  ta-lib         0.4+            feature_engineering.py                Perlu install C library dulu

  pandas-ta      0.3+            feature_engineering.py                Alternatif ta-lib, pure Python

  ccxt           4.0+            fetch_data.py                         Unified exchange API

  optuna         3.0+            bayesian_opt.py                       Bayesian optimization

  joblib         1.3+            train.py, model_registry.py           Serialisasi model

  pyarrow        12.0+           Semua read/write Parquet              Backend Parquet untuk pandas
  ------------------------------------------------------------------------------------------------------------

**8. Checklist Sebelum Deploy Model ke Runtime**

Seluruh checklist berikut WAJIB terpenuhi sebelum model dipindahkan ke runtime/agent/models/. Tandai setiap poin.

  -------------------------------------------------------------------------------------------------------------------------------------
  **No**   **Checklist Item**                                             **Cara Verifikasi**                           **PIC**
  -------- -------------------------------------------------------------- --------------------------------------------- ---------------
  1        data_quality.py passed=True untuk semua data train             run_quality_report() --- cek field passed     Data Engineer

  2        leakage_check.py returned True                                 check_temporal_leakage() tidak raise          Data Engineer

  3        Walk-forward IC \> 0.05 dan ICIR \> 0.5                        WalkForwardSummary.ic_mean & icir             ML Engineer

  4        evaluate.py lulus semua threshold                              Tidak ada ModelNotReadyError                  ML Engineer

  5        Backtest OOS Sharpe ≥ 1.0 dan Max DD \< 20%                    metrics.py report OOS period                  Quant

  6        Degradasi IS vs OOS \< 40% untuk Sharpe                        sharpe_oos \>= sharpe_is \* 0.6               Quant

  7        feature_names di metadata.json match dengan runtime pipeline   python -c \'import json; check_features()\'   ML Engineer

  8        metadata.json berisi semua field wajib                         Validasi schema JSON                          ML Engineer

  9        model_registry.py status = \'validated\'                       load dari registry, cek status                ML Engineer

  10       Backtest minimal 30 trade di OOS period                        BacktestResult.total_trades \>= 30            Quant

  11       Tidak ada dependency runtime di dalam research/                grep -r \'from runtime\' research/            Dev

  12       Paper trading dijalankan minimal 1 minggu sebelum live         Log paper mode menunjukkan sinyal normal      Ops
  -------------------------------------------------------------------------------------------------------------------------------------

**9. Experiments --- Konvensi Pencatatan**

Setiap eksperimen harus dicatat agar tidak mengulang pekerjaan yang sama. Format minimal yang harus ada:

+------------------------------------------------+
| experiments/                                   |
|                                                |
| ├── 2024_11_exp001_rsi_ema_baseline/           |
|                                                |
| │ ├── README.md ← hipotesis, hasil, kesimpulan |
|                                                |
| │ ├── notebook.ipynb ← kode eksplorasi         |
|                                                |
| │ ├── results.json ← metrik final              |
|                                                |
| │ └── config.yaml ← config yang dipakai        |
|                                                |
| ├── 2024_11_exp002_add_funding_feature/        |
|                                                |
| │ └── \...                                     |
+------------------------------------------------+

**Template README.md Eksperimen**

+----------------------------------------------------------------+
| \# Exp-001: RSI + EMA Baseline                                 |
|                                                                |
| \## Hipotesis                                                  |
|                                                                |
| Model dengan fitur RSI(14) dan EMA crossover sudah cukup untuk |
|                                                                |
| mendapatkan IC \> 0.05 pada BTCUSDT 1H.                        |
|                                                                |
| \## Config                                                     |
|                                                                |
| \- Symbol: BTCUSDT, TF: 1H, Period: 2021-01-01 -- 2023-12-31   |
|                                                                |
| \- Model: LightGBM default params                              |
|                                                                |
| \- Target: target_return_4h                                    |
|                                                                |
| \## Hasil                                                      |
|                                                                |
| \- IC mean: 0.062 ICIR: 1.21 Dir Acc: 53.1%                    |
|                                                                |
| \- Backtest OOS Sharpe: 0.94 Max DD: 18.2%                     |
|                                                                |
| \## Kesimpulan                                                 |
|                                                                |
| Hipotesis SEBAGIAN terpenuhi. IC lulus tapi Sharpe belum.      |
|                                                                |
| Next: coba tambah volatility features (exp-002).               |
|                                                                |
| \## Keputusan                                                  |
|                                                                |
| TIDAK deploy. Lanjut ke exp-002.                               |
+----------------------------------------------------------------+

+:--------------------------------------------------------------------------------------------------------------:+
| **Dokumen ini adalah kontrak implementasi Research Layer.**                                                    |
|                                                                                                                |
| Setiap perubahan interface publik atau config key default WAJIB diupdate di sini sebelum merge ke main branch. |
+----------------------------------------------------------------------------------------------------------------+
