"""
health_restart.py — Auto Restart Daemon
Memantau heartbeat dari luar proses agent. Jika agent mati atau freeze, force restart.
"""
from __future__ import annotations

import asyncio
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


HEARTBEAT_DB = Path("runtime/agent/memory/heartbeat.db")
CHECK_INTERVAL_S = 60  # Cek setiap 60 detik
DEAD_THRESHOLD_S = 180  # Dianggap mati jika tidak ada update selama 3 menit
MAX_RESTART = 3  # Maksimal restart berurutan dalam limit waktu
COOLDOWN_S = 300  # Tunggu 5 menit setelah restart sebelum restart lagi
RESET_COUNT_AFTER_S = 3600  # Reset counter restart jika agent hidup selama 1 jam


class HeartbeatStatus:
    ALIVE = "ALIVE"
    STALE = "STALE"
    DEAD = "DEAD"


def get_utcnow() -> datetime:
    return datetime.now(timezone.utc)


def read_heartbeat(db_path: Path) -> tuple[str, float]:
    """Baca status heartbeat terbaru dari SQLite, return (status, age_seconds)."""
    if not db_path.exists():
        log.warning(f"DB heartbeat tidak ditemukan di {db_path}")
        return HeartbeatStatus.DEAD, 9999.0

    try:
        conn = sqlite3.connect(db_path, timeout=5)
        # Ambil row yang is_active = 1
        cursor = conn.execute(
            "SELECT last_beat_time, service_status FROM heartbeats WHERE is_active = 1 ORDER BY last_beat_time DESC LIMIT 1"
        )
        row = cursor.fetchone()
        conn.close()

        if not row:
            return HeartbeatStatus.DEAD, 9999.0

        last_beat_iso, service_status = row
        last_beat = datetime.fromisoformat(last_beat_iso)

        # Jika timezone naive, anggap UTC
        if last_beat.tzinfo is None:
            last_beat = last_beat.replace(tzinfo=timezone.utc)

        age_s = (get_utcnow() - last_beat).total_seconds()

        if age_s > DEAD_THRESHOLD_S:
            return HeartbeatStatus.DEAD, age_s
        elif age_s > (DEAD_THRESHOLD_S / 2):
            return HeartbeatStatus.STALE, age_s
        else:
            return HeartbeatStatus.ALIVE, age_s

    except Exception as e:
        log.error(f"Gagal baca heartbeat: {e}")
        return HeartbeatStatus.DEAD, 9999.0


class HealthDaemon:
    def __init__(self, notifier: Any | None = None):
        self.notifier = notifier
        self.restart_count = 0
        self.last_restart: datetime | None = None
        self.last_alive: datetime | None = None

    async def _send_alert(self, msg: str, severity: str = "warning"):
        if self.notifier:
            try:
                await self.notifier.notify(
                    "system_alert", {"message": msg, "source": "HealthDaemon"}, severity=severity
                )
            except Exception as e:
                log.error(f"Gagal send alert: {e}")
        else:
            log.warning(f"ALERT: {severity} - {msg}")

    async def restart_agent(self) -> bool:
        """Kirim command untuk me-restart Docker container atau SystemD."""
        try:
            # Karena ini berjalan di lokal/host, kita asumsikan menggunakan docker compose up/restart atau script
            # Untuk project python lurus, mungkin ini mem-pkill python dan start ulang (tergantung setup user)
            # Karena environment user ini Windows, "docker restart crypto_ai_agent" mungkin valid jika pakai docker,
            # Tapi untuk aman kita log saja tindakan restart yang terisolasi.
            log.warning("🔄 MENGIRIM PERINTAH RESTART KE SISTEM...")

            # TODO: Sesuaikan dengan deployment aktual (Docker / Systemd / pm2)
            # result = subprocess.run(["docker", "restart", "crypto_ai_agent"], capture_output=True, text=True, timeout=60)
            # return result.returncode == 0

            log.info("Simulated restart (karena belum ditentukan metode deployment).")
            return True

        except Exception as e:
            log.error(f"Gagal eksekusi perintah restart: {e}")
            return False

    async def run_forever(self):
        log.info("HealthDaemon dimulai. Memonitor heartbeat agent...")

        while True:
            status, age_s = read_heartbeat(HEARTBEAT_DB)

            now = get_utcnow()

            # Reset restart counter jika sudah hidup lama
            if status == HeartbeatStatus.ALIVE:
                self.last_alive = now
                if (
                    self.last_restart
                    and (now - self.last_restart).total_seconds() > RESET_COUNT_AFTER_S
                ):
                    self.restart_count = 0
                    # log.info("Agent stabil > 1 jam, restart_count di-reset ke 0.")

            if status == HeartbeatStatus.DEAD:
                log.critical(f"AGENT DEAD! Heartbeat terakhir {age_s:.1f} detik yang lalu.")

                # Cek cooldown
                if self.last_restart and (now - self.last_restart).total_seconds() < COOLDOWN_S:
                    log.info("Masih dalam masa cooldown restart. Skip.")
                elif self.restart_count >= MAX_RESTART:
                    msg = f"Agent mati, max restart ({MAX_RESTART}) tercapai! Butuh intervensi manual."
                    log.critical(msg)
                    await self._send_alert(msg, severity="critical")

                    # Backoff panjang untuk mencegah loop alert
                    await asyncio.sleep(COOLDOWN_S * 2)
                else:
                    self.restart_count += 1
                    self.last_restart = now

                    msg = f"Agent tidak responsif (> {DEAD_THRESHOLD_S}s). Mencoba force restart (Percobaan {self.restart_count}/{MAX_RESTART})"
                    await self._send_alert(msg, severity="error")

                    success = await self.restart_agent()
                    if not success:
                        await self._send_alert(
                            "Perintah restart GAGAL dieksekusi oleh sistem.", severity="critical"
                        )

                    log.info(f"Menunggu {COOLDOWN_S} detik untuk agent startup...")
                    await asyncio.sleep(COOLDOWN_S)
                    continue  # skip tick agar tidak baca file lama saat startup

            elif status == HeartbeatStatus.STALE:
                log.warning(f"Agent stale (pulse melambat, {age_s:.1f}s lalu)")

            else:
                pass  # ALIVE, OK.

            await asyncio.sleep(CHECK_INTERVAL_S)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s [%(levelname)s] HealthDaemon: %(message)s"
    )
    daemon = HealthDaemon()

    try:
        asyncio.run(daemon.run_forever())
    except KeyboardInterrupt:
        log.info("HealthDaemon dihentikan user.")
