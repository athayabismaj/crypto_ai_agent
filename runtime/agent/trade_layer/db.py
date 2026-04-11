"""
db.py — Database Manager for Trade Layer.

Mengelola koneksi ke SQLite database tunggal (`trading.db`).
Database ini menggunakan mode WAL (Write-Ahead Logging) untuk performa
dan menangani 4 tabel utama:
1. active_trades
2. archived_trades
3. audit_events
4. idempotency_registry
"""

from __future__ import annotations

import logging
from pathlib import Path

import aiosqlite

logger = logging.getLogger(__name__)


class DatabaseManager:
    """Mengelola koneksi dan inisialisasi tabel SQLite."""

    def __init__(self, db_path: str | Path):
        self._db_path = Path(db_path)
        self._conn: aiosqlite.Connection | None = None

    async def connect(self) -> aiosqlite.Connection:
        """Membuka koneksi ke database dan return aiosqlite Connection."""
        if self._conn is not None:
            return self._conn

        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        # Buka koneksi
        self._conn = await aiosqlite.connect(self._db_path)
        self._conn.row_factory = aiosqlite.Row

        # Set mode WAL
        await self._conn.execute("PRAGMA journal_mode=WAL;")
        await self._conn.execute("PRAGMA synchronous=NORMAL;")
        # Set foreign keys aktif meskipun di SQLite defaultnya mati
        await self._conn.execute("PRAGMA foreign_keys=ON;")
        await self._conn.commit()

        await self._init_tables()
        return self._conn

    async def disconnect(self) -> None:
        """Menutup koneksi."""
        if self._conn:
            await self._conn.close()
            self._conn = None

    @property
    def connection(self) -> aiosqlite.Connection:
        """Mengambil koneksi aktif. Raise ValueError jika belum connect."""
        if self._conn is None:
            raise ValueError("Database belum terkoneksi. Panggil connect() dulu.")
        return self._conn

    async def _init_tables(self) -> None:
        """Membuat tabel jika belum ada."""
        if not self._conn:
            return

        # 1. Tabel active_trades (state.db)
        await self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS active_trades (
                trade_id TEXT PRIMARY KEY,
                client_order_id TEXT UNIQUE NOT NULL,
                symbol TEXT NOT NULL,
                side TEXT NOT NULL,
                order_type TEXT NOT NULL,
                strategy_id TEXT NOT NULL,
                mode TEXT NOT NULL,
                created_at TIMESTAMP NOT NULL,
                exchange_order_id TEXT,
                status TEXT NOT NULL,
                requested_qty REAL NOT NULL,
                filled_qty REAL NOT NULL,
                avg_fill_price REAL NOT NULL,
                limit_price REAL NOT NULL,
                sl_price REAL NOT NULL,
                tp_price REAL NOT NULL,
                risk_amount_usd REAL NOT NULL,
                leverage INTEGER NOT NULL,
                is_futures BOOLEAN NOT NULL,
                submitted_at TIMESTAMP,
                opened_at TIMESTAMP,
                updated_at TIMESTAMP,
                signal_confidence REAL,
                regime_at_entry TEXT,
                vol_regime_entry TEXT,
                metadata TEXT
            )
        """
        )

        # 2. Tabel archived_trades (experience.db)
        # Struktur sama dengan active_trades tapi ditambah exit info
        await self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS archived_trades (
                trade_id TEXT PRIMARY KEY,
                client_order_id TEXT UNIQUE NOT NULL,
                symbol TEXT NOT NULL,
                side TEXT NOT NULL,
                order_type TEXT NOT NULL,
                strategy_id TEXT NOT NULL,
                mode TEXT NOT NULL,
                created_at TIMESTAMP NOT NULL,
                exchange_order_id TEXT,
                status TEXT NOT NULL,
                requested_qty REAL NOT NULL,
                filled_qty REAL NOT NULL,
                avg_fill_price REAL NOT NULL,
                limit_price REAL NOT NULL,
                sl_price REAL NOT NULL,
                tp_price REAL NOT NULL,
                risk_amount_usd REAL NOT NULL,
                leverage INTEGER NOT NULL,
                is_futures BOOLEAN NOT NULL,
                submitted_at TIMESTAMP,
                opened_at TIMESTAMP,
                updated_at TIMESTAMP,
                signal_confidence REAL,
                regime_at_entry TEXT,
                vol_regime_entry TEXT,
                metadata TEXT,
                -- Exit Info --
                closed_at TIMESTAMP,
                exit_price REAL NOT NULL,
                pnl_usd REAL NOT NULL,
                pnl_pct REAL NOT NULL,
                commission_usd REAL NOT NULL,
                exit_reason TEXT
            )
        """
        )

        # 3. Tabel idempotency_registry
        await self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS idempotency_registry (
                client_order_id TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                created_at TIMESTAMP NOT NULL,
                updated_at TIMESTAMP
            )
        """
        )

        # 4. Tabel audit_events
        await self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS audit_events (
                event_id TEXT PRIMARY KEY,
                trade_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                actor TEXT NOT NULL,
                timestamp TIMESTAMP NOT NULL,
                equity_snapshot REAL NOT NULL,
                details TEXT,
                checksum TEXT NOT NULL
            )
        """
        )

        # Index untuk pencarian cepat
        await self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_active_symbol ON active_trades(symbol)"
        )
        await self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_audit_trade ON audit_events(trade_id)"
        )
        await self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_archive_symbol ON archived_trades(symbol)"
        )

        await self._conn.commit()
