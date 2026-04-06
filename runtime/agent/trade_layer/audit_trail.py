"""
audit_trail.py — Pencatatan setiap event terjadi pada trade.
Satu-satunya sumber kebenaran, tidak pernah diubah / didelete.
Dilindungi oleh SALT hash encryption. (Async Version)
"""

import hashlib
import json
import os
import uuid
from dataclasses import dataclass
from datetime import datetime

import aiosqlite

from runtime.agent.trade_layer.db import DatabaseManager
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
    def __init__(self, db_manager: DatabaseManager):
        self._db = db_manager
        self._salt = os.environ.get("AUDIT_SALT", "fallback_salt_never_use_in_prod")

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

    async def record(
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

        conn = self._db.connection
        await conn.execute(
            """
            INSERT INTO audit_events 
            (event_id, trade_id, event_type, timestamp, actor, equity_snapshot, details, checksum)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (event_id, trade.trade_id, event_type, ts, actor, equity, details_str, checksum),
        )
        await conn.commit()

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

    async def get_history(self, trade_id: str) -> list[AuditEvent]:
        events = []
        conn = self._db.connection
        cursor = await conn.execute(
            "SELECT * FROM audit_events WHERE trade_id = ? ORDER BY timestamp ASC", (trade_id,)
        )
        rows = await cursor.fetchall()
        
        for row in rows:
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

    async def verify_integrity(self, trade_id: str | None = None) -> bool:
        """Verifikasi anti tamper-proof"""
        conn = self._db.connection
        query = "SELECT * FROM audit_events"
        params: list[str] = []
        if trade_id:
            query += " WHERE trade_id = ?"
            params.append(trade_id)

        cursor = await conn.execute(query, params)
        rows = await cursor.fetchall()
        
        for row in rows:
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
