"""
strategy_killer.py — Auto-Disable Strategi
Menonaktifkan strategi underperform secara otomatis. 
Posisi yang sudah terbuka tetap dikelola sampai ditutup secara normal.
"""

import logging
from typing import Any

from runtime.agent.learning_layer.experience_processor import StrategyStats
from runtime.agent.learning_layer.learning_db import LearningDBManager
from runtime.shared.utils import utcnow  # type: ignore

log = logging.getLogger(__name__)


class StrategyKiller:
    def __init__(
        self,
        db: LearningDBManager,
        registry: Any,  # StrategyRegistry
        alerts: Any,  # Notification/AlertSystem
        config: Any,
    ):
        self._db = db
        self._registry = registry
        self._alerts = alerts
        self.config = config

    async def evaluate_all(self, processor_results: dict[str, dict[str, StrategyStats]]) -> None:
        """
        Dipanggil harian. Mengevaluasi semua strategi aktif.
        """
        for strategy_id, periods in processor_results.items():
            stats_30d = periods.get("30d")

            if not stats_30d or stats_30d.total_trades < getattr(
                self.config, "min_trades_for_eval", 20
            ):
                continue

            reasons = []

            if stats_30d.max_consecutive_losses >= getattr(self.config, "kill_consec_losses", 7):
                reasons.append(
                    f"Consecutive losses >= {getattr(self.config, 'kill_consec_losses', 7)}"
                )

            if stats_30d.sharpe_ratio < getattr(self.config, "kill_sharpe_threshold", -0.5):
                reasons.append(
                    f"Sharpe ratio < {getattr(self.config, 'kill_sharpe_threshold', -0.5)}"
                )

            if stats_30d.win_rate < getattr(self.config, "kill_win_rate_threshold", 0.35):
                reasons.append(f"Win rate {stats_30d.win_rate*100:.1f}% < threshold")

            if stats_30d.max_drawdown_pct > getattr(self.config, "kill_dd_threshold", 0.15):
                reasons.append("Max Drawdown 30 hari melampaui batas")

            if stats_30d.profit_factor < getattr(self.config, "kill_pf_threshold", 0.7):
                reasons.append(f"Profit factor {stats_30d.profit_factor:.2f} < 0.7")

            if reasons:
                await self._kill(strategy_id, reasons)

    async def _kill(self, strategy_id: str, reasons: list[str]) -> None:
        """
        Urutan disable yang aman:
        1. Stop terima signal baru dari strategi ini
        2. Posisi yang sudah ada TETAP di-manage (tidak di-force close)
        3. Catat ke disabled_strategies
        """
        # 1. Stop dari registry (asumsi StrategyRegistry memiliki fungsi disable)
        if hasattr(self._registry, "disable"):
            self._registry.disable(strategy_id, "; ".join(reasons))

        # 3. DB Persistence
        await self._save_disabled_record(strategy_id, reasons)

        # 4. Alerting
        if hasattr(self._alerts, "send"):
            # Jika async function
            import asyncio

            if asyncio.iscoroutinefunction(self._alerts.send):
                await self._alerts.send(
                    severity="WARNING",
                    title=f"Strategi Dinonaktifkan: {strategy_id}",
                    message="; ".join(reasons),
                    component="strategy_killer",
                )
            else:
                self._alerts.send(
                    severity="WARNING",
                    title=f"Strategi Dinonaktifkan: {strategy_id}",
                    message="; ".join(reasons),
                    component="strategy_killer",
                )

        log.warning(f"Strategy {strategy_id} KILLED! Reasons: {reasons}")

    async def _save_disabled_record(self, strategy_id: str, reasons: list[str]) -> None:
        from datetime import timedelta

        assert self._db.perf_conn is not None

        days_review = getattr(self.config, "review_after_days", 7)
        review_date = (utcnow() + timedelta(days=days_review)).isoformat()

        await self._db.perf_conn.execute(
            """
            INSERT OR REPLACE INTO disabled_strategies
            (strategy_id, disabled_at, reasons, review_after)
            VALUES (?, ?, ?, ?)
            """,
            (strategy_id, utcnow().isoformat(), "; ".join(reasons), review_date),
        )
        await self._db.perf_conn.commit()

    async def schedule_review(self, strategy_id: str, days: int) -> None:
        # Konsep implementasi penjadwalan via scheduler global kalau dibutuhkan
        pass
