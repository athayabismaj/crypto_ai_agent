**crypto_ai_agent**

**Dokumentasi: Notification**

**· Utils ·**

**Models Layer**

*telegram · discord · notifier*

*logger · structured_logger · time_utils · helpers*

*spot_model · futures_model · metadata.json*

Versi 1.0 \| Referensi: agent_core_docs.docx · runtime_layer_docs.docx

  ------------------------------------------------

  ------------------------------------------------

**1. Overview**

Tiga komponen pendukung ini melayani seluruh sistem tanpa punya logika bisnis sendiri. Notification mengirim pesan keluar. Utils menyediakan fungsi yang dipakai semua layer. Models menyimpan artefak ML yang menghidupkan strategi.

  ---------------------------------------------------------------------------------------------------------------------------------
  **Komponen**        **Tujuan**                                               **Siapa yang Pakai**
  ------------------- -------------------------------------------------------- ----------------------------------------------------
  **notification/**   Kirim pesan ke operator via Telegram & Discord           monitoring, trade_layer, learning_layer, main_loop

  **utils/**          Fungsi stateless yang dipakai seluruh layer              Semua layer --- import bebas

  **models/**         Simpan model ML & metadata yang dipakai strategy_layer   strategy_layer, main.py (validasi saat startup)
  ---------------------------------------------------------------------------------------------------------------------------------

**BAGIAN A --- Notification Layer**

**2. notifier.py --- Router Terpusat**

Satu-satunya titik masuk untuk semua notifikasi. Layer lain tidak boleh import telegram.py atau discord.py secara langsung --- semuanya melalui Notifier.

**2.1 EventType → Channel Mapping**

  -------------------------------------------------------------------------------------------------------
  **Event**           **Telegram**   **Discord**   **Log Level**   **Kondisi Kirim**
  ------------------- -------------- ------------- --------------- --------------------------------------
  trade_opened        Ya             Tidak         INFO            notify_trade_open = True di config

  trade_closed_win    Ya             Tidak         INFO            notify_trade_close = True

  trade_closed_loss   Ya             Ya            INFO            notify_trade_close = True

  circuit_breaker     Ya             Ya            ERROR           Selalu --- tidak bisa dimatikan

  strategy_disabled   Ya             Ya            ERROR           Selalu

  system_error        Ya             Ya            CRITICAL        Selalu

  drift_major         Ya             Tidak         WARNING         Drift severity = major atau critical

  daily_summary       Ya             Tidak         INFO            notify_daily_summary = True

  low_budget_llm      Ya             Tidak         WARNING         LLM budget \< 20% tersisa

  reconcile_issue     Ya             Ya            WARNING         Rekonsiliasi harian menemukan isu
  -------------------------------------------------------------------------------------------------------

**2.2 Interface Publik**

+-----------------------------------------------------------------+
| class Notifier:                                                 |
|                                                                 |
| def \_\_init\_\_(                                               |
|                                                                 |
| self,                                                           |
|                                                                 |
| telegram: \'TelegramNotifier\',                                 |
|                                                                 |
| discord: \'DiscordNotifier\',                                   |
|                                                                 |
| config: AgentConfig,                                            |
|                                                                 |
| ): \...                                                         |
|                                                                 |
| async def notify(                                               |
|                                                                 |
| self,                                                           |
|                                                                 |
| event_type: str,                                                |
|                                                                 |
| data: dict,                                                     |
|                                                                 |
| severity: str = \'info\', \# info \| warning \| critical        |
|                                                                 |
| ) -\> None:                                                     |
|                                                                 |
| \"\"\"                                                          |
|                                                                 |
| Entry point utama. Routing dilakukan di sini.                   |
|                                                                 |
| Gagal kirim notifikasi TIDAK boleh crash main loop.             |
|                                                                 |
| Wrap semua send() dengan try/except.                            |
|                                                                 |
| \"\"\"                                                          |
|                                                                 |
| async def send_emergency(                                       |
|                                                                 |
| self,                                                           |
|                                                                 |
| title: str,                                                     |
|                                                                 |
| message: str,                                                   |
|                                                                 |
| data: dict = None,                                              |
|                                                                 |
| ) -\> None:                                                     |
|                                                                 |
| \"\"\"                                                          |
|                                                                 |
| Kirim ke semua channel dengan retry agresif.                    |
|                                                                 |
| Dipanggil oleh safe_mode.emergency_stop().                      |
|                                                                 |
| Tidak boleh gagal diam-diam --- raise jika semua channel gagal. |
|                                                                 |
| \"\"\"                                                          |
|                                                                 |
| def format_message(                                             |
|                                                                 |
| self,                                                           |
|                                                                 |
| event_type: str,                                                |
|                                                                 |
| data: dict,                                                     |
|                                                                 |
| ) -\> str:                                                      |
|                                                                 |
| \"\"\"Delegasikan ke formatter per event type.\"\"\"            |
+-----------------------------------------------------------------+

  ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------
  **ATURAN:** Semua notifikasi harus non-blocking terhadap main loop. Gunakan asyncio.create_task() untuk fire-and-forget. Jika pengiriman gagal setelah retry, log ke errors.log dan lanjutkan.

  ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------

**3. telegram.py --- Pengiriman Telegram**

Implementasi pengiriman pesan ke Telegram Bot API. Mendukung format HTML, retry otomatis, dan pembagian pesan panjang.

**3.1 Konfigurasi**

+-------------------------------------------------------------------+
| \# .env                                                           |
|                                                                   |
| TELEGRAM_BOT_TOKEN = \'123456789:ABCdef\...\'                     |
|                                                                   |
| TELEGRAM_CHAT_ID = \'-100123456789\' \# grup atau channel         |
|                                                                   |
| \# Opsional: chat ID terpisah per severity                        |
|                                                                   |
| TELEGRAM_ALERT_CHAT_ID = \'-100000000001\' \# khusus CRITICAL     |
|                                                                   |
| TELEGRAM_TRADE_CHAT_ID = \'-100000000002\' \# khusus trade update |
+-------------------------------------------------------------------+

**3.2 Interface Publik**

+----------------------------------------------------------+
| class TelegramNotifier:                                  |
|                                                          |
| MAX_MESSAGE_LEN = 4096 \# Telegram API hard limit        |
|                                                          |
| MAX_RETRY = 3                                            |
|                                                          |
| RETRY_DELAY_S = \[2, 5, 10\]                             |
|                                                          |
| async def send(                                          |
|                                                          |
| self,                                                    |
|                                                          |
| message: str,                                            |
|                                                          |
| chat_id: str = None, \# None = TELEGRAM_CHAT_ID          |
|                                                          |
| parse_mode: str = \'HTML\',                              |
|                                                          |
| silent: bool = False, \# True = no notification sound    |
|                                                          |
| ) -\> bool:                                              |
|                                                          |
| \"\"\"                                                   |
|                                                          |
| Kirim pesan. Return True jika berhasil.                  |
|                                                          |
| Jika pesan \> MAX_MESSAGE_LEN: split otomatis per baris. |
|                                                          |
| \"\"\"                                                   |
|                                                          |
| async def send_with_retry(                               |
|                                                          |
| self,                                                    |
|                                                          |
| message: str,                                            |
|                                                          |
| \*\*kwargs                                               |
|                                                          |
| ) -\> bool:                                              |
|                                                          |
| \"\"\"Wrapper dengan exponential backoff retry.\"\"\"    |
|                                                          |
| def is_configured(self) -\> bool:                        |
|                                                          |
| \"\"\"True jika BOT_TOKEN dan CHAT_ID tersedia.\"\"\"    |
+----------------------------------------------------------+

**3.3 Template Pesan --- Semua Event**

  --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------
  **Event**           **Format HTML**                                                                            **Field Data Wajib**
  ------------------- ------------------------------------------------------------------------------------------ -----------------------------------------------------------------------
  trade_opened        🟢/🔴 \<b\>Trade Dibuka\</b\> --- Symbol Side Qty @ Price \| SL TP \| Risk \| Mode         symbol, side, qty, avg_fill_price, sl_price, tp_price, risk_usd, mode

  trade_closed_win    ✅ \<b\>Trade Ditutup\</b\> --- Symbol \| +\$PnL (+pct%) \| Exit reason \| Equity          symbol, pnl_usd, pnl_pct, exit_reason, equity

  trade_closed_loss   ❌ \<b\>Trade Ditutup\</b\> --- Symbol \| -\$PnL (-pct%) \| Exit reason \| Equity          symbol, pnl_usd, pnl_pct, exit_reason, equity

  circuit_breaker     🚨 \<b\>CIRCUIT BREAKER\</b\> --- State \| Alasan \| Daily PnL \| Equity \| Halt X menit   state, reasons, daily_pnl, equity, halt_duration

  strategy_disabled   ⚠️ \<b\>Strategi Dinonaktifkan\</b\> --- ID \| Alasan \| Stats terakhir                    strategy_id, reasons, win_rate, sharpe

  system_error        🔴 \<b\>SYSTEM ERROR\</b\> --- Komponen \| Pesan \| Waktu                                  component, message, timestamp

  daily_summary       📊 \<b\>Summary Harian\</b\> --- PnL \| Trades \| WR \| Equity \| Open positions           daily_pnl, total_trades, win_rate, equity, open_count
  --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------

**3.4 Contoh Pesan Lengkap**

+-----------------------------------------------+
| \# trade_opened --- BUY                       |
|                                               |
| 🟢 \<b\>Trade Dibuka\</b\>                    |
|                                               |
| Simbol : \<code\>BTCUSDT\</code\>             |
|                                               |
| Side : BUY \| Qty: 0.001                      |
|                                               |
| Entry : 50,000.00 USDT                        |
|                                               |
| SL : 49,000.00 \| TP: 52,000.00               |
|                                               |
| Risk : \$50.00 \| R:R = 1:2                   |
|                                               |
| Mode : \[PAPER\]                              |
|                                               |
| \# circuit_breaker --- HALTED                 |
|                                               |
| 🚨 \<b\>CIRCUIT BREAKER HALTED\</b\>          |
|                                               |
| Alasan : Daily loss 5.2% \> batas 5.0%        |
|                                               |
| PnL Hari Ini : -\$520.00 (-5.20%)             |
|                                               |
| Equity : \$9,480.00                           |
|                                               |
| Auto-resume : 60 menit                        |
|                                               |
| \# daily_summary                              |
|                                               |
| 📊 \<b\>Ringkasan Harian --- 2024-11-15\</b\> |
|                                               |
| PnL : +\$234.50 (+2.35%)                      |
|                                               |
| Trades : 6 \| Menang: 4 \| Kalah: 2           |
|                                               |
| Win Rate: 66.7%                               |
|                                               |
| Equity : \$10,234.50                          |
|                                               |
| Posisi terbuka: 0                             |
+-----------------------------------------------+

**4. discord.py --- Pengiriman Discord**

Implementasi webhook Discord untuk alert serius. Discord digunakan sebagai secondary channel --- hanya menerima WARNING dan CRITICAL alerts.

**4.1 Konfigurasi**

+--------------------------------------------------------------------------+
| \# .env                                                                  |
|                                                                          |
| DISCORD_WEBHOOK_URL = \'https://discord.com/api/webhooks/\...\'          |
|                                                                          |
| \# Opsional: webhook berbeda per channel                                 |
|                                                                          |
| DISCORD_ALERT_WEBHOOK = \'https://discord.com/api/webhooks/\.../alerts\' |
|                                                                          |
| DISCORD_TRADE_WEBHOOK = \'https://discord.com/api/webhooks/\.../trades\' |
+--------------------------------------------------------------------------+

**4.2 Discord Embed Format**

+----------------------------------------------------------------+
| class DiscordNotifier:                                         |
|                                                                |
| MAX_EMBED_DESCRIPTION = 4096                                   |
|                                                                |
| EMBED_COLORS = {                                               |
|                                                                |
| \'info\': 0x2980B9, \# biru                                    |
|                                                                |
| \'warning\': 0xF39C12, \# oranye                               |
|                                                                |
| \'critical\': 0xE74C3C, \# merah                               |
|                                                                |
| \'success\': 0x27AE60, \# hijau                                |
|                                                                |
| }                                                              |
|                                                                |
| async def send(                                                |
|                                                                |
| self,                                                          |
|                                                                |
| title: str,                                                    |
|                                                                |
| description: str,                                              |
|                                                                |
| severity: str = \'info\',                                      |
|                                                                |
| fields: list\[dict\] = None,                                   |
|                                                                |
| webhook_url: str = None,                                       |
|                                                                |
| ) -\> bool:                                                    |
|                                                                |
| \"\"\"                                                         |
|                                                                |
| Kirim Discord embed.                                           |
|                                                                |
| fields = \[{\'name\': str, \'value\': str, \'inline\': bool}\] |
|                                                                |
| \"\"\"                                                         |
|                                                                |
| payload = {                                                    |
|                                                                |
| \'embeds\': \[{                                                |
|                                                                |
| \'title\': title,                                              |
|                                                                |
| \'description\': description,                                  |
|                                                                |
| \'color\': self.EMBED_COLORS.get(severity, 0x95A5A6),          |
|                                                                |
| \'fields\': fields or \[\],                                    |
|                                                                |
| \'timestamp\': utcnow().isoformat(),                           |
|                                                                |
| \'footer\': {\'text\': \'crypto_ai_agent\'},                   |
|                                                                |
| }\]                                                            |
|                                                                |
| }                                                              |
|                                                                |
| \...                                                           |
|                                                                |
| def is_configured(self) -\> bool:                              |
|                                                                |
| \"\"\"True jika DISCORD_WEBHOOK_URL tersedia.\"\"\"            |
+----------------------------------------------------------------+

**BAGIAN B --- Utils**

**5. logger.py --- Standard Logger**

Wrapper tipis di atas Python standard logging yang menambahkan konteks otomatis (mode, layer name) ke setiap log entry.

**5.1 Setup & Penggunaan**

+----------------------------------------------------------------------+
| \# Di setiap modul --- import seperti ini:                           |
|                                                                      |
| from utils.logger import get_logger                                  |
|                                                                      |
| log = get_logger(\_\_name\_\_)                                       |
|                                                                      |
| \# Penggunaan:                                                       |
|                                                                      |
| log.debug(\'Detail debug\', value=123)                               |
|                                                                      |
| log.info(\'Trade opened\', trade_id=\'uuid\', symbol=\'BTCUSDT\')    |
|                                                                      |
| log.warning(\'Spread tinggi\', spread_pct=1.5)                       |
|                                                                      |
| log.error(\'Order failed\', error=str(e), retry_count=3)             |
|                                                                      |
| log.critical(\'Circuit breaker triggered\', reason=\'daily_loss\')   |
|                                                                      |
| \# Semua call menerima keyword arguments untuk structured context    |
|                                                                      |
| \# Context otomatis ditambahkan: timestamp, level, logger_name, mode |
+----------------------------------------------------------------------+

**5.2 Interface Publik**

+--------------------------------------------------------------------+
| def get_logger(name: str) -\> \'AgentLogger\':                     |
|                                                                    |
| \"\"\"                                                             |
|                                                                    |
| Factory function. Panggil satu kali per modul di level module.     |
|                                                                    |
| Bukan di dalam fungsi --- menghindari buat logger berulang.        |
|                                                                    |
| \"\"\"                                                             |
|                                                                    |
| class AgentLogger:                                                 |
|                                                                    |
| \"\"\"                                                             |
|                                                                    |
| Wrapper di atas logging.Logger standar.                            |
|                                                                    |
| Semua method menerima \*\*kwargs untuk structured context.         |
|                                                                    |
| Context di-serialize ke JSON di StructuredLogger,                  |
|                                                                    |
| atau ke string key=value di StandardLogger.                        |
|                                                                    |
| \"\"\"                                                             |
|                                                                    |
| def debug(self, msg: str, \*\*ctx) -\> None: \...                  |
|                                                                    |
| def info(self, msg: str, \*\*ctx) -\> None: \...                   |
|                                                                    |
| def warning(self, msg: str, \*\*ctx) -\> None: \...                |
|                                                                    |
| def error(self, msg: str, \*\*ctx) -\> None: \...                  |
|                                                                    |
| def critical(self, msg: str, \*\*ctx) -\> None: \...               |
|                                                                    |
| def bind(self, \*\*ctx) -\> \'AgentLogger\':                       |
|                                                                    |
| \"\"\"                                                             |
|                                                                    |
| Return logger baru dengan context yang sudah di-bind.              |
|                                                                    |
| Berguna untuk menambahkan trade_id ke semua log dalam satu fungsi. |
|                                                                    |
| Contoh:                                                            |
|                                                                    |
| log = log.bind(trade_id=trade.trade_id, symbol=trade.symbol)       |
|                                                                    |
| log.info(\'Trade opened\') \# otomatis ada trade_id                |
|                                                                    |
| log.info(\'Order sent\') \# otomatis ada trade_id juga             |
|                                                                    |
| \"\"\"                                                             |
+--------------------------------------------------------------------+

**5.3 Log Level Convention**

  ----------------------------------------------------------------------------------------------------------------------------------------------
  **Level**      **Kapan Digunakan**                                                         **Dikirim ke Error Log?**   **Dikirim ke Alert?**
  -------------- --------------------------------------------------------------------------- --------------------------- -----------------------
  **DEBUG**      Detail internal untuk debugging. DIMATIKAN di production.                   Tidak                       Tidak

  **INFO**       Event normal penting: trade dibuka, sync selesai, config loaded.            Tidak                       Tidak

  **WARNING**    Kondisi tidak ideal tapi sistem masih jalan: spread tinggi, WS reconnect.   Tidak                       Tidak

  **ERROR**      Kegagalan yang butuh investigasi: order rejected, sync gagal.               Ya                          Tidak

  **CRITICAL**   Kegagalan fatal: safe mode, circuit breaker, DB corrupt.                    Ya                          Ya (via alerts.py)
  ----------------------------------------------------------------------------------------------------------------------------------------------

**6. structured_logger.py --- JSON Logging**

Menulis log dalam format JSON satu baris per entry. Format ini bisa diparse langsung oleh Prometheus, Grafana Loki, atau tools log analysis lainnya.

**6.1 Format JSON Standard**

+-------------------------------------------------------------+
| \# Setiap baris log = satu JSON object                      |
|                                                             |
| {                                                           |
|                                                             |
| \"timestamp\": \"2024-11-15T08:30:00.123Z\",                |
|                                                             |
| \"level\": \"INFO\",                                        |
|                                                             |
| \"logger\": \"trade_layer.manager\",                        |
|                                                             |
| \"message\": \"Trade opened\",                              |
|                                                             |
| \"mode\": \"paper\",                                        |
|                                                             |
| \"trade_id\": \"uuid-\...\",                                |
|                                                             |
| \"symbol\": \"BTCUSDT\",                                    |
|                                                             |
| \"side\": \"BUY\",                                          |
|                                                             |
| \"qty\": 0.001,                                             |
|                                                             |
| \"price\": 50000.0,                                         |
|                                                             |
| \"equity\": 10000.0                                         |
|                                                             |
| }                                                           |
|                                                             |
| \# Field WAJIB di setiap entry:                             |
|                                                             |
| \# timestamp (ISO 8601 UTC), level, logger, message, mode   |
|                                                             |
| \# Field KONDISIONAL (tambahkan jika relevan):              |
|                                                             |
| \# trade_id, symbol, strategy_id, equity, error, latency_ms |
+-------------------------------------------------------------+

**6.2 Interface Publik**

+--------------------------------------------------------------------+
| class StructuredLogger:                                            |
|                                                                    |
| \"\"\"                                                             |
|                                                                    |
| Implementasi AgentLogger yang output ke JSON.                      |
|                                                                    |
| Diaktifkan via LOG_FORMAT=json di config.                          |
|                                                                    |
| Fallback ke plain text jika LOG_FORMAT=text (default development). |
|                                                                    |
| \"\"\"                                                             |
|                                                                    |
| def \_\_init\_\_(                                                  |
|                                                                    |
| self,                                                              |
|                                                                    |
| name: str,                                                         |
|                                                                    |
| log_file: str = \'logs/agent.log\',                                |
|                                                                    |
| log_format: str = \'json\', \# \'json\' \| \'text\'                |
|                                                                    |
| level: str = \'INFO\',                                             |
|                                                                    |
| ): \...                                                            |
|                                                                    |
| def \_serialize(self, msg: str, level: str, \*\*ctx) -\> str:      |
|                                                                    |
| \"\"\"                                                             |
|                                                                    |
| Serialize ke JSON string.                                          |
|                                                                    |
| Tangani tipe yang tidak JSON-serializable:                         |
|                                                                    |
| \- datetime → isoformat()                                          |
|                                                                    |
| \- Exception → str()                                               |
|                                                                    |
| \- dataclass → dataclasses.asdict()                                |
|                                                                    |
| \- object → repr()                                                 |
|                                                                    |
| \"\"\"                                                             |
+--------------------------------------------------------------------+

**6.3 Konsistensi dengan Prometheus**

+-----------------------------------------------------------------+
| \# structured_logger.py dan prometheus_metrics.py harus sepakat |
|                                                                 |
| \# pada nama field yang sama untuk query yang konsisten.        |
|                                                                 |
| \# Contoh: latency                                              |
|                                                                 |
| \# Di structured_logger:                                        |
|                                                                 |
| log.info(\"Order placed\", latency_ms=123.4)                    |
|                                                                 |
| \# Di prometheus_metrics.py:                                    |
|                                                                 |
| metrics.histogram(\"order_latency_ms\").observe(123.4)          |
|                                                                 |
| \# Di Grafana query:                                            |
|                                                                 |
| \# sum(rate(order_latency_ms_bucket\[5m\])) by (symbol)         |
|                                                                 |
| \# Nama field log = nama metric Prometheus HARUS sama.          |
|                                                                 |
| \# Ini memudahkan korelasi antara log dan metric saat debug.    |
+-----------------------------------------------------------------+

**6.4 Config Keys Logger**

  -----------------------------------------------------------------------------------------------------
  **Key**           **Default**   **Keterangan**
  ----------------- ------------- ---------------------------------------------------------------------
  LOG_FORMAT        json          json = structured JSON. text = human readable untuk development.

  LOG_LEVEL         INFO          DEBUG hanya untuk development. Production = INFO.

  LOG_DIR           logs/         Direktori output semua file log.

  LOG_ROTATION      daily         Rotasi per hari. File lama di-compress dan disimpan RETENTION hari.

  LOG_RETENTION     30            Hari retensi log sebelum dihapus (kecuali errors.log = 90 hari).

  LOG_MAX_SIZE_MB   100           Max ukuran per file log sebelum forced rotation.
  -----------------------------------------------------------------------------------------------------

**7. time_utils.py --- Utilitas Waktu**

Semua operasi waktu harus menggunakan fungsi dari file ini. Tidak boleh ada datetime.now() atau datetime.utcnow() langsung di kode --- selalu via time_utils untuk konsistensi dan testability.

  -------------------------------------------------------------------------------------------------------------------------------------------------------------------
  **ATURAN:** Gunakan utcnow() dari time_utils, BUKAN datetime.utcnow() langsung. Alasannya: time_utils bisa di-mock dalam unit test untuk simulasi waktu tertentu.

  -------------------------------------------------------------------------------------------------------------------------------------------------------------------

**7.1 Fungsi Publik**

  -------------------------------------------------------------------------------------------------------------------
  **Fungsi**                       **Return**              **Keterangan**
  -------------------------------- ----------------------- ----------------------------------------------------------
  utcnow()                         datetime (UTC, aware)   Ganti datetime.utcnow(). Selalu timezone-aware.

  utcnow_ms()                      int                     Timestamp Unix dalam milidetik. Untuk client_order_id.

  tf_to_seconds(tf)                int                     \'1h\'→3600, \'4h\'→14400, \'1d\'→86400, \'1m\'→60, dst.

  tf_to_ms(tf)                     int                     tf_to_seconds × 1000. Untuk timestamp Binance API.

  candle_open_time(ts,tf)          datetime                Waktu pembukaan candle yang berisi timestamp ts.

  candle_close_time(ts,tf)         datetime                Waktu penutupan candle (candle_open + tf - 1ms).

  is_new_candle(ts,tf,prev_ts)     bool                    True jika ts dan prev_ts berada di candle berbeda.

  time_until_candle_close(tf)      float (detik)           Berapa detik sampai candle current selesai.

  format_duration(seconds)         str                     123 → \'2m 3s\', 3700 → \'1h 1m 40s\'. Untuk logging.

  is_daily_reset_due(last_reset)   bool                    True jika UTC 00:00 sudah lewat sejak last_reset.
  -------------------------------------------------------------------------------------------------------------------

**7.2 Implementasi & Mock Support**

+-------------------------------------------------------------------------------------------------------+
| from datetime import datetime, timezone                                                               |
|                                                                                                       |
| from typing import Optional                                                                           |
|                                                                                                       |
| \# Internal clock --- bisa di-override untuk testing                                                  |
|                                                                                                       |
| \_mock_time: Optional\[datetime\] = None                                                              |
|                                                                                                       |
| def utcnow() -\> datetime:                                                                            |
|                                                                                                       |
| \"\"\"Selalu kembalikan datetime timezone-aware UTC.\"\"\"                                            |
|                                                                                                       |
| if \_mock_time is not None:                                                                           |
|                                                                                                       |
| return \_mock_time                                                                                    |
|                                                                                                       |
| return datetime.now(timezone.utc)                                                                     |
|                                                                                                       |
| def mock_time(dt: datetime) -\> None:                                                                 |
|                                                                                                       |
| \"\"\"Override untuk unit testing. HANYA di test code.\"\"\"                                          |
|                                                                                                       |
| global \_mock_time                                                                                    |
|                                                                                                       |
| \_mock_time = dt                                                                                      |
|                                                                                                       |
| def reset_mock_time() -\> None:                                                                       |
|                                                                                                       |
| \"\"\"Reset ke real time setelah test selesai.\"\"\"                                                  |
|                                                                                                       |
| global \_mock_time                                                                                    |
|                                                                                                       |
| \_mock_time = None                                                                                    |
|                                                                                                       |
| \# Contoh penggunaan di test:                                                                         |
|                                                                                                       |
| def test_daily_reset():                                                                               |
|                                                                                                       |
| from utils.time_utils import mock_time, reset_mock_time, utcnow                                       |
|                                                                                                       |
| mock_time(datetime(2024, 11, 15, 23, 59, 59, tzinfo=timezone.utc))                                    |
|                                                                                                       |
| assert not scheduler.is_due(\'daily_reset\')                                                          |
|                                                                                                       |
| mock_time(datetime(2024, 11, 16, 0, 0, 1, tzinfo=timezone.utc))                                       |
|                                                                                                       |
| assert scheduler.is_due(\'daily_reset\')                                                              |
|                                                                                                       |
| reset_mock_time()                                                                                     |
|                                                                                                       |
| VALID_TIMEFRAMES = {\'1m\', \'3m\', \'5m\', \'15m\', \'30m\', \'1h\', \'2h\', \'4h\', \'6h\', \'1d\'} |
|                                                                                                       |
| def tf_to_seconds(tf: str) -\> int:                                                                   |
|                                                                                                       |
| mapping = {                                                                                           |
|                                                                                                       |
| \'1m\':60, \'3m\':180, \'5m\':300, \'15m\':900, \'30m\':1800,                                         |
|                                                                                                       |
| \'1h\':3600, \'2h\':7200, \'4h\':14400, \'6h\':21600,                                                 |
|                                                                                                       |
| \'1d\':86400                                                                                          |
|                                                                                                       |
| }                                                                                                     |
|                                                                                                       |
| if tf not in mapping:                                                                                 |
|                                                                                                       |
| raise ValueError(f\'Timeframe tidak valid: {tf}. Valid: {VALID_TIMEFRAMES}\')                         |
|                                                                                                       |
| return mapping\[tf\]                                                                                  |
+-------------------------------------------------------------------------------------------------------+

**8. helpers.py --- Fungsi Umum**

Kumpulan fungsi stateless yang dipakai di seluruh codebase. Tidak ada state, tidak ada I/O --- semua pure function yang mudah ditest.

**8.1 Quantity & Price Rounding**

+--------------------------------------------------------------------+
| import math                                                        |
|                                                                    |
| def round_qty(qty: float, step_size: float) -\> float:             |
|                                                                    |
| \"\"\"                                                             |
|                                                                    |
| Bulatkan quantity ke step_size menggunakan FLOOR.                  |
|                                                                    |
| BUKAN round biasa --- floor untuk menghindari over-order.          |
|                                                                    |
| Contoh:                                                            |
|                                                                    |
| round_qty(0.1235, 0.001) → 0.123 (bukan 0.124)                     |
|                                                                    |
| round_qty(0.009, 0.01) → 0.00 (jika di bawah step)                 |
|                                                                    |
| \"\"\"                                                             |
|                                                                    |
| if step_size \<= 0:                                                |
|                                                                    |
| raise ValueError(f\'step_size harus \> 0, dapat: {step_size}\')    |
|                                                                    |
| precision = max(0, round(-math.log10(step_size)))                  |
|                                                                    |
| return round(math.floor(qty / step_size) \* step_size, precision)  |
|                                                                    |
| def round_price(price: float, tick_size: float) -\> float:         |
|                                                                    |
| \"\"\"                                                             |
|                                                                    |
| Bulatkan harga ke tick_size menggunakan ROUND biasa.               |
|                                                                    |
| (Berbeda dari round_qty yang menggunakan floor)                    |
|                                                                    |
| \"\"\"                                                             |
|                                                                    |
| if tick_size \<= 0:                                                |
|                                                                    |
| raise ValueError(f\'tick_size harus \> 0, dapat: {tick_size}\')    |
|                                                                    |
| precision = max(0, round(-math.log10(tick_size)))                  |
|                                                                    |
| return round(round(price / tick_size) \* tick_size, precision)     |
|                                                                    |
| def clamp(value: float, min_val: float, max_val: float) -\> float: |
|                                                                    |
| \"\"\"Pastikan value dalam range \[min_val, max_val\].\"\"\"       |
|                                                                    |
| return max(min_val, min(max_val, value))                           |
|                                                                    |
| def safe_div(a: float, b: float, default: float = 0.0) -\> float:  |
|                                                                    |
| \"\"\"Bagi dengan aman --- return default jika b = 0.\"\"\"        |
|                                                                    |
| return a / b if b != 0 else default                                |
|                                                                    |
| def pct_change(old: float, new: float) -\> float:                  |
|                                                                    |
| \"\"\"Return persentase perubahan dari old ke new.\"\"\"           |
|                                                                    |
| return safe_div(new - old, abs(old)) \* 100                        |
+--------------------------------------------------------------------+

**8.2 Retry & Error Handling**

+-----------------------------------------------------------+
| import asyncio                                            |
|                                                           |
| from functools import wraps                               |
|                                                           |
| from typing import Callable, TypeVar                      |
|                                                           |
| T = TypeVar(\'T\')                                        |
|                                                           |
| async def retry_async(                                    |
|                                                           |
| coro_fn: Callable,                                        |
|                                                           |
| max_retry: int = 3,                                       |
|                                                           |
| base_delay_s: float = 1.0,                                |
|                                                           |
| max_delay_s: float = 60.0,                                |
|                                                           |
| backoff: float = 2.0,                                     |
|                                                           |
| exceptions: tuple = (Exception,),                         |
|                                                           |
| ) -\> any:                                                |
|                                                           |
| \"\"\"                                                    |
|                                                           |
| Retry coroutine dengan exponential backoff.               |
|                                                           |
| Contoh: max_retry=3, base=1, backoff=2 → delay 1s, 2s, 4s |
|                                                           |
| \"\"\"                                                    |
|                                                           |
| last_exc = None                                           |
|                                                           |
| delay = base_delay_s                                      |
|                                                           |
| for attempt in range(max_retry + 1):                      |
|                                                           |
| try:                                                      |
|                                                           |
| return await coro_fn()                                    |
|                                                           |
| except exceptions as e:                                   |
|                                                           |
| last_exc = e                                              |
|                                                           |
| if attempt == max_retry:                                  |
|                                                           |
| break                                                     |
|                                                           |
| await asyncio.sleep(min(delay, max_delay_s))              |
|                                                           |
| delay \*= backoff                                         |
|                                                           |
| raise last_exc                                            |
|                                                           |
| def retry_sync(                                           |
|                                                           |
| max_retry: int = 3,                                       |
|                                                           |
| base_delay_s:float = 0.5,                                 |
|                                                           |
| exceptions: tuple = (Exception,),                         |
|                                                           |
| ):                                                        |
|                                                           |
| \"\"\"Decorator untuk fungsi synchronous.\"\"\"           |
|                                                           |
| def decorator(fn: Callable) -\> Callable:                 |
|                                                           |
| \@wraps(fn)                                               |
|                                                           |
| def wrapper(\*args, \*\*kwargs):                          |
|                                                           |
| import time                                               |
|                                                           |
| last_exc = None                                           |
|                                                           |
| for attempt in range(max_retry + 1):                      |
|                                                           |
| try:                                                      |
|                                                           |
| return fn(\*args, \*\*kwargs)                             |
|                                                           |
| except exceptions as e:                                   |
|                                                           |
| last_exc = e                                              |
|                                                           |
| if attempt \< max_retry:                                  |
|                                                           |
| time.sleep(base_delay_s \* (2 \*\* attempt))              |
|                                                           |
| raise last_exc                                            |
|                                                           |
| return wrapper                                            |
|                                                           |
| return decorator                                          |
+-----------------------------------------------------------+

**8.3 Order ID & String Utilities**

+------------------------------------------------------------------------+
| import re, uuid                                                        |
|                                                                        |
| from utils.time_utils import utcnow_ms                                 |
|                                                                        |
| ORDER_ID_PATTERN = re.compile(r\'\^\[a-zA-Z0-9\_\\-\]{1,36}\$\')       |
|                                                                        |
| def generate_order_id(strategy_id: str, symbol: str) -\> str:          |
|                                                                        |
| \"\"\"                                                                 |
|                                                                        |
| Generate client_order_id yang unik dan deterministik.                  |
|                                                                        |
| Format: {strategy_prefix}\_{symbol_lower}\_{timestamp_ms}              |
|                                                                        |
| Contoh: spot001_btcusdt_1731658200000                                  |
|                                                                        |
| Otomatis di-truncate ke 36 karakter jika strategy_id terlalu panjang.  |
|                                                                        |
| \"\"\"                                                                 |
|                                                                        |
| prefix = strategy_id\[:8\].lower().replace(\' \', \'\_\')              |
|                                                                        |
| sym = symbol\[:8\].lower()                                             |
|                                                                        |
| ts = utcnow_ms()                                                       |
|                                                                        |
| raw = f\'{prefix}\_{sym}\_{ts}\'                                       |
|                                                                        |
| return raw\[:36\]                                                      |
|                                                                        |
| def validate_order_id(cid: str) -\> tuple\[bool, str\]:                |
|                                                                        |
| \"\"\"                                                                 |
|                                                                        |
| Return (True, \'\') jika valid.                                        |
|                                                                        |
| Return (False, pesan_error) jika tidak.                                |
|                                                                        |
| \"\"\"                                                                 |
|                                                                        |
| if not cid:                                                            |
|                                                                        |
| return False, \'client_order_id kosong\'                               |
|                                                                        |
| if len(cid) \> 36:                                                     |
|                                                                        |
| return False, f\'Terlalu panjang: {len(cid)} \> 36\'                   |
|                                                                        |
| if not ORDER_ID_PATTERN.match(cid):                                    |
|                                                                        |
| return False, f\'Karakter tidak valid: {cid}\'                         |
|                                                                        |
| return True, \'\'                                                      |
|                                                                        |
| def truncate(text: str, max_len: int, suffix: str = \'\...\') -\> str: |
|                                                                        |
| \"\"\"Truncate string dengan suffix jika terlalu panjang.\"\"\"        |
|                                                                        |
| if len(text) \<= max_len: return text                                  |
|                                                                        |
| return text\[:max_len - len(suffix)\] + suffix                         |
|                                                                        |
| def mask_key(key: str, visible_chars: int = 6) -\> str:                |
|                                                                        |
| \"\"\"                                                                 |
|                                                                        |
| Mask API key untuk logging aman.                                       |
|                                                                        |
| \'abc123xyz789\' → \'abc123\...789\'                                   |
|                                                                        |
| \"\"\"                                                                 |
|                                                                        |
| if len(key) \<= visible_chars \* 2: return \'\*\*\*\'                  |
|                                                                        |
| return key\[:visible_chars\] + \'\...\' + key\[-visible_chars:\]       |
+------------------------------------------------------------------------+

**8.4 Financial Calculations**

+-----------------------------------------------------------------------------+
| def calc_pnl(                                                               |
|                                                                             |
| side: str,                                                                  |
|                                                                             |
| entry_price: float,                                                         |
|                                                                             |
| exit_price: float,                                                          |
|                                                                             |
| qty: float,                                                                 |
|                                                                             |
| commission: float = 0.0,                                                    |
|                                                                             |
| ) -\> tuple\[float, float\]:                                                |
|                                                                             |
| \"\"\"                                                                      |
|                                                                             |
| Return (pnl_usd, pnl_pct).                                                  |
|                                                                             |
| BUY: pnl = (exit - entry) × qty - commission                                |
|                                                                             |
| SELL: pnl = (entry - exit) × qty - commission                               |
|                                                                             |
| \"\"\"                                                                      |
|                                                                             |
| gross = (exit_price - entry_price) \* qty                                   |
|                                                                             |
| if side == \'SELL\': gross = -gross                                         |
|                                                                             |
| pnl_usd = gross - commission                                                |
|                                                                             |
| cost = entry_price \* qty                                                   |
|                                                                             |
| pnl_pct = safe_div(pnl_usd, cost) \* 100                                    |
|                                                                             |
| return pnl_usd, pnl_pct                                                     |
|                                                                             |
| def calc_risk_reward(entry: float, sl: float, tp: float) -\> float:         |
|                                                                             |
| \"\"\"                                                                      |
|                                                                             |
| Return risk:reward ratio.                                                   |
|                                                                             |
| Contoh: entry=50000, sl=49000, tp=52000 → RR = 2.0                          |
|                                                                             |
| \"\"\"                                                                      |
|                                                                             |
| risk = abs(entry - sl)                                                      |
|                                                                             |
| reward = abs(tp - entry)                                                    |
|                                                                             |
| return safe_div(reward, risk)                                               |
|                                                                             |
| def calc_position_value(                                                    |
|                                                                             |
| qty: float,                                                                 |
|                                                                             |
| price: float,                                                               |
|                                                                             |
| leverage: int = 1,                                                          |
|                                                                             |
| ) -\> dict:                                                                 |
|                                                                             |
| \"\"\"                                                                      |
|                                                                             |
| Return dict: notional, margin, leverage_ratio                               |
|                                                                             |
| notional = qty × price                                                      |
|                                                                             |
| margin = notional / leverage                                                |
|                                                                             |
| \"\"\"                                                                      |
|                                                                             |
| notional = qty \* price                                                     |
|                                                                             |
| margin = safe_div(notional, leverage)                                       |
|                                                                             |
| return {\'notional\': notional, \'margin\': margin, \'leverage\': leverage} |
|                                                                             |
| def annualize_return(period_return: float, days: int) -\> float:            |
|                                                                             |
| \"\"\"                                                                      |
|                                                                             |
| Annualized return dari period return.                                       |
|                                                                             |
| Misal: 5% dalam 30 hari → \~73% annualized.                                 |
|                                                                             |
| Formula: (1 + r)\^(365/days) - 1                                            |
|                                                                             |
| \"\"\"                                                                      |
|                                                                             |
| if days \<= 0: return 0.0                                                   |
|                                                                             |
| return (1 + period_return) \*\* (365 / days) - 1                            |
+-----------------------------------------------------------------------------+

**8.5 Validation Helpers**

+---------------------------------------------------------------+
| def is_valid_symbol(symbol: str) -\> bool:                    |
|                                                               |
| \"\"\"                                                        |
|                                                               |
| Validasi format simbol Binance.                               |
|                                                               |
| Valid: BTCUSDT, ETHUSDT, SOLUSDT                              |
|                                                               |
| Invalid: btcusdt (lowercase), BTC/USDT (dengan slash), kosong |
|                                                               |
| \"\"\"                                                        |
|                                                               |
| if not symbol or len(symbol) \> 20: return False              |
|                                                               |
| return symbol.isupper() and symbol.isalnum()                  |
|                                                               |
| def is_valid_side(side: str) -\> bool:                        |
|                                                               |
| return side in (\'BUY\', \'SELL\')                            |
|                                                               |
| def is_valid_timeframe(tf: str) -\> bool:                     |
|                                                               |
| from utils.time_utils import VALID_TIMEFRAMES                 |
|                                                               |
| return tf in VALID_TIMEFRAMES                                 |
|                                                               |
| def validate_config_range(                                    |
|                                                               |
| value: float,                                                 |
|                                                               |
| min_val: float,                                               |
|                                                               |
| max_val: float,                                               |
|                                                               |
| name: str,                                                    |
|                                                               |
| ) -\> None:                                                   |
|                                                               |
| \"\"\"                                                        |
|                                                               |
| Raise ValueError jika value di luar range.                    |
|                                                               |
| Digunakan oleh AgentConfig validators.                        |
|                                                               |
| \"\"\"                                                        |
|                                                               |
| if not (min_val \<= value \<= max_val):                       |
|                                                               |
| raise ValueError(                                             |
|                                                               |
| f\'{name} harus dalam range \[{min_val}, {max_val}\], \'      |
|                                                               |
| f\'dapat: {value}\'                                           |
|                                                               |
| )                                                             |
+---------------------------------------------------------------+

**BAGIAN C --- Models**

**9. Struktur Folder models/**

+------------------------------------------------+
| agent/models/                                  |
|                                                |
| ├── spot_model.pkl ← model aktif spot          |
|                                                |
| ├── futures_model.pkl ← model aktif futures    |
|                                                |
| ├── metadata.json ← kontrak interface (KRITIS) |
|                                                |
| └── archive/                                   |
|                                                |
| ├── spot_model_v2.2.1.pkl                      |
|                                                |
| ├── spot_model_v2.2.1_metadata.json            |
|                                                |
| └── \... ← simpan minimal 3 versi terakhir     |
+------------------------------------------------+

  ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------
  **ATURAN ROLLBACK:** Minimal 3 versi model sebelumnya harus ada di archive/. Saat model baru bermasalah di production, rollback harus bisa dilakukan dalam \< 5 menit dengan hanya mengganti file .pkl dan metadata.json.

  ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------

**10. metadata.json --- Kontrak Interface ML**

File ini adalah kontrak antara Research Layer (yang melatih model) dan Runtime Layer (yang menggunakan model). Setiap field wajib dan harus konsisten.

**10.1 Format Lengkap**

+----------------------------------------------------------------------+
| {                                                                    |
|                                                                      |
| \"\_version\": \"1.0\",                                              |
|                                                                      |
| \"\_doc\": \"Interface contract antara research dan runtime\",       |
|                                                                      |
| \"model_id\": \"spot_lgbm_v2_3_0\",                                  |
|                                                                      |
| \"version\": \"2.3.0\",                                              |
|                                                                      |
| \"created_at\": \"2024-11-10T08:00:00Z\",                            |
|                                                                      |
| \"deployed_at\": \"2024-11-15T00:00:00Z\",                           |
|                                                                      |
| \"deployed_by\": \"automation/deploy.py\",                           |
|                                                                      |
| \"model_type\": \"lightgbm\",                                        |
|                                                                      |
| \"task\": \"regression\",                                            |
|                                                                      |
| \"symbol\": \"BTCUSDT\",                                             |
|                                                                      |
| \"timeframe\": \"1h\",                                               |
|                                                                      |
| \"train_period\": \[\"2020-01-01\", \"2024-10-31\"\],                |
|                                                                      |
| \"test_period\": \[\"2024-11-01\", \"2024-11-10\"\],                 |
|                                                                      |
| \"feature_names\": \[                                                |
|                                                                      |
| \"rsi_7\", \"rsi_14\", \"rsi_21\",                                   |
|                                                                      |
| \"ema_ratio_9_21\", \"ema_ratio_21_50\", \"ema_ratio_50_200\",       |
|                                                                      |
| \"atr_14\", \"atr_pct_14\", \"atr_percentile\",                      |
|                                                                      |
| \"bb_width_20\", \"bb_pct_20\",                                      |
|                                                                      |
| \"volume_ratio_20\", \"obv_norm\",                                   |
|                                                                      |
| \"log_return_1\", \"log_return_5\", \"log_return_20\",               |
|                                                                      |
| \"adx_14\", \"trend_strength\",                                      |
|                                                                      |
| \"funding_rate\", \"funding_cumulative_8h\"                          |
|                                                                      |
| \],                                                                  |
|                                                                      |
| \"feature_count\": 20,                                               |
|                                                                      |
| \"feature_config\": {                                                |
|                                                                      |
| \"rsi_periods\": \[7, 14, 21\],                                      |
|                                                                      |
| \"ema_periods\": \[9, 21, 50, 200\],                                 |
|                                                                      |
| \"atr_period\": 14,                                                  |
|                                                                      |
| \"bb_period\": 20,                                                   |
|                                                                      |
| \"include_funding\": false                                           |
|                                                                      |
| },                                                                   |
|                                                                      |
| \"target_col\": \"target_return_4h\",                                |
|                                                                      |
| \"target_horizon\": 4,                                               |
|                                                                      |
| \"thresholds\": {                                                    |
|                                                                      |
| \"entry_long\": 0.003,                                               |
|                                                                      |
| \"entry_short\": -0.003,                                             |
|                                                                      |
| \"min_confidence\": 0.60                                             |
|                                                                      |
| },                                                                   |
|                                                                      |
| \"metrics\": {                                                       |
|                                                                      |
| \"ic_mean\": 0.078,                                                  |
|                                                                      |
| \"ic_std\": 0.031,                                                   |
|                                                                      |
| \"icir\": 2.52,                                                      |
|                                                                      |
| \"dir_accuracy\": 0.543,                                             |
|                                                                      |
| \"sharpe_signal\": 1.12,                                             |
|                                                                      |
| \"backtest_sharpe\": 1.34,                                           |
|                                                                      |
| \"backtest_maxdd\": 0.162,                                           |
|                                                                      |
| \"backtest_winrate\":0.551                                           |
|                                                                      |
| },                                                                   |
|                                                                      |
| \"status\": \"production\",                                          |
|                                                                      |
| \"replaces\": \"spot_lgbm_v2_2_1\",                                  |
|                                                                      |
| \"compatible_modes\": \[\"paper\", \"shadow\", \"live\"\],           |
|                                                                      |
| \"notes\": \"Ditambahkan funding rate feature. IC naik dari 0.062.\" |
|                                                                      |
| }                                                                    |
+----------------------------------------------------------------------+

**10.2 Field Wajib vs Opsional**

  ---------------------------------------------------------------------------------------------
  **Field**        **Wajib?**   **Digunakan Oleh**                   **Jika Berubah**
  ---------------- ------------ ------------------------------------ --------------------------
  feature_names    Ya           strategy_layer, main.py (validasi)   Major version bump wajib

  feature_count    Ya           main.py (validasi cepat)             Major version bump wajib

  feature_config   Ya           RuntimeFeatureEngine                 Major version bump wajib

  thresholds       Ya           spot_strategy / futures_strategy     Patch version bump

  target_col       Ya           research (dokumentasi)               Minor version bump

  metrics          Ya           model_registry, monitoring           Tidak mempengaruhi versi

  status           Ya           main.py (validasi status)            Tidak bump versi

  notes            Tidak        Dokumentasi saja                     ---
  ---------------------------------------------------------------------------------------------

**10.3 Versioning Rules**

  ----------------------------------------------------------------------------------------------------------
  **Perubahan**                                **Version Bump**        **Contoh**
  -------------------------------------------- ----------------------- -------------------------------------
  Tambah / hapus / ubah urutan feature_names   MAJOR (2.x.x → 3.0.0)   Tambah \'obv_norm\' ke feature list

  Ubah feature_config (tambah fitur baru)      MAJOR                   include_funding: false → true

  Ubah target_col atau target_horizon          MINOR (2.3.x → 2.4.0)   target_return_4h → target_return_8h

  Retrain dengan data lebih baru, fitur sama   MINOR (2.3.x → 2.4.0)   Update train_period saja

  Ubah thresholds (entry_long/short)           PATCH (2.3.0 → 2.3.1)   entry_long: 0.003 → 0.004

  Update metrics setelah evaluasi ulang        Tidak bump versi        Hanya update field metrics
  ----------------------------------------------------------------------------------------------------------

**11. Model Loader --- Validasi Saat Startup**

Setiap kali agent dimulai, model harus divalidasi untuk memastikan feature_names cocok dengan runtime feature pipeline. Ketidakcocokan harus terdeteksi sebelum agent mulai trading.

**11.1 Implementasi ModelLoader**

+----------------------------------------------------------------------------------+
| import joblib, json                                                              |
|                                                                                  |
| from pathlib import Path                                                         |
|                                                                                  |
| class ModelCompatibilityError(Exception): pass                                   |
|                                                                                  |
| class ModelStatusError(Exception): pass                                          |
|                                                                                  |
| class ModelLoader:                                                               |
|                                                                                  |
| VALID_STATUSES = (\'production\', \'validated\')                                 |
|                                                                                  |
| def load(                                                                        |
|                                                                                  |
| self,                                                                            |
|                                                                                  |
| model_path: str \| Path,                                                         |
|                                                                                  |
| meta_path: str \| Path,                                                          |
|                                                                                  |
| config: AgentConfig,                                                             |
|                                                                                  |
| ) -\> \'TrainedModel\':                                                          |
|                                                                                  |
| \"\"\"                                                                           |
|                                                                                  |
| Load model + metadata, validasi kompatibilitas.                                  |
|                                                                                  |
| Raise jika tidak kompatibel --- jangan biarkan model bermasalah jalan.           |
|                                                                                  |
| \"\"\"                                                                           |
|                                                                                  |
| model = joblib.load(model_path)                                                  |
|                                                                                  |
| metadata = json.loads(Path(meta_path).read_text())                               |
|                                                                                  |
| self.\_validate_status(metadata)                                                 |
|                                                                                  |
| self.\_validate_features(metadata, config)                                       |
|                                                                                  |
| self.\_validate_model_api(model)                                                 |
|                                                                                  |
| self.\_validate_mode_compat(metadata, config)                                    |
|                                                                                  |
| return TrainedModel(model=model, metadata=metadata)                              |
|                                                                                  |
| def \_validate_status(self, metadata: dict) -\> None:                            |
|                                                                                  |
| status = metadata.get(\'status\', \'\')                                          |
|                                                                                  |
| if status not in self.VALID_STATUSES:                                            |
|                                                                                  |
| raise ModelStatusError(                                                          |
|                                                                                  |
| f\'Model status \"{status}\" tidak diizinkan untuk deploy. \'                    |
|                                                                                  |
| f\'Valid: {self.VALID_STATUSES}\'                                                |
|                                                                                  |
| )                                                                                |
|                                                                                  |
| def \_validate_features(self, metadata: dict, config: AgentConfig) -\> None:     |
|                                                                                  |
| expected = metadata.get(\'feature_names\', \[\])                                 |
|                                                                                  |
| runtime = RuntimeFeatureEngine(metadata).feature_names                           |
|                                                                                  |
| if expected != runtime:                                                          |
|                                                                                  |
| \# Cari perbedaan yang spesifik                                                  |
|                                                                                  |
| missing = set(expected) - set(runtime)                                           |
|                                                                                  |
| extra = set(runtime) - set(expected)                                             |
|                                                                                  |
| raise ModelCompatibilityError(                                                   |
|                                                                                  |
| f\'Feature mismatch!\\n\'                                                        |
|                                                                                  |
| f\' Missing di runtime: {missing}\\n\'                                           |
|                                                                                  |
| f\' Extra di runtime: {extra}\\n\'                                               |
|                                                                                  |
| f\' Perlu update FeatureConfig atau retrain model.\'                             |
|                                                                                  |
| )                                                                                |
|                                                                                  |
| def \_validate_model_api(self, model) -\> None:                                  |
|                                                                                  |
| if not hasattr(model, \'predict\'):                                              |
|                                                                                  |
| raise ModelCompatibilityError(\'Model tidak memiliki method predict()\')         |
|                                                                                  |
| def \_validate_mode_compat(                                                      |
|                                                                                  |
| self, metadata: dict, config: AgentConfig                                        |
|                                                                                  |
| ) -\> None:                                                                      |
|                                                                                  |
| compat = metadata.get(\'compatible_modes\', \[\'paper\', \'shadow\', \'live\'\]) |
|                                                                                  |
| if config.mode not in compat:                                                    |
|                                                                                  |
| raise ModelCompatibilityError(                                                   |
|                                                                                  |
| f\'Model tidak kompatibel dengan mode {config.mode}. \'                          |
|                                                                                  |
| f\'Compatible: {compat}\'                                                        |
|                                                                                  |
| )                                                                                |
+----------------------------------------------------------------------------------+

**11.2 TrainedModel Dataclass**

+------------------------------------------------------+
| \@dataclass                                          |
|                                                      |
| class TrainedModel:                                  |
|                                                      |
| model: any \# LightGBM / XGBoost / sklearn estimator |
|                                                      |
| metadata: dict                                       |
|                                                      |
| \# Shortcut properties (derive dari metadata)        |
|                                                      |
| \@property                                           |
|                                                      |
| def feature_names(self) -\> list\[str\]:             |
|                                                      |
| return self.metadata\[\'feature_names\'\]            |
|                                                      |
| \@property                                           |
|                                                      |
| def version(self) -\> str:                           |
|                                                      |
| return self.metadata\[\'version\'\]                  |
|                                                      |
| \@property                                           |
|                                                      |
| def thresholds(self) -\> dict:                       |
|                                                      |
| return self.metadata.get(\'thresholds\', {})         |
|                                                      |
| \@property                                           |
|                                                      |
| def entry_threshold_long(self) -\> float:            |
|                                                      |
| return self.thresholds.get(\'entry_long\', 0.003)    |
|                                                      |
| \@property                                           |
|                                                      |
| def entry_threshold_short(self) -\> float:           |
|                                                      |
| return self.thresholds.get(\'entry_short\', -0.003)  |
|                                                      |
| def predict(self, X) -\> float:                      |
|                                                      |
| \"\"\"                                               |
|                                                      |
| Wrapper predict yang memastikan input shape benar.   |
|                                                      |
| X bisa berupa: pd.Series, np.ndarray, atau list.     |
|                                                      |
| \"\"\"                                               |
|                                                      |
| import numpy as np                                   |
|                                                      |
| if hasattr(X, \'values\'): X = X.values              |
|                                                      |
| X = np.array(X).reshape(1, -1)                       |
|                                                      |
| return float(self.model.predict(X)\[0\])             |
+------------------------------------------------------+

**12. Deploy Model --- automation/deploy.py**

Proses memindahkan model baru dari research/ ke runtime/agent/models/. Harus dilakukan secara atomik --- tidak boleh ada kondisi di mana model lama sudah dihapus tapi model baru belum tersedia.

**12.1 Deploy Flow**

+---------------------------------------------------------------------------+
| \# automation/deploy.py --- dipanggil setelah model lulus semua checklist |
|                                                                           |
| async def deploy_model(                                                   |
|                                                                           |
| new_model_path: Path,                                                     |
|                                                                           |
| new_meta_path: Path,                                                      |
|                                                                           |
| market_type: str, \# \'spot\' \| \'futures\'                              |
|                                                                           |
| config: AgentConfig,                                                      |
|                                                                           |
| ) -\> DeployReport:                                                       |
|                                                                           |
| \"\"\"                                                                    |
|                                                                           |
| Atomic deploy: backup dulu, baru replace.                                 |
|                                                                           |
| Urutan:                                                                   |
|                                                                           |
| 1\. Validasi model baru via ModelLoader (gagal → abort)                   |
|                                                                           |
| 2\. Archive model lama ke models/archive/ dengan timestamp                |
|                                                                           |
| 3\. Rename metadata.json lama ke archive/{id}\_metadata.json              |
|                                                                           |
| 4\. Copy model baru ke models/{market_type}\_model.pkl                    |
|                                                                           |
| 5\. Update metadata.json dengan deployed_at = now()                       |
|                                                                           |
| 6\. Validasi sekali lagi dengan ModelLoader (gagal → rollback)            |
|                                                                           |
| 7\. Kirim notifikasi deploy berhasil                                      |
|                                                                           |
| \"\"\"                                                                    |
|                                                                           |
| def rollback_model(                                                       |
|                                                                           |
| market_type: str,                                                         |
|                                                                           |
| version: str = None, \# None = versi sebelumnya                           |
|                                                                           |
| ) -\> bool:                                                               |
|                                                                           |
| \"\"\"                                                                    |
|                                                                           |
| Rollback ke versi sebelumnya dari archive/.                               |
|                                                                           |
| Tidak perlu restart agent --- agent akan load ulang model                 |
|                                                                           |
| di tick berikutnya (jika hot-reload diaktifkan).                          |
|                                                                           |
| \"\"\"                                                                    |
+---------------------------------------------------------------------------+

**12.2 Hot-Reload Model (Opsional)**

+------------------------------------------------------------------------+
| \# Jika HOT_RELOAD_MODEL = True di config:                             |
|                                                                        |
| \# Scheduler cek setiap 5 menit apakah metadata.json berubah.          |
|                                                                        |
| \# Jika berubah (deployed_at lebih baru) → reload model tanpa restart. |
|                                                                        |
| class ModelHotReloader:                                                |
|                                                                        |
| async def check_and_reload(                                            |
|                                                                        |
| self,                                                                  |
|                                                                        |
| strategy: BaseStrategy,                                                |
|                                                                        |
| model_path: Path,                                                      |
|                                                                        |
| meta_path: Path,                                                       |
|                                                                        |
| ) -\> bool:                                                            |
|                                                                        |
| \"\"\"                                                                 |
|                                                                        |
| Return True jika model di-reload.                                      |
|                                                                        |
| Reload dilakukan saat tidak ada open trades untuk symbol tersebut.     |
|                                                                        |
| \"\"\"                                                                 |
|                                                                        |
| current_meta_ts = self.\_last_meta_ts.get(model_path)                  |
|                                                                        |
| new_meta_ts = meta_path.stat().st_mtime                                |
|                                                                        |
| if current_meta_ts == new_meta_ts:                                     |
|                                                                        |
| return False \# tidak ada perubahan                                    |
|                                                                        |
| \# Tunggu sampai tidak ada posisi terbuka                              |
|                                                                        |
| if self.\_store.count_open(strategy.symbol) \> 0:                      |
|                                                                        |
| return False \# tunda reload                                           |
|                                                                        |
| new_model = self.\_loader.load(model_path, meta_path, self.\_config)   |
|                                                                        |
| strategy.model = new_model                                             |
|                                                                        |
| self.\_last_meta_ts\[model_path\] = new_meta_ts                        |
|                                                                        |
| log.info(\'Model hot-reloaded\', version=new_model.version)            |
|                                                                        |
| return True                                                            |
+------------------------------------------------------------------------+

**13. Dependency Map & Checklist**

**13.1 Dependency Map**

  ---------------------------------------------------------------------------------------------------------------------------------
  **File**               **Import Dari**                     **Digunakan Oleh**
  ---------------------- ----------------------------------- ----------------------------------------------------------------------
  notifier.py            telegram.py, discord.py             monitoring, trade_layer, learning_layer, main_loop

  telegram.py            aiohttp, utils/helpers (truncate)   notifier.py

  discord.py             aiohttp                             notifier.py

  logger.py              Python logging stdlib               Setiap file (get_logger(\_\_name\_\_))

  structured_logger.py   logger.py                           Saat LOG_FORMAT=json

  time_utils.py          datetime stdlib                     Semua layer yang butuh waktu

  helpers.py             math, re, time_utils                risk_layer (round_qty), trade_layer (generate_order_id), semua layer

  metadata.json          --- (file statis)                   main.py (validasi), strategy_layer, ModelLoader

  \*.pkl                 joblib                              ModelLoader → TrainedModel → strategy_layer
  ---------------------------------------------------------------------------------------------------------------------------------

**13.2 Checklist Implementasi**

  -----------------------------------------------------------------------------------------------------------------------------------------------------------
  **No**   **Item**                                                                 **Verifikasi**                                                 **Done**
  -------- ------------------------------------------------------------------------ -------------------------------------------------------------- ----------
  1        Semua notifikasi wrap dalam try/except --- tidak crash main loop         Test: mock Telegram error → main loop tetap jalan              ☐

  2        send_emergency() raise jika semua channel gagal                          Test: mock semua channel error → Exception                     ☐

  3        Pesan \> 4096 karakter dipecah otomatis                                  Test: kirim string 5000 karakter → 2 pesan terpisah            ☐

  4        utcnow() dari time_utils, bukan datetime.utcnow() langsung               grep -rn \'datetime.utcnow()\' \--include=\'\*.py\' → kosong   ☐

  5        round_qty() menggunakan floor, round_price() menggunakan round           Test: round_qty(0.1235, 0.001) = 0.123, bukan 0.124            ☐

  6        retry_async() berhenti setelah max_retry, raise exception terakhir       Test: inject error terus → raise setelah N retry               ☐

  7        mask_key() tidak pernah expose full API key ke log                       grep -rn \'api_key\' logs/ setelah run → semua masked          ☐

  8        ModelLoader gagal loudly jika feature_names tidak cocok                  Test: ubah satu feature_name → ModelCompatibilityError         ☐

  9        Deploy atomic: backup dulu, baru replace                                 Test: simulasi crash di tengah deploy → model lama masih ada   ☐

  10       metadata.json selalu punya field feature_names, version, status          JSON schema validation di test suite                           ☐

  11       LOG_FORMAT=json menghasilkan valid JSON di setiap baris                  jq \'.\' logs/agent.log → tidak ada parse error                ☐

  12       structured_logger field names konsisten dengan Prometheus metric names   Buat mapping table di tests/                                   ☐
  -----------------------------------------------------------------------------------------------------------------------------------------------------------

+:-------------------------------------------------------------------------------------------------:+
| **Dokumen ini menutup seri dokumentasi crypto_ai_agent --- 11 dokumen total.**                    |
|                                                                                                   |
| Perubahan pada format pesan, helpers API, atau metadata.json schema WAJIB diupdate sebelum merge. |
|                                                                                                   |
| *Referensi: agent_core_docs.docx • research_layer_docs.docx • learning_llm_docs.docx*             |
+---------------------------------------------------------------------------------------------------+
