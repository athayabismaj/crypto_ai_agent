"""
Satu-satu nya layer ORM yang berinteraksi dengan sqlite3 untuk record state.db
"""

import json
import os
import sqlite3
from datetime import datetime

from runtime.agent.trade_layer.trade import Trade, TradeStatus
from runtime.shared.utils import utcnow  # type: ignore


class TradeStore:
    def __init__(self, state_db_path: str, experience_db_path: str | None = None):
        self.state_db = state_db_path
        self.exp_db = experience_db_path or state_db_path.replace("state", "experience")
        self._init_db(self.state_db)
        self._init_db(self.exp_db)

    def _init_db(self, db_path: str) -> None:
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        with sqlite3.connect(db_path, isolation_level=None) as conn:
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS trades (
                    trade_id TEXT PRIMARY KEY,
                    client_order_id TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    side TEXT NOT NULL,
                    order_type TEXT NOT NULL,
                    strategy_id TEXT NOT NULL,
                    mode TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    status TEXT NOT NULL,
                    data JSON NOT NULL
                )
            """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_trade_status ON trades(status)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_trade_symbol ON trades(symbol)")

    def save(self, trade: Trade) -> None:
        trade.updated_at = utcnow()
        data_json = self._serialize(trade)

        with sqlite3.connect(self.state_db) as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                """
                INSERT OR REPLACE INTO trades 
                (trade_id, client_order_id, symbol, side, order_type, strategy_id, mode, created_at, status, data)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
                (
                    trade.trade_id,
                    trade.client_order_id,
                    trade.symbol,
                    trade.side,
                    trade.order_type,
                    trade.strategy_id,
                    trade.mode,
                    trade.created_at.isoformat(),
                    trade.status.value,
                    data_json,
                ),
            )

    def get(self, trade_id: str) -> Trade | None:
        with sqlite3.connect(self.state_db) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute("SELECT data FROM trades WHERE trade_id = ?", (trade_id,))
            row = cursor.fetchone()
            if row:
                return self._deserialize(row["data"])
        return None

    def get_by_order_id(self, client_order_id: str) -> Trade | None:
        with sqlite3.connect(self.state_db) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(
                "SELECT data FROM trades WHERE client_order_id = ?", (client_order_id,)
            )
            row = cursor.fetchone()
            if row:
                return self._deserialize(row["data"])
        return None

    def get_open_trades(self, symbol: str | None = None) -> list[Trade]:
        results = []
        with sqlite3.connect(self.state_db) as conn:
            conn.row_factory = sqlite3.Row
            query = "SELECT data FROM trades WHERE status IN (?, ?)"
            params: tuple = (TradeStatus.OPEN.value, TradeStatus.PARTIAL.value)

            if symbol:
                query += " AND symbol = ?"
                params = (*params, symbol)

            cursor = conn.execute(query, params)
            for row in cursor:
                results.append(self._deserialize(row["data"]))
        return results

    def get_all_active(self) -> list[Trade]:
        results = []
        active_statuses = (
            TradeStatus.PENDING.value,
            TradeStatus.SUBMITTED.value,
            TradeStatus.OPEN.value,
            TradeStatus.PARTIAL.value,
        )
        with sqlite3.connect(self.state_db) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(
                f'SELECT data FROM trades WHERE status IN ({",".join(["?"]*4)})', active_statuses
            )
            for row in cursor:
                results.append(self._deserialize(row["data"]))
        return results

    def update_status(self, trade_id: str, status: TradeStatus, **kwargs) -> None:
        trade = self.get(trade_id)
        if not trade:
            raise ValueError(f"Trade {trade_id} not found in state DB")

        trade.status = status
        trade.updated_at = utcnow()
        for k, v in kwargs.items():
            if hasattr(trade, k):
                setattr(trade, k, v)
        self.save(trade)

    def archive(self, trade: Trade) -> None:
        """Pindahkan ke experience_db."""
        trade.updated_at = utcnow()
        data_json = self._serialize(trade)

        # INSERT ke exp
        with sqlite3.connect(self.exp_db) as exp_conn:
            exp_conn.execute("BEGIN IMMEDIATE")
            exp_conn.execute(
                """
                INSERT OR REPLACE INTO trades 
                (trade_id, client_order_id, symbol, side, order_type, strategy_id, mode, created_at, status, data)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
                (
                    trade.trade_id,
                    trade.client_order_id,
                    trade.symbol,
                    trade.side,
                    trade.order_type,
                    trade.strategy_id,
                    trade.mode,
                    trade.created_at.isoformat(),
                    trade.status.value,
                    data_json,
                ),
            )

        # DELETE dari state
        with sqlite3.connect(self.state_db) as st_conn:
            st_conn.execute("DELETE FROM trades WHERE trade_id = ?", (trade.trade_id,))

    def count_open(self, symbol: str | None = None) -> int:
        return len(self.get_open_trades(symbol))

    def _serialize(self, trade: Trade) -> str:
        d = trade.__dict__.copy()

        # Convert enums to strings
        if "status" in d and hasattr(d["status"], "value"):
            d["status"] = d["status"].value

        # Convert datetimes to isoformat strings
        for k, v in d.items():
            if isinstance(v, datetime):
                d[k] = v.isoformat()
        return json.dumps(d)

    def _deserialize(self, raw: str) -> Trade:
        d = json.loads(raw)

        # Restore status enum
        if "status" in d:
            d["status"] = TradeStatus(d["status"])

        # Restore datetimes
        time_fields = ["created_at", "submitted_at", "opened_at", "closed_at", "updated_at"]
        for tf in time_fields:
            if d.get(tf):
                d[tf] = datetime.fromisoformat(d[tf])

        return Trade(**d)
