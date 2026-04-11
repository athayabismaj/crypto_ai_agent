"""
Manager.py - Orchestrator dari Siklus Hidup Trade Layer.
Semua trade dibuka, dikelola, dan ditutup melalui modul ini.
Versi Asinkron penuh.
"""

import logging
import uuid
from typing import Any

from runtime.agent.trade_layer.audit_trail import AuditTrail
from runtime.agent.trade_layer.idempotency import IdempotencyManager, IdempotencyResult
from runtime.agent.trade_layer.store import TradeStore
from runtime.agent.trade_layer.trade import Trade, TradeStatus
from runtime.agent.trade_layer.trade_validator import TradeValidator
from runtime.shared.utils import utcnow  # type: ignore


class TradeValidationError(Exception):
    pass


class DuplicateOrderError(Exception):
    pass


log = logging.getLogger(__name__)


def generate_order_id(prefix: str, symbol: str) -> str:
    ts_ms = int(utcnow().timestamp() * 1000)
    return f"{prefix}_{symbol}_{ts_ms}"


class TradeManager:
    def __init__(
        self,
        config: Any,
        store: TradeStore,
        idempotency: IdempotencyManager,
        validator: TradeValidator,
        audit: AuditTrail,
        executor: Any,  # Eksekutor (Spot/Futures)
        portfolio_state: Any,  # state saldo/portofolio saat ini
        event_bus: Any,  # Pub/Sub system
    ):
        self.config = config
        self._store = store
        self._idempotency = idempotency
        self._validator = validator
        self._audit = audit
        self._executor = executor
        self._portfolio_state = portfolio_state
        self._event_bus = event_bus

    async def open(self, signal: Any, risk_result: Any) -> Trade:
        """
        Mengeksekusi persetujuan buka posisi, mencatat ke audit dan store,
        lalu mendelegasikan perintah API ke execution_layer.
        """
        # Step 1: Validasi
        ok, msg = self._validator.validate(signal, risk_result)
        if not ok:
            raise TradeValidationError(msg)

        # Step 2: Buat Trade Object
        client_order_id = generate_order_id(signal.strategy_id, signal.symbol)

        trade = Trade(
            trade_id=str(uuid.uuid4()),
            client_order_id=client_order_id,
            symbol=signal.symbol,
            side=signal.side,
            order_type=signal.signal_type,
            strategy_id=signal.strategy_id,
            mode=getattr(self.config, "mode", "paper"),
            created_at=utcnow(),
            requested_qty=risk_result.approved_quantity,
            limit_price=signal.suggested_price,
            sl_price=risk_result.sl_price,
            tp_price=risk_result.tp_price,
            risk_amount_usd=risk_result.risk_amount_usd,
            signal_confidence=signal.final_confidence,
            regime_at_entry=getattr(signal, "regime", "unknown"),
            vol_regime_entry=getattr(signal, "vol_regime", "unknown"),
        )

        # Step 3: Idempotency Check & Register (Mengatasi pencet double / bug loop)
        idem_result = await self._idempotency.check_or_register(trade.client_order_id)
        if idem_result == IdempotencyResult.DUPLICATE:
            raise DuplicateOrderError(f"Duplicate order: {trade.client_order_id}")

        # Step 4: Simpan ke State DB as PENDING
        trade.status = TradeStatus.PENDING
        await self._store.save(trade)

        # Audit Trail
        await self._audit.record(
            trade,
            "CREATED",
            actor="manager",
            details={
                "symbol": trade.symbol,
                "side": trade.side,
                "requested_qty": trade.requested_qty,
                "sl_price": trade.sl_price,
                "tp_price": trade.tp_price,
                "strategy_id": trade.strategy_id,
            },
        )

        # Step 5: Eksekusi (Paper mode vs Live mode)
        trade.status = TradeStatus.SUBMITTED
        trade.submitted_at = utcnow()
        await self._store.save(trade)

        try:
            await self._audit.record(
                trade,
                "SUBMITTED",
                actor="manager",
                details={
                    "client_order_id": trade.client_order_id,
                    "order_type": trade.order_type,
                },
            )

            if getattr(self.config, "mode", "paper") == "paper":
                response = self._simulate_fill(trade)
            else:
                order_req = trade.to_order_request()
                response = await self._executor.place_order(order_req)

            # Step 6: Confirmation Callback Update
            trade = await self.confirm(trade, response)

            # Hubungkan Event Bus agar module lain dapat merespons
            if hasattr(self._event_bus, "publish"):
                # Kita anggap event method adalah async di fase lanjut
                # atau synchronous depending on event_bus implementation.
                if __import__("inspect").iscoroutinefunction(getattr(self._event_bus, "publish")):
                    await getattr(self._event_bus, "publish")(
                        "TRADE_OPENED", trade, "trade_manager"
                    )
                else:
                    getattr(self._event_bus, "publish")("TRADE_OPENED", trade, "trade_manager")

            log.info(
                f"Trade opened [{trade.trade_id}] {trade.symbol} {trade.side} qty: {trade.filled_qty} at {trade.avg_fill_price}"
            )
            return trade

        except Exception as e:
            await self._idempotency.mark_failed(trade.client_order_id, str(e))
            await self._store.update_status(trade.trade_id, TradeStatus.FAILED, exit_reason=str(e))
            await self._audit.record(trade, "FAILED", actor="manager", details={"reason": str(e)})
            raise

    async def confirm(self, trade: Trade, response: Any) -> Trade:
        """Update trade setelah menerima konfirmasi FILLED dari Execution Layer"""
        trade.exchange_order_id = response.exchange_order_id
        trade.filled_qty = response.filled_qty
        trade.avg_fill_price = response.avg_price
        trade.commission_usd = getattr(response, "commission", 0.0)
        trade.status = TradeStatus.OPEN
        trade.opened_at = response.timestamp or utcnow()

        await self._store.save(trade)
        await self._idempotency.confirm(trade.client_order_id)

        await self._audit.record(
            trade,
            "FILLED",
            actor="manager",
            details={
                "exchange_order_id": response.exchange_order_id,
                "filled_qty": response.filled_qty,
                "avg_price": response.avg_price,
                "commission_usd": trade.commission_usd,
            },
        )
        return trade

    def _simulate_fill(self, trade: Trade) -> Any:
        # Simulasi harga paper account
        last_price = 50000.0  # dummy default
        if hasattr(self._portfolio_state, "get"):
            last_price = self._portfolio_state.get(
                f"last_price_{trade.symbol}", trade.limit_price or last_price
            )

        class MockResponse:
            exchange_order_id = f"paper_{trade.client_order_id}"
            client_order_id = trade.client_order_id
            status = "FILLED"
            filled_qty = trade.requested_qty
            avg_price = last_price
            commission = last_price * trade.requested_qty * 0.001
            commission_asset = "USDT"
            timestamp = utcnow()

        return MockResponse()

    async def close(self, trade: Trade, reason: str, pnl_usd: float = 0.0) -> Trade:
        """Menutup posisi."""
        trade.status = TradeStatus.CLOSED
        trade.closed_at = utcnow()
        trade.exit_reason = reason
        trade.pnl_usd = pnl_usd
        trade.exit_price = getattr(self._portfolio_state, "get", lambda k, d: 0.0)(
            f"last_price_{trade.symbol}", 0.0
        )

        if trade.notional > 0:
            trade.pnl_pct = (pnl_usd / trade.notional) * 100

        await self._store.archive(
            trade
        )  # Archiving langsung save sekaligus hapus dari active pke transaction

        await self._audit.record(
            trade,
            "CLOSED",
            actor="manager",
            details={
                "exit_price": trade.exit_price,
                "exit_reason": reason,
                "pnl_usd": trade.pnl_usd,
                "pnl_pct": trade.pnl_pct,
            },
        )

        # Publish Event
        if hasattr(self._event_bus, "publish"):
            if __import__("inspect").iscoroutinefunction(getattr(self._event_bus, "publish")):
                await getattr(self._event_bus, "publish")("TRADE_CLOSED", trade, "trade_manager")
            else:
                getattr(self._event_bus, "publish")("TRADE_CLOSED", trade, "trade_manager")

        return trade

    async def update_sl(self, trade: Trade, new_sl: float, actor: str = "manager") -> Trade:
        """Update Stop Loss level pada sistem."""
        old_sl = trade.sl_price
        trade.sl_price = new_sl
        await self._store.save(trade)
        await self._audit.record(
            trade, "SL_UPDATED", actor=actor, details={"old_sl": old_sl, "new_sl": new_sl}
        )
        log.info(f"Trade [{trade.trade_id}] SL updated: {old_sl} -> {new_sl}")
        return trade
