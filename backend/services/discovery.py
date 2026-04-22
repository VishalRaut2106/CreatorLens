"""
discovery.py — Influencer discovery aggregator
Coordinates YouTube and Tavily platform searches into a unified result set.
"""

import asyncio
from typing import List

from services.platforms.youtube import discover_youtube
from services.platforms.tavily import discover_social


async def discover_influencers(keywords: List[str], platforms: List[str]) -> List[dict]:
    """
    Main discovery function — searches across all requested platforms.
    Returns list of profile dicts with handle, platform, followers, profile_url.
    """
    tasks = []
    for platform in platforms:
        if platform == "youtube":
            tasks.append(discover_youtube(keywords))
        elif platform in ("instagram", "twitter"):
            tasks.append(discover_social(keywords, platform))

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
