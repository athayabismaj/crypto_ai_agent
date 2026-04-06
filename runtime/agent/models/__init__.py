"""
runtime.agent.models — Central Pydantic V2 data models.

Import semua model dari sini:
    from runtime.agent.models import Trade, Signal, RiskResult, ...
"""

# ── Enums ──────────────────────────────────────────────────────────
# ── Audit ─────────────────────────────────────────────────────────
from runtime.agent.models.audit import AuditEvent, RecoveryReport  # type: ignore
from runtime.agent.models.enums import (  # type: ignore
    AlertSeverity,
    CircuitState,
    ComponentStatus,
    ConnStatus,
    ExitAction,
    HeartbeatStatus,
    MarketRegime,
    OrderSide,
    OrderType,
    RiskVerdict,
    TimeInForce,
    TradeStatus,
    TradingMode,
)

# ── Exit layer ────────────────────────────────────────────────────
from runtime.agent.models.exit import (  # type: ignore
    HOLD_DECISION,
    ExitDecision,
    TPState,
    TrailingState,
)

# ── Market data ────────────────────────────────────────────────────
from runtime.agent.models.market import (  # type: ignore
    AnomalyReport,
    Balance,
    Candle,
    ExchangeInfo,
    FundingRate,
    LatencyStatus,
    LotFilter,
    MarketState,
    Orderbook,
    OrderbookLevel,
    PortfolioState,
    Position,
    RateLimitStatus,
    RegimeResult,
    Ticker,
    ValidationResult,
    VolatilityMetrics,
)

# ── Monitoring & Sync ─────────────────────────────────────────────
from runtime.agent.models.monitoring import (  # type: ignore
    Alert,
    BalanceSyncReport,
    ComponentHealth,
    Discrepancy,
    HealthReport,
    OrderSyncReport,
    PositionSyncReport,
    ReconciliationReport,
)

# ── Risk layer ────────────────────────────────────────────────────
from runtime.agent.models.risk import RiskResult, TradeRequest  # type: ignore

# ── Strategy signals ──────────────────────────────────────────────
from runtime.agent.models.signal import ExitSignal, Signal  # type: ignore

# ── Trade & execution ─────────────────────────────────────────────
from runtime.agent.models.trade import (  # type: ignore
    OrderRequest,
    OrderResponse,
    Trade,
)

__all__ = [
    # Enums
    "MarketRegime",
    "TradeStatus",
    "RiskVerdict",
    "CircuitState",
    "OrderSide",
    "OrderType",
    "TimeInForce",
    "TradingMode",
    "ExitAction",
    "ComponentStatus",
    "HeartbeatStatus",
    "ConnStatus",
    "AlertSeverity",
    # Market
    "Candle",
    "Ticker",
    "Balance",
    "FundingRate",
    "OrderbookLevel",
    "Orderbook",
    "LotFilter",
    "ExchangeInfo",
    "ValidationResult",
    "AnomalyReport",
    "VolatilityMetrics",
    "RegimeResult",
    "RateLimitStatus",
    "LatencyStatus",
    "Position",
    "PortfolioState",
    "MarketState",
    # Signal
    "Signal",
    "ExitSignal",
    # Risk
    "TradeRequest",
    "RiskResult",
    # Exit
    "ExitDecision",
    "HOLD_DECISION",
    "TrailingState",
    "TPState",
    # Monitoring & Sync
    "ComponentHealth",
    "HealthReport",
    "Alert",
    "BalanceSyncReport",
    "OrderSyncReport",
    "PositionSyncReport",
    "Discrepancy",
    "ReconciliationReport",
    # Trade
    "Trade",
    "OrderRequest",
    "OrderResponse",
    # Audit
    "AuditEvent",
    "RecoveryReport",
]
