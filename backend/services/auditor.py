"""
auditor.py — Profile qualification, brand safety audit, and pricing
Uses rich YouTube data + Tavily web search for deep analysis.
"""

import asyncio
from typing import List

from services.platforms.youtube import youtube_channel_stats, build_channel_profile
from services.platforms.tavily import tavily_search


# ============================================================
# QUALIFICATION — Engagement stats
# ============================================================

async def qualify_profile(profile: dict) -> dict:
    """
    Get real engagement stats.
    YouTube  → build_channel_profile() for real likes/comments data.
    Instagram/Twitter → return empty dict, fill_missing_estimates() handles it.
    """
    platform   = profile.get("platform", "instagram")
    channel_id = profile.get("channel_id", "")

    if platform == "youtube" and channel_id:
        # build_channel_profile already fetched this during discovery —
        # if the rich fields are already present, skip the API call.
        if profile.get("engagement_source"):
            return {k: profile[k] for k in (
                "handle", "platform", "followers", "avg_views",
                "avg_likes", "avg_comments", "engagement_rate",
                "engagement_source", "description", "country",
                "channel_age_years", "topic_categories", "channel_keywords",
            ) if k in profile}

        # Otherwise fetch fresh
        rich = await build_channel_profile(channel_id, handle=profile.get("handle", ""))
        return rich if rich else {"handle": profile.get("handle"), "platform": platform}

    return {"handle": profile.get("handle", ""), "platform": platform}


# ============================================================
# BRAND SAFETY AUDIT
# ============================================================

async def audit_profile(profile: dict) -> dict:
    """
    Multi-signal brand safety audit combining:
      1. Tavily web search for controversy signals
      2. Channel metadata signals (age, country, description flags)
      3. Topic category mismatch detection
    """
    handle      = profile.get("handle", "")
    platform    = profile.get("platform", "")
    handle_lower = handle.lower().replace("-", " ").replace("_", " ")

    risk_flag     = "green"
    risk_evidence = None
    risk_sources  = []
    audit_notes   = []

    # ── Signal 1: Tavily controversy search ─────────────────
    results = await tavily_search(
        f'"{handle}" controversy OR scandal OR fraud OR accused',
        max_results=5
    )

    RED_SIGNALS = [
        "fraud", "arrested", "criminal conviction", "indicted",
        "hate speech", "sexual assault", "child abuse",
        "money laundering", "ponzi scheme",
    ]
    AMBER_SIGNALS = [
        "accused of scam", "accused of fraud", "brand boycott",
        "cancelled", "canceled", "sexual harassment allegation",
        "racist video", "offensive tweet", "misleading",
    ]

    combined_text = " ".join(
        f"{r.get('title', '')} {r.get('content', '')}" for r in results
    ).lower()

    def signal_near_handle(text: str, signal: str, window: int = 400) -> bool:
        """Return True only if signal appears within `window` chars of the handle."""
        idx = text.find(signal)
        while idx != -1:
            nearby = text[max(0, idx - window): idx + window]
            if handle_lower in nearby:
                return True
            idx = text.find(signal, idx + 1)
        return False

    for signal in RED_SIGNALS:
        if signal in combined_text and signal_near_handle(combined_text, signal):
            risk_flag     = "red"
            risk_evidence = f"Direct finding: '{signal}' associated with this creator"
            risk_sources  = [r.get("url", "") for r in results[:2]]
            break

    if risk_flag == "green":
        for signal in AMBER_SIGNALS:
            if signal in combined_text and signal_near_handle(combined_text, signal):
                risk_flag     = "amber"
                risk_evidence = f"Potential concern: '{signal}' associated with this creator"
                risk_sources  = [r.get("url", "") for r in results[:1]]
                break

    # ── Signal 2: Channel metadata checks (YouTube only) ────
    if platform == "youtube":
        channel_age  = profile.get("channel_age_years", 0)
        country      = profile.get("country", "")
        description  = (profile.get("description") or "").lower()
        followers    = profile.get("followers", 0)
        avg_likes    = profile.get("avg_likes", 0)
        avg_comments = profile.get("avg_comments", 0)

        # Very new channel with large subscriber count = suspicious
        if channel_age < 1.0 and followers > 500_000:
            if risk_flag == "green":
                risk_flag     = "amber"
                risk_evidence = f"Channel is only {channel_age} years old with {followers:,} subscribers — unusual growth"
            audit_notes.append(f"New channel: {channel_age}yr old, {followers:,} subs")

        # Comments disabled = low trust signal
        if avg_likes > 0 and avg_comments == 0:
            audit_notes.append("Comments disabled on recent videos")
            if risk_flag == "green":
                risk_flag     = "amber"
                risk_evidence = "Comments appear disabled — reduced audience trust signal"

        # Suspicious keywords in description
        SPAM_DESC = ["buy followers", "dm for promo", "paid promotion only",
                     "sub4sub", "follow4follow", "guaranteed views"]
        for kw in SPAM_DESC:
            if kw in description:
                risk_flag     = "red"
                risk_evidence = f"Suspicious phrase in channel description: '{kw}'"
                audit_notes.append(f"Spam signal in bio: '{kw}'")
                break

        if country:
            audit_notes.append(f"Channel country: {country}")

    return {
        "handle":        handle,
        "platform":      platform,
        "risk_flag":     risk_flag,
        "risk_evidence": risk_evidence,
        "risk_sources":  risk_sources,
        "audit_notes":   audit_notes,
    }


# ============================================================
# PRICING
# ============================================================

async def price_profile(profile: dict) -> dict:
    """
    Pricing is handled by fill_missing_estimates() in scoring.py.
    Return empty dict — no API call needed.
    """
    return {"handle": profile.get("handle", ""), "platform": profile.get("platform", "")}


# ============================================================
# NICHE RELEVANCE CHECK (new)
# ============================================================

def check_niche_relevance(profile: dict, brief_niche: str) -> dict:
    """
    Cross-check YouTube topic categories and channel keywords
    against the brand brief niche. Returns a relevance score 0-100.
    """
    niche_lower     = brief_niche.lower()
    topics          = " ".join(profile.get("topic_categories", [])).lower()
    keywords        = (profile.get("channel_keywords") or "").lower()
    description     = (profile.get("description") or "").lower()

    combined = f"{topics} {keywords} {description}"

    # Rough keyword match score
    niche_words = [w for w in niche_lower.split() if len(w) > 3]
    matches     = sum(1 for w in niche_words if w in combined)
    score       = min(100, int((matches / max(len(niche_words), 1)) * 100))

    return {
        "niche_relevance_score": score,
        "topic_categories":      profile.get("topic_categories", []),
    }


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
                print(f"  [AUDIT] Error: {r}")
                continue
            if isinstance(r, dict):
                key = r.get("handle", "").lower().strip().lstrip("@")
                if key:
                    m[key] = r
        return m

    qual_map    = to_map(qual_results)
    audit_map   = to_map(audit_results)
    pricing_map = to_map(pricing_results)

    def passes_hard_filter(profile: dict, qual_data: dict) -> bool:
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

        # Drop obvious bots
        if engagement_rate > 0 and engagement_rate < 0.05:
            print(f"  [FILTER] x {profile.get('handle')} — engagement too low ({engagement_rate}%)")
            return False

        # Sync verified follower count
        discovery_followers = profile.get("followers", 0) or 0
        if followers > 0 and discovery_followers > 0:
            ratio = max(discovery_followers, followers) / max(min(discovery_followers, followers), 1)
            if ratio > 10:
                profile["followers"] = followers
                profile["followers_verified"] = True

        return True

    niche = (brief_dict or {}).get("niche", "")

    enriched = []
    for profile in profiles:
        handle    = profile.get("handle", "").lower().strip().lstrip("@")
        qual_data = qual_map.get(handle, {})

        if not passes_hard_filter(profile, qual_data):
            continue

        merged = {
            **profile,
            **qual_data,
            **audit_map.get(handle, {}),
            **pricing_map.get(handle, {}),
        }

        # Add niche relevance score
        if niche:
            relevance = check_niche_relevance(merged, niche)
            merged.update(relevance)

        enriched.append(merged)

    print(f"  [AUDIT] {len(enriched)} profiles passed audit")
    return enriched
