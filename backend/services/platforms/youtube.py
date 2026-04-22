"""
youtube.py — YouTube Data API v3 client
Handles channel search, statistics fetching, and channel discovery.
"""

import os
import asyncio
import httpx
from typing import List

YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY", "")


# ============================================================
# LOW-LEVEL API CALLS
# ============================================================

async def youtube_search(query: str, max_results: int = 5) -> list:
    """Search YouTube for channels matching a keyword."""
    if not YOUTUBE_API_KEY:
        print("  [YOUTUBE] No API key set — skipping YouTube search")
        return []
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(
                "https://www.googleapis.com/youtube/v3/search",
                params={
                    "key": YOUTUBE_API_KEY,
                    "q": query,
                    "type": "channel",
                    "part": "snippet",
                    "maxResults": max_results,
                }
            )
            resp.raise_for_status()
            return resp.json().get("items", [])
    except Exception as e:
        print(f"  [YOUTUBE] Search failed for '{query}': {e}")
        return []


async def youtube_channel_stats(channel_id: str) -> dict:
    """Fetch real subscriber + view stats for a YouTube channel."""
    if not YOUTUBE_API_KEY:
        return {}
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(
                "https://www.googleapis.com/youtube/v3/channels",
                params={
                    "key": YOUTUBE_API_KEY,
                    "id": channel_id,
                    "part": "statistics,snippet",
                }
            )
            resp.raise_for_status()
            items = resp.json().get("items", [])
            return items[0] if items else {}
    except Exception as e:
        print(f"  [YOUTUBE] Stats failed for channel {channel_id}: {e}")
        return {}


# ============================================================
# DISCOVERY — Find YouTube channels by keyword
# ============================================================

async def discover_youtube(keywords: List[str]) -> List[dict]:
    """Use official YouTube API to find channels."""
    tasks = [youtube_search(f"{kw} influencer", max_results=3) for kw in keywords[:3]]
    all_results = await asyncio.gather(*tasks, return_exceptions=True)

    seen = set()
    profiles = []

    for items in all_results:
        if isinstance(items, Exception):
            continue
        for item in items:
            channel_id    = item.get("id", {}).get("channelId", "")
            channel_title = item.get("snippet", {}).get("title", "")
            handle        = item.get("snippet", {}).get("customUrl", channel_title).lstrip("@")

            if not channel_id or channel_id in seen:
                continue
            seen.add(channel_id)

            # Get real subscriber count
            stats_data = await youtube_channel_stats(channel_id)
            stats      = stats_data.get("statistics", {})
            subscribers = int(stats.get("subscriberCount", 0))

            if subscribers < 5_000:
                continue

            profiles.append({
                "handle":      handle or channel_id,
                "platform":    "youtube",
                "followers":   subscribers,
                "profile_url": f"https://www.youtube.com/channel/{channel_id}",
                "channel_id":  channel_id,
                # Pre-populate qual data — saves a Tavily call later
                "avg_views":   int(stats.get("viewCount", 0)),
            })

    return profiles
