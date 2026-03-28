"""
Risk layer I/O models — TradeRequest dan RiskResult.

TradeRequest = input ke RiskManager (dari Signal).
RiskResult   = output dari evaluasi risk.
"""

from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, Field, computed_field  # type: ignore

from runtime.agent.models.enums import RiskVerdict  # type: ignore


class TradeRequest(BaseModel):
    """
    Input ke risk_layer untuk evaluasi.

    Biasanya dibangun via `from_signal()` oleh trade_layer
    sebelum dikirim ke risk_layer.evaluate().
    """

    model_config = ConfigDict(frozen=True)

    symbol: str
    side: str  # 'BUY' | 'SELL'
    strategy_id: str
    confidence: float
    signal_type: str = "market"

    # Price levels dari signal
    suggested_price: float = 0.0
    suggested_sl: float = 0.0
    suggested_tp: float = 0.0

    # Context saat ini
    current_price: float = 0.0
    atr: float = 0.0
    regime: str = ""
    vol_regime: str = ""
    available_equity: float = 0.0

    metadata: dict = Field(default_factory=dict)  # type: ignore[assignment]
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @classmethod
    def from_signal(
        cls,
        signal: "object",
        current_price: float = 0.0,
        atr: float = 0.0,
        available_equity: float = 0.0,
    ) -> "TradeRequest":
        """Buat TradeRequest dari objek Signal."""
        kwargs = {
            "symbol": getattr(signal, "symbol", ""),
            "side": getattr(signal, "side", ""),
            "strategy_id": getattr(signal, "strategy_id", ""),
            "confidence": getattr(signal, "final_confidence", 0.0),
            "signal_type": getattr(signal, "signal_type", "market"),
            "suggested_price": getattr(signal, "suggested_price", 0.0),
            "suggested_sl": getattr(signal, "suggested_sl", 0.0),
            "suggested_tp": getattr(signal, "suggested_tp", 0.0),
            "current_price": current_price,
            "atr": atr,
            "regime": getattr(signal, "regime", ""),
            "vol_regime": getattr(signal, "vol_regime", ""),
            "available_equity": available_equity,
        }
        return cls(**kwargs)  # type: ignore[arg-type]


class RiskResult(BaseModel):
    """
    Output dari risk_layer.evaluate().

    Berisi keputusan risk (approved/warned/blocked),
    quantity yang disetujui, dan alasan.
    """

    model_config = ConfigDict(frozen=True)

    verdict: RiskVerdict = RiskVerdict.BLOCKED
    approved_quantity: float = 0.0
    risk_amount_usd: float = 0.0
    risk_pct: float = 0.0  # % dari equity yang dipertaruhkan

    # Price levels (bisa di-override dari suggested)
    stop_loss: float = 0.0
    take_profit: float = 0.0

    # Sizing detail
    sizing_method: str = ""  # 'atr_pct' | 'fixed_usd' | 'kelly'
    position_value_usd: float = 0.0
    leverage_used: int = 1

    # Alasan
    reasons: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @computed_field  # type: ignore[prop-decorator, misc]
    @property
    def is_approved(self) -> bool:
        return self.verdict in (RiskVerdict.APPROVED, RiskVerdict.WARNED)

    @computed_field  # type: ignore[prop-decorator, misc]
    @property
    def has_warnings(self) -> bool:
        return len(self.warnings) > 0
