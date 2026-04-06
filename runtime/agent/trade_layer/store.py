"""
store.py — TradeStore menggunakan aiosqlite (DatabaseManager)
Kini asinkron untuk non-blocking I/O dan table-structured.
"""

import json
from datetime import datetime

import aiosqlite

from runtime.agent.trade_layer.db import DatabaseManager
from runtime.agent.trade_layer.trade import Trade, TradeStatus
from runtime.shared.utils import utcnow  # type: ignore


class TradeStore:
    def __init__(self, db_manager: DatabaseManager):
        self._db = db_manager

    async def save(self, trade: Trade) -> None:
        """Menyimpan atau update trade ke table active_trades."""
        trade.updated_at = utcnow()
        conn = self._db.connection

        query = """
            INSERT OR REPLACE INTO active_trades (
                trade_id, client_order_id, symbol, side, order_type, strategy_id, mode,
                created_at, exchange_order_id, status, requested_qty, filled_qty,
                avg_fill_price, limit_price, sl_price, tp_price, risk_amount_usd,
                leverage, is_futures, submitted_at, opened_at, updated_at,
                signal_confidence, regime_at_entry, vol_regime_entry, metadata
            ) VALUES (
                ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?,
                ?, ?, ?, ?
            )
        """
        
        await conn.execute(query, self._to_row(trade))
        await conn.commit()

    async def get(self, trade_id: str) -> Trade | None:
        conn = self._db.connection
        cursor = await conn.execute(
            "SELECT * FROM active_trades WHERE trade_id = ?", (trade_id,)
        )
        row = await cursor.fetchone()
        return self._from_row(row) if row else None

    async def get_by_order_id(self, client_order_id: str) -> Trade | None:
        conn = self._db.connection
        cursor = await conn.execute(
            "SELECT * FROM active_trades WHERE client_order_id = ?", (client_order_id,)
        )
        row = await cursor.fetchone()
        return self._from_row(row) if row else None

    async def get_open_trades(self, symbol: str | None = None) -> list[Trade]:
        conn = self._db.connection
        query = "SELECT * FROM active_trades WHERE status IN (?, ?)"
        params: list[str | int | float] = [TradeStatus.OPEN.value, TradeStatus.PARTIAL.value]

        if symbol:
            query += " AND symbol = ?"
            params.append(symbol)

        cursor = await conn.execute(query, params)
        rows = await cursor.fetchall()
        return [self._from_row(r) for r in rows if r]

    async def get_all_active(self) -> list[Trade]:
        conn = self._db.connection
        active_statuses = [
            TradeStatus.PENDING.value,
            TradeStatus.SUBMITTED.value,
            TradeStatus.OPEN.value,
            TradeStatus.PARTIAL.value,
        ]
        
        placeholders = ",".join(["?"] * len(active_statuses))
        query = f"SELECT * FROM active_trades WHERE status IN ({placeholders})"
        
        cursor = await conn.execute(query, active_statuses)
        rows = await cursor.fetchall()
        return [self._from_row(r) for r in rows if r]

    async def update_status(self, trade_id: str, status: TradeStatus, **kwargs) -> None:
        trade = await self.get(trade_id)
        if not trade:
            raise ValueError(f"Trade {trade_id} not found in state DB")

        trade.status = status
        trade.updated_at = utcnow()
        for k, v in kwargs.items():
            if hasattr(trade, k):
                setattr(trade, k, v)
        await self.save(trade)

    async def archive(self, trade: Trade) -> None:
        """Pindahkan dari active_trades ke archived_trades secara atomic."""
        trade.updated_at = utcnow()
        conn = self._db.connection

        # Start transaction manually? aiosqlite conn.execute is auto if isolation is not None,
        # but let's use BEGIN
        await conn.execute("BEGIN IMMEDIATE")
        try:
            # 1. Insert ke archived_trades
            query_insert = """
                INSERT OR REPLACE INTO archived_trades (
                    trade_id, client_order_id, symbol, side, order_type, strategy_id, mode,
                    created_at, exchange_order_id, status, requested_qty, filled_qty,
                    avg_fill_price, limit_price, sl_price, tp_price, risk_amount_usd,
                    leverage, is_futures, submitted_at, opened_at, updated_at,
                    signal_confidence, regime_at_entry, vol_regime_entry, metadata,
                    closed_at, exit_price, pnl_usd, pnl_pct, commission_usd, exit_reason
                ) VALUES (
                    ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?,
                    ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?
                )
            """
            row_data = self._to_row(trade)
            exit_data = (
                self._isoformat(trade.closed_at),
                trade.exit_price,
                trade.pnl_usd,
                trade.pnl_pct,
                trade.commission_usd,
                trade.exit_reason
            )
            await conn.execute(query_insert, row_data + exit_data)

            # 2. Delete dari active_trades
            await conn.execute("DELETE FROM active_trades WHERE trade_id = ?", (trade.trade_id,))
            
            await conn.commit()
        except Exception:
            await conn.rollback()
            raise

    async def count_open(self, symbol: str | None = None) -> int:
        trades = await self.get_open_trades(symbol)
        return len(trades)

    # --- Helpers ---
    
    def _isoformat(self, dt: datetime | None) -> str | None:
        return dt.isoformat() if dt else None

    def _to_row(self, trade: Trade) -> tuple:
        return (
            trade.trade_id,
            trade.client_order_id,
            trade.symbol,
            trade.side,
            trade.order_type,
            trade.strategy_id,
            trade.mode,
            self._isoformat(trade.created_at),
            trade.exchange_order_id,
            trade.status.value,
            trade.requested_qty,
            trade.filled_qty,
            trade.avg_fill_price,
            trade.limit_price,
            trade.sl_price,
            trade.tp_price,
            trade.risk_amount_usd,
            trade.leverage,
            trade.is_futures,
            self._isoformat(trade.submitted_at),
            self._isoformat(trade.opened_at),
            self._isoformat(trade.updated_at),
            trade.signal_confidence,
            trade.regime_at_entry,
            trade.vol_regime_entry,
            json.dumps(trade.metadata) if trade.metadata else "{}"
        )

    def _from_row(self, row: aiosqlite.Row | None) -> Trade | None:
        if not row:
            return None
        
        return Trade(
            trade_id=row["trade_id"],
            client_order_id=row["client_order_id"],
            symbol=row["symbol"],
            side=row["side"],
            order_type=row["order_type"],
            strategy_id=row["strategy_id"],
            mode=row["mode"],
            created_at=datetime.fromisoformat(row["created_at"]),
            exchange_order_id=row["exchange_order_id"] or "",
            status=TradeStatus(row["status"]),
            requested_qty=row["requested_qty"],
            filled_qty=row["filled_qty"],
            avg_fill_price=row["avg_fill_price"],
            limit_price=row["limit_price"],
            sl_price=row["sl_price"],
            tp_price=row["tp_price"],
            risk_amount_usd=row["risk_amount_usd"],
            leverage=row["leverage"],
            is_futures=bool(row["is_futures"]),
            submitted_at=datetime.fromisoformat(row["submitted_at"]) if row["submitted_at"] else None,
            opened_at=datetime.fromisoformat(row["opened_at"]) if row["opened_at"] else None,
            updated_at=datetime.fromisoformat(row["updated_at"]) if row["updated_at"] else None,
            signal_confidence=row["signal_confidence"],
            regime_at_entry=row["regime_at_entry"] or "",
            vol_regime_entry=row["vol_regime_entry"] or "",
            metadata=json.loads(row["metadata"]) if row["metadata"] else {}
        )
