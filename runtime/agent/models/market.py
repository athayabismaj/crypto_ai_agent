"""
Market data models — output dari data_layer dan intelligence_layer.

Models: Candle, Ticker, Balance, FundingRate, OrderbookLevel, Orderbook,
LotFilter, ExchangeInfo, ValidationResult, AnomalyReport, VolatilityMetrics,
RegimeResult, RateLimitStatus.
"""

from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, Field, computed_field  # type: ignore

from runtime.agent.models.enums import MarketRegime  # type: ignore

# ══════════════════════════════════════════════════════════════════════
#  Data Layer — raw market data
# ══════════════════════════════════════════════════════════════════════


class Candle(BaseModel):
    """Satu candle OHLCV."""

    model_config = ConfigDict(frozen=True)

    symbol: str
    timeframe: str
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    is_closed: bool = False
    source: str = "websocket"  # 'websocket' | 'rest'


class Ticker(BaseModel):
    """Best bid/ask real-time snapshot."""

    model_config = ConfigDict(frozen=True)

    symbol: str
    bid: float
    ask: float
    last: float
    mid: float
    spread: float
    spread_pct: float
    volume_24h: float = 0.0
    change_24h: float = 0.0
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))


class Balance(BaseModel):
    """Saldo aset di exchange."""

    model_config = ConfigDict(frozen=True)

    asset: str
    free: float
    locked: float
    total: float
    usd_value: float = 0.0
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))


class FundingRate(BaseModel):
    """Funding rate futures."""

    model_config = ConfigDict(frozen=True)

    symbol: str
    rate: float  # contoh: 0.0001 = 0.01%
    next_time: datetime
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))


# ══════════════════════════════════════════════════════════════════════
#  Orderbook
# ══════════════════════════════════════════════════════════════════════


class OrderbookLevel(BaseModel):
    """Satu level bid/ask."""

    model_config = ConfigDict(frozen=True)

    price: float
    quantity: float


class Orderbook(BaseModel):
    """Depth orderbook snapshot."""

    model_config = ConfigDict(frozen=True)

    symbol: str
    bids: list[OrderbookLevel]  # DESC (harga tertinggi dulu)
    asks: list[OrderbookLevel]  # ASC (harga terendah dulu)
    timestamp: datetime
    last_update_id: int = 0

    @computed_field  # type: ignore[prop-decorator, misc]
    @property
    def best_bid(self) -> float:
        return self.bids[0].price if self.bids else 0.0

    @computed_field  # type: ignore[prop-decorator, misc]
    @property
    def best_ask(self) -> float:
        return self.asks[0].price if self.asks else 0.0

    @computed_field  # type: ignore[prop-decorator, misc]
    @property
    def mid_price(self) -> float:
        if not self.bids or not self.asks:
            return 0.0
        return (self.best_bid + self.best_ask) / 2

    @computed_field  # type: ignore[prop-decorator, misc]
    @property
    def spread_pct(self) -> float:
        mid = self.mid_price
        if mid == 0:
            return 0.0
        return (self.best_ask - self.best_bid) / mid * 100


# ══════════════════════════════════════════════════════════════════════
#  Exchange Info & Filters
# ══════════════════════════════════════════════════════════════════════


class LotFilter(BaseModel):
    """Filter lot exchange untuk validasi qty dan price."""

    model_config = ConfigDict(frozen=True)

    symbol: str
    min_qty: float
    max_qty: float
    step_size: float
    min_notional: float
    tick_size: float


class ExchangeInfo(BaseModel):
    """Informasi perdagangan simbol."""

    model_config = ConfigDict(frozen=True)

    symbol: str
    status: str  # 'TRADING' | 'BREAK' | 'HALT'
    lot_filter: LotFilter
    is_spot: bool = True
    is_futures: bool = False


# ══════════════════════════════════════════════════════════════════════
#  Validation & Anomaly
# ══════════════════════════════════════════════════════════════════════


class ValidationResult(BaseModel):
    """Hasil validasi data masuk."""

    model_config = ConfigDict(frozen=True)

    valid: bool
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    data_type: str = ""  # 'candle' | 'ticker' | 'orderbook'


class AnomalyReport(BaseModel):
    """Laporan anomali market yang terdeteksi."""

    model_config = ConfigDict(frozen=True)

    anomaly_id: str
    severity: str  # 'LOW' | 'MEDIUM' | 'HIGH' | 'CRITICAL'
    symbol: str
    message: str
    value: float
    threshold: float
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    recommended: str = ""


# ══════════════════════════════════════════════════════════════════════
#  Intelligence Layer — computed analysis
# ══════════════════════════════════════════════════════════════════════


class VolatilityMetrics(BaseModel):
    """Metrik volatilitas dari volatility calculator."""

    model_config = ConfigDict(frozen=True)

    symbol: str
    timeframe: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    # ATR
    atr: float = 0.0
    atr_pct: float = 0.0
    atr_percentile: float = 0.0  # 0–100
    # Realized volatility
    realized_vol_20: float = 0.0
    realized_vol_5: float = 0.0
    # Volatility regime
    vol_regime: str = "normal"  # 'low' | 'normal' | 'high' | 'extreme'
    # Bollinger Bands
    bb_width: float = 0.0
    bb_percentile: float = 0.0


class RegimeResult(BaseModel):
    """Hasil klasifikasi market regime."""

    model_config = ConfigDict(frozen=True)

    regime: MarketRegime = MarketRegime.UNDEFINED
    confidence: float = 0.0  # 0.0–1.0
    adx: float = 0.0
    adx_trend: str = "flat"  # 'rising' | 'falling' | 'flat'
    ema50_distance: float = 0.0  # (close - ema50) / ema50 × 100
    is_stable: bool = False
    lookback_candles: int = 0


class RateLimitStatus(BaseModel):
    """Snapshot penggunaan rate limit API."""

    model_config = ConfigDict(frozen=True)

    weight_used: int = 0
    weight_limit: int = 1200
    weight_pct: float = 0.0
    orders_10s: int = 0
    orders_1d: int = 0
    seconds_to_reset: float = 0.0
    is_near_limit: bool = False
