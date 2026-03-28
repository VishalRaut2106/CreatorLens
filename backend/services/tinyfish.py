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
async def run_agent(url: str, goal: str, stealth: bool = False, retries: int = 1) -> dict:
    payload = {
        "url": url,
        "goal": goal,
        "browser_profile": "stealth" if stealth else "lite"
    }

    for attempt in range(retries):
        try:
            async with httpx.AsyncClient(timeout=60 if stealth else 45) as client:
                async with client.stream("POST", TINYFISH_URL, headers=_get_headers(), json=payload) as resp:
                    resp.raise_for_status()
                    current_run_id = None
                    async for line in resp.aiter_lines():
                        if not line.startswith("data: "):
                            continue
                        event = json.loads(line[6:])

                        # Track run_id when agent starts
                        if event.get("type") == "STARTED":
                            current_run_id = event.get("run_id")
                            if current_run_id:
                                active_runs.append(current_run_id)
                                print(f"  [AGENT FIRED] {current_run_id} -> {url}")

                        if event.get("type") == "COMPLETE":
                            if current_run_id and current_run_id in active_runs:
                                active_runs.remove(current_run_id)
                            
                            result = event.get("result", {})
                            if isinstance(result, str):
                                try:
                                    result = json.loads(result)
                                except Exception:
                                    result = {}
                            return result if isinstance(result, (dict, list)) else {}
            return {}
        except Exception as e:
            print(f"  [AGENT RETRY] Attempt {attempt + 1}/{retries} failed for {url}: {e}")
            if attempt < retries - 1:
                await asyncio.sleep(2)
    return {}


# -----------------------------------------------------------
# Step 1: Discovery — find influencer profiles by keyword
# -----------------------------------------------------------
async def discover_influencers(keywords: List[str], platforms: List[str]) -> List[dict]:
    PLATFORM_URLS = {
        "instagram": "https://www.google.com/search?q={keyword}+influencer+instagram+followers",
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
                f'Max 3 results. Only real public accounts.'
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
            handle = item.get("handle", "").lower().strip().lstrip("@")
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
    platform = profile.get("platform", "instagram")
    url = profile.get("profile_url", f'https://www.{platform}.com/{profile["handle"]}/')
    goal = (
        f'Visit this {platform} profile page at {url} and extract these stats. '
        f'Return ONLY a raw JSON object. '
        f'Format: {{"handle": "{profile["handle"]}", "followers": int, '
        f'"avg_likes": int, "avg_comments": int, "engagement_rate": float}} '
        f'If you cannot find exact numbers, estimate from visible data. '
        f'Never return null — always return a number even if approximate.'
    )
    result = await run_agent(url, goal, stealth=True)

    # Fallback estimation from followers when agent can't scrape
    followers = profile.get("followers", 0)
    if not result.get("engagement_rate"):
        if platform == "youtube":
            rate = 1.5 if followers > 1_000_000 else 2.5
        else:
            rate = 1.2 if followers > 1_000_000 else 2.8
        result["engagement_rate"] = round(rate, 2)
        result["engagement_estimated"] = True

    return {"handle": profile["handle"], "platform": profile["platform"], **result}


# -----------------------------------------------------------
# Step 2b: Audit — brand safety check
# -----------------------------------------------------------
async def audit_profile(profile: dict) -> dict:
    handle = profile["handle"]
    url = f"https://www.google.com/search?q={handle}+influencer+controversy+scandal"
    goal = (
        f'Search Google for "{handle} controversy scandal" and read the TOP 3 results carefully. '
        f'ONLY flag as red/amber if there is DIRECT evidence of: hate speech, fraud, abuse, '
        f'criminal activity, or major brand boycott involving THIS specific person. '
        f'General negative YouTube comments or mild criticism = green. '
        f'A tech channel covering controversial topics ≠ controversial creator. '
        f'Return ONLY raw JSON: {{"handle": "{handle}", "risk_flag": "green/amber/red", '
        f'"risk_evidence": "specific finding or null", "risk_sources": []}} '
        f'When in doubt, return green.'
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
        f'Find or estimate the influencer pricing for "{handle}" on {platform}. '
        f'Based on their follower count and niche, estimate a realistic rate range. '
        f'Return ONLY a raw JSON object. '
        f'Format: {{"handle": "{handle}", "price_low": int, "price_high": int}} '
        f'Always return numbers — estimate if exact data not found.'
    )
    result = await run_agent(url, goal)

    # Fallback pricing tiers when agent can't scrape
    if not result.get("price_low"):
        followers = profile.get("followers", 0)
        tiers = {
            "youtube":   [(10_000_000, 50000, 150000), (1_000_000, 10000, 50000), (0, 1000, 10000)],
            "instagram": [(10_000_000, 20000, 80000),  (1_000_000, 5000, 20000),  (0, 500, 5000)],
            "twitter":   [(10_000_000, 15000, 50000),  (1_000_000, 3000, 15000),  (0, 300, 3000)],
        }
        for threshold, low, high in tiers.get(platform, tiers["instagram"]):
            if followers >= threshold:
                result["price_low"] = low
                result["price_high"] = high
                result["price_estimated"] = True
                break

    return {"handle": handle, "platform": platform, **result}


# -----------------------------------------------------------
# Step 2: Full parallel audit (qual + audit + pricing)
# -----------------------------------------------------------
async def run_full_audit(profiles: List[dict], brief_dict: dict = None) -> List[dict]:
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
            handle = r.get("handle", "").lower().strip().lstrip("@") if isinstance(r, dict) else None
            if handle:
                m[handle] = r
        return m

    qual_map    = to_map(qual_results)
    audit_map   = to_map(audit_results)
    pricing_map = to_map(pricing_results)

    # After qual_map is built, apply hard disqualifiers
    def passes_hard_filter(profile, qual_data):
        engagement_rate = qual_data.get("engagement_rate", 0)
        # Convert to float safely
        if not isinstance(engagement_rate, (int, float)):
            try:
                engagement_rate = float(engagement_rate)
            except (ValueError, TypeError):
                engagement_rate = 0
                
        followers = qual_data.get("followers", 0)
        if not isinstance(followers, (int, float)):
            try:
                followers = int(followers)
            except (ValueError, TypeError):
                followers = 0

        # Hard disqualifier 1: engagement too low
        if engagement_rate > 0 and engagement_rate < 0.1:  # only drop obvious bots
            print(f"  [FILTER] ✗ {profile['handle']} — engagement too low ({engagement_rate}%)")
            return False

        # Hard disqualifier 2: follower count mismatch
        # Discovery said 2M but qual says 27K — trust qual data
        discovery_followers = profile.get("followers", 0)
        if not isinstance(discovery_followers, (int, float)):
            try:
                discovery_followers = int(discovery_followers)
            except (ValueError, TypeError):
                discovery_followers = 0
                
        if followers > 0 and discovery_followers > 0:
            ratio = max(discovery_followers, followers) / min(discovery_followers, followers)
            if ratio > 10:
                print(f"  [FILTER] ⚠ {profile['handle']} — follower count mismatch ({discovery_followers:,} vs {followers:,})")
                # Update with real data but don't disqualify
                profile["followers"] = followers
                profile["followers_verified"] = True

        return True

    enriched = []
    for profile in profiles:
        handle = profile["handle"]
        qual_data = qual_map.get(handle, {})
        
        if not passes_hard_filter(profile, qual_data):
            continue

        merged = {
            **profile,
            **qual_data,
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


# -----------------------------------------------------------
# Competitor Intel
# -----------------------------------------------------------
async def find_competitor_influencers(competitor_brand: str) -> List[dict]:
    url = f"https://www.google.com/search?q={competitor_brand}+influencer+ambassador+sponsored+partnership"
    goal = (
        f'Find which influencers or brand ambassadors {competitor_brand} has worked with. '
        f'Look for sponsored posts, brand deals, ambassador partnerships. '
        f'Return ONLY a raw JSON array, no markdown, no explanation. '
        f'Format: [{{"handle": "username", "platform": "instagram/youtube/twitter", '
        f'"evidence": "brief description of the partnership found"}}]. '
        f'Max 5 results. Only real confirmed partnerships.'
    )
    result = await run_agent(url, goal, stealth=False)
    if isinstance(result, list):
        return result
    keys = list(result.keys())
    return result.get(keys[0], []) if keys else []

