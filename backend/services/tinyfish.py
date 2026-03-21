import httpx
import json
import os
import asyncio
from typing import List

TINYFISH_URL = "https://agent.tinyfish.ai/v1/automation/run-sse"

# Global registry of active run_ids
active_runs: list[str] = []

def _get_headers():
    return {
        "X-API-Key": os.getenv("TINYFISH_API_KEY"),
        "Content-Type": "application/json"
    }


# -----------------------------------------------------------
# Core: single agent call (SSE streaming → wait for COMPLETE)
# -----------------------------------------------------------
async def run_agent(url: str, goal: str, stealth: bool = False) -> dict:
    payload = {
        "url": url,
        "goal": goal,
        "browser_profile": "stealth" if stealth else "lite"
    }

    async with httpx.AsyncClient(timeout=120 if stealth else 60) as client:
        async with client.stream("POST", TINYFISH_URL, headers=_get_headers(), json=payload) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line.startswith("data: "):
                    continue
                event = json.loads(line[6:])

                # Track run_id when agent starts
                if event.get("type") == "STARTED":
                    run_id = event.get("run_id")
                    if run_id:
                        active_runs.append(run_id)

                if event.get("type") == "COMPLETE":
                    result = event.get("result", {})
                    if isinstance(result, str):
                        try:
                            result = json.loads(result)
                        except Exception:
                            result = {}
                    return result if isinstance(result, (dict, list)) else {}
    return {}


# -----------------------------------------------------------
# Step 1: Discovery — find influencer profiles by keyword
# -----------------------------------------------------------
async def discover_influencers(keywords: List[str], platforms: List[str]) -> List[dict]:
    PLATFORM_URLS = {
        "instagram": "https://www.instagram.com/explore/tags/{keyword}/",
        "twitter":   "https://x.com/search?q={keyword}&src=typed_query",
        "youtube":   "https://www.youtube.com/results?search_query={keyword}+influencer"
    }

    tasks = []
    for platform in platforms:
        if platform not in PLATFORM_URLS:
            continue
        for keyword in keywords:
            url = PLATFORM_URLS[platform].format(keyword=keyword.replace(" ", "+"))
            goal = (
                f'Find influencer accounts related to "{keyword}" on {platform}. '
                f'Return ONLY a raw JSON array, no markdown, no table, no explanation. '
                f'Format exactly: [{{"handle": "username", "platform": "{platform}", '
                f'"followers": 1000000, "profile_url": "https://..."}}]. '
                f'Max 5 results. Only real public accounts.'
            )
            tasks.append(run_agent(url, goal, stealth=True))

    print(f"  [DISCOVERY] Firing {len(tasks)} agents in parallel...")
    results = await asyncio.gather(*tasks, return_exceptions=True)

    seen = set()
    profiles = []

    for result in results:
        if isinstance(result, Exception):
            print(f"  [DISCOVERY] Agent error: {result}")
            continue

        print(f"  [DISCOVERY] Raw result: {result}")

        if isinstance(result, list):
            items = result
        elif isinstance(result, dict):
            keys = list(result.keys())
            items = result.get(keys[0], []) if keys else []
        else:
            continue

        for item in items:
            if not isinstance(item, dict):
                continue
            handle = item.get("handle", "").lower().strip()
            platform = item.get("platform", "")
            key = (handle, platform)
            if handle and key not in seen:
                seen.add(key)
                profiles.append(item)

    return profiles


# -----------------------------------------------------------
# Step 2a: Qualification — engagement stats per profile
# -----------------------------------------------------------
async def qualify_profile(profile: dict) -> dict:
    url = profile.get("profile_url", f'https://www.instagram.com/{profile["handle"]}/')
    goal = (
        f'Visit this Instagram profile page and extract stats. '
        f'Return ONLY a raw JSON object, no markdown. '
        f'Format: {{"handle": "{profile["handle"]}", "followers": int, '
        f'"avg_likes": int, "avg_comments": int, "engagement_rate": float}}'
    )
    result = await run_agent(url, goal, stealth=True)
    return {"handle": profile["handle"], "platform": profile["platform"], **result}


# -----------------------------------------------------------
# Step 2b: Audit — brand safety check
# -----------------------------------------------------------
async def audit_profile(profile: dict) -> dict:
    handle = profile["handle"]
    url    = f"https://www.google.com/search?q={handle}+influencer+controversy+scandal"
    goal = (
        f'Search for any controversy, scandal, or brand risk associated with "{handle}". '
        f'Return ONLY a raw JSON object, no markdown, no explanation. '
        f'Format: {{"handle": "{handle}", "risk_flag": "green or amber or red based on findings", "risk_evidence": "summary of findings or null if none"}}'
    )
    result = await run_agent(url, goal, stealth=False)
    return {"handle": handle, "platform": profile["platform"], **result}


# -----------------------------------------------------------
# Step 2c: Pricing — benchmark from Collabstr
# -----------------------------------------------------------
async def price_profile(profile: dict) -> dict:
    handle   = profile["handle"]
    platform = profile["platform"]
    url      = f"https://www.google.com/search?q={handle}+{platform}+influencer+rate+per+post+USD"
    goal = (
        f'Find the influencer pricing for "{handle}" on {platform}. '
        f'Return ONLY a raw JSON object, no markdown, no explanation. '
        f'Format: {{"handle": "{handle}", "price_low": 500, "price_high": 5000}}'
    )
    result = await run_agent(url, goal)
    return {"handle": handle, "platform": platform, **result}


# -----------------------------------------------------------
# Step 2: Full parallel audit (qual + audit + pricing)
# -----------------------------------------------------------
async def run_full_audit(profiles: List[dict]) -> List[dict]:
    qual_tasks    = [qualify_profile(p) for p in profiles]
    audit_tasks   = [audit_profile(p)   for p in profiles]
    pricing_tasks = [price_profile(p)   for p in profiles]

    print(f"  [AUDIT] Firing {len(profiles) * 3} agents in parallel (qual + audit + pricing)...")

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
            print(f"  [AUDIT] Raw result: {r}")
            handle = r.get("handle") if isinstance(r, dict) else None
            if handle:
                m[handle] = r
        return m

    qual_map    = to_map(qual_results)
    audit_map   = to_map(audit_results)
    pricing_map = to_map(pricing_results)

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


# -----------------------------------------------------------
# Cancel countermeasure
# -----------------------------------------------------------
async def cancel_all_runs() -> dict:
    """Cancel all tracked active TinyFish agents."""
    if not active_runs:
        return {"cancelled": 0, "message": "No active agents"}

    run_ids = active_runs.copy()
    active_runs.clear()

    print(f"  [CANCEL] Cancelling {len(run_ids)} agents...")
    async with httpx.AsyncClient(timeout=30) as client:
        tasks = [
            client.post(
                f"https://agent.tinyfish.ai/v1/runs/{run_id}/cancel",
                headers=_get_headers()
            )
            for run_id in run_ids
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

    cancelled = sum(
        1 for r in results
        if not isinstance(r, Exception) and r.status_code in (200, 204)
    )
    return {"cancelled": cancelled, "total": len(run_ids)}
