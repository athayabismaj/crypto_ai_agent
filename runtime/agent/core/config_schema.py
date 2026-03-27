from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator  # type: ignore


class AgentConfig(BaseModel):
    # ── MODE ────────────────────────────────────────────────
    mode: Literal["paper", "shadow", "live"] = "paper"
    initial_equity: float = Field(10_000.0, gt=0)

    # ── TRADING UNIVERSE ────────────────────────────────────
    symbols: list[str] = ["BTCUSDT"]
    timeframe: str = "1h"
    exchange: str = "binance"
    market_type: str = "spot"  # spot | futures

    # ── RISK ────────────────────────────────────────────────
    risk_per_trade_pct: float = Field(0.01, ge=0.001, le=0.05)
    max_daily_loss_pct: float = Field(0.05, ge=0.01, le=0.15)
    max_drawdown_pct: float = Field(0.10, ge=0.05, le=0.30)
    max_consecutive_loss: int = Field(5, ge=3, le=15)
    max_open_positions: int = Field(5, ge=1, le=20)
    global_max_leverage: int = Field(1, ge=1, le=20)

    # ── SIZING ──────────────────────────────────────────────
    sizing_method: str = "fixed_fractional"
    max_position_pct: float = Field(0.10, ge=0.01, le=0.30)
    min_notional_usd: float = Field(10.0, ge=5.0)

    # ── EXECUTION ───────────────────────────────────────────
    execution_delay_ms: int = Field(100, ge=0, le=5000)
    order_timeout_s: int = Field(30, ge=5, le=300)
    max_order_retry: int = Field(3, ge=1, le=10)
    slippage_tolerance: float = Field(0.005, ge=0, le=0.02)

    # ── STRATEGY ────────────────────────────────────────────
    strategy_id: str = "spot_strategy_v1"
    min_signal_confidence: float = Field(0.60, ge=0.3, le=1.0)
    signal_cooldown: int = Field(3, ge=1, le=20)

    # ── EXIT ────────────────────────────────────────────────
    default_sl_pct: float = Field(0.02, ge=0.005, le=0.10)
    default_tp_ratio: float = Field(2.0, ge=1.0, le=10.0)
    trailing_method: str = "atr"  # atr | percentage | chandelier
    trail_atr_mult: float = Field(2.0, ge=1.0, le=5.0)
    breakeven_trigger_r: float = Field(1.0, ge=0.5, le=3.0)
    max_hold_candles: int = Field(48, ge=10, le=200)

    # ── SYNC ────────────────────────────────────────────────
    balance_sync_interval_s: int = Field(60, ge=30, le=300)
    order_sync_interval_s: int = Field(30, ge=15, le=120)
    position_sync_interval_s: int = Field(60, ge=30, le=300)

    # ── LLM (opsional) ──────────────────────────────────────
    llm_enabled: bool = False
    llm_model: str = "claude-haiku-4-5-20251001"
    llm_daily_budget_usd: float = Field(1.0, ge=0.0, le=50.0)

    # ── NOTIFICATION ────────────────────────────────────────
    notify_trade_open: bool = True
    notify_trade_close: bool = True
    notify_daily_summary: bool = True
    notify_circuit_break: bool = True

    # ── VALIDATORS ──────────────────────────────────────────
    @field_validator("timeframe")
    @classmethod
    def validate_tf(cls, v: str) -> str:
        valid = ["1m", "5m", "15m", "30m", "1h", "4h", "1d"]
        if v not in valid:
            raise ValueError(f"timeframe harus salah satu dari {valid}")
        return v

    @field_validator("market_type")
    @classmethod
    def validate_market(cls, v: str) -> str:
        if v not in ("spot", "futures"):
            raise ValueError("market_type harus spot atau futures")
        return v

    @field_validator("global_max_leverage")
    @classmethod
    def leverage_spot_check(cls, v: int) -> int:
        # NOTE: cross-field validation in Pydantic V2 uses @model_validator.
        # This validator only checks the range; spot-leverage logic is enforced
        # separately when the full config is built.
        return v

    @model_validator(mode="after")
    def check_spot_leverage(self) -> "AgentConfig":
        """Spot trading tidak mendukung leverage > 1."""
        if self.market_type == "spot" and self.global_max_leverage > 1:
            raise ValueError("Spot trading tidak mendukung leverage > 1")
        return self
