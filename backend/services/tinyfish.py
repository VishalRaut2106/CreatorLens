import httpx
import json
import os
import asyncio
from typing import List

TINYFISH_URL = "https://agent.tinyfish.ai/v1/automation/run-sse"

def _get_headers():
    return {
        "X-API-Key": os.getenv("TINYFISH_API_KEY"),
        "Content-Type": "application/json"
    }


# -----------------------------------------------------------
# Core: single agent call (SSE streaming → wait for COMPLETE)
# -----------------------------------------------------------
async def run_agent(url: str, goal: str, stealth: bool = False) -> dict:
    """
    Fires one TinyFish agent at a URL with a goal.
    Streams SSE until COMPLETE, returns resultJson.
    """
    payload = {
        "url": url,
        "goal": goal,
        "browser_profile": "stealth" if stealth else "lite"
    }

    async with httpx.AsyncClient(timeout=60) as client:
        async with client.stream("POST", TINYFISH_URL, headers=_get_headers(), json=payload) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line.startswith("data: "):
                    continue
                event = json.loads(line[6:])
                if event.get("type") == "COMPLETE":
                    result = event.get("result", {})
                    if isinstance(result, str):
                        try:
                            result = json.loads(result)
                        except Exception:
                            result = {}
                    return result if isinstance(result, dict) else {"data": result}

    return {}


# -----------------------------------------------------------
# Step 1: Discovery — find influencer profiles by keyword
# -----------------------------------------------------------
async def discover_influencers(keywords: List[str], platforms: List[str]) -> List[dict]:
    """
    Fires parallel agents across platforms to find matching profiles.
    Returns deduplicated list of { handle, platform, profile_url }
    """
    PLATFORM_SEARCH_URLS = {
        "instagram": "https://www.instagram.com/explore/tags/{keyword}/",
        "tiktok":    "https://www.tiktok.com/search?q={keyword}",
        "youtube":   "https://www.youtube.com/results?search_query={keyword}+influencer"
    }

    tasks = []
    meta  = []

    for platform in platforms:
        if platform not in PLATFORM_SEARCH_URLS:
            continue
        for keyword in keywords:
            url  = PLATFORM_SEARCH_URLS[platform].format(keyword=keyword)
            goal = (
                f'Find influencer accounts related to "{keyword}". '
                f'Return a JSON array of objects: '
                f'[{{"handle": str, "platform": "{platform}", "followers": int, "profile_url": str}}]. '
                f'Max 10 results. Only return real accounts with public profiles.'
            )
            tasks.append(run_agent(url, goal, stealth=True))
            meta.append(platform)

    results = await asyncio.gather(*tasks, return_exceptions=True)

    seen = set()
    profiles = []
    for result in results:
        if isinstance(result, Exception):
            print(f"  [DISCOVERY] Agent exception: {result}")
            continue
        print(f"  [DISCOVERY] Raw result: {result}")
        keys = list(result.keys()); items = result.get(keys[0], []) if keys else []
        for item in items:
            key = (item.get("handle", "").lower(), item.get("platform", ""))
            if key not in seen and key[0]:
                seen.add(key)
                profiles.append(item)

    return profiles


# -----------------------------------------------------------
# Step 2a: Qualification — engagement stats per profile
# -----------------------------------------------------------
async def qualify_profile(profile: dict) -> dict:
    goal = (
        f'Go to this {profile["platform"]} profile and extract engagement stats. '
        f'Return JSON: {{"handle": str, "followers": int, "avg_likes": int, '
        f'"avg_comments": int, "engagement_rate": float, "post_frequency_per_week": float}}'
    )
    result = await run_agent(profile.get("profile_url", ""), goal, stealth=True)
    return {"handle": profile["handle"], "platform": profile["platform"], **result}


# -----------------------------------------------------------
# Step 2b: Audit — brand safety check
# -----------------------------------------------------------
async def audit_profile(profile: dict) -> dict:
    handle = profile["handle"]
    url    = f"https://www.google.com/search?q={handle}+influencer+controversy+scandal"
    goal   = (
        f'Search for any controversy, scandal, or brand risk associated with "{handle}". '
        f'Return JSON: {{"handle": str, "risk_flag": "green"|"amber"|"red", '
        f'"risk_evidence": str or null}}'
    )
    result = await run_agent(url, goal)
    return {"handle": handle, "platform": profile["platform"], **result}


# -----------------------------------------------------------
# Step 2c: Pricing — benchmark from Collabstr
# -----------------------------------------------------------
async def price_profile(profile: dict) -> dict:
    handle   = profile["handle"]
    platform = profile["platform"]
    url      = f"https://collabstr.com/search?username={handle}"
    goal     = (
        f'Find the influencer pricing for "{handle}" on {platform}. '
        f'Return JSON: {{"handle": str, "price_low": int, "price_high": int, "currency": "USD"}}'
    )
    result = await run_agent(url, goal)
    return {"handle": handle, "platform": platform, **result}


# -----------------------------------------------------------
# Step 2: Full parallel audit (qual + audit + pricing)
# -----------------------------------------------------------
async def run_full_audit(profiles: List[dict]) -> List[dict]:
    """
    Runs qualification, audit, and pricing in parallel for all profiles.
    Merges all results back onto each profile.
    """
    qual_tasks    = [qualify_profile(p)  for p in profiles]
    audit_tasks   = [audit_profile(p)    for p in profiles]
    pricing_tasks = [price_profile(p)    for p in profiles]

    qual_results, audit_results, pricing_results = await asyncio.gather(
        asyncio.gather(*qual_tasks,    return_exceptions=True),
        asyncio.gather(*audit_tasks,   return_exceptions=True),
        asyncio.gather(*pricing_tasks, return_exceptions=True),
    )

    # Index by handle for easy merge
    qual_map    = {r["handle"]: r for r in qual_results    if isinstance(r, dict)}
    audit_map   = {r["handle"]: r for r in audit_results   if isinstance(r, dict)}
    pricing_map = {r["handle"]: r for r in pricing_results if isinstance(r, dict)}

    enriched = []
    for profile in profiles:
        handle = profile["handle"]
        merged = {
            **profile,
            **qual_map.get(handle, {}),
            **audit_map.get(handle, {}),
            **pricing_map.get(handle, {})
        }
        enriched.append(merged)

    return enriched