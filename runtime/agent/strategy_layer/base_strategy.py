"""
BaseStrategy — Kontrak utama untuk semua strategi trading.

Mendefinisikan abstract class dan tipe pengembalian (`Signal`, `ExitSignal`)
yang wajib diikuti oleh seluruh implementasi strategi.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import UTC, datetime

from runtime.agent.core.config_schema import AgentConfig  # type: ignore
from runtime.agent.models import MarketRegime, MarketState, Position  # type: ignore


def utcnow() -> datetime:
    """Helper untuk mendapatkan UTC datetime saat ini."""
    return datetime.now(UTC)


@dataclass
class Signal:
    """Kontrak output dari generate_signal()"""

    # ── Identitas
    symbol: str
    side: str  # 'BUY' | 'SELL'
    strategy_id: str
    timestamp: datetime = field(default_factory=utcnow)

    # ── Order parameters
    signal_type: str = "market"  # 'market' | 'limit' | 'conditional'
    suggested_price: float = 0.0  # 0.0 = gunakan market price
    suggested_sl: float = 0.0  # WAJIB diisi — risk_layer butuh ini
    suggested_tp: float = 0.0  # opsional (0.0 = tidak ada TP fixed)

    # ── Confidence
    confidence: float = 0.0  # 0.0 - 1.0 (raw dari model)
    final_confidence: float = 0.0  # setelah multiplier & LLM filter

    # ── Context
    reasoning: str = ""  # teks penjelasan untuk logging
    model_output: float = 0.0  # raw output model (return atau prob)
    regime: str = ""  # regime saat signal dibuat
    vol_regime: str = ""

    metadata: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.final_confidence == 0.0:
            self.final_confidence = self.confidence
        if self.suggested_sl <= 0.0:
            raise ValueError("suggested_sl WAJIB > 0")


@dataclass
class ExitSignal:
    """Sinyal keluar paksa dari strategi (di luar SL/TP/Risk)."""

    position_id: str
    reason: str  # 'regime_change' | 'model_flip' | 'time_exit' | 'manual'
    urgency: str  # 'normal' | 'urgent'
    exit_price: float = 0.0  # 0.0 = market price
    confidence: float = 1.0  # seberapa yakin harus keluar (0.0-1.0)
    metadata: dict = field(default_factory=dict)


class BaseStrategy(ABC):
    """
    Abstract contract untuk strategi.
    Tidak ada state I/O, murni evaluasi MarketState -> Signal.
    """

    def __init__(self, config: AgentConfig, model) -> None:  # type: ignore
        self.config = config
        self.model = model
        self.strategy_id = self.__class__.__name__

        # State minimal untuk rate-limiting & cooldown
        self._last_signal: dict[str, datetime] = {}
        self._signal_count: dict[str, int] = {}

    # ── Abstract Methods ───────────────────────────────────────────────

    @abstractmethod
    def generate_signal(self, state: MarketState) -> Signal | None:
        """
        Kembalikan Signal jika ada peluang, None jika tidak.
        Dipanggil SETIAP TICK. DILARANG ada akses eksternal di sini.
        """
        pass

    @abstractmethod
    def should_exit(self, position: Position, state: MarketState) -> ExitSignal | None:
        """
        Evaluasi apakah posisi harus ditutup secara manual oleh strategi.
        """
        pass

    @abstractmethod
    def get_signal_metadata(self, state: MarketState) -> dict:  # type: ignore
        """Return metadata tambahan untuk analisa."""
        pass

    # ── Implementasi Default ───────────────────────────────────────────

    def is_allowed_to_trade(self, state: MarketState) -> tuple[bool, str]:
        """
        Filter global sebelum compute / panggil model prediktor.
        Mengembalikan True jika sehat, False + Alasan if blocked.
        """
        # 1. Safety
        if not state.is_safe_to_trade:
            return False, "Market not safe to trade (anomaly detected)"

        # 2. Regime Filter
        # Note: getattr handles config keys smartly
        if getattr(self.config, "regime_filter", True):
            if state.regime == MarketRegime.HIGH_VOLATILITY:
                return False, "Blocked: regime is HIGH_VOLATILITY"

        # 3. Spread Filter
        max_spread = getattr(self.config, "max_spread_pct", 0.5)
        if state.spread_pct > max_spread:
            return False, f"Spread {state.spread_pct:.3f}% > max {max_spread:.3f}%"

        # 4. Cooldown Check
        last = self._last_signal.get(state.symbol)
        if last:
            elapsed = (utcnow() - last).total_seconds()

            # Kita parsing timeframe ke jumlah detik (e.g '1h' -> 3600)
            tf = state.timeframe
            tf_seconds = 3600  # Default fallback
            if tf.endswith("h"):
                tf_seconds = int(tf[:-1]) * 3600
            elif tf.endswith("m"):
                tf_seconds = int(tf[:-1]) * 60

            required = getattr(self.config, "signal_cooldown", 3) * tf_seconds
            if elapsed < required:
                return False, f"Cooldown: {elapsed:.0f}s < {required:.0f}s required"

        return True, ""

    def get_confidence_multiplier(self, state: MarketState) -> float:
        """
        Modifier confidence default (1.0).
        Silakan override pada subclass.
        """
        return 1.0
