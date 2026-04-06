"""
Risk Layer — Gerbang Tunggal Sebelum Order Ke Exchange
========================================================
Semua public classes di-export dari sini untuk akses yang clean.

Contoh penggunaan:
    from runtime.agent.risk_layer import RiskManager, RiskResult

Flow:
    Signal → SignalProcessor (5-gate) → RiskManager.evaluate() → RiskResult
    → Trade Layer (jika APPROVED/WARNED)
"""

from runtime.agent.risk_layer.circuit_breaker import CircuitBreaker
from runtime.agent.risk_layer.exposure_control import ExposureControl
from runtime.agent.risk_layer.leverage_control import LeverageControl
from runtime.agent.risk_layer.position_size import PositionSizer
from runtime.agent.risk_layer.pre_trade_check import PreTradeCheck, generate_order_id
from runtime.agent.risk_layer.risk_manager import RiskManager
from runtime.agent.risk_layer.stoploss import StopLossManager

__all__ = [
    "RiskManager",
    "PreTradeCheck",
    "CircuitBreaker",
    "LeverageControl",
    "ExposureControl",
    "PositionSizer",
    "StopLossManager",
    "generate_order_id",
]
