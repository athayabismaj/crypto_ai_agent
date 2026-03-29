"""
Sistem Anti-Double Order menggunakan transaksi SQLite atomik (INSERT OR IGNORE).
Mencegah masalah idempotensi karena concurrency race conditions.
"""

import os
import sqlite3
from enum import Enum

from runtime.shared.utils import utcnow  # type: ignore


class IdempotencyResult(str, Enum):
    PROCEED = "proceed"  # order baru, lanjutkan
    DUPLICATE = "duplicate"  # sudah ada dan confirmed
    PENDING = "pending"  # sedang diproses


class IdempotencyManager:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self._init_db()

    def _init_db(self) -> None:
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        with sqlite3.connect(self.db_path, isolation_level=None) as conn:
            # WAL Mode untuk menghindari database is locked
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS order_registry (
                    client_order_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT
                )
            """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_status ON order_registry(status)")

    def check_or_register(self, client_order_id: str) -> IdempotencyResult:
        """
        Atomic check dan register dalam satu transaksi DB.
        Menggunakan BEGIN IMMEDIATE untuk write lock, mencegah race condition.
        """
        now_str = utcnow().isoformat()

        with sqlite3.connect(self.db_path) as conn:
            conn.execute("BEGIN IMMEDIATE")  # Lock untuk penulisan
            try:
                # Coba insert (abaikan kalau duplicate)
                conn.execute(
                    """
                    INSERT OR IGNORE INTO order_registry 
                    (client_order_id, status, created_at, updated_at) 
                    VALUES (?, 'pending', ?, ?)
                """,
                    (client_order_id, now_str, now_str),
                )

                # Baca state record terakhir
                cur = conn.execute(
                    "SELECT status, created_at FROM order_registry WHERE client_order_id = ?",
                    (client_order_id,),
                )
                row = cur.fetchone()

                # Evaluasi siapakah yang merubah data
                status = row[0]
                created_at = row[1]

                if status == "pending" and created_at == now_str:
                    return IdempotencyResult.PROCEED
                elif status == "pending":
                    return IdempotencyResult.PENDING
                else:
                    # confirmed, failed, dsb
                    return IdempotencyResult.DUPLICATE
            finally:
                conn.commit()

    def confirm(self, client_order_id: str) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "UPDATE order_registry SET status = ?, updated_at = ? WHERE client_order_id = ?",
                ("confirmed", utcnow().isoformat(), client_order_id),
            )

    def mark_failed(self, client_order_id: str, reason: str = "") -> None:
        """Boleh diretry dengan order_id yang baru kelak"""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "UPDATE order_registry SET status = ?, updated_at = ? WHERE client_order_id = ?",
                ("failed", utcnow().isoformat(), client_order_id),
            )

    def get_status(self, client_order_id: str) -> dict | None:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.execute(
                "SELECT * FROM order_registry WHERE client_order_id = ?", (client_order_id,)
            )
            row = cur.fetchone()
            return dict(row) if row else None

    def cleanup_old(self, older_than_days: int = 30) -> int:
        from datetime import timedelta

        cutoff = (utcnow() - timedelta(days=older_than_days)).isoformat()
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.execute(
                'DELETE FROM order_registry WHERE updated_at < ? AND status IN ("confirmed", "failed")',
                (cutoff,),
            )
            return cur.rowcount
