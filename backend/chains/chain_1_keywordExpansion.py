"""
Chain 1 — Keyword Expansion
============================
Takes the ICPProfile from Chain 0 and formats every keyword bucket into
API-ready search objects for Chain 2 (Discovery).

Why NO LLM here:
  Chain 0 already paid Groq to think. This chain's job is pure formatting
  — turning "whey protein taste test honest" into the exact query object that
  the YouTube Data API v3 and Tavily REST API expect. Deterministic, instant,
  zero API cost.

Output:
  ExpandedKeywordSet — a flat, validated object that Chain 2 consumes directly.
  No ambiguity about what to search or how.
"""

from __future__ import annotations

import logging
import re
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from chain_0_ICP import ICPProfile, Platform

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────
# CONSTANTS
# ─────────────────────────────────────────────

# YouTube Data API v3 quota costs (units per call)
# search.list(type=video)   = 100 units
# channels.list             =   3 units
# videos.list (stats)       =   1 unit per video
YT_SEARCH_QUOTA_COST  = 100
YT_CHANNEL_QUOTA_COST = 3
DAILY_QUOTA_LIMIT     = 10_000   # default Google Cloud quota

# How many YouTube results to pull per query
YT_RESULTS_PER_QUERY = 10

# Recency modifier — added to queries to surface recent content
RECENCY_YEAR = "2025"

# Location modifiers for geo-targeted campaigns
LOCATION_MODIFIERS: dict[str, list[str]] = {
    "India":          ["India", "Indian", "Hindi"],
    "United States":  ["US", "America"],
    "United Kingdom": ["UK", "British"],
    "Australia":      ["Australia", "Aussie"],
}


# ─────────────────────────────────────────────
# OUTPUT SCHEMAS
# ─────────────────────────────────────────────

class YouTubeQuery(BaseModel):
    """
    A single formatted query for the YouTube Data API v3 search.list endpoint.
    Chain 2 iterates this list directly — no further formatting needed.
    """
    query:          str
    search_type:    Literal["video", "channel"] = "video"
    purpose:        Literal["discovery", "competitor", "niche_authority"]
    quota_cost:     int = YT_SEARCH_QUOTA_COST
    max_results:    int = YT_RESULTS_PER_QUERY
    # Optional filters passed to the API
    region_code:    str | None = None   # e.g. "IN" for India
    relevance_language: str | None = None  # e.g. "hi" for Hindi

    @field_validator("query")
    @classmethod
    def query_must_be_specific(cls, v: str) -> str:
        if len(v.split()) < 3:
            raise ValueError(
                f"YouTube query too short ('{v}'). "
                "Queries must be at least 3 words to be topically specific."
            )
        return v.strip()


class TavilyQuery(BaseModel):
    """
    A single formatted query for the Tavily Search API.
    Uses Boolean operators: OR, AND, site:, quotes for exact phrases.
    Chain 2 sends these to Tavily's /search endpoint.
    """
    query:          str
    purpose:        Literal["discovery", "competitor", "audience_intent"]
    search_depth:   Literal["basic", "advanced"] = "advanced"
    max_results:    int = 5
    # Tavily supports include_domains / exclude_domains
    include_domains: list[str] = Field(default_factory=list)
    exclude_domains: list[str] = Field(default_factory=list)


class HashtagSet(BaseModel):
    """Hashtags extracted from ICP for use in discovery queries."""
    raw:        list[str]  # without # — used in query construction


class ExpandedKeywordSet(BaseModel):
    """
    The complete, API-ready keyword set produced by Chain 1.
    Chain 2 reads this object and fires every query.

    Structure:
      youtube_queries       → passed to youtube.py  (Chain 2)
      tavily_discovery      → passed to tavily.py   (Chain 2) for creator discovery
      tavily_competitor     → passed to tavily.py   (Chain 2) for competitor intel
      tavily_audience       → passed to tavily.py   (Chain 2) for audience-intent search
      hashtags              → used in Tavily Instagram Boolean queries
      estimated_quota_cost  → logged before execution so we know if we'll hit limits
    """
    youtube_queries:        list[YouTubeQuery]
    tavily_discovery:       list[TavilyQuery]
    tavily_competitor:      list[TavilyQuery]
    tavily_audience:        list[TavilyQuery]
    hashtags:               HashtagSet

    # Metadata
    total_youtube_queries:      int
    estimated_quota_cost:       int   # total YouTube API units this run will consume
    quota_budget_remaining_pct: float  # 0–100, so pipeline can warn if >80%

    @property
    def will_exceed_quota(self) -> bool:
        return self.estimated_quota_cost > DAILY_QUOTA_LIMIT

    @property
    def all_tavily_queries(self) -> list[TavilyQuery]:
        return self.tavily_discovery + self.tavily_competitor + self.tavily_audience


# ─────────────────────────────────────────────
# FORMATTERS
# ─────────────────────────────────────────────

class YouTubeQueryFormatter:
    """
    Converts ICP discovery bucket → YouTube API query objects.

    Strategy (matches how real agencies search YouTube):
      1. Search by VIDEO TITLE, not channel — we find creators through their content.
      2. Add a recency signal ("2025") so we surface active creators, not stale ones.
      3. Add location modifier only if the audience is geo-specific.
      4. Add "review | honest | test | routine" angle variants for key queries —
         these are the content patterns that indicate a creator has topical authority.
      5. Add a channel-type search for niche authority (finding established channels).
    """

    CONTENT_ANGLE_SUFFIXES = [
        "honest review",
        "routine",
        "best products",
        "worth it",
        "comparison",
    ]

    def __init__(self, icp: ICPProfile):
        self.icp = icp
        self.location_mod = self._resolve_location_modifier()
        self.region_code  = self._resolve_region_code()
        self.lang_code    = self._resolve_language_code()

    def _resolve_location_modifier(self) -> str | None:
        loc = self.icp.audience.location
        for key, mods in LOCATION_MODIFIERS.items():
            if key.lower() in loc.lower():
                return mods[0]   # e.g. "India"
        return None

    def _resolve_region_code(self) -> str | None:
        mapping = {
            "India": "IN", "United States": "US", "United Kingdom": "GB",
            "Australia": "AU", "Canada": "CA", "Germany": "DE",
        }
        for k, v in mapping.items():
            if k.lower() in self.icp.audience.location.lower():
                return v
        return None

    def _resolve_language_code(self) -> str | None:
        mapping = {"Hindi": "hi", "English": "en", "Spanish": "es",
                   "French": "fr", "German": "de", "Tamil": "ta"}
        for lang, code in mapping.items():
            if lang.lower() in self.icp.audience.language.lower():
                return code
        return "en"

    def _append_location(self, query: str) -> str:
        """Append location modifier only if not already present in the query."""
        if self.location_mod and self.location_mod.lower() not in query.lower():
            return f"{query} {self.location_mod}"
        return query

    def format(self) -> list[YouTubeQuery]:
        queries: list[YouTubeQuery] = []

        # ── 1. Core discovery queries (from ICP bucket, enriched) ──
        for raw_q in self.icp.keyword_buckets.discovery:
            # Add recency only if not already present
            year_suffix = f" {RECENCY_YEAR}" if RECENCY_YEAR not in raw_q else ""
            enriched = f"{raw_q}{year_suffix}"
            enriched = self._append_location(enriched)
            queries.append(YouTubeQuery(
                query=enriched,
                search_type="video",
                purpose="discovery",
                region_code=self.region_code,
                relevance_language=self.lang_code,
            ))

        # ── 2. Niche-authority channel search ──
        # Searches for channels, not videos. Finds established creators
        # who have built an audience around this niche.
        for niche in self.icp.primary_niches[:2]:   # top 2 niches only — quota control
            channel_query = self._append_location(f"{niche} creator channel")
            queries.append(YouTubeQuery(
                query=channel_query,
                search_type="channel",
                purpose="niche_authority",
                region_code=self.region_code,
            ))

        # ── 3. Content angle variants for top query ──
        # Takes the first discovery query and generates angle variants.
        # These catch creators who cover the niche from slightly different angles.
        if self.icp.keyword_buckets.discovery:
            base = self.icp.primary_niches[0] if self.icp.primary_niches else \
                   self.icp.keyword_buckets.discovery[0].split()[0]
            for angle in self.CONTENT_ANGLE_SUFFIXES[:3]:   # top 3 angles
                angle_q = self._append_location(f"{base} {angle} {RECENCY_YEAR}")
                queries.append(YouTubeQuery(
                    query=angle_q,
                    search_type="video",
                    purpose="discovery",
                    region_code=self.region_code,
                    relevance_language=self.lang_code,
                ))

        # ── 4. Competitor ambassador video search ──
        for comp_q in self.icp.keyword_buckets.competitor[:3]:  # cap at 3 — quota
            loc_q = self._append_location(comp_q)
            queries.append(YouTubeQuery(
                query=loc_q,
                search_type="video",
                purpose="competitor",
                region_code=self.region_code,
            ))

        logger.info("YouTubeQueryFormatter produced %d queries (est. %d quota units)",
                    len(queries), len(queries) * YT_SEARCH_QUOTA_COST)
        return queries


class TavilyQueryFormatter:
    """
    Converts ICP keyword buckets → Tavily Boolean search strings.

    Tavily supports:
      - Quoted exact phrases: "gifted by brand"
      - OR: keyword1 OR keyword2
      - site: operator for platform-specific search
      - Domains include/exclude

    Generates platform-agnostic web discovery queries.
    """

    def __init__(self, icp: ICPProfile):
        self.icp = icp

    def _format_discovery_queries(self) -> list[TavilyQuery]:
        """
        Discovery queries: find creator profiles and content
        using Boolean queries. Platform-agnostic web search.
        """
        queries: list[TavilyQuery] = []
        niches = self.icp.primary_niches

        for raw_q in self.icp.keyword_buckets.discovery[:5]:   # top 5
            # General creator discovery (no site: restriction)
            web_query = (
                f'"{raw_q}" '
                f'("collab" OR "gifted" OR "ad" OR "review") '
                f'creator OR influencer'
            )
            queries.append(TavilyQuery(
                query=web_query,
                purpose="discovery",
                max_results=5,
            ))

        # Niche-authority queries: creators who self-identify
        for niche in niches[:2]:
            bio_query = (
                f'"{niche} creator" OR "{niche} influencer" OR "{niche} blogger" '
                f'{self.icp.audience.location}'
            )
            queries.append(TavilyQuery(
                query=bio_query,
                purpose="discovery",
                max_results=8,
            ))

        return queries

    def _format_competitor_queries(self) -> list[TavilyQuery]:
        """
        Competitor queries: find creators who are confirmed ambassadors
        of competing brands. These are 'proven performers' in the vertical.
        """
        queries: list[TavilyQuery] = []

        for comp_q in self.icp.keyword_buckets.competitor:
            # Find confirmed brand deals
            deal_query = (
                f'{comp_q} '
                f'("brand ambassador" OR "brand partner" OR "#gifted" OR "#ad")'
            )
            queries.append(TavilyQuery(
                query=deal_query,
                purpose="competitor",
                max_results=8,
                search_depth="advanced",
            ))

            # Find press / round-up articles that list their ambassadors
            press_query = f'{comp_q} influencer campaign ambassador list'
            queries.append(TavilyQuery(
                query=press_query,
                purpose="competitor",
                max_results=5,
            ))

        return queries

    def _format_audience_intent_queries(self) -> list[TavilyQuery]:
        """
        Audience intent queries: what does the TARGET AUDIENCE search for?
        These find creators who are answering real audience questions —
        the most reliable signal of niche authority and genuine engagement.
        """
        queries: list[TavilyQuery] = []

        for raw_q in self.icp.keyword_buckets.audience_intent:
            # Find who Google thinks answers this query — those are the topical leaders
            queries.append(TavilyQuery(
                query=raw_q,
                purpose="audience_intent",
                max_results=5,
                search_depth="advanced",
            ))

        return queries

    def format_discovery(self) -> list[TavilyQuery]:
        return self._format_discovery_queries()

    def format_competitor(self) -> list[TavilyQuery]:
        return self._format_competitor_queries()

    def format_audience(self) -> list[TavilyQuery]:
        return self._format_audience_intent_queries()


class HashtagFormatter:
    """
    Formats ICP hashtags for use in discovery queries.

    Raw hashtags from ICP look like: "#skincareIndia", "#VitaminCSerum"
    We store them without # for query construction.
    """

    def __init__(self, icp: ICPProfile):
        self.icp = icp

    def _clean(self, tag: str) -> str:
        """Strip # and lowercase for query use."""
        return re.sub(r"^#+", "", tag).strip()

    def format(self) -> HashtagSet:
        raw_tags = [self._clean(t) for t in self.icp.hashtags]
        return HashtagSet(raw=raw_tags)


# ─────────────────────────────────────────────
# QUOTA MANAGEMENT
# ─────────────────────────────────────────────

def estimate_quota_cost(youtube_queries: list[YouTubeQuery], n_candidates: int = 20) -> int:
    """
    Estimates total YouTube API quota units for this run.

    Formula:
      search.list calls × 100
      + channels.list calls × 3  (one per candidate discovered)
      + videos.list calls × 1    (last 10 videos per candidate for engagement data)
    """
    search_cost    = len(youtube_queries) * YT_SEARCH_QUOTA_COST
    channel_cost   = n_candidates * YT_CHANNEL_QUOTA_COST
    video_cost     = n_candidates * 10   # 10 recent videos per candidate
    return search_cost + channel_cost + video_cost


# ─────────────────────────────────────────────
# MAIN CALLABLE
# ─────────────────────────────────────────────

def run_keyword_expansion(icp: ICPProfile) -> ExpandedKeywordSet:
    """
    Chain 1 entry point. Called by pipeline.py after Chain 0 completes.

    Synchronous — no I/O, no LLM call, runs in microseconds.
    Returns ExpandedKeywordSet ready for Chain 2.
    """
    logger.info("Chain 1 starting for niches=%s location=%s",
                icp.primary_niches, icp.audience.location)

    # Run all three formatters
    yt_formatter       = YouTubeQueryFormatter(icp)
    tavily_formatter   = TavilyQueryFormatter(icp)
    hashtag_formatter  = HashtagFormatter(icp)

    youtube_queries    = yt_formatter.format()
    tavily_discovery   = tavily_formatter.format_discovery()
    tavily_competitor  = tavily_formatter.format_competitor()
    tavily_audience    = tavily_formatter.format_audience()
    hashtags           = hashtag_formatter.format()

    # Quota accounting
    est_quota = estimate_quota_cost(youtube_queries, n_candidates=25)
    quota_pct  = (est_quota / DAILY_QUOTA_LIMIT) * 100

    if quota_pct > 80:
        logger.warning(
            "Estimated quota usage %.1f%% of daily limit (%d / %d units). "
            "Consider reducing queries or enabling Redis caching in Chain 2.",
            quota_pct, est_quota, DAILY_QUOTA_LIMIT
        )

    result = ExpandedKeywordSet(
        youtube_queries=youtube_queries,
        tavily_discovery=tavily_discovery,
        tavily_competitor=tavily_competitor,
        tavily_audience=tavily_audience,
        hashtags=hashtags,
        total_youtube_queries=len(youtube_queries),
        estimated_quota_cost=est_quota,
        quota_budget_remaining_pct=round(100 - quota_pct, 1),
    )

    logger.info(
        "Chain 1 complete: %d YT queries, %d Tavily discovery, "
        "%d Tavily competitor, %d Tavily audience. Est. quota: %d units (%.1f%% of daily limit)",
        len(youtube_queries), len(tavily_discovery),
        len(tavily_competitor), len(tavily_audience),
        est_quota, quota_pct,
    )

    return result


# ─────────────────────────────────────────────
# HOW PIPELINE.PY CALLS CHAINS 0 AND 1
# ─────────────────────────────────────────────

async def run_icp_and_keywords(brief, groq_api_key: str) -> tuple[ICPProfile, ExpandedKeywordSet]:
    """
    Convenience wrapper for pipeline.py.
    Chains 0 and 1 always run together — Chain 1 has no meaning without Chain 0's output.

    Usage in pipeline.py:
        icp, keywords = await run_icp_and_keywords(brief, settings.GROQ_API_KEY)
        # Then pass both into Chain 2
        candidates = await run_discovery(icp, keywords)
    """
    from chain_0_ICP import run_icp_chain

    icp      = await run_icp_chain(brief, groq_api_key)
    keywords = run_keyword_expansion(icp)   # sync — no await needed
    return icp, keywords


# ─────────────────────────────────────────────
# QUICK LOCAL TEST
# ─────────────────────────────────────────────

if __name__ == "__main__":
    import asyncio, os, json
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))
    from chain_0_ICP import (
        BrandBrief, CampaignGoal, Platform, FollowerTier
    )

    sample_brief = BrandBrief(
        brand_name         = "Dot & Key",
        product_description= "Vitamin C serum for hyperpigmentation targeting Indian women",
        campaign_goal      = CampaignGoal.CONVERSION,
        niche              = "skincare",
        platforms          = [Platform.YOUTUBE],
        follower_tier      = FollowerTier.MICRO,
        target_audience    = "Indian women 22–35, interested in clean beauty",
        audience_location  = "India",
        audience_age_range = "22–35",
        language           = "English and Hindi",
        competitor_brands  = ["Minimalist", "Plum"],
        excluded_niches    = ["adult content", "alcohol"],
    )

    async def main():
        icp, keywords = await run_icp_and_keywords(sample_brief, os.environ["GROQ_API_KEY"])

        print("\n== YOUTUBE QUERIES ==")
        for i, q in enumerate(keywords.youtube_queries, 1):
            print(f"  {i:02d}. [{q.purpose:15}] [{q.search_type}] {q.query}")

        print("\n== TAVILY DISCOVERY ==")
        for i, q in enumerate(keywords.tavily_discovery, 1):
            print(f"  {i:02d}. {q.query[:90]}")

        print("\n== TAVILY COMPETITOR ==")
        for i, q in enumerate(keywords.tavily_competitor, 1):
            print(f"  {i:02d}. {q.query[:90]}")

        print("\n== HASHTAGS ==")
        print("  ", " ".join(keywords.hashtags.raw[:10]))

        print(f"\n== QUOTA ==")
        print(f"  Estimated cost : {keywords.estimated_quota_cost} units")
        print(f"  Daily budget   : {DAILY_QUOTA_LIMIT} units")
        print(f"  Remaining      : {keywords.quota_budget_remaining_pct}%")
        print(f"  Will exceed?   : {keywords.will_exceed_quota}")

    asyncio.run(main())