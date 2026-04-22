"""
tavily.py — Tavily web-search API client
Handles web searches, profile parsing, social discovery, and competitor intel.
"""

import os
import re
import asyncio
import httpx
from typing import List

TAVILY_API_KEY = os.getenv("TAVILY_API_KEY", "")


# ============================================================
# LOW-LEVEL API CALL
# ============================================================

async def tavily_search(query: str, max_results: int = 3) -> list:
    """Search the web via Tavily and return result list."""
    if not TAVILY_API_KEY:
        print("  [TAVILY] No API key set — skipping search")
        return []
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                "https://api.tavily.com/search",
                json={
                    "api_key": TAVILY_API_KEY,
                    "query": query,
                    "max_results": max_results,
                    "search_depth": "basic",
                }
            )
            resp.raise_for_status()
            return resp.json().get("results", [])
    except Exception as e:
        print(f"  [TAVILY] Search failed for '{query}': {e}")
        return []


# ============================================================
# PARSING HELPERS
# ============================================================

def extract_followers(text: str) -> int:
    """Parse follower count from a text snippet."""
    text = text.lower()
    patterns = [
        (r'([\d.]+)\s*m(?:illion)?\s*(?:followers|subscribers|subs)', 1_000_000),
        (r'([\d.]+)\s*k\s*(?:followers|subscribers|subs)',            1_000),
        (r'([\d,]+)\s*(?:followers|subscribers|subs)',                 1),
    ]
    for pattern, multiplier in patterns:
        match = re.search(pattern, text)
        if match:
            try:
                num = float(match.group(1).replace(',', ''))
                return int(num * multiplier)
            except (ValueError, AttributeError):
                pass
    return 0


def extract_handle_from_url(url: str, platform: str) -> str:
    """Pull the username/handle out of a profile URL."""
    SKIP = {'p', 'reel', 'stories', 'search', 'explore',
            'watch', 'results', 'channel', 'shorts', 'feed'}
    patterns = {
        'instagram': r'instagram\.com/([^/?#\s]+)',
        'twitter':   r'(?:twitter|x)\.com/([^/?#\s]+)',
        'youtube':   r'youtube\.com/(?:@|c/|user/)?([^/?#\s]+)',
    }
    pat = patterns.get(platform, '')
    if pat:
        m = re.search(pat, url or '')
        if m:
            handle = m.group(1).strip('/')
            if handle.lower() not in SKIP and len(handle) > 1:
                return handle
    return ''


def parse_tavily_profiles(results: list, platform: str) -> list:
    """
    Convert raw Tavily search results into profile dicts.
    Looks for profile URLs and follower mentions in snippets.
    """
    profiles = []
    seen = set()

    for r in results:
        url     = r.get("url", "")
        title   = r.get("title", "")
        content = r.get("content", "")
        text    = f"{title} {content}".lower()

        # Only keep actual profile pages
        platform_domains = {
            'instagram': 'instagram.com',
            'twitter':   ('twitter.com', 'x.com'),
            'youtube':   'youtube.com',
        }
        domain = platform_domains.get(platform, '')
        domains = domain if isinstance(domain, tuple) else (domain,)
        if not any(d in url for d in domains):
            continue

        handle = extract_handle_from_url(url, platform)
        if not handle or handle in seen:
            continue

        followers = extract_followers(text)
        # If no follower count found, set a placeholder so pre_filter_score
        # gives it a chance — fill_missing_estimates will refine it later
        if followers == 0:
            followers = 50_000

        seen.add(handle)
        profiles.append({
            "handle":      handle,
            "platform":    platform,
            "followers":   followers,
            "profile_url": url,
        })

    return profiles


# ============================================================
# DISCOVERY — Find social profiles by keyword
# ============================================================

async def discover_social(keywords: List[str], platform: str) -> List[dict]:
    """Use Tavily to find Instagram / Twitter influencer profiles."""
    tasks = [
        tavily_search(f"{kw} influencer {platform} profile followers", max_results=3)
        for kw in keywords[:3]
    ]
    all_results = await asyncio.gather(*tasks, return_exceptions=True)

    seen = set()
    profiles = []

    for results in all_results:
        if isinstance(results, Exception):
            continue
        for p in parse_tavily_profiles(results, platform):
            if p["handle"] not in seen:
                seen.add(p["handle"])
                profiles.append(p)

    return profiles


# ============================================================
# COMPETITOR INTEL
# ============================================================

async def find_competitor_influencers(competitor_brand: str) -> List[dict]:
    """
    Find influencers working with a competitor brand using Tavily.
    """
    results = await tavily_search(
        f"{competitor_brand} influencer ambassador sponsored partnership",
        max_results=5
    )

    partnerships = []
    seen = set()

    for r in results:
        url     = r.get("url", "")
        title   = r.get("title", "")

        # Try to extract handles from URLs and text
        for platform in ("instagram", "youtube", "twitter"):
            handle = extract_handle_from_url(url, platform)
            if handle and handle not in seen:
                seen.add(handle)
                partnerships.append({
                    "handle":   handle,
                    "platform": platform,
                    "evidence": f"Found via search: {title[:100]}",
                })
                break

    print(f"  [COMPETITOR] Found {len(partnerships)} potential partnerships for {competitor_brand}")
    return partnerships
