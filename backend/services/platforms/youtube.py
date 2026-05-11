"""
youtube.py — YouTube Data API v3 client
========================================
Improvements over previous version:
  FIX  CRITICAL : channels.list now batches up to 50 IDs per call (was 1-per-call)
  FIX  BUG      : avg_views/likes/comments now use MEDIAN not mean (viral outlier resistance)
  FIX  MISSING  : httpx retry with exponential backoff on every API call
  FIX  MISSING  : Semaphore rate-limiter — max 5 concurrent requests to avoid 429s
  FIX  MISSING  : video title included in recent_videos_data for Chain 4 niche relevance
  FIX  MINOR    : API key read at call time via getter function, not at module load
  REMOVED       : discover_youtube() — dead code, bypassed by Chain 2
"""

import asyncio
import logging
import os
import statistics
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# ── Rate limiting: max 5 concurrent YouTube API requests ──────────────────────
_YT_SEMAPHORE = asyncio.Semaphore(5)

# Quota costs (units):
#   search.list        = 100/call  ← expensive
#   channels.list      =   1/call  (batches up to 50 IDs)
#   playlistItems.list =   1/call
#   videos.list        =   1/call  (batches up to 50 IDs)


def _api_key() -> str:
    """Read key at call time so late-loading .env is respected."""
    key = os.getenv("YOUTUBE_API_KEY", "")
    if not key:
        raise ValueError("YOUTUBE_API_KEY is not set in environment.")
    return key


# ─────────────────────────────────────────────
# RETRY HELPER
# ─────────────────────────────────────────────

async def _get_with_retry(
    client: httpx.AsyncClient,
    url: str,
    params: dict,
    max_attempts: int = 3,
) -> dict:
    """
    GET with exponential backoff retry.
    Raises on final failure so callers can handle gracefully.
    """
    for attempt in range(1, max_attempts + 1):
        try:
            async with _YT_SEMAPHORE:
                resp = await client.get(url, params=params, timeout=30)

            if resp.status_code == 429:
                wait = 2 ** attempt
                logger.warning("YouTube 429 (rate limit). Waiting %ds (attempt %d/%d)",
                               wait, attempt, max_attempts)
                await asyncio.sleep(wait)
                continue

            resp.raise_for_status()
            return resp.json()

        except httpx.TimeoutException:
            wait = 2 ** attempt
            logger.warning("YouTube timeout on attempt %d/%d. Retrying in %ds",
                           attempt, max_attempts, wait)
            await asyncio.sleep(wait)

        except httpx.HTTPStatusError as e:
            if e.response.status_code in (400, 403, 404):
                # Non-retryable errors
                logger.error("YouTube HTTP %d: %s", e.response.status_code, str(e)[:200])
                raise
            await asyncio.sleep(2 ** attempt)

    raise RuntimeError(f"YouTube API call failed after {max_attempts} attempts: {url}")


# ─────────────────────────────────────────────
# LOW-LEVEL API CALLS
# ─────────────────────────────────────────────

async def youtube_search(
    query: str,
    max_results: int = 10,
    search_type: str = "video",
    region_code: str | None = None,
    relevance_language: str | None = None,
) -> list:
    """
    Search YouTube for videos or channels.
    Cost: 100 quota units per call.

    Returns raw API items list.
    """
    params: dict[str, Any] = {
        "key":        _api_key(),
        "q":          query,
        "type":       search_type,
        "part":       "snippet",
        "maxResults": min(max_results, 50),
    }
    if region_code:
        params["regionCode"] = region_code
    if relevance_language:
        params["relevanceLanguage"] = relevance_language

    try:
        async with httpx.AsyncClient() as client:
            data = await _get_with_retry(
                client,
                "https://www.googleapis.com/youtube/v3/search",
                params,
            )
        items = data.get("items", [])
        logger.debug("youtube_search('%s'): %d results", query[:60], len(items))
        return items
    except Exception as e:
        logger.error("youtube_search failed for '%s': %s", query[:60], e)
        return []


async def youtube_channel_stats_batch(channel_ids: list[str]) -> list[dict]:
    """
    Fetch rich channel data for up to 50 channels in ONE API call.

    FIX: Previous version called channels.list once per channel (N calls = N units).
         This version batches all IDs → 1 call = 1 unit regardless of N channels.

    Parts fetched:
      statistics      → subscribers, views, videoCount
      snippet         → description, publishedAt, country, title
      topicDetails    → YouTube content categories
      brandingSettings → self-declared keywords
      contentDetails  → uploads playlist ID (for recent videos)

    Cost: 1 unit per call (batch of up to 50).
    """
    if not channel_ids:
        return []

    results = []

    # Process in batches of 50 (API hard limit)
    for i in range(0, len(channel_ids), 50):
        batch = channel_ids[i : i + 50]
        try:
            async with httpx.AsyncClient() as client:
                data = await _get_with_retry(
                    client,
                    "https://www.googleapis.com/youtube/v3/channels",
                    {
                        "key":  _api_key(),
                        "id":   ",".join(batch),
                        "part": "statistics,snippet,topicDetails,brandingSettings,contentDetails",
                    },
                )
            results.extend(data.get("items", []))
            logger.debug("Fetched %d channels in batch %d", len(data.get("items", [])), i // 50 + 1)
        except Exception as e:
            logger.error("channels.list batch failed (ids: %s...): %s", batch[:3], e)

    return results


async def youtube_recent_video_ids(uploads_playlist_id: str, max_videos: int = 10) -> list[str]:
    """
    Fetch most recent video IDs from a channel's uploads playlist.
    Cost: 1 unit.
    """
    if not uploads_playlist_id:
        return []
    try:
        async with httpx.AsyncClient() as client:
            data = await _get_with_retry(
                client,
                "https://www.googleapis.com/youtube/v3/playlistItems",
                {
                    "key":        _api_key(),
                    "playlistId": uploads_playlist_id,
                    "part":       "contentDetails",
                    "maxResults": min(max_videos, 50),
                },
            )
        return [
            i["contentDetails"]["videoId"]
            for i in data.get("items", [])
            if "contentDetails" in i
        ]
    except Exception as e:
        logger.error("youtube_recent_video_ids failed for playlist %s: %s",
                     uploads_playlist_id[:20], e)
        return []


async def youtube_video_stats_batch(video_ids: list[str]) -> list[dict]:
    """
    Fetch likes, comments, views AND TITLE for a batch of video IDs.
    Cost: 1 unit per call (handles up to 50 IDs).

    FIX: Added snippet part so we get video titles for niche relevance analysis in Chain 4.
    """
    if not video_ids:
        return []

    results = []
    for i in range(0, len(video_ids), 50):
        batch = video_ids[i : i + 50]
        try:
            async with httpx.AsyncClient() as client:
                data = await _get_with_retry(
                    client,
                    "https://www.googleapis.com/youtube/v3/videos",
                    {
                        "key":  _api_key(),
                        "id":   ",".join(batch),
                        "part": "statistics,snippet",  # FIX: added snippet for titles
                    },
                )
            results.extend(data.get("items", []))
        except Exception as e:
            logger.error("videos.list batch failed: %s", e)

    return results


async def youtube_channel_comments(channel_id: str, max_comments: int = 15) -> list[str]:
    """
    Fetch top-level comments across all videos for a channel.
    Cost: 1 unit per call.
    """
    if not channel_id:
        return []
    try:
        async with httpx.AsyncClient() as client:
            data = await _get_with_retry(
                client,
                "https://www.googleapis.com/youtube/v3/commentThreads",
                {
                    "key": _api_key(),
                    "allThreadsRelatedToChannelId": channel_id,
                    "part": "snippet",
                    "maxResults": min(max_comments, 100),
                    "order": "time",
                },
            )
        comments = []
        for item in data.get("items", []):
            snippet = item.get("snippet", {}).get("topLevelComment", {}).get("snippet", {})
            text = snippet.get("textOriginal", "")
            if text:
                comments.append(text)
        return comments
    except httpx.HTTPStatusError as e:
        if e.response.status_code in (403, 400, 404):
            logger.warning("Comments disabled or forbidden for channel %s", channel_id)
            return []
        logger.error("youtube_channel_comments failed for channel %s: %s", channel_id, e)
        return []
    except Exception as e:
        logger.error("youtube_channel_comments failed for channel %s: %s", channel_id, e)
        return []


# ─────────────────────────────────────────────
# MEDIAN HELPER
# ─────────────────────────────────────────────

def _median(values: list[int | float]) -> float:
    """
    Return median of a list.

    FIX: Previous code used mean (sum/n). One viral video with 10M views
    inflates the average and makes a dead channel look active.
    Median is resistant to outliers — the correct metric for ER calculation.
    """
    if not values:
        return 0.0
    return float(statistics.median(values))


# ─────────────────────────────────────────────
# RICH CHANNEL PROFILE BUILDER
# ─────────────────────────────────────────────

async def build_channel_profile(channel_id: str, raw_data: dict | None = None) -> dict:
    """
    Build a complete channel profile with real engagement data.

    Args:
        channel_id: YouTube channel ID
        raw_data:   Pre-fetched channel API response (pass this when using batch fetch
                    to avoid re-fetching). If None, fetches individually (1 unit).

    Returns a standardised profile dict ready for Chain 3 and Chain 4.

    Total quota cost when raw_data is provided: ~2 units (playlist + videos)
    Total quota cost when raw_data is None:     ~3 units (channel + playlist + videos)
    """
    # Step 1: Use pre-fetched data or fetch individually
    if raw_data is None:
        batch = await youtube_channel_stats_batch([channel_id])
        if not batch:
            logger.warning("No data returned for channel %s", channel_id)
            return {}
        data = batch[0]
    else:
        data = raw_data

    snippet         = data.get("snippet", {})
    statistics_     = data.get("statistics", {})
    topic_details   = data.get("topicDetails", {})
    branding        = data.get("brandingSettings", {}).get("channel", {})
    content_details = data.get("contentDetails", {})

    subscribers = int(statistics_.get("subscriberCount", 0))
    total_views = int(statistics_.get("viewCount", 0))
    video_count = max(int(statistics_.get("videoCount", 1)), 1)

    description   = snippet.get("description", "")[:500]
    country       = snippet.get("country", "")
    published_at  = snippet.get("publishedAt", "")
    channel_title = snippet.get("title", "")
    custom_url    = snippet.get("customUrl", "").lstrip("@")

    # Channel age
    channel_age_years = 0.0
    if published_at:
        try:
            from datetime import datetime, timezone
            created = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
            channel_age_years = round((datetime.now(timezone.utc) - created).days / 365.25, 1)
        except Exception:
            pass

    # Topic categories from YouTube's own taxonomy
    topic_categories = [
        t.split("/wiki/")[-1].replace("_", " ")
        for t in topic_details.get("topicCategories", [])
    ]
    channel_keywords = branding.get("keywords", "")

    # Step 2: Real engagement from recent videos
    avg_views    = total_views // video_count  # fallback until real data fetched
    avg_likes    = 0
    avg_comments = 0
    median_views = 0.0
    median_er    = 0.0
    engagement_rate   = 0.0
    engagement_source = "estimated"
    recent_videos_data: list[dict] = []

    uploads_playlist_id = content_details.get("relatedPlaylists", {}).get("uploads", "")

    if uploads_playlist_id:
        video_ids = await youtube_recent_video_ids(uploads_playlist_id, max_videos=10)
        if video_ids:
            video_items = await youtube_video_stats_batch(video_ids)
            if video_items:
                views_list    = []
                likes_list    = []
                comments_list = []

                for v in video_items:
                    s     = v.get("statistics", {})
                    snip  = v.get("snippet", {})
                    views    = int(s.get("viewCount",   0))
                    likes    = int(s.get("likeCount",   0))
                    comments = int(s.get("commentCount", 0))

                    views_list.append(views)
                    likes_list.append(likes)
                    comments_list.append(comments)

                    recent_videos_data.append({
                        "video_id": v.get("id", ""),
                        "title":    snip.get("title", ""),   # FIX: added title
                        "views":    views,
                        "likes":    likes,
                        "comments": comments,
                    })

                # FIX: Use median, not mean, to resist viral outliers
                avg_views    = int(_median(views_list))
                avg_likes    = int(_median(likes_list))
                avg_comments = int(_median(comments_list))
                median_views = _median(views_list)

                if subscribers > 0:
                    # Compute per-video ERs then take the median ER
                    per_video_ers = [
                        ((l + c) / subscribers) * 100
                        for l, c in zip(likes_list, comments_list)
                    ]
                    median_er         = round(_median(per_video_ers), 3)
                    engagement_rate   = median_er
                    engagement_source = "real"

                logger.info(
                    "[YouTube] %s | subs=%s | median_views=%s | median_er=%.2f%%",
                    channel_title, f"{subscribers:,}", f"{int(median_views):,}", median_er,
                )

    # Fetch recent comments
    recent_comments = await youtube_channel_comments(channel_id, max_comments=15)

    # Step 3: Fallback engagement estimate if no videos available
    if engagement_source == "estimated" and subscribers > 0:
        if   subscribers >= 10_000_000: base = 1.2
        elif subscribers >=  5_000_000: base = 1.8
        elif subscribers >=  1_000_000: base = 2.5
        elif subscribers >=    500_000: base = 3.5
        elif subscribers >=    100_000: base = 5.0
        else:                           base = 7.0

        view_ratio = (avg_views / subscribers) * 100
        if   view_ratio > 20: engagement_rate = round(base * 1.3, 2)
        elif view_ratio >  5: engagement_rate = round(base * 1.0, 2)
        else:                 engagement_rate = round(base * 0.7, 2)

    # Derived ratios (pre-computed for Chain 3 + Chain 4)
    view_to_sub_ratio    = round(avg_views / subscribers, 4) if subscribers > 0 else 0.0
    like_to_comment_ratio = round(avg_likes / avg_comments, 1) if avg_comments > 0 else None

    return {
        # Core identity
        "handle":        custom_url or channel_title,
        "platform":      "youtube",
        "channel_id":    channel_id,
        "profile_url":   f"https://www.youtube.com/channel/{channel_id}",
        "channel_title": channel_title,
        "data_confidence": "real",

        # Audience size
        "followers": subscribers,

        # Channel metadata
        "description":      description,
        "country":          country,
        "published_at":     published_at,
        "channel_age_years": channel_age_years,
        "topic_categories": topic_categories,
        "channel_keywords": channel_keywords,

        # Engagement (real or estimated)
        "avg_views":    avg_views,
        "avg_likes":    avg_likes,
        "avg_comments": avg_comments,
        "median_views": median_views,
        "median_er":    median_er,

        "engagement_rate":   engagement_rate,
        "engagement_source": engagement_source,

        # Pre-computed ratios for Chain 3 hard-drop rules
        "view_to_sub_ratio":     view_to_sub_ratio,
        "like_to_comment_ratio": like_to_comment_ratio,

        # Recent videos with titles (used by Chain 4 for niche relevance)
        "recent_videos":   recent_videos_data,
        "recent_video_titles": [v["title"] for v in recent_videos_data],
        
        # Recent comments (used by Chain 4 for authenticity audit)
        "recent_comments": recent_comments,
    }


# ─────────────────────────────────────────────
# BATCH PROFILE BUILDER  (called by Chain 2)
# ─────────────────────────────────────────────

async def build_channel_profiles_batch(channel_ids: list[str]) -> list[dict]:
    """
    Build profiles for multiple channels efficiently.

    Optimisation:
      1. Fetch ALL channel metadata in one batched API call (1 unit total).
      2. Then fetch playlist + video stats concurrently per channel.

    Without batching: N channels = N × 3 serial quota units.
    With batching:    N channels = 1 + N × 2 concurrent quota units.
    """
    if not channel_ids:
        return []

    logger.info("Fetching %d channel profiles (batched)", len(channel_ids))

    # FIX CRITICAL: one call for all channels
    all_channel_data = await youtube_channel_stats_batch(channel_ids)

    # Map channel_id → raw API data for fast lookup
    id_to_data: dict[str, dict] = {
        item["id"]: item for item in all_channel_data
    }

    # Build profiles concurrently, passing pre-fetched data
    tasks = []
    for cid in channel_ids:
        raw = id_to_data.get(cid)
        if raw:
            tasks.append(build_channel_profile(cid, raw_data=raw))
        else:
            logger.warning("No channel data returned for %s — skipping", cid)

    results = await asyncio.gather(*tasks, return_exceptions=True)

    profiles = []
    for r in results:
        if isinstance(r, Exception):
            logger.error("Profile build failed: %s", r)
            continue
        if r:
            profiles.append(r)

    logger.info("Built %d/%d channel profiles successfully", len(profiles), len(channel_ids))
    return profiles