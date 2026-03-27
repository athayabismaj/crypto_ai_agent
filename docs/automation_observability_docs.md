**crypto_ai_agent**

**Dokumentasi: Automation**

**· Observability ·**

**Audit & Docs**

*retrain · deploy · scheduler · health_restart*

*prometheus_metrics · grafana_dashboard · alert_rules*

*trade_history · tax_report · pnl_tracker*

*architecture · agent_flow · risk_management · deployment_guide · rollback_guide*

Versi 1.0 \| Dokumen Terakhir --- Penutup Seri crypto_ai_agent

  ------------------------------------------------

  ------------------------------------------------

**1. Overview --- Tiga Lapisan Pendukung Operasional**

Tiga komponen ini memastikan sistem dapat beroperasi secara berkelanjutan: Automation menjalankan tugas rutin tanpa intervensi manusia, Observability memberikan visibilitas penuh ke kondisi sistem, dan Docs menjadi sumber kebenaran untuk semua operasional.

  ----------------------------------------------------------------------------------------------------------------
  **Komponen**         **Tujuan**                                                     **Siapa yang Butuh**
  -------------------- -------------------------------------------------------------- ----------------------------
  **automation/**      Jalankan tugas rutin: retrain, deploy, health restart          DevOps, sistem itu sendiri

  **observability/**   Monitor performa & kesehatan sistem secara real-time           Quant, DevOps, operator

  **audit/**           Simpan histori permanen untuk compliance & analisis            Quant, akuntan, audit

  **docs/**            Dokumentasi operasional --- cara deploy, rollback, emergency   Semua anggota tim
  ----------------------------------------------------------------------------------------------------------------

**BAGIAN A --- automation/**

**2. retrain.py --- Auto-Retrain Model**

Dipanggil secara otomatis ketika drift_detection.py mendeteksi drift signifikan, atau secara terjadwal setiap minggu. Koordinasi seluruh pipeline research untuk menghasilkan model baru.

**2.1 Trigger Kondisi**

  ------------------------------------------------------------------------------------------------------------------------
  **Trigger**           **Kondisi**                       **Aksi**                                         **Prioritas**
  --------------------- --------------------------------- ------------------------------------------------ ---------------
  **Drift kritis**      DriftReport.severity = critical   Retrain segera, suspend trading sampai selesai   1 (tertinggi)

  **Drift major**       DriftReport.severity = major      Schedule retrain malam ini                       2

  **Jadwal mingguan**   Setiap Minggu 02:00 UTC           Retrain dengan data terbaru                      3

  **Manual trigger**    Via gateway endpoint atau CLI     Retrain segera                                   Manual
  ------------------------------------------------------------------------------------------------------------------------

**2.2 Interface Publik**

+----------------------------------------------------------------------------+
| class RetrainPipeline:                                                     |
|                                                                            |
| async def run(                                                             |
|                                                                            |
| self,                                                                      |
|                                                                            |
| strategy_id: str,                                                          |
|                                                                            |
| force: bool = False, \# True = lewati drift check                          |
|                                                                            |
| suspend_trading:bool = False, \# True = hentikan trading saat retrain      |
|                                                                            |
| ) -\> \'RetrainReport\':                                                   |
|                                                                            |
| \"\"\"                                                                     |
|                                                                            |
| Urutan pipeline retrain:                                                   |
|                                                                            |
| 1\. Fetch data terbaru (fetch_data.py)                                     |
|                                                                            |
| 2\. Clean & feature engineering                                            |
|                                                                            |
| 3\. Leakage check --- gagal → abort                                        |
|                                                                            |
| 4\. Train model                                                            |
|                                                                            |
| 5\. Walk-forward validation                                                |
|                                                                            |
| 6\. Evaluate --- gagal threshold → abort, tidak deploy                     |
|                                                                            |
| 7\. Backtest OOS --- gagal threshold → abort                               |
|                                                                            |
| 8\. Deploy via deploy.py                                                   |
|                                                                            |
| 9\. Kirim notifikasi hasil                                                 |
|                                                                            |
| \"\"\"                                                                     |
|                                                                            |
| def get_last_report(self, strategy_id: str) -\> \'RetrainReport \| None\': |
|                                                                            |
| \...                                                                       |
|                                                                            |
| \@dataclass                                                                |
|                                                                            |
| class RetrainReport:                                                       |
|                                                                            |
| strategy_id: str                                                           |
|                                                                            |
| trigger: str \# \'drift\' \| \'scheduled\' \| \'manual\'                   |
|                                                                            |
| started_at: datetime                                                       |
|                                                                            |
| finished_at: datetime                                                      |
|                                                                            |
| success: bool                                                              |
|                                                                            |
| deployed: bool                                                             |
|                                                                            |
| new_version: str \# contoh: \'2.4.0\'                                      |
|                                                                            |
| old_version: str \# contoh: \'2.3.1\'                                      |
|                                                                            |
| metrics_new: dict \# IC, Sharpe, win rate model baru                       |
|                                                                            |
| metrics_old: dict \# IC, Sharpe, win rate model lama                       |
|                                                                            |
| abort_reason: str \# diisi jika success=False                              |
|                                                                            |
| duration_s: float                                                          |
+----------------------------------------------------------------------------+

**2.3 Safeguard --- Tidak Deploy Jika Lebih Buruk**

+--------------------------------------------------------------------------------------------------------------------+
| def \_should_deploy(                                                                                               |
|                                                                                                                    |
| self,                                                                                                              |
|                                                                                                                    |
| new_metrics: dict,                                                                                                 |
|                                                                                                                    |
| old_metrics: dict,                                                                                                 |
|                                                                                                                    |
| ) -\> tuple\[bool, str\]:                                                                                          |
|                                                                                                                    |
| \"\"\"                                                                                                             |
|                                                                                                                    |
| Model baru hanya di-deploy jika LEBIH BAIK dari model lama.                                                        |
|                                                                                                                    |
| Minimal pertahankan performa dalam toleransi.                                                                      |
|                                                                                                                    |
| Cek:                                                                                                               |
|                                                                                                                    |
| 1\. Sharpe baru \>= Sharpe lama × 0.90 (max turun 10%)                                                             |
|                                                                                                                    |
| 2\. IC baru \>= IC lama × 0.85 (max turun 15%)                                                                     |
|                                                                                                                    |
| 3\. Backtest OOS tetap lulus semua threshold minimum                                                               |
|                                                                                                                    |
| \"\"\"                                                                                                             |
|                                                                                                                    |
| if new_metrics\[\'sharpe\'\] \< old_metrics\[\'sharpe\'\] \* 0.90:                                                 |
|                                                                                                                    |
| return False, f\'Sharpe turun terlalu banyak: {new_metrics\[\"sharpe\"\]:.2f} vs {old_metrics\[\"sharpe\"\]:.2f}\' |
|                                                                                                                    |
| if new_metrics\[\'ic_mean\'\] \< old_metrics\[\'ic_mean\'\] \* 0.85:                                               |
|                                                                                                                    |
| return False, f\'IC turun terlalu banyak: {new_metrics\[\"ic_mean\"\]:.3f} vs {old_metrics\[\"ic_mean\"\]:.3f}\'   |
|                                                                                                                    |
| return True, \'\'                                                                                                  |
+--------------------------------------------------------------------------------------------------------------------+

**3. deploy.py --- Deploy Artefak ke Runtime**

Memindahkan model dan parameter hasil research ke runtime secara atomik. Bisa dipanggil oleh retrain.py secara otomatis atau oleh operator secara manual.

**3.1 Interface Publik**

+-----------------------------------------------------------------------------------+
| class DeployManager:                                                              |
|                                                                                   |
| async def deploy_model(                                                           |
|                                                                                   |
| self,                                                                             |
|                                                                                   |
| new_model_path: Path,                                                             |
|                                                                                   |
| new_meta_path: Path,                                                              |
|                                                                                   |
| market_type: str, \# \'spot\' \| \'futures\'                                      |
|                                                                                   |
| notify: bool = True,                                                              |
|                                                                                   |
| ) -\> \'DeployReport\':                                                           |
|                                                                                   |
| \"\"\"                                                                            |
|                                                                                   |
| Atomic deploy --- lihat models/deploy flow di notification_utils_models_docs.docx |
|                                                                                   |
| \"\"\"                                                                            |
|                                                                                   |
| def deploy_params(                                                                |
|                                                                                   |
| self,                                                                             |
|                                                                                   |
| strategy_id: str,                                                                 |
|                                                                                   |
| new_params_path: Path,                                                            |
|                                                                                   |
| ) -\> bool:                                                                       |
|                                                                                   |
| \"\"\"                                                                            |
|                                                                                   |
| Deploy best_params.json hasil optimasi ke runtime config.                         |
|                                                                                   |
| Simpan backup params lama sebelum replace.                                        |
|                                                                                   |
| \"\"\"                                                                            |
|                                                                                   |
| def rollback_model(                                                               |
|                                                                                   |
| self,                                                                             |
|                                                                                   |
| market_type: str,                                                                 |
|                                                                                   |
| version: str = None, \# None = versi sebelumnya                                   |
|                                                                                   |
| ) -\> bool:                                                                       |
|                                                                                   |
| \"\"\"Rollback ke versi model sebelumnya dari archive/.\"\"\"                     |
|                                                                                   |
| def list_available_versions(self, market_type: str) -\> list\[str\]:              |
|                                                                                   |
| \"\"\"List semua versi yang tersedia di archive/ untuk rollback.\"\"\"            |
|                                                                                   |
| \@dataclass                                                                       |
|                                                                                   |
| class DeployReport:                                                               |
|                                                                                   |
| market_type: str                                                                  |
|                                                                                   |
| old_version: str                                                                  |
|                                                                                   |
| new_version: str                                                                  |
|                                                                                   |
| success: bool                                                                     |
|                                                                                   |
| deployed_at: datetime                                                             |
|                                                                                   |
| rollback_path: str \# path backup jika perlu rollback                             |
|                                                                                   |
| error: str \# diisi jika success=False                                            |
+-----------------------------------------------------------------------------------+

**3.2 Deploy Checklist Otomatis**

  -------------------------------------------------------------------------------------------------------------------------
  **Step**   **Aksi**                                                                   **Gagal →**
  ---------- -------------------------------------------------------------------------- -----------------------------------
  1          Validasi model baru via ModelLoader (feature_names, status, mode compat)   Abort deploy, notifikasi error

  2          Backup model lama ke archive/{model_id}\_{timestamp}.pkl                   Abort jika backup gagal

  3          Backup metadata lama ke archive/{model_id}\_{timestamp}\_metadata.json     Abort jika backup gagal

  4          Copy model baru ke runtime/agent/models/{type}\_model.pkl                  Rollback ke backup, abort

  5          Update metadata.json dengan deployed_at = utcnow()                         Rollback ke backup, abort

  6          Validasi ulang dengan ModelLoader (smoke test)                             Auto rollback + notifikasi KRITIS

  7          Kirim notifikasi deploy berhasil ke Telegram                               Log error, lanjut (non-fatal)
  -------------------------------------------------------------------------------------------------------------------------

**4. scheduler.py & health_restart.py --- Automation Tasks**

**4.1 automation/scheduler.py --- Cron Jobs**

Bukan scheduler yang sama dengan core/scheduler.py di dalam agent. File ini adalah cron runner eksternal yang bisa berjalan bahkan saat agent mati.

+-----------------------------------------------------------------------------------+
| \# automation/scheduler.py                                                        |
|                                                                                   |
| \# Berjalan sebagai process terpisah (cron atau systemd timer)                    |
|                                                                                   |
| SCHEDULE = \[                                                                     |
|                                                                                   |
| \# Format: (cron_expression, task_function, description)                          |
|                                                                                   |
| (\'0 2 \* \* 0\', run_weekly_retrain, \'Retrain mingguan --- Minggu 02:00 UTC\'), |
|                                                                                   |
| (\'0 1 \* \* \*\', rotate_logs, \'Rotasi log harian --- 01:00 UTC\'),             |
|                                                                                   |
| (\'0 3 \* \* 0\', vacuum_databases, \'VACUUM SQLite --- Minggu 03:00 UTC\'),      |
|                                                                                   |
| (\'0 4 \* \* \*\', backup_databases, \'Backup DB harian --- 04:00 UTC\'),         |
|                                                                                   |
| (\'0 6 \* \* 1\', run_full_backtest, \'Backtest mingguan --- Senin 06:00 UTC\'),  |
|                                                                                   |
| \]                                                                                |
|                                                                                   |
| async def run_weekly_retrain():                                                   |
|                                                                                   |
| \"\"\"                                                                            |
|                                                                                   |
| Jalankan retrain jika model sudah \> 7 hari tidak diupdate                        |
|                                                                                   |
| dan tidak ada retrain yang sedang berjalan.                                       |
|                                                                                   |
| \"\"\"                                                                            |
|                                                                                   |
| async def rotate_logs():                                                          |
|                                                                                   |
| \"\"\"                                                                            |
|                                                                                   |
| Compress log \> 7 hari ke .gz.                                                    |
|                                                                                   |
| Hapus log \> 30 hari (kecuali errors.log = 90 hari).                              |
|                                                                                   |
| \"\"\"                                                                            |
|                                                                                   |
| async def vacuum_databases():                                                     |
|                                                                                   |
| \"\"\"                                                                            |
|                                                                                   |
| VACUUM semua SQLite database untuk reclaim space.                                 |
|                                                                                   |
| Jalankan saat agent idle (tidak ada trade aktif).                                 |
|                                                                                   |
| \"\"\"                                                                            |
|                                                                                   |
| async def backup_databases():                                                     |
|                                                                                   |
| \"\"\"                                                                            |
|                                                                                   |
| Copy semua .db ke backup/ dengan timestamp.                                       |
|                                                                                   |
| Compress dengan gzip.                                                             |
|                                                                                   |
| Hapus backup \> 7 hari.                                                           |
|                                                                                   |
| \"\"\"                                                                            |
+-----------------------------------------------------------------------------------+

**4.2 health_restart.py --- Auto Restart**

Memantau heartbeat agent dari luar process. Jika agent mati atau tidak responsif, otomatis restart.

+-------------------------------------------------------------------------------------------+
| \# automation/health_restart.py                                                           |
|                                                                                           |
| \# Berjalan sebagai Docker sidecar atau systemd service terpisah                          |
|                                                                                           |
| HEARTBEAT_DB = \'runtime/agent/memory/heartbeat.db\'                                      |
|                                                                                           |
| CHECK_INTERVAL_S = 60 \# cek setiap 60 detik                                              |
|                                                                                           |
| DEAD_THRESHOLD_S = 180 \# agent dianggap mati setelah 3 menit                             |
|                                                                                           |
| MAX_RESTART = 3 \# max restart dalam 1 jam                                                |
|                                                                                           |
| COOLDOWN_S = 300 \# tunggu 5 menit antar restart                                          |
|                                                                                           |
| async def main():                                                                         |
|                                                                                           |
| restart_count = 0                                                                         |
|                                                                                           |
| last_restart = None                                                                       |
|                                                                                           |
| while True:                                                                               |
|                                                                                           |
| await asyncio.sleep(CHECK_INTERVAL_S)                                                     |
|                                                                                           |
| status = read_heartbeat(HEARTBEAT_DB)                                                     |
|                                                                                           |
| if status == HeartbeatStatus.DEAD:                                                        |
|                                                                                           |
| \# Cek apakah masih dalam cooldown                                                        |
|                                                                                           |
| if last_restart and (utcnow()-last_restart).seconds \< COOLDOWN_S:                        |
|                                                                                           |
| continue                                                                                  |
|                                                                                           |
| if restart_count \>= MAX_RESTART:                                                         |
|                                                                                           |
| await send_alert_critical(\'Agent mati, max restart tercapai. Butuh intervensi manual.\') |
|                                                                                           |
| break                                                                                     |
|                                                                                           |
| log.critical(\'Agent DEAD --- restarting\', count=restart_count+1)                        |
|                                                                                           |
| await send_alert(\'Agent tidak responsif, me-restart\...\')                               |
|                                                                                           |
| await restart_agent()                                                                     |
|                                                                                           |
| restart_count += 1                                                                        |
|                                                                                           |
| last_restart = utcnow()                                                                   |
|                                                                                           |
| elif status == HeartbeatStatus.STALE:                                                     |
|                                                                                           |
| await send_alert_warning(\'Agent pulse lambat --- memantau\')                             |
|                                                                                           |
| async def restart_agent():                                                                |
|                                                                                           |
| \"\"\"Restart via Docker atau systemd.\"\"\"                                              |
|                                                                                           |
| import subprocess                                                                         |
|                                                                                           |
| result = subprocess.run(                                                                  |
|                                                                                           |
| \[\'docker\', \'restart\', \'crypto_ai_agent\'\],                                         |
|                                                                                           |
| capture_output=True, text=True, timeout=60                                                |
|                                                                                           |
| )                                                                                         |
|                                                                                           |
| if result.returncode != 0:                                                                |
|                                                                                           |
| log.error(\'Docker restart gagal\', stderr=result.stderr)                                 |
|                                                                                           |
| await asyncio.sleep(30) \# tunggu agent startup                                           |
+-------------------------------------------------------------------------------------------+

**BAGIAN B --- observability/**

**5. prometheus_metrics.py --- Metrics Endpoint**

Mengekspos semua metrik penting sistem dalam format Prometheus. Grafana membaca dari endpoint ini untuk membangun dashboard real-time.

**5.1 Semua Metrik yang Di-expose**

  ----------------------------------------------------------------------------------------------------------
  **Metric Name**          **Type**    **Label**             **Keterangan**
  ------------------------ ----------- --------------------- -----------------------------------------------
  agent_alive              Gauge       mode                  1 = hidup, 0 = mati. Alert jika 0 \> 90 detik

  equity_usd               Gauge       mode                  Equity saat ini dalam USDT

  daily_pnl_usd            Gauge       mode, strategy        PnL hari ini per strategi

  open_positions_total     Gauge       symbol, side          Jumlah posisi terbuka per simbol

  trade_opened_total       Counter     symbol, strategy      Total trade yang dibuka (cumulative)

  trade_closed_total       Counter     symbol, exit_reason   Total trade yang ditutup per alasan

  win_rate_30d             Gauge       strategy              Win rate 30 hari terakhir per strategi

  order_latency_ms         Histogram   exchange, endpoint    Latency order ke exchange (P50/P95/P99)

  circuit_breaker_state    Gauge       mode                  0=normal, 1=warned, 2=halted

  drawdown_pct             Gauge       mode                  Drawdown dari peak dalam persen

  ws_last_message_age_s    Gauge       symbol, stream        Detik sejak pesan WebSocket terakhir

  api_weight_used          Gauge       exchange              API weight yang digunakan (dari 1200)

  db_query_duration_ms     Histogram   db, operation         Latency query database

  llm_cost_usd_total       Counter     model                 Total biaya LLM yang dikeluarkan

  signal_generated_total   Counter     symbol, strategy      Total signal yang dihasilkan

  signal_blocked_total     Counter     symbol, reason        Total signal yang diblokir per alasan
  ----------------------------------------------------------------------------------------------------------

**5.2 Interface Publik**

+---------------------------------------------------------------------------------------------------+
| from prometheus_client import (Counter, Gauge, Histogram,                                         |
|                                                                                                   |
| start_http_server, REGISTRY)                                                                      |
|                                                                                                   |
| class AgentMetrics:                                                                               |
|                                                                                                   |
| \"\"\"                                                                                            |
|                                                                                                   |
| Singleton --- inisialisasi sekali di main.py.                                                     |
|                                                                                                   |
| Semua layer mengakses via metrics.gauge(\...), metrics.counter(\...), dll.                        |
|                                                                                                   |
| \"\"\"                                                                                            |
|                                                                                                   |
| \_instance: \'AgentMetrics\' = None                                                               |
|                                                                                                   |
| \@classmethod                                                                                     |
|                                                                                                   |
| def get(cls) -\> \'AgentMetrics\':                                                                |
|                                                                                                   |
| if cls.\_instance is None:                                                                        |
|                                                                                                   |
| cls.\_instance = cls()                                                                            |
|                                                                                                   |
| return cls.\_instance                                                                             |
|                                                                                                   |
| def start_server(self, port: int = 8090) -\> None:                                                |
|                                                                                                   |
| \"\"\"Start HTTP server di port 8090 untuk Prometheus scrape.\"\"\"                               |
|                                                                                                   |
| start_http_server(port, registry=REGISTRY)                                                        |
|                                                                                                   |
| log.info(\'Metrics server started\', port=port)                                                   |
|                                                                                                   |
| def gauge(self, name: str) -\> Gauge:                                                             |
|                                                                                                   |
| \"\"\"Return atau buat Gauge metric.\"\"\"                                                        |
|                                                                                                   |
| def counter(self, name: str) -\> Counter:                                                         |
|                                                                                                   |
| \"\"\"Return atau buat Counter metric.\"\"\"                                                      |
|                                                                                                   |
| def histogram(self, name: str) -\> Histogram:                                                     |
|                                                                                                   |
| \"\"\"Return atau buat Histogram metric.\"\"\"                                                    |
|                                                                                                   |
| def update_all(self, portfolio_state: dict, system_state: dict) -\> None:                         |
|                                                                                                   |
| \"\"\"                                                                                            |
|                                                                                                   |
| Update semua gauge sekaligus.                                                                     |
|                                                                                                   |
| Dipanggil oleh scheduler setiap 30 detik.                                                         |
|                                                                                                   |
| \"\"\"                                                                                            |
|                                                                                                   |
| \# Cara penggunaan di layer lain:                                                                 |
|                                                                                                   |
| \# from observability.prometheus_metrics import AgentMetrics                                      |
|                                                                                                   |
| \# metrics = AgentMetrics.get()                                                                   |
|                                                                                                   |
| \# metrics.gauge(\'equity_usd\').set(10234.50)                                                    |
|                                                                                                   |
| \# metrics.counter(\'trade_opened_total\').labels(symbol=\'BTCUSDT\', strategy=\'spot_v1\').inc() |
|                                                                                                   |
| \# metrics.histogram(\'order_latency_ms\').labels(exchange=\'binance\').observe(123.4)            |
+---------------------------------------------------------------------------------------------------+

**6. grafana_dashboard.json --- Dashboard Visualisasi**

File JSON Grafana yang bisa di-import langsung. Berisi semua panel untuk monitoring trading agent secara real-time.

**6.1 Panel yang Harus Ada**

  ----------------------------------------------------------------------------------------------------
  **Panel**               **Metric Source**            **Visualisasi**           **Alert Threshold**
  ----------------------- ---------------------------- ------------------------- ---------------------
  Agent Status            agent_alive                  Stat (hijau/merah)        \< 1 selama 90s

  Equity Curve            equity_usd                   Time series               \< 95% initial

  Daily PnL               daily_pnl_usd                Bar chart per hari        \< -5% equity

  Drawdown                drawdown_pct                 Time series + threshold   Garis merah di 10%

  Open Positions          open_positions_total         Table                     \> max_positions

  Win Rate 30d            win_rate_30d                 Gauge (0--100%)           \< 40%

  Order Latency P95       order_latency_ms             Time series               \> 2000ms

  Circuit Breaker State   circuit_breaker_state        Stat (warna per state)    = 2 (HALTED)

  API Weight Usage        api_weight_used              Gauge (0--1200)           \> 900

  Signal vs Blocked       signal_generated / blocked   Stacked bar               ---

  LLM Cost Daily          llm_cost_usd_total           Stat                      \> daily budget

  WS Staleness            ws_last_message_age_s        Stat per stream           \> 30s
  ----------------------------------------------------------------------------------------------------

**6.2 Cara Import Dashboard**

+------------------------------------------------------------------+
| \# 1. Buka Grafana: http://localhost:3000                        |
|                                                                  |
| \# 2. Menu: Dashboards → Import                                  |
|                                                                  |
| \# 3. Upload observability/grafana_dashboard.json                |
|                                                                  |
| \# 4. Pilih Prometheus data source                               |
|                                                                  |
| \# 5. Klik Import                                                |
|                                                                  |
| \# Setup Prometheus scrape di prometheus.yml:                    |
|                                                                  |
| scrape_configs:                                                  |
|                                                                  |
| \- job_name: crypto_ai_agent                                     |
|                                                                  |
| static_configs:                                                  |
|                                                                  |
| \- targets: \[\'localhost:8090\'\]                               |
|                                                                  |
| scrape_interval: 15s                                             |
|                                                                  |
| \# Docker Compose snippet untuk monitoring stack:                |
|                                                                  |
| services:                                                        |
|                                                                  |
| prometheus:                                                      |
|                                                                  |
| image: prom/prometheus:latest                                    |
|                                                                  |
| volumes:                                                         |
|                                                                  |
| \- ./observability/prometheus.yml:/etc/prometheus/prometheus.yml |
|                                                                  |
| ports: \[\'9090:9090\'\]                                         |
|                                                                  |
| grafana:                                                         |
|                                                                  |
| image: grafana/grafana:latest                                    |
|                                                                  |
| ports: \[\'3000:3000\'\]                                         |
|                                                                  |
| environment:                                                     |
|                                                                  |
| \- GF_SECURITY_ADMIN_PASSWORD=your_password                      |
+------------------------------------------------------------------+

**7. alert_rules.yml --- Aturan Alert Otomatis**

Prometheus alerting rules yang akan trigger alert ke Alertmanager (yang bisa forward ke Telegram, Discord, atau email). Ini adalah lapisan alert terakhir di luar sistem agent itu sendiri.

**7.1 Format & Semua Rules**

+---------------------------------------------------------------------+
| \# observability/alert_rules.yml                                    |
|                                                                     |
| groups:                                                             |
|                                                                     |
| \- name: crypto_ai_agent_critical                                   |
|                                                                     |
| interval: 30s                                                       |
|                                                                     |
| rules:                                                              |
|                                                                     |
| \# Agent mati                                                       |
|                                                                     |
| \- alert: AgentDead                                                 |
|                                                                     |
| expr: agent_alive == 0                                              |
|                                                                     |
| for: 2m                                                             |
|                                                                     |
| labels: { severity: critical }                                      |
|                                                                     |
| annotations:                                                        |
|                                                                     |
| summary: \'Agent tidak responsif selama 2 menit\'                   |
|                                                                     |
| action: \'Cek health_restart.py, cek Docker logs\'                  |
|                                                                     |
| \# Circuit breaker aktif                                            |
|                                                                     |
| \- alert: CircuitBreakerHalted                                      |
|                                                                     |
| expr: circuit_breaker_state == 2                                    |
|                                                                     |
| for: 0m                                                             |
|                                                                     |
| labels: { severity: critical }                                      |
|                                                                     |
| annotations:                                                        |
|                                                                     |
| summary: \'Circuit breaker HALTED --- trading dihentikan\'          |
|                                                                     |
| action: \'Cek daily PnL dan drawdown. Lihat logs.\'                 |
|                                                                     |
| \# Drawdown kritis                                                  |
|                                                                     |
| \- alert: DrawdownCritical                                          |
|                                                                     |
| expr: drawdown_pct \> 8                                             |
|                                                                     |
| for: 5m                                                             |
|                                                                     |
| labels: { severity: critical }                                      |
|                                                                     |
| annotations:                                                        |
|                                                                     |
| summary: \'Drawdown {{ \$value }}% mendekati batas 10%\'            |
|                                                                     |
| \- name: crypto_ai_agent_warning                                    |
|                                                                     |
| interval: 60s                                                       |
|                                                                     |
| rules:                                                              |
|                                                                     |
| \# WebSocket stale                                                  |
|                                                                     |
| \- alert: WebSocketStale                                            |
|                                                                     |
| expr: ws_last_message_age_s \> 60                                   |
|                                                                     |
| for: 2m                                                             |
|                                                                     |
| labels: { severity: warning }                                       |
|                                                                     |
| annotations:                                                        |
|                                                                     |
| summary: \'WebSocket tidak menerima data {{ \$value }}s\'           |
|                                                                     |
| \# API weight tinggi                                                |
|                                                                     |
| \- alert: APIWeightHigh                                             |
|                                                                     |
| expr: api_weight_used \> 900                                        |
|                                                                     |
| for: 5m                                                             |
|                                                                     |
| labels: { severity: warning }                                       |
|                                                                     |
| annotations:                                                        |
|                                                                     |
| summary: \'API weight {{ \$value }}/1200 --- mendekati rate limit\' |
|                                                                     |
| \# Win rate rendah                                                  |
|                                                                     |
| \- alert: WinRateLow                                                |
|                                                                     |
| expr: win_rate_30d \< 0.40                                          |
|                                                                     |
| for: 0m                                                             |
|                                                                     |
| labels: { severity: warning }                                       |
|                                                                     |
| annotations:                                                        |
|                                                                     |
| summary: \'Win rate 30d {{ \$value }}% di bawah 40%\'               |
|                                                                     |
| \# Daily PnL negatif                                                |
|                                                                     |
| \- alert: DailyPnLNegative                                          |
|                                                                     |
| expr: daily_pnl_usd \< -(equity_usd \* 0.03)                        |
|                                                                     |
| for: 0m                                                             |
|                                                                     |
| labels: { severity: warning }                                       |
|                                                                     |
| annotations:                                                        |
|                                                                     |
| summary: \'Daily PnL sudah -{{ \$value }} USD (\>3% equity)\'       |
|                                                                     |
| \# LLM budget hampir habis                                          |
|                                                                     |
| \- alert: LLMBudgetLow                                              |
|                                                                     |
| expr: llm_cost_usd_total \> (llm_daily_budget \* 0.80)              |
|                                                                     |
| for: 0m                                                             |
|                                                                     |
| labels: { severity: info }                                          |
|                                                                     |
| annotations:                                                        |
|                                                                     |
| summary: \'LLM budget sudah 80% terpakai hari ini\'                 |
+---------------------------------------------------------------------+

**BAGIAN C --- audit/**

**8. trade_history.db --- Database Audit Permanen**

Database immutable yang menyimpan semua trade selamanya untuk keperluan compliance, tax reporting, dan analisis historis jangka panjang. Berbeda dari experience.db yang bisa di-archive.

**8.1 Schema**

+-----------------------------------------------------------+
| \-- Database: audit/trade_history.db                      |
|                                                           |
| \-- TIDAK boleh ada UPDATE atau DELETE                    |
|                                                           |
| \-- Hanya INSERT                                          |
|                                                           |
| CREATE TABLE all_trades (                                 |
|                                                           |
| \-- Identitas                                             |
|                                                           |
| trade_id TEXT PRIMARY KEY,                                |
|                                                           |
| client_order_id TEXT UNIQUE NOT NULL,                     |
|                                                           |
| exchange_order_id TEXT,                                   |
|                                                           |
| symbol TEXT NOT NULL,                                     |
|                                                           |
| side TEXT NOT NULL,                                       |
|                                                           |
| \-- Eksekusi                                              |
|                                                           |
| entry_price REAL NOT NULL,                                |
|                                                           |
| exit_price REAL,                                          |
|                                                           |
| qty REAL NOT NULL,                                        |
|                                                           |
| commission_usd REAL DEFAULT 0,                            |
|                                                           |
| \-- PnL                                                   |
|                                                           |
| pnl_usd REAL,                                             |
|                                                           |
| pnl_pct REAL,                                             |
|                                                           |
| net_pnl_usd REAL, \-- pnl_usd - commission_usd            |
|                                                           |
| \-- Context                                               |
|                                                           |
| strategy_id TEXT,                                         |
|                                                           |
| exit_reason TEXT,                                         |
|                                                           |
| regime_at_entry TEXT,                                     |
|                                                           |
| mode TEXT NOT NULL,                                       |
|                                                           |
| \-- Timestamps                                            |
|                                                           |
| opened_at TEXT NOT NULL,                                  |
|                                                           |
| closed_at TEXT,                                           |
|                                                           |
| \-- Audit metadata                                        |
|                                                           |
| recorded_at TEXT NOT NULL DEFAULT (datetime(\'now\'))     |
|                                                           |
| );                                                        |
|                                                           |
| CREATE TABLE reconciliation_reports (                     |
|                                                           |
| date TEXT PRIMARY KEY, \-- YYYY-MM-DD                     |
|                                                           |
| payload TEXT NOT NULL, \-- JSON report                    |
|                                                           |
| recorded_at TEXT NOT NULL                                 |
|                                                           |
| );                                                        |
|                                                           |
| CREATE TABLE daily_summaries (                            |
|                                                           |
| date TEXT NOT NULL,                                       |
|                                                           |
| strategy_id TEXT NOT NULL,                                |
|                                                           |
| total_trades INTEGER,                                     |
|                                                           |
| win_rate REAL,                                            |
|                                                           |
| pnl_usd REAL,                                             |
|                                                           |
| equity_end REAL,                                          |
|                                                           |
| recorded_at TEXT NOT NULL,                                |
|                                                           |
| PRIMARY KEY (date, strategy_id)                           |
|                                                           |
| );                                                        |
|                                                           |
| \-- Index untuk query tax report                          |
|                                                           |
| CREATE INDEX idx_closed_at ON all_trades(closed_at);      |
|                                                           |
| CREATE INDEX idx_symbol ON all_trades(symbol, closed_at); |
+-----------------------------------------------------------+

**9. tax_report.py & pnl_tracker.py**

**9.1 tax_report.py --- Laporan Pajak**

Generate laporan semua trade dalam periode tertentu dalam format yang bisa digunakan untuk perhitungan pajak. Format output: CSV dan ringkasan JSON.

+----------------------------------------------------------------------------------------------+
| class TaxReporter:                                                                           |
|                                                                                              |
| def generate(                                                                                |
|                                                                                              |
| self,                                                                                        |
|                                                                                              |
| year: int,                                                                                   |
|                                                                                              |
| output_dir: Path = Path(\'audit/reports/\'),                                                 |
|                                                                                              |
| ) -\> \'TaxReport\':                                                                         |
|                                                                                              |
| \"\"\"                                                                                       |
|                                                                                              |
| Generate laporan pajak untuk satu tahun.                                                     |
|                                                                                              |
| Output:                                                                                      |
|                                                                                              |
| 1\. audit/reports/tax\_{year}.csv --- semua trade detail                                     |
|                                                                                              |
| 2\. audit/reports/tax\_{year}\_summary.json --- ringkasan per bulan                          |
|                                                                                              |
| \"\"\"                                                                                       |
|                                                                                              |
| def generate_period(                                                                         |
|                                                                                              |
| self,                                                                                        |
|                                                                                              |
| start: datetime,                                                                             |
|                                                                                              |
| end: datetime,                                                                               |
|                                                                                              |
| ) -\> \'TaxReport\':                                                                         |
|                                                                                              |
| \"\"\"Generate untuk periode kustom.\"\"\"                                                   |
|                                                                                              |
| \@dataclass                                                                                  |
|                                                                                              |
| class TaxReport:                                                                             |
|                                                                                              |
| period: str \# \'2024\' atau \'2024-Q1\', dll                                                |
|                                                                                              |
| total_trades: int                                                                            |
|                                                                                              |
| winning_trades: int                                                                          |
|                                                                                              |
| losing_trades: int                                                                           |
|                                                                                              |
| gross_profit: float                                                                          |
|                                                                                              |
| gross_loss: float                                                                            |
|                                                                                              |
| net_pnl: float                                                                               |
|                                                                                              |
| total_commission:float                                                                       |
|                                                                                              |
| net_after_commission: float                                                                  |
|                                                                                              |
| by_symbol: dict \# {symbol: {pnl, trades, commission}}                                       |
|                                                                                              |
| by_month: dict \# {month: {pnl, trades}}                                                     |
|                                                                                              |
| csv_path: Path                                                                               |
|                                                                                              |
| json_path: Path                                                                              |
|                                                                                              |
| generated_at: datetime                                                                       |
|                                                                                              |
| \# Format CSV header:                                                                        |
|                                                                                              |
| \# date, symbol, side, entry_price, exit_price, qty, pnl_usd, commission, net_pnl, hold_days |
+----------------------------------------------------------------------------------------------+

**9.2 pnl_tracker.py --- Tracking PnL Real-time**

+-----------------------------------------------------------+
| class PnLTracker:                                         |
|                                                           |
| def get_current_pnl(self) -\> \'PnLSnapshot\':            |
|                                                           |
| \"\"\"                                                    |
|                                                           |
| Snapshot PnL saat ini:                                    |
|                                                           |
| \- Realized: dari trade yang sudah close                  |
|                                                           |
| \- Unrealized: estimasi dari posisi terbuka               |
|                                                           |
| \- Total: realized + unrealized                           |
|                                                           |
| \"\"\"                                                    |
|                                                           |
| def get_daily_pnl(self, days: int = 30) -\> list\[dict\]: |
|                                                           |
| \"\"\"Return list daily PnL untuk N hari terakhir.\"\"\"  |
|                                                           |
| def get_monthly_summary(self, year: int) -\> dict:        |
|                                                           |
| \"\"\"Return monthly PnL summary untuk satu tahun.\"\"\"  |
|                                                           |
| \@dataclass                                               |
|                                                           |
| class PnLSnapshot:                                        |
|                                                           |
| timestamp: datetime                                       |
|                                                           |
| realized_pnl_usd: float \# total dari trade closed        |
|                                                           |
| unrealized_pnl_usd: float \# estimasi dari open positions |
|                                                           |
| total_pnl_usd: float                                      |
|                                                           |
| total_commission: float                                   |
|                                                           |
| net_pnl_usd: float \# total - commission                  |
|                                                           |
| roi_pct: float \# net_pnl / initial_equity × 100          |
|                                                           |
| daily_pnl: float \# PnL hari ini saja                     |
|                                                           |
| weekly_pnl: float                                         |
|                                                           |
| monthly_pnl: float                                        |
+-----------------------------------------------------------+

**BAGIAN D --- docs/ --- Dokumentasi Operasional**

Dokumen-dokumen yang ada di folder docs/ adalah panduan operasional yang dibaca oleh manusia --- bukan dokumentasi kode (itu ada di seri docx yang sedang dibuat ini).

  -----------------------------------------------------------------------------------------------------------------------------
  **File**              **Isi**                                             **Pembaca Utama**   **Update Kapan**
  --------------------- --------------------------------------------------- ------------------- -------------------------------
  architecture.md       Diagram arsitektur layer & alur data                Developer baru      Saat ada perubahan arsitektur

  agent_flow.md         Flowchart: signal → risk → trade → close            Developer, Quant    Saat alur berubah

  risk_management.md    Penjelasan lengkap semua parameter risk             Quant, operator     Saat threshold diubah

  deployment_guide.md   Panduan deploy paper → shadow → live step by step   DevOps, operator    Saat prosedur berubah

  rollback_guide.md     Prosedur emergency: shutdown, rollback, recovery    Semua (emergency)   Saat prosedur berubah
  -----------------------------------------------------------------------------------------------------------------------------

**10. deployment_guide.md --- Panduan Deploy**

Konten yang harus ada di file ini. Bukan panduan teknis lengkap --- itu ada di seri docx --- tapi ringkasan operasional yang bisa dibaca cepat saat dibutuhkan.

**10.1 Struktur Dokumen**

+------------------------------------------------------------+
| \# deployment_guide.md                                     |
|                                                            |
| \## Prerequisites                                          |
|                                                            |
| \- Python 3.11+                                            |
|                                                            |
| \- Docker & Docker Compose                                 |
|                                                            |
| \- Akun Binance dengan API key (spot + futures)            |
|                                                            |
| \- Telegram Bot Token + Chat ID                            |
|                                                            |
| \- Server dengan minimal 2GB RAM, 20GB disk                |
|                                                            |
| \## Step-by-Step: Paper Mode (Mulai di Sini)               |
|                                                            |
| 1\. Clone repository                                       |
|                                                            |
| 2\. Copy .env.example ke .env, isi semua field             |
|                                                            |
| 3\. Set AGENT_MODE=paper                                   |
|                                                            |
| 4\. docker-compose up -d                                   |
|                                                            |
| 5\. Verifikasi: docker logs crypto_ai_agent \| tail -50    |
|                                                            |
| 6\. Cek Telegram --- harus ada pesan \'Agent started\'     |
|                                                            |
| 7\. Pantau Grafana dashboard                               |
|                                                            |
| \## Transisi ke Shadow Mode                                |
|                                                            |
| Syarat:                                                    |
|                                                            |
| \- Paper mode sudah jalan 2 minggu                         |
|                                                            |
| \- Sharpe 2 minggu \> 1.0                                  |
|                                                            |
| \- Tidak ada BLOCKED order yang mestinya jalan             |
|                                                            |
| Langkah:                                                   |
|                                                            |
| 1\. Pastikan tidak ada open position                       |
|                                                            |
| 2\. Ubah AGENT_MODE=shadow di .env                         |
|                                                            |
| 3\. docker-compose restart                                 |
|                                                            |
| \## Transisi ke Live Mode                                  |
|                                                            |
| Syarat:                                                    |
|                                                            |
| \- Shadow mode 1 minggu tanpa error kritis                 |
|                                                            |
| \- Circuit breaker tidak pernah trigger di shadow          |
|                                                            |
| \- Semua integration test lulus                            |
|                                                            |
| \- Checklist di runtime_layer_docs.docx Section 16 selesai |
|                                                            |
| Langkah:                                                   |
|                                                            |
| 1\. Pastikan tidak ada open position                       |
|                                                            |
| 2\. Backup semua database                                  |
|                                                            |
| 3\. Ubah AGENT_MODE=live di .env                           |
|                                                            |
| 4\. docker-compose restart                                 |
|                                                            |
| 5\. Monitor ketat 24 jam pertama                           |
+------------------------------------------------------------+

**11. rollback_guide.md --- Prosedur Darurat**

Dokumen yang paling penting dibaca sebelum live trading. Berisi prosedur yang harus dilakukan saat terjadi situasi darurat.

**11.1 Situasi Darurat & Respons**

  ----------------------------------------------------------------------------------------------------------------------------------------
  **Situasi**                         **Tingkat Urgensi**   **Aksi Pertama**                       **Aksi Lanjutan**
  ----------------------------------- --------------------- -------------------------------------- ---------------------------------------
  Circuit breaker HALTED              SEDANG                Pantau saja --- auto-resume 60 menit   Investigasi logs, cek daily PnL

  Agent tidak responsif (heartbeat)   TINGGI                health_restart.py akan auto-restart    Cek Docker logs, jangan panic

  Posisi terbuka tanpa pantauan       KRITIS                Login Binance web, close manual        Investigasi mengapa agent tidak close

  Model menghasilkan sinyal aneh      TINGGI                Manual halt via Telegram command       Rollback model ke versi sebelumnya

  Equity turun drastis                KRITIS                Emergency stop via gateway             Tutup semua posisi, investigasi

  DB corrupt                          KRITIS                Stop agent, restore dari backup        Jalankan recovery, rekonsiliasi

  API key dicurigai bocor             SANGAT KRITIS         Revoke key di Binance web SEGERA       Buat key baru, update .env, restart
  ----------------------------------------------------------------------------------------------------------------------------------------

**11.2 Emergency Stop --- Cara Cepat**

+------------------------------------------------------------------------------+
| \# Cara 1: Via Telegram Bot Command                                          |
|                                                                              |
| \# Kirim ke bot: /halt                                                       |
|                                                                              |
| \# Agent akan masuk safe_mode.emergency_stop()                               |
|                                                                              |
| \# Cara 2: Via Gateway API                                                   |
|                                                                              |
| curl -X POST http://localhost:8080/api/v1/control/halt \\                    |
|                                                                              |
| -H \'Authorization: Bearer YOUR_GATEWAY_KEY\' \\                             |
|                                                                              |
| -H \'Content-Type: application/json\' \\                                     |
|                                                                              |
| -d \'{\"reason\": \"manual halt\"}\'                                         |
|                                                                              |
| \# Cara 3: Docker stop (paling kasar --- agent tidak sempat clean up)        |
|                                                                              |
| docker stop crypto_ai_agent                                                  |
|                                                                              |
| \# PERINGATAN: Cara ini tidak cancel open orders!                            |
|                                                                              |
| \# Setelah docker stop, cek Binance web untuk open orders dan cancel manual. |
|                                                                              |
| \# Cara 4: Kill process (HANYA JIKA cara lain tidak bisa)                    |
|                                                                              |
| kill -SIGTERM \$(pgrep -f \'python main.py\')                                |
|                                                                              |
| \# Agent akan catch SIGTERM dan jalankan graceful_shutdown()                 |
+------------------------------------------------------------------------------+

**11.3 Model Rollback --- Prosedur**

+--------------------------------------------------------------------------+
| \# Rollback ke versi model sebelumnya                                    |
|                                                                          |
| \# Lihat versi yang tersedia:                                            |
|                                                                          |
| ls runtime/agent/models/archive/                                         |
|                                                                          |
| \# Output: spot_model_v2.2.1.pkl spot_model_v2.2.1_metadata.json \...    |
|                                                                          |
| \# Cara 1: Via CLI script                                                |
|                                                                          |
| python automation/deploy.py rollback \--market spot \--version 2.2.1     |
|                                                                          |
| \# Cara 2: Manual (jika CLI tidak bisa)                                  |
|                                                                          |
| cp runtime/agent/models/archive/spot_model_v2.2.1.pkl \\                 |
|                                                                          |
| runtime/agent/models/spot_model.pkl                                      |
|                                                                          |
| cp runtime/agent/models/archive/spot_model_v2.2.1_metadata.json \\       |
|                                                                          |
| runtime/agent/models/metadata.json                                       |
|                                                                          |
| \# Setelah rollback:                                                     |
|                                                                          |
| \# 1. Jika HOT_RELOAD_MODEL=True: model otomatis di-reload dalam 5 menit |
|                                                                          |
| \# 2. Jika tidak: docker-compose restart crypto_ai_agent                 |
|                                                                          |
| \# Verifikasi rollback berhasil:                                         |
|                                                                          |
| python -c \"                                                             |
|                                                                          |
| import json                                                              |
|                                                                          |
| meta = json.load(open(\'runtime/agent/models/metadata.json\'))           |
|                                                                          |
| print(\'Model version:\', meta\[\'version\'\])                           |
|                                                                          |
| print(\'Status:\', meta\[\'status\'\])                                   |
|                                                                          |
| \"                                                                       |
+--------------------------------------------------------------------------+

**11.4 Database Recovery --- Prosedur**

+---------------------------------------------------------------------+
| \# Jika state.db corrupt atau missing                               |
|                                                                     |
| \# Step 1: Stop agent                                               |
|                                                                     |
| docker stop crypto_ai_agent                                         |
|                                                                     |
| \# Step 2: Cek backup terbaru                                       |
|                                                                     |
| ls -lt runtime/agent/memory/backups/ \| head -5                     |
|                                                                     |
| \# Step 3: Restore dari backup                                      |
|                                                                     |
| cp runtime/agent/memory/backups/state_20241115_000000.db \\         |
|                                                                     |
| runtime/agent/memory/state.db                                       |
|                                                                     |
| \# Step 4: Restart agent --- recovery.py akan otomatis rekonsiliasi |
|                                                                     |
| docker start crypto_ai_agent                                        |
|                                                                     |
| \# Step 5: Monitor log recovery                                     |
|                                                                     |
| docker logs -f crypto_ai_agent \| grep -i recovery                  |
|                                                                     |
| \# Harus ada: \'Recovery complete, 0 discrepancies\'                |
|                                                                     |
| \# PENTING: Setelah recovery dari backup lama,                      |
|                                                                     |
| \# mungkin ada trade yang hilang. Cek Binance web                   |
|                                                                     |
| \# untuk posisi yang mungkin tidak ada di DB.                       |
+---------------------------------------------------------------------+

**12. docker-compose.yml --- Container Orchestration**

Konfigurasi Docker Compose untuk menjalankan seluruh stack: agent, monitoring, database backup, dan health restart.

+-----------------------------------------------------------------------+
| \# runtime/docker-compose.yml                                         |
|                                                                       |
| version: \'3.8\'                                                      |
|                                                                       |
| services:                                                             |
|                                                                       |
| \# ── Agent utama ───────────────────────────────────────────         |
|                                                                       |
| agent:                                                                |
|                                                                       |
| build: .                                                              |
|                                                                       |
| container_name: crypto_ai_agent                                       |
|                                                                       |
| restart: unless-stopped                                               |
|                                                                       |
| env_file: .env                                                        |
|                                                                       |
| volumes:                                                              |
|                                                                       |
| \- ./agent:/app/agent                                                 |
|                                                                       |
| \- ./logs:/app/logs                                                   |
|                                                                       |
| ports:                                                                |
|                                                                       |
| \- \'8080:8080\' \# gateway API                                       |
|                                                                       |
| \- \'8090:8090\' \# prometheus metrics                                |
|                                                                       |
| depends_on:                                                           |
|                                                                       |
| \- prometheus                                                         |
|                                                                       |
| healthcheck:                                                          |
|                                                                       |
| test: \[\'CMD\', \'python\', \'-c\',                                  |
|                                                                       |
| \'from monitoring.heartbeat import check_alive; check_alive()\'\]     |
|                                                                       |
| interval: 30s                                                         |
|                                                                       |
| timeout: 10s                                                          |
|                                                                       |
| retries: 3                                                            |
|                                                                       |
| \# ── Health restart sidecar ────────────────────────────────         |
|                                                                       |
| health_restart:                                                       |
|                                                                       |
| build: .                                                              |
|                                                                       |
| container_name: crypto_health_restart                                 |
|                                                                       |
| restart: always                                                       |
|                                                                       |
| command: python automation/health_restart.py                          |
|                                                                       |
| env_file: .env                                                        |
|                                                                       |
| volumes:                                                              |
|                                                                       |
| \- ./agent/memory:/app/agent/memory:ro \# read-only heartbeat DB      |
|                                                                       |
| depends_on:                                                           |
|                                                                       |
| \- agent                                                              |
|                                                                       |
| \# ── Prometheus ────────────────────────────────────────────         |
|                                                                       |
| prometheus:                                                           |
|                                                                       |
| image: prom/prometheus:latest                                         |
|                                                                       |
| container_name: crypto_prometheus                                     |
|                                                                       |
| restart: unless-stopped                                               |
|                                                                       |
| volumes:                                                              |
|                                                                       |
| \- ./observability/prometheus.yml:/etc/prometheus/prometheus.yml:ro   |
|                                                                       |
| \- ./observability/alert_rules.yml:/etc/prometheus/alert_rules.yml:ro |
|                                                                       |
| ports: \[\'9090:9090\'\]                                              |
|                                                                       |
| \# ── Grafana ───────────────────────────────────────────────         |
|                                                                       |
| grafana:                                                              |
|                                                                       |
| image: grafana/grafana:latest                                         |
|                                                                       |
| container_name: crypto_grafana                                        |
|                                                                       |
| restart: unless-stopped                                               |
|                                                                       |
| volumes:                                                              |
|                                                                       |
| \- grafana_data:/var/lib/grafana                                      |
|                                                                       |
| ports: \[\'3000:3000\'\]                                              |
|                                                                       |
| environment:                                                          |
|                                                                       |
| GF_SECURITY_ADMIN_PASSWORD: \'\${GRAFANA_PASSWORD}\'                  |
|                                                                       |
| volumes:                                                              |
|                                                                       |
| grafana_data:                                                         |
+-----------------------------------------------------------------------+

**13. Checklist Final & Ringkasan Seluruh Seri**

**13.1 Checklist Automation, Observability & Audit**

  -------------------------------------------------------------------------------------------------------------------------------------------------------
  **No**   **Item**                                                             **Verifikasi**                                                 **Done**
  -------- -------------------------------------------------------------------- -------------------------------------------------------------- ----------
  1        retrain.py abort jika model baru lebih buruk dari model lama         Test: inject metrics yang lebih rendah → should_deploy=False   ☐

  2        deploy.py atomic: backup dulu, replace, validasi ulang               Simulasi crash setelah replace → backup masih ada              ☐

  3        health_restart.py tidak restart lebih dari MAX_RESTART dalam 1 jam   Test: mock agent mati 5x → hanya 3x restart, lalu alert        ☐

  4        Semua Prometheus metrics ter-expose di port 8090                     curl localhost:8090/metrics \| grep agent_alive → ada          ☐

  5        alert_rules.yml diload oleh Prometheus tanpa error                   promtool check rules alert_rules.yml → OK                      ☐

  6        Grafana dashboard bisa di-import tanpa error                         Import JSON ke Grafana → semua panel terbuka                   ☐

  7        audit/trade_history.db tidak bisa di-UPDATE atau DELETE              Coba UPDATE → SQLite trigger atau aplikasi error               ☐

  8        tax_report.py menghasilkan CSV yang valid                            Open CSV di Excel → semua kolom benar, tidak ada NaN           ☐

  9        rollback_guide.md sudah dibaca oleh semua yang akan live trading     Team sign-off di dokumen                                       ☐

  10       docker-compose.yml bisa di-up tanpa error                            docker-compose up -d → semua service running                   ☐
  -------------------------------------------------------------------------------------------------------------------------------------------------------

**13.2 Ringkasan Seluruh Seri --- 13 Dokumen Selesai**

  --------------------------------------------------------------------------------------------------------------------------------------
  **No**   **Dokumen**                        **Layer**                                            **File**
  -------- ---------------------------------- ---------------------------------------------------- -------------------------------------
  1        Struktur Proyek Final              Semua layer (overview)                               crypto_ai_agent_structure.docx

  2        Research Layer                     pipeline, validation, modeling, backtest             research_layer_docs.docx

  3        Runtime Layer                      security, gateway, overview deployment               runtime_layer_docs.docx

  4        Agent Core                         main, config, modes, scheduler, event bus, memory    agent_core_docs.docx

  5        Data & Intelligence Layer          market, orderbook, websocket, regime, volatility     data_intelligence_docs.docx

  6        Strategy & Portfolio Layer         base/spot/futures strategy, allocator, risk budget   strategy_portfolio_docs.docx

  7        Risk Layer                         risk_manager, circuit_breaker, position_size, SL     risk_layer_docs.docx

  8        Trade & Execution Layer            trade, manager, idempotency, recovery, binance       trade_execution_docs.docx

  9        Exit, Monitoring & Sync Layer      exit_manager, trailing, heartbeat, sync              exit_monitoring_sync_docs.docx

  10       Learning & LLM Layer               drift, reflection, adaptation, strategy_killer       learning_llm_docs.docx

  11       Notification, Utils & Models       telegram, discord, helpers, time_utils, metadata     notification_utils_models_docs.docx

  12       Tests Layer                        unit, integration, mocks, conftest, coverage         tests_layer_docs.docx

  13       Automation, Observability & Docs   retrain, deploy, prometheus, grafana, rollback       automation_observability_docs.docx
  --------------------------------------------------------------------------------------------------------------------------------------

  -----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------
  **CARA PAKAI SERI INI:** Saat akan implementasi satu layer, upload dokumen yang relevan ke sesi baru bersama skeleton code. AI dapat generate implementasi yang konsisten dengan seluruh arsitektur tanpa perlu ulang penjelasan.

  -----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------

+:-------------------------------------------------------------------------------------------------------------------:+
| **Seri dokumentasi crypto_ai_agent selesai.**                                                                       |
|                                                                                                                     |
| **13 dokumen · seluruh layer tercakup · siap untuk implementasi.**                                                  |
|                                                                                                                     |
| Mulai dari Research Layer → implement satu layer per sesi → test → deploy bertahap.                                 |
|                                                                                                                     |
| *Setiap perubahan interface, config key default, atau prosedur operasional WAJIB diupdate di dokumen yang relevan.* |
+---------------------------------------------------------------------------------------------------------------------+
