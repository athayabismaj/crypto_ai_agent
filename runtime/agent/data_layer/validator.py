"""
Layer Data: Data Validator
Gerbang perlindungan data pertama. Memastikan Integritas Lilin Harga, Spread Wajar, 
dan Sinkronisasi Timestamp absolut sebelum dicerna oleh Mesin AI.
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from runtime.agent.core.config_schema import AgentConfig  # type: ignore
from runtime.agent.data_layer.market import Candle, Ticker

logger = logging.getLogger(__name__)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class ValidationResult:
    valid: bool
    errors: list[str]
    warnings: list[str]
    data_type: str


@dataclass
class ValidationStats:
    reject_rate_pct: dict[str, float]
    most_common_errors: list[str]
    total_processed: int
    total_rejected: int


class DataValidator:
    """Validator Garis Depan untuk melindungi strategi dari data rusak yang dikirim bursa."""

    def __init__(self, config: AgentConfig) -> None:
        self.config = config
        self.max_gap_multiplier = getattr(config, "ws_max_gap_multiplier", 3)
        self.max_spread_pct = getattr(config, "ws_max_spread_pct", 2.0)
        self.max_ticker_age = getattr(config, "ws_max_ticker_age_s", 10.0)
        self.max_ob_age = getattr(config, "ws_max_ob_age_s", 5.0)
        self.strict_mode = getattr(config, "ws_strict_mode", False)

        # Statistik
        self.processed = {"candle": 0, "ticker": 0, "orderbook": 0}
        self.rejected = {"candle": 0, "ticker": 0, "orderbook": 0}

    def _tf_to_seconds(self, tf: str) -> float:
        tf_map = {
            "1m": 60,
            "3m": 180,
            "5m": 300,
            "15m": 900,
            "30m": 1800,
            "1h": 3600,
            "2h": 7200,
            "4h": 14400,
            "6h": 21600,
            "8h": 28800,
            "12h": 43200,
            "1d": 86400,
        }
        return float(tf_map.get(tf, 60))

    def validate_candle(
        self, candle: Candle, prev_candle: Candle | None = None
    ) -> ValidationResult:
        self.processed["candle"] += 1
        errors = []
        warnings = []

        # 1. Price Logic Check
        if not (
            candle.high >= max(candle.open, candle.close)
            and candle.low <= min(candle.open, candle.close)
        ):
            errors.append("OHLC Logic Failed: High/Low tidak rasional terhadap Open/Close.")

        # 2. Positive Numbers
        if candle.open <= 0 or candle.high <= 0 or candle.low <= 0 or candle.close <= 0:
            errors.append("Price <= 0: Terdapat harga negatif/Nol.")

        if candle.volume < 0:
            errors.append("Volume Negatif.")

        # 3. Timestamp Series Check
        if prev_candle:
            gap_seconds = (candle.timestamp - prev_candle.timestamp).total_seconds()
            tf_seconds = self._tf_to_seconds(candle.timeframe)

            if gap_seconds < 0:
                errors.append(
                    f"Timestamp Mundur: (Prev: {prev_candle.timestamp}, Curr: {candle.timestamp})"
                )

            if gap_seconds > tf_seconds * self.max_gap_multiplier:
                warnings.append(
                    f"Timestamp Gap Terlalu Jauh: Gap sebesar {gap_seconds}s (TF {candle.timeframe})"
                )

        # 4. Status Keputusan
        is_invalid = len(errors) > 0 or (self.strict_mode and len(warnings) > 0)

        if is_invalid:
            self.rejected["candle"] += 1
            logger.error(f"[Validator] Lilin Ditolak! {errors} | {warnings}")
        elif len(warnings) > 0:
            logger.warning(f"[Validator] Lilin Peringatan: {warnings}")

        return ValidationResult(
            valid=not is_invalid, errors=errors, warnings=warnings, data_type="candle"
        )

    def validate_ticker(self, ticker: Ticker) -> ValidationResult:
        self.processed["ticker"] += 1
        errors = []
        warnings = []

        # 1. Spread Logic
        if ticker.bid <= 0 or ticker.ask <= 0:
            errors.append("Bid/Ask <= 0.")
        if ticker.bid >= ticker.ask:
            errors.append(f"CROSSED BOOK DETECTED: Bid ({ticker.bid}) >= Ask ({ticker.ask})")

        # 2. Spread Percentage Limit
        if ticker.spread_pct > self.max_spread_pct:
            warnings.append(
                f"Spread Ekstrem: Spread mencapai {ticker.spread_pct:.2f}% (max {self.max_spread_pct}%)"
            )

        # 3. Freshness
        age_seconds = (utcnow() - ticker.timestamp).total_seconds()
        if age_seconds > self.max_ticker_age:
            errors.append(
                f"Data Stale: Ticker telat {age_seconds:.1f}s (max {self.max_ticker_age}s)"
            )

        is_invalid = len(errors) > 0 or (self.strict_mode and len(warnings) > 0)
        if is_invalid:
            self.rejected["ticker"] += 1
            if "CROSSED" in str(errors):
                logger.critical(f"[Validator] KRITIS! {errors}")

        return ValidationResult(
            valid=not is_invalid, errors=errors, warnings=warnings, data_type="ticker"
        )

    def validate_orderbook(self, ob: Any) -> ValidationResult:
        self.processed["orderbook"] += 1
        errors: list[str] = []
        warnings: list[str] = []

        # MOCK ob validation
        is_invalid = False
        if is_invalid:
            self.rejected["orderbook"] += 1

        return ValidationResult(
            valid=not is_invalid, errors=errors, warnings=warnings, data_type="orderbook"
        )

    def get_validation_stats(self) -> ValidationStats:
        total_p = sum(self.processed.values())
        total_r = sum(self.rejected.values())

        pcts = {}
        for k in self.processed.keys():
            if self.processed[k] > 0:
                pcts[k] = round((self.rejected[k] / self.processed[k]) * 100, 2)
            else:
                pcts[k] = 0.0

        return ValidationStats(
            reject_rate_pct=pcts,
            most_common_errors=[],
            total_processed=total_p,
            total_rejected=total_r,
        )
