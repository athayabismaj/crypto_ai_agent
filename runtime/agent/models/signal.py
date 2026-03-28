"""
Strategy layer output models — Signal dan ExitSignal.

Signal adalah kontrak antara strategy_layer dan risk_layer.
ExitSignal adalah kontrak antara strategy_layer dan exit_layer.
"""

from datetime import UTC, datetime

from pydantic import (  # type: ignore
    BaseModel,
    ConfigDict,
    Field,
    model_validator,
)


class Signal(BaseModel):
    """
    Sinyal trading dari strategy layer.

    Setelah dibuat, signal bersifat immutable — strategy layer
    tidak boleh mengubah signal yang sudah di-emit.
    """

    model_config = ConfigDict(frozen=True)

    # ── Identitas ──────────────────────────────────────────
    symbol: str
    side: str  # 'BUY' | 'SELL'
    strategy_id: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))

    # ── Order parameters ───────────────────────────────────
    signal_type: str = "market"  # 'market' | 'limit' | 'conditional'
    suggested_price: float = 0.0  # 0.0 = gunakan market price
    suggested_sl: float  # WAJIB > 0 — risk_layer butuh ini
    suggested_tp: float = 0.0  # 0.0 = tidak ada TP fixed

    # ── Confidence ─────────────────────────────────────────
    confidence: float  # 0.0–1.0 (raw dari model)
    final_confidence: float = 0.0  # setelah multiplier & LLM filter

    # ── Context ────────────────────────────────────────────
    reasoning: str = ""
    model_output: float = 0.0
    regime: str = ""
    vol_regime: str = ""
    metadata: dict = Field(default_factory=dict)  # type: ignore[assignment]

    @model_validator(mode="after")
    def _validate_signal(self) -> "Signal":
        if self.suggested_sl <= 0:
            raise ValueError("suggested_sl WAJIB > 0")
        # Auto-set final_confidence jika belum diisi
        if self.final_confidence == 0.0:
            object.__setattr__(self, "final_confidence", self.confidence)
        return self


class ExitSignal(BaseModel):
    """
    Sinyal keluar dari strategy layer.

    Digunakan ketika strategy mendeteksi kondisi exit
    di luar SL/TP yang dikelola exit_layer.
    """

    model_config = ConfigDict(frozen=True)

    position_id: str
    reason: str  # 'regime_change' | 'model_flip' | 'time_exit' | 'manual'
    urgency: str = "normal"  # 'normal' | 'urgent'
    exit_price: float = 0.0  # 0.0 = market price
    confidence: float = 0.0  # 0.0–1.0
    metadata: dict = Field(default_factory=dict)  # type: ignore[assignment]
