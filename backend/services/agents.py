"""
agents.py — Free replacement for tinyfish.py
Uses: Tavily API (web search) + YouTube Data API v3
Drop-in replacement: same function signatures, same return format.
"""

import os
import re
import asyncio
import httpx
import json
from typing import List

# ── API Keys ────────────────────────────────────────────────
YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY", "")
TAVILY_API_KEY  = os.getenv("TAVILY_API_KEY", "")

# Compatibility shim — campaign.py imports this
active_runs: list[str] = []


# ============================================================
# LOW-LEVEL HELPERS
# ============================================================

async def _youtube_search(query: str, max_results: int = 5) -> list:
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


async def _youtube_channel_stats(channel_id: str) -> dict:
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


async def _tavily_search(query: str, max_results: int = 3) -> list:
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

def _extract_followers(text: str) -> int:
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


def _extract_handle_from_url(url: str, platform: str) -> str:
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


def _parse_tavily_profiles(results: list, platform: str, keyword: str) -> list:
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

        handle = _extract_handle_from_url(url, platform)
        if not handle or handle in seen:
            continue

        followers = _extract_followers(text)
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
# STEP 1 — DISCOVERY
# ============================================================

async def _discover_youtube(keywords: List[str]) -> List[dict]:
    """Use official YouTube API to find channels."""
    tasks = [_youtube_search(f"{kw} influencer", max_results=3) for kw in keywords[:3]]
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
            stats_data = await _youtube_channel_stats(channel_id)
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


async def _discover_social(keywords: List[str], platform: str) -> List[dict]:
    """Use Tavily to find Instagram / Twitter influencer profiles."""
    tasks = [
        _tavily_search(f"{kw} influencer {platform} profile followers", max_results=3)
        for kw in keywords[:3]
    ]
    all_results = await asyncio.gather(*tasks, return_exceptions=True)

    seen = set()
    profiles = []

    for results in all_results:
        if isinstance(results, Exception):
            continue
        for p in _parse_tavily_profiles(results, platform, ""):
            if p["handle"] not in seen:
                seen.add(p["handle"])
                profiles.append(p)

    return profiles


async def discover_influencers(keywords: List[str], platforms: List[str]) -> List[dict]:
    """
    Main discovery function — replaces tinyfish.discover_influencers().
    Returns list of profile dicts with handle, platform, followers, profile_url.
    """
    tasks = []
    for platform in platforms:
        if platform == "youtube":
            tasks.append(_discover_youtube(keywords))
        elif platform in ("instagram", "twitter"):
            tasks.append(_discover_social(keywords, platform))

    print(f"  [DISCOVERY] Searching {len(tasks)} platform(s) for {len(keywords)} keyword(s)...")
    results = await asyncio.gather(*tasks, return_exceptions=True)

    all_profiles = []
    seen = set()
    for batch in results:
        if isinstance(batch, Exception):
            print(f"  [DISCOVERY] Platform error: {batch}")
            continue
        for p in batch:
            key = (p.get("handle", "").lower(), p.get("platform", ""))
            if key not in seen:
                seen.add(key)
                all_profiles.append(p)

    print(f"  [DISCOVERY] Found {len(all_profiles)} unique profiles")
    return all_profiles


# ============================================================
# STEP 2a — QUALIFICATION (engagement stats)
# ============================================================

async def qualify_profile(profile: dict) -> dict:
    """
    Get real engagement stats.
    YouTube → YouTube API (exact data).
    Instagram/Twitter → return empty dict, let fill_missing_estimates() handle it.
    """
    platform   = profile.get("platform", "instagram")
    handle     = profile.get("handle", "")
    channel_id = profile.get("channel_id", "")

    if platform == "youtube" and channel_id:
        stats_data  = await _youtube_channel_stats(channel_id)
        stats       = stats_data.get("statistics", {})
        subscribers = int(stats.get("subscriberCount", 0))
        view_count  = int(stats.get("viewCount", 0))
        video_count = max(int(stats.get("videoCount", 1)), 1)

        avg_views = view_count // video_count

        # Use subscriber-based benchmark instead of total views
        # (total views / video count skews massively for older channels)
        if   subscribers >= 10_000_000: base_rate = 1.2
        elif subscribers >=  5_000_000: base_rate = 1.8
        elif subscribers >=  1_000_000: base_rate = 2.5
        elif subscribers >=    500_000: base_rate = 3.5
        elif subscribers >=    100_000: base_rate = 5.0
        else:                           base_rate = 7.0

        # Nudge based on actual view activity vs subs
        view_ratio = (avg_views / max(subscribers, 1)) * 100
        if view_ratio > 20:   engagement_rate = round(base_rate * 1.3, 2)
        elif view_ratio > 5:  engagement_rate = round(base_rate * 1.0, 2)
        else:                 engagement_rate = round(base_rate * 0.7, 2)

        return {
            "handle":          handle,
            "platform":        platform,
            "followers":       subscribers,
            "avg_views":       avg_views,
            "engagement_rate": engagement_rate,
        }

    # Instagram / Twitter — no free scraping API, use estimates
    return {"handle": handle, "platform": platform}


# ============================================================
# STEP 2b — BRAND SAFETY AUDIT
# ============================================================

async def audit_profile(profile: dict) -> dict:
    """
    Search for controversy/scandal using Tavily.
    Returns risk_flag: green / amber / red.
    """
    handle = profile.get("handle", "")

    results = await _tavily_search(
        f"{handle} influencer controversy scandal",
        max_results=3
    )

    risk_flag     = "green"
    risk_evidence = None
    risk_sources  = []

    RED_SIGNALS   = [
        "fraud", "abuse", "arrested", "criminal conviction",
        "hate speech", "sexual assault", "child abuse",
        "money laundering", "ponzi", "indicted"
    ]
    AMBER_SIGNALS = [
        "accused of scam", "accused of fraud", "brand boycott",
        "canceled", "cancelled", "sexual harassment allegation",
        "racist video", "offensive tweet"
    ]

    # Require the handle name AND a signal to appear close together
    # This prevents generic "fitness influencer scam" pages from flagging
    handle_lower = handle.lower().replace("-", " ").replace("_", " ")
    combined_text = " ".join(
        f"{r.get('title','')} {r.get('content','')}" for r in results
    ).lower()

    # Only flag if the handle name appears near the signal word
    def signal_near_handle(text, signal, handle, window=300):
        idx = text.find(signal)
        while idx != -1:
            nearby = text[max(0, idx - window): idx + window]
            if handle in nearby:
                return True
            idx = text.find(signal, idx + 1)
        return False

    for signal in RED_SIGNALS:
        if signal in combined_text and signal_near_handle(combined_text, signal, handle_lower):
            risk_flag     = "red"
            risk_evidence = f"Specific finding: '{signal}' linked to this creator"
            risk_sources  = [r.get("url", "") for r in results[:2]]
            break

    if risk_flag == "green":
        for signal in AMBER_SIGNALS:
            if signal in combined_text and signal_near_handle(combined_text, signal, handle_lower):
                risk_flag     = "amber"
                risk_evidence = f"Potential concern: '{signal}' linked to this creator"
                risk_sources  = [r.get("url", "") for r in results[:1]]
                break

    return {
        "handle":        handle,
        "platform":      profile.get("platform", ""),
        "risk_flag":     risk_flag,
        "risk_evidence": risk_evidence,
        "risk_sources":  risk_sources,
    }


# ============================================================
# STEP 2c — PRICING
# ============================================================

async def price_profile(profile: dict) -> dict:
    """
    Pricing is handled entirely by fill_missing_estimates() in scoring.py.
    Return empty dict here — no API calls needed.
    """
    return {"handle": profile.get("handle", ""), "platform": profile.get("platform", "")}


# ============================================================
# STEP 2 — FULL PARALLEL AUDIT
# (mirrors tinyfish.run_full_audit exactly)
# ============================================================

async def run_full_audit(profiles: List[dict], brief_dict: dict = None) -> List[dict]:
    """
    Run qualification, audit, and pricing in parallel for all profiles.
    Same signature and return format as tinyfish.run_full_audit().
    """
    qual_tasks    = [qualify_profile(p)  for p in profiles]
    audit_tasks   = [audit_profile(p)    for p in profiles]
    pricing_tasks = [price_profile(p)    for p in profiles]

    print(f"  [AUDIT] Running qual + audit + pricing for {len(profiles)} profiles...")

    qual_results, audit_results, pricing_results = await asyncio.gather(
        asyncio.gather(*qual_tasks,    return_exceptions=True),
        asyncio.gather(*audit_tasks,   return_exceptions=True),
        asyncio.gather(*pricing_tasks, return_exceptions=True),
    )

    def to_map(results):
        m = {}
        for r in results:
            if isinstance(r, Exception):
                print(f"  [AUDIT] Agent error: {r}")
                continue
            if isinstance(r, dict):
                handle = r.get("handle", "").lower().strip().lstrip("@")
                if handle:
                    m[handle] = r
        return m

    qual_map    = to_map(qual_results)
    audit_map   = to_map(audit_results)
    pricing_map = to_map(pricing_results)

    def passes_hard_filter(profile, qual_data):
        engagement_rate = qual_data.get("engagement_rate", 0)
        try:
            engagement_rate = float(engagement_rate)
        except (ValueError, TypeError):
            engagement_rate = 0

        followers = qual_data.get("followers", 0)
        try:
            followers = int(followers)
        except (ValueError, TypeError):
            followers = 0

        # Drop obvious bots (engagement too low only if we have real data)
        if engagement_rate > 0 and engagement_rate < 0.1:
            print(f"  [FILTER] ✗ {profile['handle']} — engagement too low ({engagement_rate}%)")
            return False

        # Update with verified follower count if significantly different
        discovery_followers = profile.get("followers", 0)
        try:
            discovery_followers = int(discovery_followers)
        except (ValueError, TypeError):
            discovery_followers = 0

        if followers > 0 and discovery_followers > 0:
            ratio = max(discovery_followers, followers) / max(min(discovery_followers, followers), 1)
            if ratio > 10:
                print(f"  [FILTER] ⚠ {profile['handle']} — follower mismatch ({discovery_followers:,} vs {followers:,})")
                profile["followers"] = followers
                profile["followers_verified"] = True

        return True

    enriched = []
    for profile in profiles:
        handle    = profile["handle"].lower().strip().lstrip("@")
        qual_data = qual_map.get(handle, {})

        if not passes_hard_filter(profile, qual_data):
            continue

        merged = {
            **profile,
            **qual_data,
            **audit_map.get(handle, {}),
            **pricing_map.get(handle, {}),
        }
        enriched.append(merged)

    print(f"  [AUDIT] ✓ {len(enriched)} profiles passed audit")
    return enriched


# ============================================================
# CANCEL STUB (compatibility with campaign.py)
# ============================================================

async def cancel_all_runs() -> dict:
    """No real browser agents to cancel. Returns success for compatibility."""
    active_runs.clear()
    return {"cancelled": 0, "message": "No active agents (using free API stack)"}


# ============================================================
# COMPETITOR INTEL
# ============================================================

async def find_competitor_influencers(competitor_brand: str) -> List[dict]:
    """
    Find influencers working with a competitor brand using Tavily.
    Same return format as tinyfish.find_competitor_influencers().
    """
    results = await _tavily_search(
        f"{competitor_brand} influencer ambassador sponsored partnership",
        max_results=5
    )

    partnerships = []
    seen = set()

    for r in results:
        url     = r.get("url", "")
        content = r.get("content", "")
        title   = r.get("title", "")

        # Try to extract handles from URLs and text
        for platform in ("instagram", "youtube", "twitter"):
            handle = _extract_handle_from_url(url, platform)
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
