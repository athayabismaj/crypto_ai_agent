"""
idempotency.py — Sistem Anti-Double Order menggunakan transaksi SQLite atomik async.
Mencegah masalah idempotensi karena concurrency race conditions.
"""

from enum import Enum

import aiosqlite

from runtime.agent.trade_layer.db import DatabaseManager
from runtime.shared.utils import utcnow  # type: ignore


class IdempotencyResult(str, Enum):
    PROCEED = "proceed"  # order baru, lanjutkan
    DUPLICATE = "duplicate"  # sudah ada dan confirmed
    PENDING = "pending"  # sedang diproses


class IdempotencyManager:
    def __init__(self, db_manager: DatabaseManager):
        self._db = db_manager

    async def check_or_register(self, client_order_id: str) -> IdempotencyResult:
        """
        Atomic check dan register dalam satu transaksi DB.
        Menggunakan BEGIN IMMEDIATE untuk write lock, mencegah race condition.
        """
        now_str = utcnow().isoformat()
        conn = self._db.connection

        # Memastikan eksekusi atomic
        await conn.execute("BEGIN IMMEDIATE")
        try:
            # Coba insert (abaikan kalau duplicate)
            await conn.execute(
                """
                INSERT OR IGNORE INTO idempotency_registry 
                (client_order_id, status, created_at, updated_at) 
                VALUES (?, 'pending', ?, ?)
                """,
                (client_order_id, now_str, now_str),
            )

            # Baca state record terakhir
            cursor = await conn.execute(
                "SELECT status, created_at FROM idempotency_registry WHERE client_order_id = ?",
                (client_order_id,),
            )
            row: aiosqlite.Row | None = await cursor.fetchone()

            if not row:
                raise RuntimeError("Gagal membaca hasil insert idempotency_registry")

            status = row["status"]
            created_at = row["created_at"]

            if status == "pending" and created_at == now_str:
                result = IdempotencyResult.PROCEED
            elif status == "pending":
                result = IdempotencyResult.PENDING
            else:
                # confirmed, failed, dsb
                result = IdempotencyResult.DUPLICATE

            await conn.commit()
            return result
        except Exception:
            await conn.rollback()
            raise

    async def confirm(self, client_order_id: str) -> None:
        conn = self._db.connection
        await conn.execute(
            "UPDATE idempotency_registry SET status = ?, updated_at = ? WHERE client_order_id = ?",
            ("confirmed", utcnow().isoformat(), client_order_id),
        )
        await conn.commit()

    async def mark_failed(self, client_order_id: str, reason: str = "") -> None:
        """Boleh diretry dengan order_id yang baru kelak"""
        conn = self._db.connection
        # Bisa juga menyimpan reason di kolom error jika tabel dimodif ke depannya
        await conn.execute(
            "UPDATE idempotency_registry SET status = ?, updated_at = ? WHERE client_order_id = ?",
            ("failed", utcnow().isoformat(), client_order_id),
        )
        await conn.commit()

    async def get_status(self, client_order_id: str) -> dict | None:
        conn = self._db.connection
        cursor = await conn.execute(
            "SELECT * FROM idempotency_registry WHERE client_order_id = ?", (client_order_id,)
        )
        row = await cursor.fetchone()
        return dict(row) if row else None

    async def cleanup_old(self, older_than_days: int = 30) -> int:
        from datetime import timedelta

        cutoff = (utcnow() - timedelta(days=older_than_days)).isoformat()
        conn = self._db.connection

        cursor = await conn.execute(
            'DELETE FROM idempotency_registry WHERE updated_at < ? AND status IN ("confirmed", "failed")',
            (cutoff,),
        )
        await conn.commit()
        return cursor.rowcount
