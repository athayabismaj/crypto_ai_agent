"""
Model data untuk Risk Layer
Mendefinisikan input (TradeRequest) dan output (RiskResult) dari evaluasi risiko.
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from runtime.agent.models.signal import Signal  # type: ignore

from runtime.agent.models.enums import RiskVerdict  # type: ignore


@dataclass
class RiskResult:
    verdict: RiskVerdict
    approved_quantity: float = 0.0  # qty final setelah sizing (0 jika BLOCKED)
    risk_amount_usd: float = 0.0  # estimasi max loss dalam USD
    sl_price: float = 0.0  # SL yang divalidasi / dikalkulasi
    tp_price: float = 0.0  # TP yang divalidasi (0 = tidak ada)
    reasons: list[str] = field(default_factory=list)  # alasan block (kosong jika APPROVED)
    warnings: list[str] = field(default_factory=list)  # peringatan (tetap bisa lanjut)
    sizing_method: str = ""  # metode ukuran yang digunakan
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))

    @property
    def is_approved(self) -> bool:
        return self.verdict != RiskVerdict.BLOCKED

    @property
    def has_warnings(self) -> bool:
        return len(self.warnings) > 0


@dataclass
class TradeRequest:
    symbol: str
    side: str  # 'BUY' | 'SELL'
    quantity: float  # qty yang diminta (bisa di-scale down)
    price: float = 0.0  # 0.0 = market order
    suggested_sl: float = 0.0  # dari Signal.suggested_sl
    suggested_tp: float = 0.0
    leverage: int = 1  # 1 untuk spot
    is_futures: bool = False
    strategy_id: str = ""
    client_order_id: str = ""  # diisi sebelum dikirim ke idempotency
    signal_confidence: float = 0.0

    @classmethod
    def from_signal(
        cls,
        signal: "Signal",  # Dicuplik dari model Strategy Layer
        quantity: float,
    ) -> "TradeRequest":
        return cls(
            symbol=signal.symbol,
            side=signal.side,
            quantity=quantity,
            price=signal.suggested_price,
            suggested_sl=signal.suggested_sl,
            suggested_tp=signal.suggested_tp,
            strategy_id=signal.strategy_id,
            signal_confidence=signal.final_confidence,
        )
