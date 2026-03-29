"""
Definisi Data Model utama untuk Trade Layer.
Semua trade direpresentasikan oleh objek ini.
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

from runtime.shared.utils import utcnow  # type: ignore


class TradeStatus(str, Enum):
    PENDING = "pending"  # dibuat, belum dikirim ke exchange
    SUBMITTED = "submitted"  # dikirim, menunggu konfirmasi
    OPEN = "open"  # terkonfirmasi, posisi aktif
    CLOSED = "closed"  # posisi ditutup
    CANCELLED = "cancelled"  # dibatalkan sebelum fill
    FAILED = "failed"  # gagal total setelah retry
    PARTIAL = "partial"  # partial fill, sisanya pending


@dataclass
class Trade:
    # ── IMMUTABLE setelah dibuat ─────────────────────────────
    trade_id: str  # UUID v4
    client_order_id: str  # format unik {strategy}_{symbol}_{ts_ms}
    symbol: str
    side: str  # 'BUY' | 'SELL'
    order_type: str  # 'market' | 'limit' | 'stop_market'
    strategy_id: str
    mode: str  # 'paper' | 'shadow' | 'live'
    created_at: datetime

    # ── MUTABLE --- update seiring siklus hidup ────────────────
    exchange_order_id: str = ""
    status: TradeStatus = TradeStatus.PENDING

    # Quantity & Price
    requested_qty: float = 0.0
    filled_qty: float = 0.0
    avg_fill_price: float = 0.0
    limit_price: float = 0.0  # 0.0 = market

    # Risk params
    sl_price: float = 0.0
    tp_price: float = 0.0
    risk_amount_usd: float = 0.0
    leverage: int = 1
    is_futures: bool = False

    # PnL (diisi saat close)
    exit_price: float = 0.0
    pnl_usd: float = 0.0
    pnl_pct: float = 0.0
    commission_usd: float = 0.0
    exit_reason: str = ""

    # Timestamps
    submitted_at: datetime | None = None
    opened_at: datetime | None = None
    closed_at: datetime | None = None
    updated_at: datetime | None = None

    # Metadata
    signal_confidence: float = 0.0
    regime_at_entry: str = ""
    vol_regime_entry: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    # ── Computed properties ──────────────────────────────────
    @property
    def notional(self) -> float:
        return self.filled_qty * self.avg_fill_price

    @property
    def is_open(self) -> bool:
        return self.status in (TradeStatus.OPEN, TradeStatus.PARTIAL)

    @property
    def hold_duration_s(self) -> float:
        if not self.opened_at:
            return 0.0
        end = self.closed_at or utcnow()
        return (end - self.opened_at).total_seconds()

    def to_order_request(self) -> "Any":
        """Konversi ke OrderRequest untuk execution layer."""
        from runtime.agent.execution_layer.exchange import OrderRequest

        return OrderRequest(
            symbol=self.symbol,
            side=self.side,
            order_type=self.order_type.upper(),
            quantity=self.requested_qty,
            price=self.limit_price,
            stop_price=0.0,
            client_order_id=self.client_order_id,
            is_futures=self.is_futures,
            reduce_only=False,
            leverage=self.leverage,
        )
