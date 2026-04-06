"""
adaptation.py — Update Parameter Otomatis
Menyesuaikan parameter strategi berdasarkan hasil analisis. 
Hanya mengubah parameter dalam batas yang ditentukan.
"""

import copy
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from runtime.agent.learning_layer.experience_processor import StrategyStats
from runtime.agent.learning_layer.learning_db import LearningDBManager
from runtime.shared.utils import utcnow  # type: ignore

log = logging.getLogger(__name__)


@dataclass
class AdaptationResult:
    strategy_id: str
    timestamp: datetime
    parameter_name: str
    old_value: float
    new_value: float
    reason: str
    applied: bool
    config_backup: dict[str, Any]


class AdaptationEngine:
    # Batas aman seperti yang disyaratkan
    LIMITS = {
        "min_signal_confidence": {"min": 0.40, "max": 0.90, "step": 0.05},
        "sl_atr_multiplier": {"min": 1.0, "max": 3.5, "step": 0.2},
        "default_tp_ratio": {"min": 1.2, "max": 5.0, "step": 0.2},
        "signal_cooldown": {"min": 1, "max": 10, "step": 1},
        "trail_atr_mult": {"min": 1.0, "max": 4.0, "step": 0.2},
        "risk_per_trade_pct": {"min": 0.005, "max": 0.03, "step": -0.002},
    }

    def __init__(self, db: LearningDBManager):
        self._db = db

    def evaluate(self, stats: StrategyStats, config: Any) -> list[AdaptationResult]:
        """Evaluasi & usulkan perubahan. TIDAK langsung apply."""
        proposals = []
        
        # Aturan 1: Win rate < 45% -> naikkan min_signal_confidence
        if stats.win_rate < 0.45 and stats.total_trades >= 10:
            old_val = getattr(config, "min_signal_confidence", 0.60)
            new_val = min(old_val + self.LIMITS["min_signal_confidence"]["step"], self.LIMITS["min_signal_confidence"]["max"])
            if new_val != old_val:
                proposals.append(AdaptationResult(
                    strategy_id=stats.strategy_id,
                    timestamp=utcnow(),
                    parameter_name="min_signal_confidence",
                    old_value=old_val,
                    new_value=new_val,
                    reason=f"Win rate {stats.win_rate*100:.1f}% < 45%",
                    applied=False,
                    config_backup={}
                ))

        # Aturan 2: Avg loss > 1.2R (disimulasikan avg_loss_usd > threshold)
        # Asumsikan avg risk sekitar $10
        if stats.avg_loss_usd > 12.0 and stats.total_trades >= 20:
             old_val = getattr(config, "sl_atr_multiplier", 2.0)
             new_val = min(old_val + self.LIMITS["sl_atr_multiplier"]["step"], self.LIMITS["sl_atr_multiplier"]["max"])
             if new_val != old_val:
                 proposals.append(AdaptationResult(
                     strategy_id=stats.strategy_id,
                     timestamp=utcnow(),
                     parameter_name="sl_atr_multiplier",
                     old_value=old_val,
                     new_value=new_val,
                     reason="Avg loss terlalu besar",
                     applied=False,
                     config_backup={}
                 ))

        return proposals

    async def apply(self, result: AdaptationResult, config: Any, dry_run: bool = False) -> bool:
        """
        Simpan config_backup SEBELUM apply.
        dry_run=True -> hanya log, tidak ubah.
        """
        if dry_run:
            log.info(f"[DRY-RUN] Akan mengubah {result.parameter_name} dari {result.old_value} ke {result.new_value}")
            return True

        # Backup config
        result.config_backup = {result.parameter_name: result.old_value}
        
        # Apply dinamis
        setattr(config, result.parameter_name, result.new_value)
        result.applied = True
        
        # Simpan audit ke DB
        assert self._db.perf_conn is not None
        await self._db.perf_conn.execute(
            """
            INSERT INTO adaptation_history 
            (strategy_id, timestamp, parameter_name, old_value, new_value, reason)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                result.strategy_id,
                result.timestamp.isoformat(),
                result.parameter_name,
                result.old_value,
                result.new_value,
                result.reason
            )
        )
        await self._db.perf_conn.commit()
        log.warning(f"ADAPTATION APPLIED: {result.parameter_name} -> {result.new_value}")
        return True

    def rollback(self, result: AdaptationResult, config: Any) -> bool:
        """Kembalikan ke config_backup dari result."""
        if not result.applied or not result.config_backup:
            return False
            
        old_val = result.config_backup[result.parameter_name]
        setattr(config, result.parameter_name, old_val)
        log.warning(f"ADAPTATION ROLLBACK: {result.parameter_name} -> {old_val}")
        return True

    async def get_adaptation_history(self, strategy_id: str, last_n: int = 10) -> list[Any]:
        """Riwayat adaptasi dari DB untuk audit."""
        assert self._db.perf_conn is not None
        cursor = await self._db.perf_conn.execute(
            "SELECT * FROM adaptation_history WHERE strategy_id = ? ORDER BY timestamp DESC LIMIT ?", 
            (strategy_id, last_n)
        )
        return [dict(row) for row in await cursor.fetchall()]
