"""
AnomalyDetector — Deteksi kondisi market abnormal.

Berbeda dengan validator (cek integritas data), anomaly detector
mendeteksi kondisi market yang berbahaya untuk trading.
"""

import logging
import math
from datetime import UTC, datetime

from runtime.agent.models import (  # type: ignore
    AnomalyReport,
    Candle,
    Orderbook,
    Ticker,
)

logger = logging.getLogger(__name__)


class AnomalyDetector:
    """
    Deteksi anomali market: spike, stale data, crossed book, dll.

    Anomali yang terdeteksi disimpan sebagai 'active' sampai
    di-resolve atau auto-expire setelah ANOMALY_PERSIST_S.
    """

    def __init__(
        self,
        price_spike_threshold: float = 0.05,
        wide_spread_pct: float = 1.0,
        min_bid_depth_usd: float = 10_000.0,
        rapid_move_candles: int = 3,
        rapid_move_threshold: float = 0.03,
        anomaly_persist_s: float = 300.0,
        funding_extreme_pct: float = 0.001,
    ) -> None:
        self._price_spike_threshold = price_spike_threshold
        self._wide_spread_pct = wide_spread_pct
        self._min_bid_depth_usd = min_bid_depth_usd
        self._rapid_move_candles = rapid_move_candles
        self._rapid_move_threshold = rapid_move_threshold
        self._anomaly_persist_s = anomaly_persist_s
        self._funding_extreme_pct = funding_extreme_pct

        # Active anomalies: {(anomaly_id, symbol): AnomalyReport}
        self._active: dict[tuple[str, str], AnomalyReport] = {}

    # ── Candle checks ──────────────────────────────────────────

    def check_candle(
        self,
        candle: Candle,
        prev_candles: list[Candle] | None = None,
    ) -> list[AnomalyReport]:
        """Cek anomali berbasis candle. Return list kosong jika aman."""
        reports: list[AnomalyReport] = []
        prev = prev_candles or []

        # PRICE_SPIKE — |log_return| > threshold
        if prev:
            last_close = prev[-1].close
            if last_close > 0 and candle.close > 0:
                log_ret = abs(math.log(candle.close / last_close))
                if log_ret > self._price_spike_threshold:
                    r = AnomalyReport(
                        anomaly_id="PRICE_SPIKE",
                        severity="HIGH",
                        symbol=candle.symbol,
                        message=(
                            f"Price spike: |log_return| = "
                            f"{log_ret:.4f} > {self._price_spike_threshold}"
                        ),
                        value=log_ret,
                        threshold=self._price_spike_threshold,
                        recommended="Skip candle, alert",
                    )
                    reports.append(r)
                    self._activate(r)

        # VOLUME_ZERO — volume = 0 pada pair yang seharusnya liquid
        if candle.volume == 0:
            r = AnomalyReport(
                anomaly_id="VOLUME_ZERO",
                severity="HIGH",
                symbol=candle.symbol,
                message="Volume = 0 pada candle",
                value=0.0,
                threshold=0.0,
                recommended="Skip candle, alert",
            )
            reports.append(r)
            self._activate(r)

        # RAPID_MOVE — N candle berturut bergerak > threshold
        if len(prev) >= self._rapid_move_candles:
            recent = prev[-self._rapid_move_candles :]  # type: ignore[index]
            all_big = True
            for c in recent:
                if c.close > 0 and c.open > 0:
                    move = abs(c.close - c.open) / c.open
                    if move < self._rapid_move_threshold:
                        all_big = False
                        break
                else:
                    all_big = False
                    break

            if all_big:
                r = AnomalyReport(
                    anomaly_id="RAPID_MOVE",
                    severity="MEDIUM",
                    symbol=candle.symbol,
                    message=(
                        f"{self._rapid_move_candles} candle berturut "
                        f"bergerak > {self._rapid_move_threshold*100:.0f}%"
                    ),
                    value=self._rapid_move_threshold,
                    threshold=self._rapid_move_threshold,
                    recommended="Apply HIGH_VOLATILITY regime",
                )
                reports.append(r)
                self._activate(r)

        return reports

    # ── Ticker checks ──────────────────────────────────────────

    def check_ticker(self, ticker: Ticker) -> list[AnomalyReport]:
        """Cek anomali berbasis ticker."""
        reports: list[AnomalyReport] = []

        # CROSSED_BOOK — bid >= ask
        if ticker.bid > 0 and ticker.ask > 0 and ticker.bid >= ticker.ask:
            r = AnomalyReport(
                anomaly_id="CROSSED_BOOK",
                severity="CRITICAL",
                symbol=ticker.symbol,
                message=(f"Crossed book: bid ({ticker.bid}) >= ask ({ticker.ask})"),
                value=ticker.bid,
                threshold=ticker.ask,
                recommended="Halt trading, alert KRITIS",
            )
            reports.append(r)
            self._activate(r)

        # WIDE_SPREAD
        if ticker.spread_pct > self._wide_spread_pct:
            r = AnomalyReport(
                anomaly_id="WIDE_SPREAD",
                severity="MEDIUM",
                symbol=ticker.symbol,
                message=(f"Wide spread: {ticker.spread_pct:.2f}% > " f"{self._wide_spread_pct}%"),
                value=ticker.spread_pct,
                threshold=self._wide_spread_pct,
                recommended="Larang market order",
            )
            reports.append(r)
            self._activate(r)

        return reports

    # ── Orderbook checks ───────────────────────────────────────

    def check_orderbook(self, ob: Orderbook) -> list[AnomalyReport]:
        """Cek anomali berbasis orderbook."""
        reports: list[AnomalyReport] = []

        # CROSSED_BOOK
        if ob.bids and ob.asks and ob.best_bid >= ob.best_ask:
            r = AnomalyReport(
                anomaly_id="CROSSED_BOOK",
                severity="CRITICAL",
                symbol=ob.symbol,
                message=(f"Crossed book: best_bid ({ob.best_bid}) >= " f"best_ask ({ob.best_ask})"),
                value=ob.best_bid,
                threshold=ob.best_ask,
                recommended="Halt trading, alert KRITIS",
            )
            reports.append(r)
            self._activate(r)

        # LOW_LIQUIDITY — depth di 5 level bid < threshold
        if ob.bids:
            top5 = ob.bids[:5]
            bid_depth_usd = sum(lvl.price * lvl.quantity for lvl in top5)
            if bid_depth_usd < self._min_bid_depth_usd:
                r = AnomalyReport(
                    anomaly_id="LOW_LIQUIDITY",
                    severity="MEDIUM",
                    symbol=ob.symbol,
                    message=(
                        f"Low bid depth: ${bid_depth_usd:,.0f} < "
                        f"${self._min_bid_depth_usd:,.0f}"
                    ),
                    value=bid_depth_usd,
                    threshold=self._min_bid_depth_usd,
                    recommended="Kurangi position size 50%",
                )
                reports.append(r)
                self._activate(r)

        # WIDE_SPREAD (dari orderbook)
        if ob.spread_pct > self._wide_spread_pct:
            r = AnomalyReport(
                anomaly_id="WIDE_SPREAD",
                severity="MEDIUM",
                symbol=ob.symbol,
                message=(f"Wide spread: {ob.spread_pct:.2f}% > " f"{self._wide_spread_pct}%"),
                value=ob.spread_pct,
                threshold=self._wide_spread_pct,
                recommended="Larang market order",
            )
            reports.append(r)
            self._activate(r)

        return reports

    # ── Aggregate queries ──────────────────────────────────────

    def is_safe_to_trade(self, symbol: str) -> tuple[bool, list[AnomalyReport]]:
        """
        Aggregasi semua anomali aktif untuk symbol.

        Return (True, []) jika aman.
        Return (False, [reports]) jika ada HIGH atau CRITICAL.
        """
        self._expire_old()
        active = self.get_active_anomalies(symbol)
        blocking = [r for r in active if r.severity in ("HIGH", "CRITICAL")]
        return (len(blocking) == 0, active)

    def get_active_anomalies(self, symbol: str | None = None) -> list[AnomalyReport]:
        """Return anomali yang masih aktif."""
        self._expire_old()
        if symbol is None:
            return list(self._active.values())
        return [r for (_, sym), r in self._active.items() if sym == symbol]

    def resolve(self, anomaly_id: str, symbol: str) -> None:
        """Mark anomali sebagai resolved."""
        key = (anomaly_id, symbol)
        if key in self._active:
            del self._active[key]  # type: ignore[arg-type]
            logger.info("Anomaly resolved: %s %s", anomaly_id, symbol)

    # ── Internal ───────────────────────────────────────────────

    def _activate(self, report: AnomalyReport) -> None:
        """Add/update anomali ke active store."""
        key = (report.anomaly_id, report.symbol)
        self._active[key] = report

    def _expire_old(self) -> None:
        """Remove anomali yang sudah lewat ANOMALY_PERSIST_S."""
        now = datetime.now(UTC)
        expired = [
            key
            for key, r in self._active.items()
            if (now - r.timestamp).total_seconds() > self._anomaly_persist_s
        ]
        for key in expired:
            del self._active[key]  # type: ignore[arg-type]
