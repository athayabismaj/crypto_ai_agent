"""
news_layer/aggregator.py — Crypto News & Sentiment Aggregator
Pulls headlines from CryptoPanic and Fear & Greed Index from Alternative.me.
Both sources are free. CryptoPanic requires an API key; F&G does not.

This module is a PURE DATA COLLECTOR. It does NOT interpret sentiment —
that responsibility belongs to sentiment.py (Gemini).
"""

import asyncio
import logging
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum

import aiohttp

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data Models
# ---------------------------------------------------------------------------


class HeadlineSource(str, Enum):
    CRYPTOPANIC = "cryptopanic"
    MANUAL = "manual"


class HeadlineImpact(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    UNKNOWN = "unknown"


@dataclass
class Headline:
    title: str
    source: HeadlineSource
    published_at: datetime
    url: str = ""
    currencies: list[str] = field(default_factory=list)
    impact: HeadlineImpact = HeadlineImpact.UNKNOWN
    votes_positive: int = 0
    votes_negative: int = 0
    votes_important: int = 0


@dataclass
class FearGreedReading:
    value: int  # 0-100
    classification: str  # "Extreme Fear", "Fear", "Neutral", "Greed", "Extreme Greed"
    timestamp: datetime
    time_until_update: int = 0  # seconds


# ---------------------------------------------------------------------------
# Aggregator
# ---------------------------------------------------------------------------


class NewsAggregator:
    """Collects crypto headlines and macro sentiment from free public APIs.

    Design principles (institutional grade):
    - All network calls have strict timeouts (8s) to never block the main loop.
    - Results are cached in-memory with configurable TTL to avoid API spam.
    - If any API fails, the system degrades gracefully — stale data is returned
      with a freshness flag so downstream consumers can decide whether to trust it.
    - No LLM calls happen here. Raw data only.
    """

    CRYPTOPANIC_URL = "https://cryptopanic.com/api/v1/posts/"
    FEAR_GREED_URL = "https://api.alternative.me/fng/"

    def __init__(
        self,
        cryptopanic_api_key: str | None = None,
        cache_ttl_s: int = 900,  # 15 minutes default
        request_timeout_s: int = 8,
    ) -> None:
        self._cp_key = cryptopanic_api_key or os.environ.get("CRYPTOPANIC_API_KEY", "")
        self._cache_ttl_s = cache_ttl_s
        self._timeout = aiohttp.ClientTimeout(total=request_timeout_s)

        # In-memory caches
        self._headlines: list[Headline] = []
        self._headlines_ts: float = 0.0

        self._fear_greed: FearGreedReading | None = None
        self._fear_greed_ts: float = 0.0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def fetch_headlines(self, limit: int = 30) -> list[Headline]:
        """Fetch latest crypto headlines from CryptoPanic.

        Returns cached results if still fresh. Falls back to stale cache
        on network failure — never raises to caller.
        """
        if self._is_cache_fresh(self._headlines_ts):
            return self._headlines

        if not self._cp_key:
            log.warning("CRYPTOPANIC_API_KEY not set — headline feed disabled.")
            return self._headlines  # return stale or empty

        try:
            params = {
                "auth_token": self._cp_key,
                "kind": "news",
                "filter": "important",
                "public": "true",
            }
            async with aiohttp.ClientSession(timeout=self._timeout) as session:
                async with session.get(self.CRYPTOPANIC_URL, params=params) as resp:
                    if resp.status != 200:
                        log.warning("CryptoPanic API returned %d", resp.status)
                        return self._headlines
                    data = await resp.json()

            results = data.get("results", [])[:limit]
            headlines: list[Headline] = []

            for item in results:
                votes = item.get("votes", {})
                currencies = [c.get("code", "") for c in item.get("currencies", [])]
                pub_str = item.get("published_at", "")
                try:
                    pub_dt = datetime.fromisoformat(pub_str.replace("Z", "+00:00"))
                except (ValueError, AttributeError):
                    pub_dt = datetime.now(timezone.utc)

                # Determine impact from community votes
                total_votes = sum(votes.values()) if isinstance(votes, dict) else 0
                important_votes = votes.get("important", 0) if isinstance(votes, dict) else 0
                if important_votes >= 5 or total_votes >= 20:
                    impact = HeadlineImpact.HIGH
                elif important_votes >= 2 or total_votes >= 8:
                    impact = HeadlineImpact.MEDIUM
                else:
                    impact = HeadlineImpact.LOW

                headlines.append(
                    Headline(
                        title=item.get("title", ""),
                        source=HeadlineSource.CRYPTOPANIC,
                        published_at=pub_dt,
                        url=item.get("url", ""),
                        currencies=currencies,
                        impact=impact,
                        votes_positive=votes.get("positive", 0) if isinstance(votes, dict) else 0,
                        votes_negative=votes.get("negative", 0) if isinstance(votes, dict) else 0,
                        votes_important=important_votes,
                    )
                )

            self._headlines = headlines
            self._headlines_ts = time.monotonic()
            log.info("Fetched %d headlines from CryptoPanic.", len(headlines))

        except asyncio.TimeoutError:
            log.warning("CryptoPanic request timed out — using stale cache.")
        except Exception as exc:
            log.warning("CryptoPanic fetch error: %s — using stale cache.", exc)

        return self._headlines

    async def fetch_fear_greed(self) -> FearGreedReading | None:
        """Fetch the current Fear & Greed Index from Alternative.me.

        100% free, no API key required.
        """
        if self._is_cache_fresh(self._fear_greed_ts):
            return self._fear_greed

        try:
            async with aiohttp.ClientSession(timeout=self._timeout) as session:
                async with session.get(self.FEAR_GREED_URL, params={"limit": "1"}) as resp:
                    if resp.status != 200:
                        log.warning("Fear & Greed API returned %d", resp.status)
                        return self._fear_greed
                    data = await resp.json()

            entry = data.get("data", [{}])[0]
            self._fear_greed = FearGreedReading(
                value=int(entry.get("value", 50)),
                classification=entry.get("value_classification", "Neutral"),
                timestamp=datetime.fromtimestamp(
                    int(entry.get("timestamp", time.time())), tz=timezone.utc
                ),
                time_until_update=int(entry.get("time_until_update", 0)),
            )
            self._fear_greed_ts = time.monotonic()
            log.info(
                "Fear & Greed Index: %d (%s)",
                self._fear_greed.value,
                self._fear_greed.classification,
            )

        except asyncio.TimeoutError:
            log.warning("Fear & Greed request timed out — using stale cache.")
        except Exception as exc:
            log.warning("Fear & Greed fetch error: %s — using stale cache.", exc)

        return self._fear_greed

    async def fetch_all(self, headline_limit: int = 30) -> dict:
        """Convenience: fetch headlines + fear/greed in parallel."""
        headlines_task = self.fetch_headlines(limit=headline_limit)
        fg_task = self.fetch_fear_greed()
        headlines, fg = await asyncio.gather(headlines_task, fg_task)
        return {
            "headlines": headlines,
            "fear_greed": fg,
            "headlines_fresh": self._is_cache_fresh(self._headlines_ts),
            "fear_greed_fresh": self._is_cache_fresh(self._fear_greed_ts),
        }

    # ------------------------------------------------------------------
    # Snapshot for downstream consumption
    # ------------------------------------------------------------------

    def headline_summary(self, max_titles: int = 20) -> str:
        """Concatenate headline titles into a single text block for Gemini."""
        if not self._headlines:
            return "No recent crypto headlines available."
        titles = [h.title for h in self._headlines[:max_titles]]
        return "\n".join(f"- {t}" for t in titles)

    def fear_greed_score(self) -> int:
        """Return cached F&G score, or 50 (neutral) if unavailable."""
        return self._fear_greed.value if self._fear_greed else 50

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _is_cache_fresh(self, ts: float) -> bool:
        if ts == 0.0:
            return False
        return (time.monotonic() - ts) < self._cache_ttl_s
