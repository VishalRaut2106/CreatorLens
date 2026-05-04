"""
youtube.py — YouTube Data API v3 client
Fetches rich channel data including real engagement from recent videos.
"""

import os
import asyncio
import httpx
from typing import List

YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY", "")

# Quota costs (units):
#   search.list        = 100/call  ← expensive, use sparingly
#   channels.list      =   1/call
#   playlistItems.list =   1/call
#   videos.list        =   1/call


# ============================================================
# LOW-LEVEL API CALLS
# ============================================================

async def youtube_search(
    query: str, 
    max_results: int = 5, 
    search_type: str = "channel",
    region_code: str | None = None,
    relevance_language: str | None = None
) -> list:
    """Search YouTube for videos or channels. Cost: 100 units per call."""
    if not YOUTUBE_API_KEY:
        print("  [YOUTUBE] No API key — skipping search")
        return []
    try:
        params = {
            "key":        YOUTUBE_API_KEY,
            "q":          query,
            "type":       search_type,
            "part":       "snippet",
            "maxResults": max_results,
        }
        if region_code:
            params["regionCode"] = region_code
        if relevance_language:
            params["relevanceLanguage"] = relevance_language

        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(
                "https://www.googleapis.com/youtube/v3/search",
                params=params
            )
            resp.raise_for_status()
            return resp.json().get("items", [])
    except Exception as e:
        print(f"  [YOUTUBE] Search failed for '{query}': {e}")
        return []


async def youtube_channel_stats(channel_id: str) -> dict:
    """
    Fetch rich channel data.
    Parts fetched:
      - statistics     → subscribers, views, videoCount
      - snippet        → description, publishedAt, country, title
      - topicDetails   → YouTube content categories
      - brandingSettings → self-declared keywords
      - contentDetails → uploads playlist ID (for recent videos)
    Cost: 1 unit.
    """
    if not YOUTUBE_API_KEY:
        return {}
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(
                "https://www.googleapis.com/youtube/v3/channels",
                params={
                    "key":  YOUTUBE_API_KEY,
                    "id":   channel_id,
                    "part": "statistics,snippet,topicDetails,brandingSettings,contentDetails",
                }
            )
            resp.raise_for_status()
            items = resp.json().get("items", [])
            return items[0] if items else {}
    except Exception as e:
        print(f"  [YOUTUBE] Channel stats failed for {channel_id}: {e}")
        return {}


async def youtube_recent_videos(uploads_playlist_id: str, max_videos: int = 10) -> list:
    """
    Fetch the most recent video IDs from a channel's uploads playlist.
    Cost: 1 unit.
    """
    if not YOUTUBE_API_KEY or not uploads_playlist_id:
        return []
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(
                "https://www.googleapis.com/youtube/v3/playlistItems",
                params={
                    "key":        YOUTUBE_API_KEY,
                    "playlistId": uploads_playlist_id,
                    "part":       "contentDetails",
                    "maxResults": max_videos,
                }
            )
            resp.raise_for_status()
            items = resp.json().get("items", [])
            return [i["contentDetails"]["videoId"] for i in items if "contentDetails" in i]
    except Exception as e:
        print(f"  [YOUTUBE] Recent videos failed for playlist {uploads_playlist_id}: {e}")
        return []


async def youtube_video_stats(video_ids: list) -> list:
    """
    Fetch likes, comments, views for a batch of video IDs.
    Cost: 1 unit per call (handles up to 50 IDs).
    """
    if not YOUTUBE_API_KEY or not video_ids:
        return []
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(
                "https://www.googleapis.com/youtube/v3/videos",
                params={
                    "key":  YOUTUBE_API_KEY,
                    "id":   ",".join(video_ids[:50]),
                    "part": "statistics",
                }
            )
            resp.raise_for_status()
            return resp.json().get("items", [])
    except Exception as e:
        print(f"  [YOUTUBE] Video stats failed: {e}")
        return []


# ============================================================
# RICH CHANNEL PROFILE BUILDER
# ============================================================

async def build_channel_profile(channel_id: str, handle: str = "") -> dict:
    """
    Build a complete channel profile with real engagement data.
    Total API cost: ~3 units per channel.

    Returns:
      handle, platform, followers, channel_id, profile_url,
      description, country, published_at, channel_age_years,
      topic_categories, channel_keywords,
      avg_views, avg_likes, avg_comments,
      engagement_rate (real, from recent videos),
      engagement_source ("real" or "estimated")
    """
    # Step 1: Fetch rich channel metadata (1 unit)
    data = await youtube_channel_stats(channel_id)
    if not data:
        return {}

    snippet           = data.get("snippet", {})
    statistics        = data.get("statistics", {})
    topic_details     = data.get("topicDetails", {})
    branding          = data.get("brandingSettings", {}).get("channel", {})
    content_details   = data.get("contentDetails", {})

    subscribers  = int(statistics.get("subscriberCount", 0))
    total_views  = int(statistics.get("viewCount", 0))
    video_count  = max(int(statistics.get("videoCount", 1)), 1)

    # Channel metadata
    description      = snippet.get("description", "")[:500]
    country          = snippet.get("country", "")
    published_at     = snippet.get("publishedAt", "")
    channel_title    = snippet.get("title", handle)
    custom_url       = snippet.get("customUrl", "").lstrip("@")

    # Channel age
    channel_age_years = 0.0
    if published_at:
        try:
            from datetime import datetime, timezone
            created = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
            now     = datetime.now(timezone.utc)
            channel_age_years = round((now - created).days / 365.25, 1)
        except Exception:
            pass

    # Topic categories — YouTube's own classification
    raw_topics = topic_details.get("topicCategories", [])
    topic_categories = [
        t.split("/wiki/")[-1].replace("_", " ")
        for t in raw_topics
    ]

    # Self-declared keywords
    channel_keywords = branding.get("keywords", "")

    # Step 2: Fetch real engagement from recent videos (2 units)
    avg_views    = total_views // video_count  # fallback
    avg_likes    = 0
    avg_comments = 0
    engagement_rate   = 0.0
    engagement_source = "estimated"
    recent_videos_data = []

    uploads_playlist_id = (
        content_details.get("relatedPlaylists", {}).get("uploads", "")
    )

    if uploads_playlist_id:
        video_ids = await youtube_recent_videos(uploads_playlist_id, max_videos=10)
        if video_ids:
            video_items = await youtube_video_stats(video_ids)
            if video_items:
                views_list    = []
                likes_list    = []
                comments_list = []

                for v in video_items:
                    s = v.get("statistics", {})
                    views = int(s.get("viewCount", 0))
                    likes = int(s.get("likeCount", 0))
                    comments = int(s.get("commentCount", 0))
                    
                    views_list.append(views)
                    likes_list.append(likes)
                    comments_list.append(comments)
                    
                    recent_videos_data.append({
                        "video_id": v.get("id"),
                        "views": views,
                        "likes": likes,
                        "comments": comments
                    })

                n = len(video_items)
                avg_views    = sum(views_list)    // n
                avg_likes    = sum(likes_list)    // n
                avg_comments = sum(comments_list) // n

                if subscribers > 0:
                    # Real engagement = (likes + comments) / subscribers
                    raw_rate = ((avg_likes + avg_comments) / subscribers) * 100
                    engagement_rate   = round(raw_rate, 3)
                    engagement_source = "real"

                print(
                    f"  [YOUTUBE] {channel_title}: "
                    f"subs={subscribers:,} | "
                    f"avg_views={avg_views:,} | "
                    f"avg_likes={avg_likes:,} | "
                    f"avg_comments={avg_comments:,} | "
                    f"engagement={engagement_rate}% ({engagement_source})"
                )

    # Step 3: Fallback engagement estimate if videos unavailable
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

    return {
        # Core identity
        "handle":              custom_url or channel_title,
        "platform":            "youtube",
        "channel_id":          channel_id,
        "profile_url":         f"https://www.youtube.com/channel/{channel_id}",
        "channel_title":       channel_title,

        # Audience size
        "followers":           subscribers,

        # Channel metadata (used for audit + scoring)
        "description":         description,
        "country":             country,
        "published_at":        published_at,
        "channel_age_years":   channel_age_years,
        "topic_categories":    topic_categories,
        "channel_keywords":    channel_keywords,

        # Engagement (real or estimated)
        "avg_views":           avg_views,
        "avg_likes":           avg_likes,
        "avg_comments":        avg_comments,
        "engagement_rate":     engagement_rate,
        "engagement_source":   engagement_source,
        "recent_videos":       recent_videos_data,
    }


# ============================================================
# DISCOVERY
# ============================================================

async def discover_youtube(keywords: List[str]) -> List[dict]:
    """Find YouTube channels for given keywords and build rich profiles."""
    # Limit search calls to save quota (each costs 100 units)
    search_keywords = keywords[:3]
    tasks = [youtube_search(f"{kw} influencer", max_results=3) for kw in search_keywords]
    all_results = await asyncio.gather(*tasks, return_exceptions=True)

    seen       = set()
    channel_ids = []

    for items in all_results:
        if isinstance(items, Exception):
            continue
        for item in items:
            cid = item.get("id", {}).get("channelId", "")
            if cid and cid not in seen:
                seen.add(cid)
                channel_ids.append(cid)

    if not channel_ids:
        return []

    # Build rich profiles in parallel (3 units each)
    profile_tasks = [build_channel_profile(cid) for cid in channel_ids]
    profiles_raw  = await asyncio.gather(*profile_tasks, return_exceptions=True)

    profiles = []
    for p in profiles_raw:
        if isinstance(p, Exception) or not p:
            continue
        if p.get("followers", 0) < 5_000:
            continue
        profiles.append(p)

    print(f"  [YOUTUBE] Built {len(profiles)} rich channel profiles")
    return profiles
