"""
Konektor Anthropic API untuk LLM Layer.
Dilengkapi timeout ketat agar tidak memblokir main loop (asycnio).
"""

import asyncio
import logging
import os
import time
from dataclasses import dataclass

import aiohttp

log = logging.getLogger(__name__)


@dataclass
class LLMRequest:
    system_prompt: str
    user_message: str
    model: str = "claude-3-5-haiku-20241022"
    max_tokens: int = 512
    temperature: float = 0.1  # rendah untuk konsistensi
    timeout_s: int = 10  # timeout ketat


@dataclass
class LLMResponse:
    content: str
    input_tokens: int
    output_tokens: int
    model: str
    latency_ms: float
    cost_usd: float
    success: bool
    error: str = ""


class LLMClient:
    # Estimate prices per 1M tokens as of late 2024
    PRICING = {
        "claude-3-5-haiku-20241022": {"input": 0.25, "output": 1.25},
        "claude-3-5-sonnet-20241022": {"input": 3.00, "output": 15.00},
        "claude-3-opus-20240229": {"input": 15.00, "output": 75.00},
    }

    def __init__(self):
        self.api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        self.base_url = "https://api.anthropic.com/v1/messages"

        if not self.api_key:
            log.warning("ANTHROPIC_API_KEY kosong. LLMClient akan berjalan dalam MOCK MODE.")

    def _compute_cost(self, usage: dict, model: str) -> float:
        pricing = self.PRICING.get(model, {"input": 1.0, "output": 1.0})
        in_t = usage.get("input_tokens", 0)
        out_t = usage.get("output_tokens", 0)
        return (in_t * pricing["input"] + out_t * pricing["output"]) / 1_000_000

    async def _api_call(self, request: LLMRequest) -> dict:
        if not self.api_key:
            # MOCK MODE
            await asyncio.sleep(0.5)
            # Default to "Proceed" mock json
            content = '{"confidence_multiplier": 1.2, "proceed": true, "reasoning": "Mock evaluation approved", "concerns": []}'
            return {
                "content": [{"text": content}],
                "usage": {"input_tokens": 150, "output_tokens": 50},
            }

        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        payload = {
            "model": request.model,
            "max_tokens": request.max_tokens,
            "system": request.system_prompt,
            "messages": [{"role": "user", "content": request.user_message}],
            "temperature": request.temperature,
        }

        async with aiohttp.ClientSession() as session:
            async with session.post(self.base_url, headers=headers, json=payload) as resp:
                if resp.status != 200:
                    err_txt = await resp.text()
                    raise RuntimeError(f"Anthropic API Error {resp.status}: {err_txt}")
                return await resp.json()

    async def call(self, request: LLMRequest) -> LLMResponse:
        try:
            start = time.time()
            resp = await asyncio.wait_for(self._api_call(request), timeout=request.timeout_s)

            content = resp["content"][0]["text"]
            usage = resp.get("usage", {"input_tokens": 0, "output_tokens": 0})

            return LLMResponse(
                content=content,
                input_tokens=usage["input_tokens"],
                output_tokens=usage["output_tokens"],
                model=request.model,
                latency_ms=(time.time() - start) * 1000,
                cost_usd=self._compute_cost(usage, request.model),
                success=True,
            )

        except asyncio.TimeoutError:
            return LLMResponse(
                content="",
                input_tokens=0,
                output_tokens=0,
                model=request.model,
                latency_ms=request.timeout_s * 1000,
                cost_usd=0.0,
                success=False,
                error="Timeout 10s",
            )

        except Exception as e:
            return LLMResponse(
                content="",
                input_tokens=0,
                output_tokens=0,
                model=request.model,
                latency_ms=0,
                cost_usd=0.0,
                success=False,
                error=str(e),
            )
