"""
strategy_feedback.py — Feedback Mingguan
Mengirim ringkasan performa ke LLM mingguan dan meminta saran perbaikan.
"""

import json
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from runtime.agent.learning_layer.experience_processor import ExperienceProcessor
from runtime.agent.learning_layer.reflection import TradeReflection
from runtime.agent.llm_layer.llm_budget import LLMBudget
from runtime.agent.llm_layer.llm_client import LLMClient, LLMRequest
from runtime.shared.utils import utcnow  # type: ignore

log = logging.getLogger(__name__)


@dataclass
class FeedbackReport:
    strategy_id: str
    week_ending: datetime
    recommendations: list[str]  # 3 rekomendasi dari LLM
    model_used: str
    cost_usd: float


SYSTEM_PROMPT = """
Anda adalah Penasehat Algorithmic Trading Kuantitatif.
Di bawah ini adalah riwayat evaluasi trading mingguan.
Analisa data tsb dan berikan TEPAT 3 saran perbaikan (terfokus pada risk bounds, kondisi market yang dihindari, atau timing exit).
Jawab HANYA dalam format JSON:
{
  "recommendations": ["saran 1", "saran 2", "saran 3"]
}
"""


class StrategyFeedback:
    def __init__(self, client: LLMClient, budget: LLMBudget, processor: ExperienceProcessor):
        self._client = client
        self._budget = budget
        self._processor = processor

    async def weekly_feedback(
        self,
        strategy_id: str,
        stats: Any,
        reflections: list[TradeReflection],
    ) -> FeedbackReport | None:
        """
        Dipanggil scheduler mingguan Minggu 03:00 UTC.
        Menggunakan claude-sonnet (lebih dalam dari haiku).
        """
        if not await self._budget.can_call(
            estimated_tokens=800, model="claude-3-5-sonnet-20241022"
        ):
            log.warning("Budget limit tercapai, skip weekly feedback.")
            return None

        # Bangun Prompt dari Stats & Reflections
        msg = f"STRATEGY: {strategy_id}\n"
        msg += f"Win Rate: {getattr(stats, 'win_rate', 0.0) * 100:.1f}%\n"
        msg += f"Profit Factor: {getattr(stats, 'profit_factor', 0.0):.2f}\n"
        msg += f"Max DD: {getattr(stats, 'max_drawdown_pct', 0.0):.2f}\n\n"

        # Summary refleksi
        bad_reflections = [r for r in reflections if r.verdict in ("bad", "terrible")]
        msg += f"Total Review: {len(reflections)}, Bad/Terrible: {len(bad_reflections)}\n"
        if bad_reflections:
            msg += "Recent Mistakes:\n"
            for br in bad_reflections[-3:]:
                msg += f"- {'; '.join(br.negatives)} | Regime: {br.regime_at_entry}\n"

        req = LLMRequest(
            system_prompt=SYSTEM_PROMPT,
            user_message=msg,
            model="claude-3-5-sonnet-20241022",
            max_tokens=256,
            temperature=0.2,
            timeout_s=25,
        )

        resp = await self._client.call(req)

        if not resp.success:
            log.error(f"Weekly feedback error: {resp.error}")
            return None

        try:
            data = json.loads(resp.content)
            recs = data.get("recommendations", [])
            await self._budget.record_usage(resp.cost_usd)

            return FeedbackReport(
                strategy_id=strategy_id,
                week_ending=utcnow(),
                recommendations=recs,
                model_used=resp.model,
                cost_usd=resp.cost_usd,
            )
        except Exception as e:
            log.error(f"Gagal parse JSON strategy_feedback: {e}")
            return None
