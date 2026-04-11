"""
scheduler.py — Cron Jobs Eksternal
Berjalan sebagai proses terpisah dari main agent.
Menjalankan tugas pemeliharaan: retrain mingguan, vacuum DB, backup, log rotate.
"""
from __future__ import annotations

import asyncio
import logging
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)


# ── Konfigurasi Paths ──
DB_DIR = Path("runtime/agent/memory")
LOG_DIR = Path("logs")
BACKUP_DIR = Path("backup/db")
ARCHIVE_LOGS_DIR = Path("logs/archive")

DB_DIR.mkdir(parents=True, exist_ok=True)
LOG_DIR.mkdir(parents=True, exist_ok=True)
BACKUP_DIR.mkdir(parents=True, exist_ok=True)
ARCHIVE_LOGS_DIR.mkdir(parents=True, exist_ok=True)


async def run_weekly_retrain() -> None:
    """
    Cron: Minggu 02:00 UTC
    Jalankan retrain jika model sudah > 7 hari.
    """
    log.info("Memulai cron run_weekly_retrain...")
    from automation.retrain import RetrainPipeline

    # Simulasi panggil semua strategy aktif
    # Dalam implementasi sesungguhnya, ini akan loop mengambil strategy aktif dari DB
    active_strategies = ["spot_lgbm_v1"]

    for strategy in active_strategies:
        pipeline = RetrainPipeline()
        report = await pipeline.run(strategy_id=strategy, force=False)
        if report.success:
            log.info(f"Retrain mingguan {strategy} BERHASIL.")
        else:
            log.error(f"Retrain mingguan {strategy} GAGAL: {report.abort_reason}")


async def rotate_logs() -> None:
    """
    Cron: Harian 01:00 UTC
    Compress log > 7 hari ke .gz, hapus log > 30 hari.
    """
    log.info("Memulai cron rotate_logs...")
    now = datetime.now(timezone.utc)

    try:
        import gzip

        for log_file in LOG_DIR.glob("*.log"):
            # Jangan rotate log yang sedang aktif hari ini (misal di_rotasi oleh logger sendiri, ini backup eksternal)
            mtime = datetime.fromtimestamp(log_file.stat().st_mtime, tz=timezone.utc)
            days_old = (now - mtime).days

            if days_old > 30 and log_file.name != "errors.log":
                log_file.unlink()
                log.info(f"Dihapus log usang: {log_file}")
            elif days_old > 7:
                # Compress
                gz_path = ARCHIVE_LOGS_DIR / f"{log_file.name}.gz"
                if not gz_path.exists():
                    with open(log_file, "rb") as f_in:
                        with gzip.open(gz_path, "wb") as f_out:
                            shutil.copyfileobj(f_in, f_out)
                    log_file.unlink()
                    log.info(f"Dikompres ke GZ: {gz_path}")
    except Exception as e:
        log.error(f"Log rotation gagal: {e}")


async def vacuum_databases() -> None:
    """
    Cron: Minggu 03:00 UTC
    VACUUM semua db SQLite untuk reclaim space.
    """
    log.info("Memulai cron vacuum_databases...")

    for db_path in DB_DIR.glob("*.db"):
        log.info(f"Vacuuming {db_path}...")
        try:
            # Gunakan sqlite3 biasa, pastikan tidak lock lama
            conn = sqlite3.connect(db_path, timeout=30)
            conn.execute("VACUUM")
            conn.close()

            # Print file size info
            size_mb = db_path.stat().st_size / (1024 * 1024)
            log.info(f"Vacuum selesai: {db_path.name} (Ukuran sekarang: {size_mb:.2f} MB)")
        except sqlite3.OperationalError as e:
            if "database is locked" in str(e):
                log.warning(f"Skip vacuum {db_path.name} (sedang dilock agent)")
            else:
                log.error(f"Gagal vacuum {db_path.name}: {e}")
        except Exception as e:
            log.error(f"Gagal vacuum {db_path.name}: {e}")


async def backup_databases() -> None:
    """
    Cron: Harian 04:00 UTC
    Copy semua .db ke backup/, lalu compress gzip.
    Hapus backup > 7 hari.
    """
    log.info("Memulai cron backup_databases...")
    import gzip

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d")

    try:
        # Buat backup baru
        for db_path in DB_DIR.glob("*.db"):
            backup_file = BACKUP_DIR / f"{db_path.stem}_{timestamp}.db.gz"
            if not backup_file.exists():
                with open(db_path, "rb") as f_in:
                    with gzip.open(backup_file, "wb") as f_out:
                        shutil.copyfileobj(f_in, f_out)
                log.info(f"Database backup: {backup_file}")

        # Clean up backup lama (> 7 hari)
        now = datetime.now(timezone.utc)
        for gz_file in BACKUP_DIR.glob("*.db.gz"):
            mtime = datetime.fromtimestamp(gz_file.stat().st_mtime, tz=timezone.utc)
            days_old = (now - mtime).days
            if days_old > 7:
                gz_file.unlink()
                log.info(f"Menghapus backup DB lama (>7 hari): {gz_file.name}")

    except Exception as e:
        log.error(f"Backup databases gagal: {e}")


# ── Runner Sederhana ──
if __name__ == "__main__":
    # Setup basic logging
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    # Arg parsing sangat sederhana
    import sys

    if len(sys.argv) < 2:
        print("Usage: python scheduler.py [retrain|rotate|vacuum|backup]")
        sys.exit(1)

    task = sys.argv[1]

    if task == "retrain":
        asyncio.run(run_weekly_retrain())
    elif task == "rotate":
        asyncio.run(rotate_logs())
    elif task == "vacuum":
        asyncio.run(vacuum_databases())
    elif task == "backup":
        asyncio.run(backup_databases())
    else:
        print(f"Task tidak dikenali: {task}")
