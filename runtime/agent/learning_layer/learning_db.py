"""
Manajer Database Learning Layer.
Pusat koneksi untuk experience.db (log histori trade yang ditutup) 
dan performance.db (agregasi PnL metrik, riwayat adaptasi).
"""

import logging
import os

import aiosqlite

log = logging.getLogger(__name__)


class LearningDBManager:
    def __init__(self, data_dir: str):
        self.data_dir = data_dir
        self.exp_db_path = os.path.join(data_dir, "experience.db")
        self.perf_db_path = os.path.join(data_dir, "performance.db")
        self.exp_conn: aiosqlite.Connection | None = None
        self.perf_conn: aiosqlite.Connection | None = None

    async def connect(self) -> None:
        """Koneksi dan inisiasi skema awal jika dibutuhkan."""
        os.makedirs(self.data_dir, exist_ok=True)
        
        self.exp_conn = await aiosqlite.connect(self.exp_db_path)
        self.exp_conn.row_factory = aiosqlite.Row
        await self.exp_conn.execute("PRAGMA journal_mode=WAL;")
        
        self.perf_conn = await aiosqlite.connect(self.perf_db_path)
        self.perf_conn.row_factory = aiosqlite.Row
        await self.perf_conn.execute("PRAGMA journal_mode=WAL;")
        
        await self._init_schema()

    async def disconnect(self) -> None:
        if self.exp_conn:
            await self.exp_conn.close()
        if self.perf_conn:
            await self.perf_conn.close()

    async def _init_schema(self) -> None:
        if not self.exp_conn or not self.perf_conn:
            raise RuntimeError("Database belum terkoneksi!")

        # Experience DB (Raw Closed Trades - ditulis asumsikan oleh Exit Layer)
        await self.exp_conn.execute(
            """
            CREATE TABLE IF NOT EXISTS closed_trades (
                trade_id TEXT PRIMARY KEY,
                strategy_id TEXT NOT NULL,
                symbol TEXT NOT NULL,
                side TEXT NOT NULL,
                entry_time TEXT NOT NULL,
                exit_time TEXT NOT NULL,
                hold_candles INTEGER NOT NULL,
                confidence_at_entry REAL NOT NULL,
                regime_at_entry TEXT NOT NULL,
                pnl_usd REAL NOT NULL,
                pnl_pct REAL NOT NULL,
                exit_reason TEXT NOT NULL,
                risk_amount_usd REAL NOT NULL
            )
            """
        )
        await self.exp_conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_closed_strat ON closed_trades(strategy_id)"
        )
        await self.exp_conn.commit()

        # Performance DB (Agregasi)
        await self.perf_conn.execute(
            """
            CREATE TABLE IF NOT EXISTS strategy_stats (
                strategy_id TEXT,
                period TEXT,
                computed_at TEXT,
                total_trades INTEGER,
                winning_trades INTEGER,
                win_rate REAL,
                profit_factor REAL,
                total_pnl_usd REAL,
                max_drawdown_pct REAL,
                sharpe_ratio REAL,
                PRIMARY KEY (strategy_id, period)
            )
            """
        )
        await self.perf_conn.execute(
            """
            CREATE TABLE IF NOT EXISTS adaptation_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                strategy_id TEXT,
                timestamp TEXT,
                parameter_name TEXT,
                old_value REAL,
                new_value REAL,
                reason TEXT
            )
            """
        )
        await self.perf_conn.execute(
            """
            CREATE TABLE IF NOT EXISTS disabled_strategies (
                strategy_id TEXT PRIMARY KEY,
                disabled_at TEXT,
                reasons TEXT,
                review_after TEXT
            )
            """
        )
        await self.perf_conn.commit()
        log.info("Learning Layer DB Schema initialized.")
