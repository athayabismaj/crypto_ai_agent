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


# ══════════════════════════════════════════════════════════════════════
#  Exit Layer
# ══════════════════════════════════════════════════════════════════════


class ExitAction(str, Enum):
    """Aksi yang diputuskan oleh ExitManager untuk satu posisi."""

    HOLD = "hold"  # jangan lakukan apa-apa
    EXIT_SL = "exit_sl"  # stop loss tercapai
    EXIT_TP = "exit_tp"  # take profit tercapai
    EXIT_TRAIL = "exit_trail"  # trailing stop tercapai
    EXIT_SIGNAL = "exit_signal"  # strategi minta keluar
    EXIT_TIMEOUT = "exit_timeout"  # posisi terlalu lama
    EXIT_FORCED = "exit_forced"  # circuit breaker / safe mode
    UPDATE_SL = "update_sl"  # geser SL (breakeven / trailing)
    PARTIAL_CLOSE = "partial_close"  # tutup sebagian posisi


# ══════════════════════════════════════════════════════════════════════
#  Monitoring Layer
# ══════════════════════════════════════════════════════════════════════


class ComponentStatus(str, Enum):
    """Status kesehatan satu komponen sistem."""

    HEALTHY = "healthy"
    DEGRADED = "degraded"  # masih jalan tapi tidak optimal
    UNHEALTHY = "unhealthy"  # perlu intervensi


class HeartbeatStatus(str, Enum):
    """Status heartbeat agent."""

    ALIVE = "alive"
    STALE = "stale"  # pulse ada tapi sudah lama
    DEAD = "dead"  # tidak ada pulse → trigger restart


class ConnStatus(str, Enum):
    """Status koneksi ke endpoint."""

    UP = "up"
    SLOW = "slow"  # response > 2000ms
    DOWN = "down"  # timeout atau error


class AlertSeverity(str, Enum):
    """Tingkat keparahan alert."""

    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"  # bangunkan operator
