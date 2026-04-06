"""
trade_history.py — Database Audit (Immutable)
Menyimpan semua trade secara permanen untuk keperluan compliance, pajak, dan audit.
DILARANG KERAS update apalagi delete (kecuali lewat direct SQL query manual).
"""
from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

log = logging.getLogger(__name__)


AUDIT_DB_PATH = Path("audit/trade_history.db")
AUDIT_DB_PATH.parent.mkdir(parents=True, exist_ok=True)


@dataclass
class AuditTrade:
    trade_id: str
    client_order_id: str
    exchange_order_id: str
    symbol: str
    side: str
    entry_price: float
    exit_price: float | None
    qty: float
    pnl: float | None
    commission: float | None
    entry_time: str      # ISO format
    exit_time: str | None
    strategy: str


def _init_db(db_path: Path):
    """Inisialisasi tabel jika belum ada."""
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS all_trades (
            trade_id TEXT PRIMARY KEY,
            client_order_id TEXT UNIQUE NOT NULL,
            exchange_order_id TEXT,
            symbol TEXT NOT NULL,
            side TEXT NOT NULL,
            entry_price REAL NOT NULL,
            exit_price REAL,
            qty REAL NOT NULL,
            pnl REAL,
            commission REAL,
            entry_time TEXT NOT NULL,
            exit_time TEXT,
            strategy TEXT NOT NULL
        )
    """)
    # Membuat index agar pencarian per tahun/simbol/strategi cepat
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_sym_time ON all_trades(symbol, entry_time)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_strat ON all_trades(strategy)")
    
    conn.commit()
    conn.close()

# Inisialisasi DB saat modul di-load
_init_db(AUDIT_DB_PATH)


class AuditManager:
    """Manajer untuk menulis ke DB Audit. TIDAK MENYEDIAKAN fungsi update/delete."""
    
    def __init__(self, db_path: Path | str = AUDIT_DB_PATH):
        self.db_path = Path(db_path)

    def record_trade(self, trade: AuditTrade) -> bool:
        """Mencatat trade baru secara utuh setelah posisi ditutup."""
        try:
             conn = sqlite3.connect(self.db_path, timeout=5)
             cursor = conn.cursor()
             
             cursor.execute("""
                 INSERT INTO all_trades (
                     trade_id, client_order_id, exchange_order_id, symbol, side,
                     entry_price, exit_price, qty, pnl, commission,
                     entry_time, exit_time, strategy
                 ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
             """, (
                 trade.trade_id, trade.client_order_id, trade.exchange_order_id,
                 trade.symbol, trade.side, trade.entry_price, trade.exit_price,
                 trade.qty, trade.pnl, trade.commission, trade.entry_time,
                 trade.exit_time, trade.strategy
             ))
             
             conn.commit()
             conn.close()
             log.info(f"AUDIT LOG: Dicatat trade {trade.trade_id} ({trade.symbol} {trade.side}) secara permanen.")
             return True
             
        except sqlite3.IntegrityError as e:
             # Mungkin record duplikat, sangat krusial jangan panic, log sebagai warning saja
             log.warning(f"AUDIT LOG: Trade duplicate / Integrity constraint failed: {e}")
             return False
        except Exception as e:
             log.error(f"AUDIT LOG GAGAL disimpan: {e}")
             return False
             
    def get_trades_by_date(self, start_date_iso: str, end_date_iso: str) -> list[dict]:
        """Ambil data histori untuk akuntan/pajak."""
        try:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            
            cursor.execute("""
                 SELECT * FROM all_trades 
                 WHERE entry_time >= ? AND entry_time <= ?
                 ORDER BY entry_time ASC
            """, (start_date_iso, end_date_iso))
            
            rows = cursor.fetchall()
            conn.close()
            return [dict(r) for r in rows]
            
        except Exception as e:
            log.error(f"Gagal mengambil audit record: {e}")
            return []
