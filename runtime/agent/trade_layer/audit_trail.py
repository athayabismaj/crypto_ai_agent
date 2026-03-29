"""
Pencatatan setiap event terjadi pada trade.
Satu-satunya sumber kebenaran, tidak pernah diubah / didelete.
Dilindungi oleh SALT hash encryption.
"""

import hashlib
import json
import os
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime

from runtime.agent.trade_layer.trade import Trade
from runtime.shared.utils import utcnow  # type: ignore


@dataclass
class AuditEvent:
    event_id: str
    trade_id: str
    event_type: str
    timestamp: datetime
    actor: str
    equity_snapshot: float
    details: dict
    checksum: str


class AuditTrail:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self._salt = os.environ.get("AUDIT_SALT", "fallback_salt_never_use_in_prod")
        self._init_db()

    def _init_db(self) -> None:
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        with sqlite3.connect(self.db_path, isolation_level=None) as conn:
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS audit_events (
                    event_id TEXT PRIMARY KEY,
                    trade_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    equity_snapshot REAL NOT NULL,
                    details TEXT NOT NULL,
                    checksum TEXT NOT NULL
                )
            """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_trade ON audit_events(trade_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_type ON audit_events(event_type)")

    def _generate_checksum(
        self,
        event_id: str,
        trade_id: str,
        event_type: str,
        ts: str,
        actor: str,
        equity: float,
        details: str,
    ) -> str:
        payload = f"{event_id}|{trade_id}|{event_type}|{ts}|{actor}|{equity}|{details}|{self._salt}"
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def record(
        self,
        trade: Trade,
        event_type: str,
        actor: str,
        details: dict | None = None,
        equity: float = 0.0,
    ) -> AuditEvent:
        event_id = str(uuid.uuid4())
        ts = utcnow().isoformat()
        details_str = json.dumps(details or {})

        checksum = self._generate_checksum(
            event_id, trade.trade_id, event_type, ts, actor, equity, details_str
        )

        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO audit_events 
                (event_id, trade_id, event_type, timestamp, actor, equity_snapshot, details, checksum)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
                (event_id, trade.trade_id, event_type, ts, actor, equity, details_str, checksum),
            )

        return AuditEvent(
            event_id=event_id,
            trade_id=trade.trade_id,
            event_type=event_type,
            timestamp=datetime.fromisoformat(ts),
            actor=actor,
            equity_snapshot=equity,
            details=details or {},
            checksum=checksum,
        )

    def get_history(self, trade_id: str) -> list[AuditEvent]:
        events = []
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(
                "SELECT * FROM audit_events WHERE trade_id = ? ORDER BY timestamp ASC", (trade_id,)
            )
            for row in cursor:
                ts = datetime.fromisoformat(row["timestamp"])
                events.append(
                    AuditEvent(
                        event_id=row["event_id"],
                        trade_id=row["trade_id"],
                        event_type=row["event_type"],
                        timestamp=ts,
                        actor=row["actor"],
                        equity_snapshot=row["equity_snapshot"],
                        details=json.loads(row["details"]),
                        checksum=row["checksum"],
                    )
                )
        return events

    def verify_integrity(self, trade_id: str | None = None) -> bool:
        """Verifikasi anti tamper-proof"""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            query = "SELECT * FROM audit_events"
            params: tuple = ()
            if trade_id:
                query += " WHERE trade_id = ?"
                params = (trade_id,)

            cursor = conn.execute(query, params)
            for row in cursor:
                computed = self._generate_checksum(
                    row["event_id"],
                    row["trade_id"],
                    row["event_type"],
                    row["timestamp"],
                    row["actor"],
                    row["equity_snapshot"],
                    row["details"],
                )
                if computed != row["checksum"]:
                    return False
        return True
