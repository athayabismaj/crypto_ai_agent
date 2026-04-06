"""
trade_analyzer.py — Analisis Trade Mendalam
Analisis mendalam trade signifikan (|pnl| > 2× risk) 
dan kondisi market tidak biasa.
"""

import json
import logging
from dataclasses import dataclass
from typing import Any

from runtime.agent.llm_layer.llm_budget import LLMBudget
from runtime.agent.llm_layer.llm_client import LLMClient, LLMRequest

log = logging.getLogger(__name__)


@dataclass
class TradeAnalysis:
    trade_id: str
    timing: str
    sl_assessment: str
    lesson: str
    model_used: str
    cost_usd: float


SYSTEM_PROMPT = """
Berikan analisa tentang eksekusi trading ini.
Jawab HANYA dalam format JSON seperti ini:
{
  "timing": "<analisa timing entry/exit singkat>",
  "sl_assessment": "<analisa penempatan stop loss>",
  "lesson": "<pelajaran utama dari trade ini>"
}
"""


class TradeAnalyzer:
    def __init__(self, client: LLMClient, budget: LLMBudget):
        self._client = client
        self._budget = budget

    async def analyze_closed_trade(self, trade: Any) -> TradeAnalysis | None:
        """Hanya untuk trade dengan |pnl| > 2x risk_amount."""
        
        # Guard clause
        risk = getattr(trade, "risk_amount_usd", 1.0)
        pnl = getattr(trade, "pnl_usd", 0.0)
        
        if abs(pnl) < risk * 2:
             return None
             
        if not await self._budget.can_call(estimated_tokens=500, model="claude-3-5-sonnet-20241022"):
            log.warning("Cost budget limits reached. Skipping trade analysis.")
            return None
            
        msg = f"TRADE_ID: {trade.trade_id}\n"
        msg += f"SYMBOL: {trade.symbol} | SIDE: {trade.side}\n"
        msg += f"PnL: ${pnl:.2f}\n"
        msg += f"Regime: {trade.regime_at_entry} | Hold: {trade.hold_candles} candles\n"
        msg += f"Exit Reason: {trade.exit_reason}\n"

        req = LLMRequest(
            system_prompt=SYSTEM_PROMPT,
            user_message=msg,
            model="claude-3-5-sonnet-20241022",
            max_tokens=256,
            temperature=0.3,
            timeout_s=15, # Boleh agak lama, bukan main tick
        )
        
        resp = await self._client.call(req)
        if not resp.success:
            log.warning(f"Trade Analysis Gagal: {resp.error}")
            return None
            
        try:
            data = json.loads(resp.content)
            await self._budget.record_usage(resp.cost_usd)
            return TradeAnalysis(
                trade_id=trade.trade_id,
                timing=data.get("timing", ""),
                sl_assessment=data.get("sl_assessment", ""),
                lesson=data.get("lesson", ""),
                model_used=resp.model,
                cost_usd=resp.cost_usd
            )
        except Exception as e:
            log.error(f"Gagal memparsing trade_analyzer json: {e}")
            return None

    async def analyze_unusual_market(self, state: Any, anomalies: list[str]) -> str:
        """Dipanggil saat anomaly_detector mendeteksi MEDIUM anomali."""
        # Optional implementation for now
        return ""
