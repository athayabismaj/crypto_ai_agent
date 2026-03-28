"""
Trade & execution layer models — Trade, OrderRequest, OrderResponse.

Trade = lifecycle model (mutable, status berubah seiring waktu).
OrderRequest/OrderResponse = kontrak antara trade_layer dan exchange.
"""

from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, Field, computed_field  # type: ignore

from runtime.agent.models.enums import (  # type: ignore
    OrderSide,
    OrderType,
    TimeInForce,
    TradeStatus,
)


class Trade(BaseModel):
    """
    Full lifecycle model sebuah trade.

    MUTABLE — status, filled_qty, pnl berubah seiring waktu.
    Field-field ini di-update oleh sync layer dan trade manager.
    """

    # Tidak frozen=True karena Trade mutable
    model_config = ConfigDict(validate_assignment=True)

    # ── Identitas ──────────────────────────────────────────
    trade_id: str
    client_order_id: str = ""
    exchange_order_id: str = ""
    strategy_id: str = ""
    symbol: str = ""

    # ── Order detail ───────────────────────────────────────
    side: str = "BUY"
    order_type: str = "MARKET"
    quantity: float = 0.0
    filled_qty: float = 0.0
    entry_price: float = 0.0
    avg_fill_price: float = 0.0

    # ── Risk levels ────────────────────────────────────────
    stop_loss: float = 0.0
    take_profit: float = 0.0

    # ── Status ─────────────────────────────────────────────
    status: TradeStatus = TradeStatus.PENDING
    is_futures: bool = False
    leverage: int = 1
    reduce_only: bool = False

    # ── Outcomes ───────────────────────────────────────────
    exit_price: float = 0.0
    pnl: float = 0.0
    pnl_pct: float = 0.0
    commission: float = 0.0
    commission_asset: str = ""

    # ── Timestamps ─────────────────────────────────────────
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    opened_at: datetime | None = None
    closed_at: datetime | None = None

    # ── Context ────────────────────────────────────────────
    regime_at_entry: str = ""
    confidence: float = 0.0
    risk_amount_usd: float = 0.0
    metadata: dict = Field(default_factory=dict)  # type: ignore[assignment]

    @computed_field  # type: ignore[prop-decorator, misc]
    @property
    def notional(self) -> float:
        """Nilai nominal = qty × avg_fill_price."""
        return self.filled_qty * self.avg_fill_price

    @computed_field  # type: ignore[prop-decorator, misc]
    @property
    def is_open(self) -> bool:
        return self.status in (TradeStatus.OPEN, TradeStatus.PARTIAL)

    @computed_field  # type: ignore[prop-decorator, misc]
    @property
    def hold_duration_s(self) -> float:
        """Durasi hold dalam detik. 0 jika belum open."""
        if self.opened_at is None:
            return 0.0
        if self.closed_at is not None:
            end = self.closed_at
        else:
            end = datetime.now(UTC)
        return (end - self.opened_at).total_seconds()  # type: ignore[operator]


class OrderRequest(BaseModel):
    """
    Request ke exchange — sudah siap dikirim.

    Quantity sudah di-round ke step_size. Price sudah di-round ke tick_size.
    """

    model_config = ConfigDict(frozen=True)

    symbol: str
    side: OrderSide
    order_type: OrderType = OrderType.MARKET
    quantity: float  # sudah di-round ke step_size
    price: float = 0.0  # 0.0 untuk market order
    stop_price: float = 0.0  # untuk STOP_MARKET
    client_order_id: str = ""

    is_futures: bool = False
    reduce_only: bool = False
    time_in_force: TimeInForce = TimeInForce.GTC
    leverage: int = 1


class OrderResponse(BaseModel):
    """Response dari exchange setelah place_order."""

    model_config = ConfigDict(frozen=True)

    exchange_order_id: str
    client_order_id: str = ""
    status: str = ""  # NEW|FILLED|PARTIALLY_FILLED|CANCELLED|REJECTED

    filled_qty: float = 0.0
    avg_price: float = 0.0
    commission: float = 0.0
    commission_asset: str = ""
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    raw_response: dict = Field(default_factory=dict)  # type: ignore[assignment]

    @computed_field  # type: ignore[prop-decorator, misc]
    @property
    def is_filled(self) -> bool:
        return self.status in ("FILLED", "PARTIALLY_FILLED")

    @computed_field  # type: ignore[prop-decorator, misc]
    @property
    def is_rejected(self) -> bool:
        return self.status == "REJECTED"
