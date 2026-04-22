"""
auditor.py — Profile qualification, brand safety audit, and pricing
Handles the deep-dive analysis of discovered influencer profiles.
"""

import asyncio
from typing import List

from services.platforms.youtube import youtube_channel_stats
from services.platforms.tavily import tavily_search


# ============================================================
# QUALIFICATION — Engagement stats
# ============================================================

async def qualify_profile(profile: dict) -> dict:
    """
    Get real engagement stats.
    YouTube -> YouTube API (exact data).
    Instagram/Twitter -> return empty dict, let fill_missing_estimates() handle it.
    """
    platform   = profile.get("platform", "instagram")
    handle     = profile.get("handle", "")
    channel_id = profile.get("channel_id", "")

    if platform == "youtube" and channel_id:
        stats_data  = await youtube_channel_stats(channel_id)
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
# BRAND SAFETY AUDIT
# ============================================================

async def audit_profile(profile: dict) -> dict:
    """
    Search for controversy/scandal using Tavily.
    Returns risk_flag: green / amber / red.
    """
    handle = profile.get("handle", "")

    results = await tavily_search(
        f"{handle} influencer controversy scandal",
        max_results=3
    )

    risk_flag     = "green"
    risk_evidence = None
    risk_sources  = []

    RED_SIGNALS = [
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
# PRICING
# ============================================================

async def price_profile(profile: dict) -> dict:
    """
    Pricing is handled entirely by fill_missing_estimates() in scoring.py.
    Return empty dict here — no API calls needed.
    """
    return {"handle": profile.get("handle", ""), "platform": profile.get("platform", "")}


# ============================================================
# FULL PARALLEL AUDIT
# ============================================================

async def run_full_audit(profiles: List[dict], brief_dict: dict = None) -> List[dict]:
    """
    Run qualification, audit, and pricing in parallel for all profiles.
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
            print(f"  [FILTER] x {profile['handle']} — engagement too low ({engagement_rate}%)")
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
                print(f"  [FILTER] ! {profile['handle']} — follower mismatch ({discovery_followers:,} vs {followers:,})")
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

    print(f"  [AUDIT] {len(enriched)} profiles passed audit")
    return enriched
