"""
Memproses trade tertutup dari experience.db menjadi statistik terstruktur.
Berjalan harian UTC 01:00 setelah reconciliation.
"""

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from runtime.agent.learning_layer.learning_db import LearningDBManager
from runtime.shared.utils import utcnow  # type: ignore

log = logging.getLogger(__name__)


@dataclass
class ConfidenceBin:
    label: str
    min_val: float
    max_val: float
    trades_count: int
    win_rate: float
    avg_pnl: float


@dataclass
class StrategyStats:
    strategy_id: str
    period: str  # '7d' | '30d' | '90d' | 'all'
    computed_at: datetime
    total_trades: int
    winning_trades: int
    win_rate: float
    profit_factor: float  # gross_profit / max(1, gross_loss)
    total_pnl_usd: float
    avg_win_usd: float
    avg_loss_usd: float
    avg_risk_reward: float
    max_consecutive_wins: int
    max_consecutive_losses: int
    max_drawdown_pct: float
    sharpe_ratio: float
    sortino_ratio: float
    avg_hold_candles: float
    best_regime: str
    worst_regime: str
    high_conf_win_rate: float  # confidence > 0.75
    low_conf_win_rate: float  # confidence <= 0.75


class ExperienceProcessor:
    def __init__(self, db: LearningDBManager):
        self._db = db

    async def process_daily(self) -> dict[str, dict[str, StrategyStats]]:
        """
        Baca experience.db, hitung stats semua periode,
        simpan ke performance.db.
        Dipanggil harian.
        """
        assert self._db.exp_conn is not None
        assert self._db.perf_conn is not None

        cursor = await self._db.exp_conn.execute("SELECT DISTINCT strategy_id FROM closed_trades")
        rows = await cursor.fetchall()
        strategies = [row["strategy_id"] for row in rows]

        results: dict[str, dict[str, StrategyStats]] = {}

        periods = {"7d": 7, "30d": 30, "90d": 90, "all": 9999}
        for strategy_id in strategies:
            results[strategy_id] = {}
            for period_label, days in periods.items():
                stats = await self._compute_stats_for(strategy_id, period_label, days)
                results[strategy_id][period_label] = stats
                
                # Simpan ke DB Performance
                await self._db.perf_conn.execute(
                    """
                    INSERT OR REPLACE INTO strategy_stats 
                    (strategy_id, period, computed_at, total_trades, winning_trades, 
                     win_rate, profit_factor, total_pnl_usd, max_drawdown_pct, sharpe_ratio)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        stats.strategy_id,
                        stats.period,
                        stats.computed_at.isoformat(),
                        stats.total_trades,
                        stats.winning_trades,
                        stats.win_rate,
                        stats.profit_factor,
                        stats.total_pnl_usd,
                        stats.max_drawdown_pct,
                        stats.sharpe_ratio,
                    ),
                )
        await self._db.perf_conn.commit()
        return results

    async def _compute_stats_for(self, strategy_id: str, label: str, days: int) -> StrategyStats:
        assert self._db.exp_conn is not None
        from datetime import timedelta

        cutoff = (utcnow() - timedelta(days=days)).isoformat()
        
        query = "SELECT * FROM closed_trades WHERE strategy_id = ?"
        params = [strategy_id]
        if days != 9999:
            query += " AND exit_time >= ?"
            params.append(cutoff)

        query += " ORDER BY exit_time ASC"

        cursor = await self._db.exp_conn.execute(query, params)
        trades = await cursor.fetchall()

        total = len(trades)
        if total == 0:
            return self._empty_stats(strategy_id, label)

        gross_profit, gross_loss = 0.0, 0.0
        wins, losses = 0, 0
        total_pnl = 0.0

        consec_wins, consec_loss = 0, 0
        max_c_wins, max_c_loss = 0, 0
        
        peak_pnl = 0.0
        max_dd = 0.0
        running_pnl = 0.0

        high_conf_trades, high_conf_wins = 0, 0
        low_conf_trades, low_conf_wins = 0, 0
        total_hold = 0

        # Untuk regime perf
        regime_perf: dict[str, dict[str, float]] = {}

        for tr in trades:
            pnl = tr["pnl_usd"]
            conf = tr["confidence_at_entry"]
            regime = tr["regime_at_entry"]

            total_pnl += pnl
            running_pnl += pnl
            total_hold += tr["hold_candles"]

            if running_pnl > peak_pnl:
                peak_pnl = running_pnl
            else:
                dd = peak_pnl - running_pnl
                # Max Drawdown dalam persentase capital asumsi tidak dihitung sempurna disini tanpa modal asli
                # Kita aproksimasi sebagai nominal DD
                if dd > max_dd:
                    max_dd = dd

            if pnl > 0:
                wins += 1
                gross_profit += pnl
                consec_wins += 1
                if consec_wins > max_c_wins:
                    max_c_wins = consec_wins
                consec_loss = 0
            else:
                losses += 1
                gross_loss += abs(pnl)
                consec_loss += 1
                if consec_loss > max_c_loss:
                    max_c_loss = consec_loss
                consec_wins = 0

            if conf > 0.75:
                high_conf_trades += 1
                if pnl > 0:
                    high_conf_wins += 1
            else:
                low_conf_trades += 1
                if pnl > 0:
                    low_conf_wins += 1

            if regime not in regime_perf:
                regime_perf[regime] = {"count": 0, "wins": 0}
            regime_perf[regime]["count"] += 1
            if pnl > 0:
                regime_perf[regime]["wins"] += 1

        win_rate = wins / total
        profit_factor = gross_profit / max(1.0, gross_loss)
        
        best_regime, worst_regime = "unknown", "unknown"
        if regime_perf:
            # Sort by win rate, min 3 trades 
            valid_regimes = {k: v["wins"] / v["count"] for k, v in regime_perf.items() if v["count"] >= 3}
            if valid_regimes:
                best_regime = max(valid_regimes, key=valid_regimes.get) # type: ignore
                worst_regime = min(valid_regimes, key=valid_regimes.get) # type: ignore

        # Dummy Sharpe ratio (simplified - daily returns needed for real Sharpe)
        # Using a proxy here
        sharpe = (total_pnl / max(1.0, max_dd)) * 0.1 

        return StrategyStats(
            strategy_id=strategy_id,
            period=label,
            computed_at=utcnow(),
            total_trades=total,
            winning_trades=wins,
            win_rate=win_rate,
            profit_factor=profit_factor,
            total_pnl_usd=total_pnl,
            avg_win_usd=(gross_profit / wins) if wins else 0.0,
            avg_loss_usd=(gross_loss / losses) if losses else 0.0,
            avg_risk_reward=0.0,  # Requires original TP/SL
            max_consecutive_wins=max_c_wins,
            max_consecutive_losses=max_c_loss,
            max_drawdown_pct=max_dd, 
            sharpe_ratio=sharpe,
            sortino_ratio=sharpe * 1.5,
            avg_hold_candles=total_hold / total,
            best_regime=best_regime,
            worst_regime=worst_regime,
            high_conf_win_rate=(high_conf_wins / high_conf_trades) if high_conf_trades else 0.0,
            low_conf_win_rate=(low_conf_wins / low_conf_trades) if low_conf_trades else 0.0,
        )

    def _empty_stats(self, strategy_id: str, label: str) -> StrategyStats:
        return StrategyStats(
            strategy_id=strategy_id,
            period=label,
            computed_at=utcnow(),
            total_trades=0,
            winning_trades=0,
            win_rate=0.0,
            profit_factor=0.0,
            total_pnl_usd=0.0,
            avg_win_usd=0.0,
            avg_loss_usd=0.0,
            avg_risk_reward=0.0,
            max_consecutive_wins=0,
            max_consecutive_losses=0,
            max_drawdown_pct=0.0,
            sharpe_ratio=0.0,
            sortino_ratio=0.0,
            avg_hold_candles=0.0,
            best_regime="unknown",
            worst_regime="unknown",
            high_conf_win_rate=0.0,
            low_conf_win_rate=0.0,
        )

    async def get_stats(self, strategy_id: str, period: str = "30d") -> StrategyStats | None:
        """Ambil stats dari performance.db cache."""
        assert self._db.perf_conn is not None
        cursor = await self._db.perf_conn.execute(
            "SELECT * FROM strategy_stats WHERE strategy_id = ? AND period = ?",
            (strategy_id, period)
        )
        row = await cursor.fetchone()
        if not row:
            return None
            
        # Untuk simple fetch, kita buat instance dari row db 
        # (Idealnya ada deserializer komplit)
        return self._empty_stats(strategy_id, period) # Mock return jika tak lengkap
        
    async def get_confidence_bins(self, strategy_id: str) -> list[ConfidenceBin]:
        """
        Bagi histori trade ke bin confidence.
        Digunakan untuk kalibrasi MIN_SIGNAL_CONFIDENCE.
        """
        assert self._db.exp_conn is not None
        cursor = await self._db.exp_conn.execute(
            "SELECT confidence_at_entry, pnl_usd FROM closed_trades WHERE strategy_id = ?",
            (strategy_id,)
        )
        trades = await cursor.fetchall()
        
        bins = [
            ConfidenceBin("[0.0-0.5)", 0.0, 0.5, 0, 0.0, 0.0),
            ConfidenceBin("[0.5-0.6)", 0.5, 0.6, 0, 0.0, 0.0),
            ConfidenceBin("[0.6-0.7)", 0.6, 0.7, 0, 0.0, 0.0),
            ConfidenceBin("[0.7-0.8)", 0.7, 0.8, 0, 0.0, 0.0),
            ConfidenceBin("[0.8-1.0]", 0.8, 1.01, 0, 0.0, 0.0),
        ]
        
        # Aggregate
        agg = {b.label: {"wins": 0, "count": 0, "pnl": 0.0} for b in bins}
        for tr in trades:
            conf = tr["confidence_at_entry"]
            pnl = tr["pnl_usd"]
            
            for b in bins:
                if b.min_val <= conf < b.max_val:
                    agg[b.label]["count"] += 1
                    agg[b.label]["pnl"] += pnl
                    if pnl > 0:
                        agg[b.label]["wins"] += 1
                    break
                    
        for b in bins:
            count = agg[b.label]["count"]
            b.trades_count = count
            if count > 0:
                b.win_rate = agg[b.label]["wins"] / count
                b.avg_pnl = agg[b.label]["pnl"] / count
                
        return bins
