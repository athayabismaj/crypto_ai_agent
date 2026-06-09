"""
Unit tests for news_layer — aggregator, sentiment, and calendar.
All external API calls are mocked. No network required.
"""

import time
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from runtime.agent.news_layer.aggregator import (
    FearGreedReading,
    Headline,
    HeadlineImpact,
    HeadlineSource,
    NewsAggregator,
)
from runtime.agent.news_layer.calendar import (
    EconomicCalendar,
    EconomicEvent,
    EventImpact,
)
from runtime.agent.news_layer.sentiment import (
    SentimentAnalyzer,
    SentimentResult,
)

# =====================================================================
# AGGREGATOR TESTS
# =====================================================================


class TestNewsAggregator:
    def test_headline_model(self):
        h = Headline(
            title="Bitcoin surges past 100k",
            source=HeadlineSource.CRYPTOPANIC,
            published_at=datetime.now(timezone.utc),
            currencies=["BTC"],
            impact=HeadlineImpact.HIGH,
            votes_positive=10,
            votes_negative=2,
            votes_important=5,
        )
        assert h.title == "Bitcoin surges past 100k"
        assert h.impact == HeadlineImpact.HIGH
        assert "BTC" in h.currencies

    def test_fear_greed_model(self):
        fg = FearGreedReading(
            value=25,
            classification="Extreme Fear",
            timestamp=datetime.now(timezone.utc),
        )
        assert fg.value == 25
        assert fg.classification == "Extreme Fear"

    def test_aggregator_no_api_key(self):
        agg = NewsAggregator(cryptopanic_api_key="")
        assert agg._cp_key == ""

    def test_headline_summary_empty(self):
        agg = NewsAggregator()
        assert "No recent" in agg.headline_summary()

    def test_headline_summary_with_data(self):
        agg = NewsAggregator()
        agg._headlines = [
            Headline(
                title="BTC hits ATH",
                source=HeadlineSource.CRYPTOPANIC,
                published_at=datetime.now(timezone.utc),
            ),
            Headline(
                title="ETH upgrade success",
                source=HeadlineSource.CRYPTOPANIC,
                published_at=datetime.now(timezone.utc),
            ),
        ]
        summary = agg.headline_summary()
        assert "BTC hits ATH" in summary
        assert "ETH upgrade success" in summary

    def test_fear_greed_score_default(self):
        agg = NewsAggregator()
        assert agg.fear_greed_score() == 50  # neutral default

    def test_fear_greed_score_cached(self):
        agg = NewsAggregator()
        agg._fear_greed = FearGreedReading(
            value=15,
            classification="Extreme Fear",
            timestamp=datetime.now(timezone.utc),
        )
        assert agg.fear_greed_score() == 15

    def test_cache_freshness_logic(self):
        agg = NewsAggregator(cache_ttl_s=60)
        assert not agg._is_cache_fresh(0.0)
        assert agg._is_cache_fresh(time.monotonic())
        assert not agg._is_cache_fresh(time.monotonic() - 120)

    @pytest.mark.asyncio
    async def test_fetch_headlines_no_key(self):
        agg = NewsAggregator(cryptopanic_api_key="")
        result = await agg.fetch_headlines()
        assert result == []

    @pytest.mark.asyncio
    async def test_fetch_fear_greed_mock(self):
        agg = NewsAggregator()
        mock_resp = {
            "data": [
                {
                    "value": "72",
                    "value_classification": "Greed",
                    "timestamp": str(int(time.time())),
                    "time_until_update": "3600",
                }
            ]
        }

        with patch("aiohttp.ClientSession") as mock_session_cls:
            mock_resp_obj = AsyncMock()
            mock_resp_obj.status = 200
            mock_resp_obj.json = AsyncMock(return_value=mock_resp)

            mock_ctx = AsyncMock()
            mock_ctx.__aenter__ = AsyncMock(return_value=mock_resp_obj)
            mock_ctx.__aexit__ = AsyncMock(return_value=False)

            mock_session = AsyncMock()
            mock_session.get = MagicMock(return_value=mock_ctx)

            mock_session_ctx = AsyncMock()
            mock_session_ctx.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session_ctx.__aexit__ = AsyncMock(return_value=False)

            mock_session_cls.return_value = mock_session_ctx

            result = await agg.fetch_fear_greed()

        assert result is not None
        assert result.value == 72
        assert result.classification == "Greed"


# =====================================================================
# CALENDAR TESTS
# =====================================================================


class TestEconomicCalendar:
    def test_event_model(self):
        ev = EconomicEvent(
            name="CPI Release",
            country="US",
            scheduled_at=datetime.now(timezone.utc),
            impact=EventImpact.HIGH,
            forecast="3.2%",
            previous="3.4%",
        )
        assert ev.name == "CPI Release"
        assert ev.impact == EventImpact.HIGH

    def test_no_events_no_freeze(self):
        cal = EconomicCalendar()
        status = cal.get_status()
        assert not status.is_frozen
        assert status.freeze_reason == ""
        assert status.events_today == 0

    def test_pre_event_freeze(self):
        cal = EconomicCalendar(freeze_minutes_before=30, thaw_minutes_after=30)
        # Inject a HIGH event 15 minutes from now
        future = datetime.now(timezone.utc) + timedelta(minutes=15)
        cal._events = [
            EconomicEvent(
                name="FOMC Rate Decision",
                country="US",
                scheduled_at=future,
                impact=EventImpact.HIGH,
            )
        ]
        status = cal.get_status()
        assert status.is_frozen is True
        assert "PRE-EVENT FREEZE" in status.freeze_reason
        assert "FOMC" in status.freeze_reason

    def test_post_event_freeze(self):
        cal = EconomicCalendar(freeze_minutes_before=30, thaw_minutes_after=30)
        # Inject a HIGH event 10 minutes ago
        past = datetime.now(timezone.utc) - timedelta(minutes=10)
        cal._events = [
            EconomicEvent(
                name="Non-Farm Payrolls",
                country="US",
                scheduled_at=past,
                impact=EventImpact.HIGH,
            )
        ]
        status = cal.get_status()
        assert status.is_frozen is True
        assert "POST-EVENT FREEZE" in status.freeze_reason

    def test_no_freeze_after_thaw_period(self):
        cal = EconomicCalendar(freeze_minutes_before=30, thaw_minutes_after=30)
        # Event was 60 minutes ago — well past the thaw window
        old = datetime.now(timezone.utc) - timedelta(minutes=60)
        cal._events = [
            EconomicEvent(
                name="GDP Release",
                country="US",
                scheduled_at=old,
                impact=EventImpact.HIGH,
            )
        ]
        status = cal.get_status()
        assert status.is_frozen is False

    def test_low_impact_no_freeze(self):
        cal = EconomicCalendar(freeze_minutes_before=30, thaw_minutes_after=30)
        future = datetime.now(timezone.utc) + timedelta(minutes=5)
        cal._events = [
            EconomicEvent(
                name="Redbook Index",
                country="US",
                scheduled_at=future,
                impact=EventImpact.LOW,
            )
        ]
        status = cal.get_status()
        assert status.is_frozen is False  # LOW impact = no freeze

    def test_should_freeze_convenience(self):
        cal = EconomicCalendar(freeze_minutes_before=30, thaw_minutes_after=30)
        future = datetime.now(timezone.utc) + timedelta(minutes=10)
        cal._events = [
            EconomicEvent(
                name="CPI",
                country="US",
                scheduled_at=future,
                impact=EventImpact.HIGH,
            )
        ]
        frozen, reason = cal.should_freeze()
        assert frozen is True
        assert "CPI" in reason

    @pytest.mark.asyncio
    async def test_fetch_today_no_key(self):
        cal = EconomicCalendar(fmp_api_key="")
        result = await cal.fetch_today()
        assert result == []


# =====================================================================
# SENTIMENT TESTS
# =====================================================================


class TestSentimentAnalyzer:
    def test_result_properties(self):
        r = SentimentResult(
            score=-0.8,
            confidence=0.9,
            summary="Market crash",
            headline_count=10,
            fear_greed_value=15,
            model_used="gemini-1.5-flash",
            latency_ms=200.0,
            cost_usd=0.001,
            is_fallback=False,
        )
        assert r.is_bearish is True
        assert r.is_bullish is False
        assert r.is_extreme is True

    def test_bullish_result(self):
        r = SentimentResult(
            score=0.6,
            confidence=0.8,
            summary="ETF approved",
            headline_count=5,
            fear_greed_value=80,
            model_used="gemini-1.5-flash",
            latency_ms=150.0,
            cost_usd=0.001,
            is_fallback=False,
        )
        assert r.is_bullish is True
        assert r.is_bearish is False
        assert r.is_extreme is False

    def test_neutral_result_not_extreme(self):
        r = SentimentResult(
            score=0.0,
            confidence=0.5,
            summary="Mixed",
            headline_count=3,
            fear_greed_value=50,
            model_used="fallback",
            latency_ms=0.0,
            cost_usd=0.0,
            is_fallback=True,
        )
        assert r.is_bearish is False
        assert r.is_bullish is False
        assert r.is_extreme is False

    @pytest.mark.asyncio
    async def test_fallback_no_api_key(self):
        sa = SentimentAnalyzer(gemini_api_key="")
        result = await sa.analyze("BTC surges", fear_greed_value=70)
        assert result.is_fallback is True
        assert result.score == 0.0
        assert result.model_used == "fallback"

    def test_cached_score_default(self):
        sa = SentimentAnalyzer()
        assert sa.get_cached_score() == 0.0

    def test_cached_score_after_set(self):
        sa = SentimentAnalyzer()
        sa._last_result = SentimentResult(
            score=0.75,
            confidence=0.9,
            summary="Bull run",
            headline_count=10,
            fear_greed_value=85,
            model_used="gemini-1.5-flash",
            latency_ms=100.0,
            cost_usd=0.001,
            is_fallback=False,
        )
        assert sa.get_cached_score() == 0.75

    def test_prompt_builder(self):
        sa = SentimentAnalyzer()
        prompt = sa._build_prompt("- BTC hits 100k\n- ETH upgrade", 72)
        assert "BTC hits 100k" in prompt
        assert "72" in prompt
        assert "FEAR & GREED" in prompt
