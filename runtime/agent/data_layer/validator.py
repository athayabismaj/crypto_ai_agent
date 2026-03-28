"""
DataValidator — Gate pertama validasi data masuk.

Memastikan setiap Candle, Ticker, dan Orderbook yang diproses
memenuhi syarat minimum integritas sebelum masuk ke intelligence layer.
"""

import logging
from collections import deque
from datetime import UTC, datetime

from runtime.agent.models import (  # type: ignore
    Candle,
    Orderbook,
    Ticker,
    ValidationResult,
)

logger = logging.getLogger(__name__)


class DataValidator:
    """
    Validasi integritas data market masuk.

    Setiap metode return ValidationResult:
    - valid=True  → data siap diproses
    - valid=False → data harus di-reject
    - warnings    → data tetap valid tapi ada catatan
    """

    def __init__(
        self,
        max_gap_multiplier: int = 3,
        max_spread_pct: float = 2.0,
        max_ticker_age_s: float = 10.0,
        max_ob_age_s: float = 5.0,
        strict_mode: bool = False,
    ) -> None:
        self._max_gap_multiplier = max_gap_multiplier
        self._max_spread_pct = max_spread_pct
        self._max_ticker_age_s = max_ticker_age_s
        self._max_ob_age_s = max_ob_age_s
        self._strict_mode = strict_mode

        # Rolling stats (terakhir 1000 validasi)
        self._history: deque[tuple[str, bool]] = deque(maxlen=1000)

    # ── Candle validation ──────────────────────────────────────

    def validate_candle(
        self,
        candle: Candle,
        prev_candle: Candle | None = None,
        expected_symbol: str | None = None,
        expected_tf: str | None = None,
    ) -> ValidationResult:
        """Validasi satu candle berdasarkan OHLC logic dan integrity rules."""
        errors: list[str] = []
        warnings: list[str] = []

        # 1. Price positif
        if candle.open <= 0 or candle.high <= 0 or candle.low <= 0 or candle.close <= 0:
            errors.append("PRICE_NON_POSITIVE: semua harga harus > 0")

        # 2. Volume non-negatif
        if candle.volume < 0:
            errors.append("VOLUME_NEGATIVE: volume harus >= 0")

        # 3. OHLC logic
        if candle.high < max(candle.open, candle.close):
            errors.append(
                f"OHLC_LOGIC: high ({candle.high}) < max(open, close) "
                f"({max(candle.open, candle.close)})"
            )
        if candle.low > min(candle.open, candle.close):
            errors.append(
                f"OHLC_LOGIC: low ({candle.low}) > min(open, close) "
                f"({min(candle.open, candle.close)})"
            )

        # 4. Symbol match
        if expected_symbol and candle.symbol != expected_symbol:
            errors.append(f"SYMBOL_MISMATCH: expected {expected_symbol}, " f"got {candle.symbol}")

        # 5. Timeframe match
        if expected_tf and candle.timeframe != expected_tf:
            errors.append(f"TF_MISMATCH: expected {expected_tf}, " f"got {candle.timeframe}")

        # 6. Timestamp ordering (jika ada prev_candle)
        if prev_candle is not None:
            if candle.timestamp <= prev_candle.timestamp:
                errors.append("TIMESTAMP_ORDER: candle <= prev candle timestamp")

            # 7. Gap check
            gap_s = (candle.timestamp - prev_candle.timestamp).total_seconds()
            tf_s = self._tf_to_seconds(candle.timeframe)
            max_gap = tf_s * self._max_gap_multiplier
            if tf_s > 0 and gap_s > max_gap:
                warnings.append(
                    f"TIMESTAMP_GAP: gap {gap_s:.0f}s > "
                    f"max {max_gap:.0f}s ({self._max_gap_multiplier}× tf)"
                )

        valid = len(errors) == 0
        if self._strict_mode and warnings:
            valid = False

        self._history.append(("candle", valid))
        if not valid:
            logger.warning("Candle rejected: %s %s", candle.symbol, errors)

        return ValidationResult(
            valid=valid,
            errors=errors,
            warnings=warnings,
            data_type="candle",
        )

    # ── Ticker validation ──────────────────────────────────────

    def validate_ticker(self, ticker: Ticker) -> ValidationResult:
        """Validasi ticker — bid/ask positif, spread wajar, freshness."""
        errors: list[str] = []
        warnings: list[str] = []

        # Bid/ask positif
        if ticker.bid <= 0 or ticker.ask <= 0:
            errors.append("PRICE_NON_POSITIVE: bid dan ask harus > 0")

        # Bid < ask (normal book)
        if ticker.bid > 0 and ticker.ask > 0 and ticker.bid >= ticker.ask:
            errors.append(f"CROSSED_BOOK: bid ({ticker.bid}) >= ask ({ticker.ask})")

        # Spread wajar
        if ticker.spread_pct > self._max_spread_pct:
            warnings.append(
                f"WIDE_SPREAD: {ticker.spread_pct:.2f}% > " f"max {self._max_spread_pct}%"
            )

        # Freshness
        age_s = (datetime.now(UTC) - ticker.timestamp).total_seconds()
        if age_s > self._max_ticker_age_s:
            errors.append(f"STALE_TICKER: age {age_s:.1f}s > " f"max {self._max_ticker_age_s}s")

        valid = len(errors) == 0
        if self._strict_mode and warnings:
            valid = False

        self._history.append(("ticker", valid))
        if not valid:
            logger.warning("Ticker rejected: %s %s", ticker.symbol, errors)

        return ValidationResult(
            valid=valid,
            errors=errors,
            warnings=warnings,
            data_type="ticker",
        )

    # ── Orderbook validation ───────────────────────────────────

    def validate_orderbook(self, ob: Orderbook) -> ValidationResult:
        """Validasi orderbook — non-empty, bid < ask, freshness."""
        errors: list[str] = []
        warnings: list[str] = []

        # Non-empty
        if not ob.bids and not ob.asks:
            errors.append("EMPTY_BOOK: bids dan asks kosong")

        # Bid < ask
        if ob.bids and ob.asks:
            if ob.best_bid >= ob.best_ask:
                errors.append(
                    f"CROSSED_BOOK: best_bid ({ob.best_bid}) >= " f"best_ask ({ob.best_ask})"
                )

        # Freshness
        age_s = (datetime.now(UTC) - ob.timestamp).total_seconds()
        if age_s > self._max_ob_age_s:
            warnings.append(f"STALE_ORDERBOOK: age {age_s:.1f}s > " f"max {self._max_ob_age_s}s")

        valid = len(errors) == 0
        if self._strict_mode and warnings:
            valid = False

        self._history.append(("orderbook", valid))
        if not valid:
            logger.warning("Orderbook rejected: %s %s", ob.symbol, errors)

        return ValidationResult(
            valid=valid,
            errors=errors,
            warnings=warnings,
            data_type="orderbook",
        )

    # ── Stats ──────────────────────────────────────────────────

    def get_validation_stats(self) -> dict[str, float]:
        """Rolling reject rate per data type (last 1000)."""
        stats: dict[str, list[bool]] = {}
        for dtype, valid in self._history:
            stats.setdefault(dtype, []).append(valid)

        result: dict[str, float] = {}
        for dtype, values in stats.items():
            total = len(values)
            rejected = sum(1 for v in values if not v)
            result[f"{dtype}_reject_rate_pct"] = (
                round(rejected / total * 100, 1) if total > 0 else 0.0  # type: ignore[call-overload]
            )
            result[f"{dtype}_total"] = float(total)
        return result

    # ── Internal ───────────────────────────────────────────────

    @staticmethod
    def _tf_to_seconds(tf: str) -> float:
        """Convert timeframe string ke detik. E.g. '1h' → 3600."""
        multipliers = {"m": 60, "h": 3600, "d": 86400, "w": 604800}
        if not tf:
            return 0
        unit = tf[-1]
        try:
            value = int(tf[:-1])  # type: ignore[index]
        except ValueError:
            return 0
        return float(value * multipliers.get(unit, 0))
