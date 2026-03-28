"""
Shared enums used across all layers of the crypto AI agent.

Setiap enum menggunakan (str, Enum) untuk serialisasi JSON otomatis.
"""

from enum import Enum


class MarketRegime(str, Enum):
    """Klasifikasi kondisi market dari intelligence_layer."""

    STRONG_TREND_UP = "strong_trend_up"
    WEAK_TREND_UP = "weak_trend_up"
    SIDEWAYS = "sideways"
    WEAK_TREND_DOWN = "weak_trend_down"
    STRONG_TREND_DOWN = "strong_trend_down"
    HIGH_VOLATILITY = "high_volatility"  # override semua regime lain
    UNDEFINED = "undefined"  # data tidak cukup (< MIN_HISTORY)


class TradeStatus(str, Enum):
    """Lifecycle status sebuah trade."""

    PENDING = "pending"  # dibuat, belum dikirim ke exchange
    SUBMITTED = "submitted"  # dikirim, menunggu konfirmasi
    OPEN = "open"  # terkonfirmasi, posisi aktif
    CLOSED = "closed"  # posisi ditutup
    CANCELLED = "cancelled"  # dibatalkan sebelum fill
    FAILED = "failed"  # gagal total setelah retry
    PARTIAL = "partial"  # partial fill, sisanya pending


class RiskVerdict(str, Enum):
    """Hasil evaluasi risk layer."""

    APPROVED = "approved"
    WARNED = "warned"  # boleh lanjut tapi ada catatan
    BLOCKED = "blocked"  # order tidak boleh dikirim


class CircuitState(str, Enum):
    """State machine circuit breaker."""

    NORMAL = "normal"
    WARNED = "warned"  # mendekati batas, trading masih boleh
    HALTED = "halted"  # trading STOP sampai kondisi membaik


class OrderSide(str, Enum):
    """Sisi order."""

    BUY = "BUY"
    SELL = "SELL"


class OrderType(str, Enum):
    """Tipe order yang didukung."""

    MARKET = "MARKET"
    LIMIT = "LIMIT"
    STOP_MARKET = "STOP_MARKET"


class TimeInForce(str, Enum):
    """Time-in-force untuk limit orders."""

    GTC = "GTC"  # Good Till Cancel
    IOC = "IOC"  # Immediate Or Cancel
    FOK = "FOK"  # Fill Or Kill


class TradingMode(str, Enum):
    """Mode operasi agent."""

    PAPER = "paper"
    SHADOW = "shadow"
    LIVE = "live"
