"""
Layer Data: Pendeteksi Anomali
Proteksi Tingkat Tinggi: Menghentikan trading bila Market sedang Extreme
atau Data Feed sedang bermasalah (Spike / Stale / Crossed).
"""

import logging
import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from runtime.agent.core.config_schema import AgentConfig  # type: ignore
from runtime.agent.data_layer.market import Candle, Ticker

logger = logging.getLogger(__name__)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class AnomalyReport:
    anomaly_id: str
    severity: str  # LOW | MEDIUM | HIGH | CRITICAL
    symbol: str
    message: str
    value: float
    threshold: float
    timestamp: datetime = field(default_factory=utcnow)
    recommended: str = ""


class AnomalyDetector:
    """Mendeteksi kondisi bursa abnormal yang membahayakan Agen."""

    def __init__(self, config: AgentConfig) -> None:
        self.config = config

        # Thresholds
        self.spike_threshold = getattr(config, "ws_price_spike_threshold", 0.05)
        self.wide_spread_pct = getattr(config, "ws_wide_spread_pct", 1.0)
        self.min_bid_depth_usd = getattr(config, "ws_min_bid_depth", 10000.0)
        self.rapid_move_candles = getattr(config, "ws_rapid_move_candles", 3)
        self.rapid_move_threshold = getattr(config, "ws_rapid_move_threshold", 0.03)
        self.persist_s = getattr(config, "ws_anomaly_persist_s", 300.0)

        # Active anomalies tracking [symbol] = { anomaly_id: AnomalyReport }
        self._active_anomalies: dict[str, dict[str, AnomalyReport]] = {}

    def _add_anomaly(self, report: AnomalyReport) -> None:
        sym = report.symbol
        if sym not in self._active_anomalies:
            self._active_anomalies[sym] = {}

        self._active_anomalies[sym][report.anomaly_id] = report
        sv = report.severity
        if sv in ["HIGH", "CRITICAL"]:
            logger.error(f"[Anomaly] {sv}: {report.message} ({report.recommended})")
        else:
            logger.warning(f"[Anomaly] {sv}: {report.message} ({report.recommended})")

    def _cleanup_anomalies(self) -> None:
        now = utcnow().timestamp()
        for sym in list(self._active_anomalies.keys()):
            for k in list(self._active_anomalies[sym].keys()):
                # Kalau sudah lewat batas persist (default 300s/5menit), hapus
                if now - self._active_anomalies[sym][k].timestamp.timestamp() > self.persist_s:
                    del self._active_anomalies[sym][k]
                    logger.info(f"[{sym}] Anomaly '{k}' Kadaluwarsa. Kondisi kembali normal.")

            if len(self._active_anomalies[sym]) == 0:
                del self._active_anomalies[sym]

    def check_candle(self, candle: Candle, prev_candles: list[Candle]) -> list[AnomalyReport]:
        self._cleanup_anomalies()
        new_anomalies = []

        # 1. Price Spike (log return drastis > 5%)
        # Cek return terhadap candle sebelumnya jika ada
        if len(prev_candles) > 0:
            last = prev_candles[-1]
            if last.close > 0:
                log_ret = abs(math.log(candle.close / last.close))
                if log_ret > self.spike_threshold:
                    rep = AnomalyReport(
                        "PRICE_SPIKE",
                        "HIGH",
                        candle.symbol,
                        f"Spike Dideteksi ({log_ret*100:.1f}% per candle)",
                        value=log_ret,
                        threshold=self.spike_threshold,
                        recommended="Skip Candle. Halt Trading.",
                    )
                    new_anomalies.append(rep)
                    self._add_anomaly(rep)

        # 2. Volume Zero
        if candle.volume == 0 and candle.is_closed:
            rep = AnomalyReport(
                "VOLUME_ZERO",
                "HIGH",
                candle.symbol,
                "Lilin Mati (No Volume). Indikasi bursa Maintenance/HALT.",
                value=0.0,
                threshold=0.1,
                recommended="Halt Trading",
            )
            new_anomalies.append(rep)
            self._add_anomaly(rep)

        # 3. Rapid Move (Flash Rally/Crash Beruntun)
        if len(prev_candles) >= self.rapid_move_candles - 1:
            recent = prev_candles[-(self.rapid_move_candles - 1) :] + [candle]
            changes = []
            for i in range(1, len(recent)):
                c1, c2 = recent[i - 1], recent[i]
                if c1.close > 0:
                    chg = (c2.close - c1.close) / c1.close
                    changes.append(chg)

            # Jika semua positif dan > 3%, atau semua negatif dan < -3%
            if all(chg > self.rapid_move_threshold for chg in changes) or all(
                chg < -self.rapid_move_threshold for chg in changes
            ):
                rep = AnomalyReport(
                    "RAPID_MOVE",
                    "MEDIUM",
                    candle.symbol,
                    f"Pergerakan Sepihak {self.rapid_move_candles} Lilin Berturut-turut",
                    value=abs(sum(changes)),
                    threshold=self.rapid_move_threshold * self.rapid_move_candles,
                    recommended="Gunakan Mode HIGH_VOLATILITY (Batasi size 50%)",
                )
                new_anomalies.append(rep)
                self._add_anomaly(rep)

        return new_anomalies

    def check_ticker(self, ticker: Ticker) -> list[AnomalyReport]:
        self._cleanup_anomalies()
        new_anomalies = []

        # 1. Crossed Book
        if ticker.bid >= ticker.ask:
            rep = AnomalyReport(
                "CROSSED_BOOK",
                "CRITICAL",
                ticker.symbol,
                f"Kondisi Mustahil: Membeli di {ticker.bid} lalu Jual di {ticker.ask} Instan! (Broken Stream)",
                value=ticker.bid - ticker.ask,
                threshold=0,
                recommended="Darurat! Putuskan Engine dari Order Market.",
            )
            new_anomalies.append(rep)
            self._add_anomaly(rep)

        # 2. Wide Spread
        if ticker.spread_pct > self.wide_spread_pct:
            rep = AnomalyReport(
                "WIDE_SPREAD",
                "MEDIUM",
                ticker.symbol,
                f"Selisih B/A Lebar: {ticker.spread_pct:.2f}% (Standar max {self.wide_spread_pct}%)",
                value=ticker.spread_pct,
                threshold=self.wide_spread_pct,
                recommended="Larang Market Order (Cegah Slippage Ekstrem).",
            )
            new_anomalies.append(rep)
            self._add_anomaly(rep)

        return new_anomalies

    def check_orderbook(self, ob: Any) -> list[AnomalyReport]:
        self._cleanup_anomalies()
        # Mock Check untuk Low Liquidity
        # ob is Orderbook tapi kita blm buat file aslinya jadi pakai Any
        return []

    def is_safe_to_trade(self, symbol: str) -> tuple[bool, list[AnomalyReport]]:
        """
        Jika ada anomali berlevel HIGH atau CRITICAL, kunci keamanan akan hidup
        dan mencegah strategi menghasilkan sinyal entry.
        """
        self._cleanup_anomalies()
        active = self.get_active_anomalies(symbol)

        severe = [a for a in active if a.severity in ("HIGH", "CRITICAL")]
        if len(severe) > 0:
            return False, active

        return True, active

    def get_active_anomalies(self, symbol: str | None = None) -> list[AnomalyReport]:
        if symbol:
            return list(self._active_anomalies.get(symbol, {}).values())

        all_anom = []
        for sym_dict in self._active_anomalies.values():
            all_anom.extend(list(sym_dict.values()))
        return all_anom

    def resolve(self, anomaly_id: str, symbol: str) -> None:
        if symbol in self._active_anomalies and anomaly_id in self._active_anomalies[symbol]:
            del self._active_anomalies[symbol][anomaly_id]
            logger.info(f"[{symbol}] Anomaly '{anomaly_id}' Dirilis Paksa Secara Tangan Kosong.")
