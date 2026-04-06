"""
llm_filter.py — Confidence Scoring
Komponen utama LLM layer. Menghasilkan confidence_multiplier 
yang dikalikan dengan signal confidence asli sebelum dikirim ke risk layer.
"""

import json
import logging
from dataclasses import dataclass
from typing import Any

from runtime.agent.llm_layer.llm_budget import LLMBudget
from runtime.agent.llm_layer.llm_client import LLMClient, LLMRequest
from runtime.agent.llm_layer.llm_rate_limiter import LLMRateLimiter

log = logging.getLogger(__name__)


@dataclass
class LLMScore:
    confidence_multiplier: float  # 0.0--1.5
    proceed: bool  # True jika multiplier >= 0.5
    reasoning: str
    concerns: list[str]
    model_used: str
    cost_usd: float
    latency_ms: float
    fallback_used: bool  # True jika LLM gagal, pakai default 1.0

    @property
    def is_blocking(self) -> bool:
        return self.confidence_multiplier < 0.3


SYSTEM_PROMPT = """
Kamu adalah analis trading cryptocurrency berpengalaman.
Evaluasi signal trading dan berikan respons HANYA dalam format JSON:
{
  "confidence_multiplier": <float 0.0-1.5>,
  "proceed": <bool>,
  "reasoning": "<satu kalimat>",
  "concerns": ["<concern 1>", "<concern 2>"]
}

Panduan confidence_multiplier:
1.5 = signal sangat kuat, kondisi ideal
1.0 = signal normal, tidak ada kekhawatiran
0.7 = ada kekhawatiran minor
0.3 = kekhawatiran serius, pertimbangkan skip
0.0 = jangan trade
"""


class LLMFilter:
    def __init__(
        self,
        client: LLMClient,
        budget: LLMBudget,
        rate_limiter: LLMRateLimiter,
        config: Any,
    ):
        self._client = client
        self._budget = budget
        self._rate_limiter = rate_limiter
        self._config = config

    def _fallback(self, reason: str) -> LLMScore:
        log.warning(f"LLM fallback: {reason}")
        return LLMScore(
            confidence_multiplier=1.0,  # default: tidak mengubah signal
            proceed=True,
            reasoning=f"Fallback: {reason}",
            concerns=[],
            model_used="fallback",
            cost_usd=0.0,
            latency_ms=0.0,
            fallback_used=True,
        )

    def _build_message(self, signal: Any, state: Any, last_trades: list) -> str:
        # Prompt user payload
        msg = f"SIGNAL: {signal.side} {signal.symbol} via {signal.strategy_id}\n"
        msg += f"Confidence Base: {getattr(signal, 'final_confidence', 0.5):.2f}\n"
        
        # Market State
        msg += f"MARKET REGIME: {getattr(state, 'regime', 'unknown')}\n"
        if hasattr(state, 'volatility_atr'):
             msg += f"VOLATILITY ATR: {getattr(state, 'volatility_atr', 'unknown')}\n"
             
        # History
        win_count = sum(1 for t in last_trades if t.get('pnl_usd', 0) > 0)
        total_recent = len(last_trades)
        msg += f"RECENT TRADES: {win_count} wins out of {total_recent}\n\n"
        msg += "Berdasarkan data di atas, tolong berikan penilaian JSON Anda."
        return msg

    async def score_signal(self, signal: Any, state: Any, last_trades: list[dict[str, Any]]) -> LLMScore:
        # Step 1: Budget check
        if not await self._budget.can_call(estimated_tokens=300):
            return self._fallback("Budget harian habis")

        # Step 2: Rate limit check
        if not self._rate_limiter.acquire(estimated_tokens=300):
            return self._fallback("Rate limit tercapai")

        # Step 3: Build & call
        request = LLMRequest(
            system_prompt=SYSTEM_PROMPT,
            user_message=self._build_message(signal, state, last_trades),
            model=getattr(self._config, "llm_model", "claude-3-5-haiku-20241022"),
            max_tokens=256,
            temperature=0.1,
            timeout_s=10, # jangan sampai memblokir main loop terlalu lama
        )

        response = await self._client.call(request)

        if not response.success:
            return self._fallback(f"LLM error: {response.error}")

        # Step 4: Parse JSON & clamp
        try:
            data = json.loads(response.content)
            multiplier = max(0.0, min(1.5, float(data["confidence_multiplier"])))
        except (json.JSONDecodeError, KeyError, ValueError):
            return self._fallback("Invalid JSON dari LLM")

        # Step 5: Record usage
        await self._budget.record_usage(response.cost_usd)

        return LLMScore(
            confidence_multiplier=multiplier,
            proceed=data.get("proceed", multiplier >= 0.5),
            reasoning=data.get("reasoning", ""),
            concerns=data.get("concerns", []),
            model_used=response.model,
            cost_usd=response.cost_usd,
            latency_ms=response.latency_ms,
            fallback_used=False,
        )

    async def apply_llm_filter(self, signal: Any, state: Any, last_trades: list[dict[str, Any]]) -> Any | None:
        """Helper flow wrapper untuk main_loop"""
        if not getattr(self._config, "llm_enabled", False):
            return signal 

        score = await self.score_signal(signal, state, last_trades)

        signal.final_confidence = min(
            getattr(signal, 'final_confidence', 0.5) * score.confidence_multiplier, 1.0
        )
        
        if not hasattr(signal, 'metadata'):
            signal.metadata = {}
            
        signal.metadata['llm_score'] = {
            'multiplier': score.confidence_multiplier,
            'reasoning': score.reasoning,
            'fallback': score.fallback_used,
        }

        if signal.final_confidence < getattr(self._config, "min_signal_confidence", 0.60) or not score.proceed:
            log.info(f"Signal {signal.symbol} ditolak LLM. Confidence {signal.final_confidence:.2f} < Minimum.")
            return None

        return signal
