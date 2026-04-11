"""
news_layer/sentiment.py — Gemini-Powered Sentiment Analyzer
Takes raw headlines from aggregator.py and distills them into a single
sentiment score via Google Gemini Flash.

Architecture rationale:
- Gemini Flash has a 1M token context window — perfect for ingesting
  dozens of headlines in a single call without truncation.
- Cost: ~$0.075 per 1M input tokens — negligible at our volume.
- Output is constrained to a tiny JSON object (~30 tokens).
- Full fallback: if Gemini is unreachable, returns NEUTRAL (0.0).
"""

import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone

import aiohttp

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data Models
# ---------------------------------------------------------------------------


@dataclass
class SentimentResult:
    score: float  # -1.0 (extreme bearish) to +1.0 (extreme bullish)
    confidence: float  # 0.0 to 1.0
    summary: str  # 1-sentence Gemini summary
    headline_count: int  # how many headlines were analyzed
    fear_greed_value: int  # 0-100 from Alternative.me
    model_used: str
    latency_ms: float
    cost_usd: float
    is_fallback: bool  # True if Gemini failed and we defaulted to neutral

    @property
    def is_bearish(self) -> bool:
        return self.score < -0.3

    @property
    def is_bullish(self) -> bool:
        return self.score > 0.3

    @property
    def is_extreme(self) -> bool:
        return abs(self.score) > 0.7


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------


SYSTEM_PROMPT = """You are a quantitative crypto market sentiment analyst.
Analyze the provided crypto news headlines and Fear & Greed data.
Respond ONLY in this exact JSON format — no prose, no markdown:
{
  "score": <float -1.0 to 1.0>,
  "confidence": <float 0.0 to 1.0>,
  "summary": "<one sentence>"
}

Scoring guide:
 +1.0 = extreme bullish (institutional accumulation, major adoption)
 +0.5 = moderately bullish
  0.0 = neutral / mixed signals
 -0.5 = moderately bearish
 -1.0 = extreme bearish (exchange collapse, regulatory ban, black swan)
"""


# ---------------------------------------------------------------------------
# Sentiment Engine
# ---------------------------------------------------------------------------


class SentimentAnalyzer:
    """Converts raw news data into a quantitative sentiment signal.

    Design principles (institutional grade):
    - Gemini Flash is chosen for its massive context window and low cost.
    - Output is strictly JSON-constrained (temperature=0.1, max_tokens=100).
    - Full graceful degradation: on any failure, returns score=0.0 (NEUTRAL).
    - Results are cached with configurable TTL (default 30min).
    - Never blocks the main trading loop — strict 12s timeout.
    """

    GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    PRICING_PER_M = {"input": 0.075, "output": 0.30}  # Gemini Flash pricing

    def __init__(
        self,
        gemini_api_key: str | None = None,
        model: str = "gemini-1.5-flash",
        cache_ttl_s: int = 1800,  # 30 minutes
        request_timeout_s: int = 12,
    ) -> None:
        self._api_key = gemini_api_key or os.environ.get("GEMINI_API_KEY", "")
        self._model = model
        self._cache_ttl_s = cache_ttl_s
        self._timeout = aiohttp.ClientTimeout(total=request_timeout_s)

        # Cache
        self._last_result: SentimentResult | None = None
        self._last_ts: float = 0.0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def analyze(
        self,
        headlines_text: str,
        fear_greed_value: int = 50,
        force_refresh: bool = False,
    ) -> SentimentResult:
        """Analyze headlines and return a sentiment score.

        Args:
            headlines_text: Concatenated headline titles from aggregator.
            fear_greed_value: Current F&G index (0-100).
            force_refresh: If True, bypass cache.
        """
        # Check cache
        if not force_refresh and self._is_cache_fresh():
            return self._last_result  # type: ignore[return-value]

        # Count headlines for metadata
        headline_count = headlines_text.count("\n") + 1 if headlines_text.strip() else 0

        # Fallback if no API key
        if not self._api_key:
            log.warning("GEMINI_API_KEY not set — sentiment analyzer in MOCK MODE.")
            return self._fallback("No API key", headline_count, fear_greed_value)

        # Build user message
        user_msg = self._build_prompt(headlines_text, fear_greed_value)

        try:
            start = time.time()
            result_json = await asyncio.wait_for(
                self._call_gemini(user_msg),
                timeout=self._timeout.total,
            )
            latency = (time.time() - start) * 1000

            # Parse Gemini response
            data = json.loads(result_json)
            score = max(-1.0, min(1.0, float(data["score"])))
            confidence = max(0.0, min(1.0, float(data.get("confidence", 0.5))))
            summary = data.get("summary", "")

            # Estimate cost (very rough — Gemini doesn't return exact token counts)
            est_input_tokens = len(user_msg) // 4  # ~4 chars per token
            est_output_tokens = len(result_json) // 4
            cost = (
                est_input_tokens * self.PRICING_PER_M["input"]
                + est_output_tokens * self.PRICING_PER_M["output"]
            ) / 1_000_000

            result = SentimentResult(
                score=score,
                confidence=confidence,
                summary=summary,
                headline_count=headline_count,
                fear_greed_value=fear_greed_value,
                model_used=self._model,
                latency_ms=latency,
                cost_usd=cost,
                is_fallback=False,
            )

            self._last_result = result
            self._last_ts = time.monotonic()

            log.info(
                "Sentiment score=%.2f confidence=%.2f (%d headlines, F&G=%d) [%.0fms]",
                score,
                confidence,
                headline_count,
                fear_greed_value,
                latency,
            )
            return result

        except json.JSONDecodeError:
            log.warning("Gemini returned invalid JSON — falling back to neutral.")
            return self._fallback("Invalid JSON from Gemini", headline_count, fear_greed_value)
        except asyncio.TimeoutError:
            log.warning("Gemini request timed out — falling back to neutral.")
            return self._fallback("Timeout", headline_count, fear_greed_value)
        except Exception as exc:
            log.warning("Gemini sentiment error: %s — falling back to neutral.", exc)
            return self._fallback(str(exc), headline_count, fear_greed_value)

    def get_cached_score(self) -> float:
        """Return last computed sentiment score, or 0.0 if none."""
        return self._last_result.score if self._last_result else 0.0

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _build_prompt(self, headlines_text: str, fear_greed_value: int) -> str:
        return (
            f"CRYPTO NEWS HEADLINES (last few hours):\n"
            f"{headlines_text}\n\n"
            f"FEAR & GREED INDEX: {fear_greed_value}/100\n\n"
            f"Based on the data above, provide your JSON sentiment assessment."
        )

    async def _call_gemini(self, user_message: str) -> str:
        """Make a single Gemini API call and return the text response."""
        url = self.GEMINI_URL.format(model=self._model)
        params = {"key": self._api_key}

        payload = {
            "contents": [{"parts": [{"text": user_message}]}],
            "systemInstruction": {"parts": [{"text": SYSTEM_PROMPT}]},
            "generationConfig": {
                "temperature": 0.1,
                "maxOutputTokens": 100,
                "responseMimeType": "application/json",
            },
        }

        async with aiohttp.ClientSession(timeout=self._timeout) as session:
            async with session.post(url, params=params, json=payload) as resp:
                if resp.status != 200:
                    err = await resp.text()
                    raise RuntimeError(f"Gemini API error {resp.status}: {err}")
                data = await resp.json()

        # Extract text from Gemini response structure
        candidates = data.get("candidates", [])
        if not candidates:
            raise RuntimeError("Gemini returned no candidates")

        parts = candidates[0].get("content", {}).get("parts", [])
        if not parts:
            raise RuntimeError("Gemini returned empty parts")

        return parts[0].get("text", "{}")

    def _fallback(self, reason: str, headline_count: int, fg_value: int) -> SentimentResult:
        """Return a neutral fallback result — never crash the trading pipeline."""
        log.info("Sentiment fallback: %s", reason)
        return SentimentResult(
            score=0.0,
            confidence=0.0,
            summary=f"Fallback: {reason}",
            headline_count=headline_count,
            fear_greed_value=fg_value,
            model_used="fallback",
            latency_ms=0.0,
            cost_usd=0.0,
            is_fallback=True,
        )

    def _is_cache_fresh(self) -> bool:
        if self._last_ts == 0.0 or self._last_result is None:
            return False
        return (time.monotonic() - self._last_ts) < self._cache_ttl_s
