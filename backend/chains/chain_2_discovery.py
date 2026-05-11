"""
Chain 2 — Discovery  (YouTube-only)
====================================
Improvements over previous version:
  FIX  CRITICAL : Channel ID extraction bug fixed. YouTube search.list returns
                  kind=youtube#searchResult, not youtube#video. channelId must be
                  read from item["snippet"]["channelId"] for video results.
  FIX  MISSING  : RawCreatorProfile Pydantic schema added — no more raw dicts.
  FIX  MISSING  : Deduplication by handle (case-insensitive).
  FIX  MISSING  : data_confidence label propagated from YouTube client.
  FIX  MINOR    : batch profile builder used for YouTube (quota savings).
"""

from __future__ import annotations

import asyncio
import logging
import sys
import os
from typing import Literal

from pydantic import BaseModel, Field
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from chain_0_ICP import ICPProfile, Platform
from chain_1_keywordExpansion import ExpandedKeywordSet
from services.platforms.youtube import youtube_search, build_channel_profiles_batch

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────
# OUTPUT SCHEMA  (FIX MISSING: was raw dict)
# ─────────────────────────────────────────────

class RawCreatorProfile(BaseModel):
    """
    Standardised YouTube creator profile produced by Chain 2.
    Every downstream chain reads from this — no raw dict access.

    data_confidence:
      "real"      = sourced from YouTube Data API (exact numbers)
      "estimated" = fallback when API data is incomplete
    """
    # Identity
    handle:          str
    platform:        Literal["youtube"] = "youtube"
    channel_title:   str = ""
    profile_url:     str
    data_confidence: Literal["real", "estimated"]

    # Audience size
    followers:       int | None = None

    # Channel metadata
    channel_id:          str | None = None
    description:         str | None = None
    country:             str | None = None
    published_at:        str | None = None
    channel_age_years:   float | None = None
    topic_categories:    list[str] = Field(default_factory=list)
    channel_keywords:    str | None = None

    # Engagement
    avg_views:           int | None = None
    avg_likes:           int | None = None
    avg_comments:        int | None = None
    median_views:        float | None = None
    median_er:           float | None = None
    engagement_rate:     float | None = None
    engagement_source:   Literal["real", "estimated"] = "estimated"

    # Pre-computed ratios (for Chain 3 hard-drop rules)
    view_to_sub_ratio:     float | None = None
    like_to_comment_ratio: float | None = None

    # Recent video data — used by Chain 4 for niche relevance
    recent_videos:        list[dict] = Field(default_factory=list)
    recent_video_titles:  list[str]  = Field(default_factory=list)

    # Source metadata
    discovery_source: Literal[
        "youtube_video_search",
        "youtube_channel_search",
    ]


# ─────────────────────────────────────────────
# CHANNEL ID EXTRACTION  (FIX CRITICAL BUG)
# ─────────────────────────────────────────────

def extract_channel_id(item: dict) -> str | None:
    """
    FIX CRITICAL: Previous code checked item["id"]["kind"] == "youtube#video"
    which NEVER matches. YouTube search.list always returns kind="youtube#searchResult".

    Correct logic:
      - For video searches: channelId is in item["snippet"]["channelId"]
      - For channel searches: channelId is in item["id"]["channelId"]
      - Kind "youtube#searchResult" means look at item["id"]["kind"] sub-field:
          "youtube#video"   → video result → get channelId from snippet
          "youtube#channel" → channel result → get channelId from id
    """
    id_block = item.get("id", {})
    kind = id_block.get("kind", "")

    if kind == "youtube#channel":
        # Channel search result
        return id_block.get("channelId")

    if kind == "youtube#video":
        # Video search result — channel is in snippet
        return item.get("snippet", {}).get("channelId")

    # Fallback: try both locations
    cid = id_block.get("channelId")
    if cid:
        return cid
    return item.get("snippet", {}).get("channelId")


def _dict_to_profile(raw: dict, source: str) -> RawCreatorProfile | None:
    """Convert a raw dict from youtube.py into a typed RawCreatorProfile."""
    if not raw or not raw.get("handle"):
        return None

    return RawCreatorProfile(
        handle=raw.get("handle", ""),
        platform="youtube",
        channel_title=raw.get("channel_title", ""),
        profile_url=raw.get("profile_url", ""),
        data_confidence=raw.get("data_confidence", "real"),
        followers=raw.get("followers"),
        channel_id=raw.get("channel_id"),
        description=raw.get("description"),
        country=raw.get("country"),
        published_at=raw.get("published_at"),
        channel_age_years=raw.get("channel_age_years"),
        topic_categories=raw.get("topic_categories", []),
        channel_keywords=raw.get("channel_keywords"),
        avg_views=raw.get("avg_views"),
        avg_likes=raw.get("avg_likes"),
        avg_comments=raw.get("avg_comments"),
        median_views=raw.get("median_views"),
        median_er=raw.get("median_er"),
        engagement_rate=raw.get("engagement_rate"),
        engagement_source=raw.get("engagement_source", "estimated"),
        view_to_sub_ratio=raw.get("view_to_sub_ratio"),
        like_to_comment_ratio=raw.get("like_to_comment_ratio"),
        recent_videos=raw.get("recent_videos", []),
        recent_video_titles=raw.get("recent_video_titles", []),
        discovery_source=source,
    )




# ─────────────────────────────────────────────
# YOUTUBE DISCOVERY
# ─────────────────────────────────────────────

async def _discover_youtube(keywords: ExpandedKeywordSet) -> list[RawCreatorProfile]:
    """
    Run all YouTube queries concurrently and extract unique channel profiles.
    Uses batched channel stats fetch (1 API call for all channels).
    """
    logger.info("YouTube discovery: %d queries", len(keywords.youtube_queries))

    # Run all YouTube search queries concurrently
    search_tasks = [
        youtube_search(
            query=q.query,
            max_results=q.max_results,
            search_type=q.search_type,
            region_code=q.region_code,
            relevance_language=q.relevance_language,
        )
        for q in keywords.youtube_queries
    ]
    search_results = await asyncio.gather(*search_tasks, return_exceptions=True)

    # FIX CRITICAL: correct channel ID extraction
    unique_channel_ids: dict[str, str] = {}  # channel_id → source_type
    for q, items in zip(keywords.youtube_queries, search_results):
        if isinstance(items, Exception):
            logger.error("YouTube search failed ('%s'): %s", q.query[:50], items)
            continue
        for item in items:
            cid = extract_channel_id(item)
            if cid and cid not in unique_channel_ids:
                unique_channel_ids[cid] = (
                    "youtube_channel_search"
                    if q.search_type == "channel"
                    else "youtube_video_search"
                )

    logger.info("Discovered %d unique YouTube channel IDs", len(unique_channel_ids))

    if not unique_channel_ids:
        return []

    # Batch-fetch all profiles (FIX: 1 API call instead of N)
    raw_profiles = await build_channel_profiles_batch(list(unique_channel_ids.keys()))

    profiles: list[RawCreatorProfile] = []
    for raw in raw_profiles:
        cid    = raw.get("channel_id", "")
        source = unique_channel_ids.get(cid, "youtube_video_search")
        profile = _dict_to_profile(raw, source)
        if profile:
            profiles.append(profile)

    logger.info("YouTube discovery complete: %d profiles", len(profiles))
    return profiles



# ─────────────────────────────────────────────
# DEDUPLICATION
# ─────────────────────────────────────────────

def _deduplicate(profiles: list[RawCreatorProfile]) -> list[RawCreatorProfile]:
    """
    Deduplicate by handle — case-insensitive.
    Priority: "real" data_confidence wins over "estimated".
    """
    seen: dict[str, RawCreatorProfile] = {}

    for p in profiles:
        key = p.handle.lower().lstrip("@")
        existing = seen.get(key)

        if existing is None:
            seen[key] = p
        elif p.data_confidence == "real" and existing.data_confidence == "estimated":
            # Real data wins over estimated
            seen[key] = p
        # else keep existing

    result = list(seen.values())
    logger.info("Deduplication: %d → %d unique profiles", len(profiles), len(result))
    return result


# ─────────────────────────────────────────────
# MAIN CALLABLE
# ─────────────────────────────────────────────

async def run_discovery(
    icp: ICPProfile,
    keywords: ExpandedKeywordSet,
) -> list[RawCreatorProfile]:
    """
    Chain 2 entry point. Called by pipeline.py after Chain 1 completes.

    Runs YouTube discovery and returns deduplicated RawCreatorProfile list.
    """
    logger.info("Chain 2: YouTube discovery starting")

    yt_profiles = await _discover_youtube(keywords)

    unique_profiles = _deduplicate(yt_profiles)

    logger.info(
        "Chain 2 complete: %d raw → %d unique YouTube profiles",
        len(yt_profiles),
        len(unique_profiles),
    )

    return unique_profiles


# ─────────────────────────────────────────────
# QUICK LOCAL TEST
# ─────────────────────────────────────────────

if __name__ == "__main__":
    import json
    from chain_0_ICP import BrandBrief, CampaignGoal, Platform, FollowerTier, run_icp_chain
    from chain_1_keywordExpansion import run_keyword_expansion

    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    sample_brief = BrandBrief(
        brand_name         = "Dot & Key",
        product_description= "Vitamin C serum for hyperpigmentation targeting Indian women",
        campaign_goal      = CampaignGoal.CONVERSION,
        niche              = "skincare",
        platforms          = [Platform.YOUTUBE],
        follower_tier      = FollowerTier.MICRO,
        target_audience    = "Indian women 22-35, interested in clean beauty",
        audience_location  = "India",
        audience_age_range = "22-35",
        language           = "English and Hindi",
        competitor_brands  = ["Minimalist", "Plum"],
        excluded_niches    = ["adult content", "alcohol"],
    )

    async def main():
        api_key = os.environ.get("GROQ_API_KEY")
        if not api_key:
            print("ERROR: GROQ_API_KEY not found.")
            return

        icp = await run_icp_chain(sample_brief, api_key)
        keywords = run_keyword_expansion(icp)

        # Limit for local test
        keywords.youtube_queries = keywords.youtube_queries[:2]

        candidates = await run_discovery(icp, keywords)

        print(f"\nFound {len(candidates)} unique candidates:")
        for c in candidates:
            followers = f"{c.followers:,}" if c.followers else "unknown"
            print(f"  [{c.platform:9}] [{c.data_confidence:9}] "
                  f"{c.handle:<30} followers={followers}")

    asyncio.run(main())