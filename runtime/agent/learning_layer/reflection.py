"""
reflection.py — Analisis Kualitatif
Menganalisis trade yang sudah ditutup untuk mengidentifikasi 
pola keberhasilan dan kegagalan secara kualitatif.
"""

from dataclasses import dataclass
from typing import Any

from runtime.agent.learning_layer.experience_processor import ExperienceProcessor


@dataclass
class TradeReflection:
    trade_id: str
    verdict: str  # 'good' | 'acceptable' | 'bad' | 'terrible'
    pnl_r: float  # PnL dalam unit risk (1R = entry ke SL)
    positives: list[str]
    negatives: list[str]
    process_followed: bool
    lessons: list[str]
    regime_at_entry: str
    confidence: float
    hold_candles: int
    exit_reason: str


@dataclass
class BatchReflection:
    strategy_id: str
    summary_lessons: list[str]
    repeated_mistakes: list[str]
    recommendation_tags: list[str]


class ReflectionEngine:
    def __init__(self, processor: ExperienceProcessor):
        self._processor = processor

    def reflect_trade(self, row: dict[str, Any]) -> TradeReflection:
        """
        Menilai trade tunggal menjadi verdict bedasarkan R-multiples:
        PnL >= 2R -> good
        PnL >= 0 atau loss < -0.5R -> acceptable
        PnL dari -0.5R sampai -1.0R -> bad
        PnL < -1.0R -> terrible
        """
        trade_id = row["trade_id"]
        pnl = row["pnl_usd"]
        risk = row["risk_amount_usd"]

        pnl_r = pnl / max(0.0001, risk)
        verdict = "acceptable"

        if pnl_r >= 2.0:
            verdict = "good"
        elif pnl_r >= 0 or -0.5 < pnl_r <= 0:
            verdict = "acceptable"
        elif -1.0 < pnl_r <= -0.5:
            verdict = "bad"
        else:
            verdict = "terrible"

        positives = []
        negatives = []
        lessons = []
        process_followed = True

        if verdict == "terrible":
            process_followed = False
            negatives.append("SL tidak dihormati atau sizing melebihi batas")
            lessons.append("Tegakkan strict SL")

        if row["hold_candles"] < 3 and pnl > 0:
            negatives.append("Keluar posisi terlalu cepat (prematur profit taking)")
            lessons.append("Geser trailing mult untuk memberi ruang napas")

        if pnl_r > 3.0:
            positives.append("Excellent trend capture")

        return TradeReflection(
            trade_id=trade_id,
            verdict=verdict,
            pnl_r=pnl_r,
            positives=positives,
            negatives=negatives,
            process_followed=process_followed,
            lessons=lessons,
            regime_at_entry=row["regime_at_entry"],
            confidence=row["confidence_at_entry"],
            hold_candles=row["hold_candles"],
            exit_reason=row["exit_reason"],
        )

    async def reflect_batch(self, strategy_id: str) -> BatchReflection:
        """
        Analisa pattern harian untuk melihat pola kesalahan jamak.
        """
        assert self._processor._db.exp_conn is not None

        cursor = await self._processor._db.exp_conn.execute(
            "SELECT * FROM closed_trades WHERE strategy_id = ? ORDER BY exit_time DESC LIMIT 50",
            (strategy_id,),
        )
        rows = await cursor.fetchall()

        reflections = [self.reflect_trade(dict(r)) for r in rows]

        # Contoh pattern matching sederhana
        repeated = []
        tags = []

        # Cek sideways loss
        sideways_loss = sum(
            1 for r in reflections if r.regime_at_entry == "sideways" and r.pnl_r < 0
        )
        if sideways_loss >= 3:
            repeated.append("Loss berulang di regime SIDEWAYS")
            tags.append("ADD_REGIME_FILTER_SIDEWAYS")

        # Cek trailing ketat
        trail_premature = sum(
            1 for r in reflections if r.exit_reason == "TRAIL" and r.pnl_r < 1.0 and r.pnl_r > 0
        )
        if trail_premature >= 5:
            repeated.append("Profit tergerus oleh trailing terlalu ketat")
            tags.append("INCREASE_TRAIL_MULT")

        return BatchReflection(
            strategy_id=strategy_id,
            summary_lessons=["Review completed"],
            repeated_mistakes=repeated,
            recommendation_tags=tags,
        )
