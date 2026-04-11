"""
llm_budget.py - Anggaran harian untuk API LLM
Menghindari kebocoran cost runaway pada token.
"""

import json
import logging
import os
from dataclasses import dataclass

log = logging.getLogger(__name__)


@dataclass
class LLMBudgetLimits:
    daily_usd_limit: float = 1.0
    daily_call_limit: int = 1000


class LLMBudget:
    def __init__(
        self, limits: LLMBudgetLimits, storage_file: str = "runtime/agent/llm_layer/budget.json"
    ):
        self._limits = limits
        self._storage_file = storage_file
        self._spent_today = 0.0
        self._calls_today = 0
        self._load()

    def _load(self):
        if os.path.exists(self._storage_file):
            try:
                with open(self._storage_file) as f:
                    data = json.load(f)
                    self._spent_today = data.get("spent_today", 0.0)
                    self._calls_today = data.get("calls_today", 0)
            except Exception as e:
                log.error(f"Gagal load budget: {e}")

    async def _persist(self):
        os.makedirs(os.path.dirname(self._storage_file), exist_ok=True)
        try:
            with open(self._storage_file, "w") as f:
                json.dump({"spent_today": self._spent_today, "calls_today": self._calls_today}, f)
        except Exception as e:
            log.error(f"Gagal simpan budget: {e}")

    async def can_call(
        self, estimated_tokens: int = 500, model: str = "claude-haiku-4-5-20251001"
    ) -> bool:
        """Return False jika spent + estimated_cost > daily_limit."""
        if self._calls_today >= self._limits.daily_call_limit:
            log.warning("Daily call limit tercapai.")
            return False

        pricing = {
            "claude-haiku-4-5-20251001": {"input": 0.25, "output": 1.25},
            "claude-sonnet-4-6": {"input": 3.00, "output": 15.00},
            "claude-opus-4-6": {"input": 15.00, "output": 75.00},
        }.get(model, {"input": 1.0, "output": 1.0})

        est_cost = (
            estimated_tokens * pricing["input"] + estimated_tokens * pricing["output"]
        ) / 1_000_000

        if (self._spent_today + est_cost) > self._limits.daily_usd_limit:
            log.warning(
                f"Budget USD LLM Habis! Spent: ${self._spent_today:.4f}, Est: ${est_cost:.4f}"
            )
            return False

        return True

    async def record_usage(self, cost_usd: float) -> None:
        self._spent_today += cost_usd
        self._calls_today += 1
        await self._persist()

    def reset_daily(self) -> None:
        """Dipanggil scheduler UTC 00:00."""
        self._spent_today = 0.0
        self._calls_today = 0
        import asyncio

        asyncio.create_task(self._persist())
