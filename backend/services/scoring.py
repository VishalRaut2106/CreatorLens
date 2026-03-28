import httpx
import json
import os
import re
import asyncio

# ── LLM Provider Config ──────────────────────────────────────
# Set LLM_PROVIDER to "gemini" (default) or "ollama"
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "gemini").lower()

# Gemini config
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")
GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"

# Ollama config (optional fallback)
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.2")

print(f"[SCORING] LLM provider: {LLM_PROVIDER.upper()} "
      f"({'model=' + GEMINI_MODEL if LLM_PROVIDER == 'gemini' else 'model=' + OLLAMA_MODEL})")

SCORING_SYSTEM_PROMPT = """
You are an influencer marketing analyst. Score each candidate on:

1. Engagement Quality (40%) 
   - engagement_rate vs platform benchmarks
   - Instagram benchmark: >2% excellent, 1-2% good, 0.5-1% average, <0.5% poor
   - YouTube benchmark: >1% excellent, 0.5-1% good, <0.3% poor

2. Audience Authenticity (30%)
   - Does follower count match engagement numbers?
   - High followers + very low engagement = suspicious
   - Flag if engagement_rate < 0.3% regardless of followers

3. Niche Relevance (20%)
   - How well does their handle/summary match the brand brief niche?
   - Score 0-100 based on content alignment

4. Brand Safety (10%)
   - green = 100, amber = 50, red = 0

composite_score = (engagement * 0.4) + (authenticity * 0.3) + (relevance * 0.2) + (safety * 0.1)

CRITICAL: Only score influencers from the provided list.
Return ONLY a valid JSON array. No markdown, no explanation, no backticks.

Each object must have:
- handle (string)
- platform (string)
- composite_score (float 0-100)
- score_breakdown (object with: engagement, authenticity, relevance, safety — each 0-100)
- ai_summary (string, 2-3 sentences, professional tone mentioning their niche relevance)
- risk_flag (string: "green", "amber", or "red")

IMPORTANT: Every candidate must get a DIFFERENT composite_score. Do not give identical scores.
"""


async def _ollama_chat(system: str, user: str) -> str:
    """Call Ollama local LLM."""
    payload = {
        "model": OLLAMA_MODEL,
        "stream": False,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user}
        ]
    }
    async with httpx.AsyncClient(timeout=300) as client:
        resp = await client.post(f"{OLLAMA_BASE_URL}/api/chat", json=payload)
        resp.raise_for_status()
        return resp.json()["message"]["content"].strip()


async def _gemini_chat(system: str, user: str, retries: int = 5) -> str:
    """Call Google Gemini API via REST with retry on 429."""
    if not GEMINI_API_KEY:
        raise ValueError("GEMINI_API_KEY is not set. Add it to your .env file.")

    # Try primary model first, fall back to alternative on persistent 429
    models_to_try = [GEMINI_MODEL, "gemini-1.5-flash", "gemini-1.5-flash-8b"]

    for model in models_to_try:
        url = f"{GEMINI_BASE_URL}/models/{model}:generateContent?key={GEMINI_API_KEY}"
        payload = {
            "system_instruction": {"parts": [{"text": system}]},
            "contents": [{"parts": [{"text": user}]}],
            "generationConfig": {"temperature": 0.7, "maxOutputTokens": 4096}
        }
        for attempt in range(retries):
            async with httpx.AsyncClient(timeout=120) as client:
                resp = await client.post(url, json=payload)
                if resp.status_code == 429:
                    wait = 15 * (attempt + 1)
                    print(f"  [LLM] {model} 429 rate limit, retrying in {wait}s (attempt {attempt+1}/{retries})...")
                    await asyncio.sleep(wait)
                    continue
                if resp.status_code in (200,):
                    data = resp.json()
                    return data["candidates"][0]["content"]["parts"][0]["text"].strip()
                # non-429 error, try next model
                print(f"  [LLM] {model} error {resp.status_code}, trying next model...")
                break
        else:
            # all retries exhausted for this model, try next
            print(f"  [LLM] {model} rate limit exhausted, trying next model...")
            continue

    raise Exception("All Gemini models rate limited. Try again in a minute.")


async def _llm_chat(system: str, user: str) -> str:
    """Unified LLM router — Gemini only with retry."""
    return await _gemini_chat(system, user)


def _parse_json(raw: str):
    try:
        return json.loads(raw, strict=False)
    except json.JSONDecodeError:
        pass
    clean = raw.replace("```json", "").replace("```", "").strip()
    try:
        return json.loads(clean, strict=False)
    except json.JSONDecodeError:
        pass
    clean = re.sub(r'[\x00-\x09\x0b\x0c\x0e-\x1f]', '', clean)
    return json.loads(clean, strict=False)


def pre_filter_score(p):
    followers = p.get("followers", 0)
    # The dictionary might have actual string values or numbers or be missing
    if not isinstance(followers, (int, float)):
        try:
            followers = int(followers)
        except (ValueError, TypeError):
            followers = 0
            
    handle = p.get("handle", "")
    platform = p.get("platform", "")
    profile_url = p.get("profile_url", "")

    if followers < 5000:
        return 0  # hard disqualifier — not influential

    score = 0

    # ── Platform-weighted reach ───────────────────────────
    platform_weight = {
        "youtube": 1.5,   # hardest to game
        "twitter": 1.3,
        "instagram": 1.0,
        "tiktok": 0.8     # easiest to inflate
    }
    score += followers * platform_weight.get(platform, 1.0)

    # ── Fake follower signal ──────────────────────────────
    # Perfectly round numbers = likely inflated
    if followers % 1000000 == 0: score *= 0.7
    elif followers % 100000 == 0: score *= 0.85
    elif followers % 10000 == 0:  score *= 0.92

    # ── Handle authenticity ───────────────────────────────
    spam_signals = ["follow", "f4f", "promo", "viral",
                    "trending", "repost", "explore", "official123"]
    if any(s in handle.lower() for s in spam_signals):
        return 0  # hard disqualifier

    # Reward real-name style handles
    if len(handle) >= 4 and not handle.replace("_","").isdigit():
        score *= 1.1

    # ── Has valid profile URL ─────────────────────────────
    if not profile_url:
        score *= 0.7

    return score


# -----------------------------------------------------------
# Fallback estimator for missing agent data
# -----------------------------------------------------------
def fill_missing_estimates(profiles: list) -> list:
    """
    When TinyFish qualification or pricing agents fail (timeout,
    blocked, etc.), fill in reasonable estimates so the dashboard
    doesn't show N/A everywhere.
    """

    # Industry-average engagement rates by platform
    AVG_ENGAGEMENT = {
        "instagram": 1.5,
        "youtube":   0.5,
        "twitter":   0.5,
        "tiktok":    3.0,
    }

    # Rough CPP (cost-per-post) rates per 1K followers by platform
    # Returns (low_multiplier, high_multiplier) per 1K followers
    CPP_PER_1K = {
        "instagram": (8,  15),   # $8-15 per 1K followers
        "youtube":   (15, 30),   # $15-30 per 1K followers
        "twitter":   (3,  8),    # $3-8 per 1K followers
        "tiktok":    (5,  12),   # $5-12 per 1K followers
    }

    for p in profiles:
        platform = (p.get("platform") or "instagram").lower()
        followers = p.get("followers", 0)
        if not isinstance(followers, (int, float)):
            try:
                followers = int(followers)
            except (ValueError, TypeError):
                followers = 0

        # ── Fill engagement_rate ─────────────────────────────
        eng = p.get("engagement_rate")
        if eng is None or eng == 0 or eng == "N/A":
            avg = AVG_ENGAGEMENT.get(platform, 1.0)
            # Larger accounts tend to have lower engagement
            if followers > 5_000_000:
                est = round(avg * 0.4, 2)
            elif followers > 1_000_000:
                est = round(avg * 0.6, 2)
            elif followers > 500_000:
                est = round(avg * 0.8, 2)
            else:
                est = round(avg, 2)
            p["engagement_rate"] = est
            p["engagement_estimated"] = True
            print(f"  [ESTIMATE] {p.get('handle')} engagement → {est}% (estimated)")

        # ── Fill pricing ─────────────────────────────────────
        price_low  = p.get("price_low", 0) or 0
        price_high = p.get("price_high", 0) or 0
        if price_low == 0 and price_high == 0 and followers > 0:
            low_mult, high_mult = CPP_PER_1K.get(platform, (8, 15))
            k = followers / 1000
            p["price_low"]  = int(k * low_mult)
            p["price_high"] = int(k * high_mult)
            p["price_estimated"] = True
            print(f"  [ESTIMATE] {p.get('handle')} pricing → ${p['price_low']:,}–${p['price_high']:,} (estimated)")

    return profiles


async def score_influencers(enriched_profiles: list, brand_brief: dict) -> list:
    # Score in batches of 5 to avoid Ollama timeout
    BATCH_SIZE = 5
    all_scored = []

    for i in range(0, len(enriched_profiles), BATCH_SIZE):
        batch = enriched_profiles[i:i + BATCH_SIZE]
        print(f"  [SCORING] Batch {i // BATCH_SIZE + 1}: scoring {len(batch)} profiles...")

        user_message = f"""
Brand brief:
{json.dumps(brand_brief, indent=2)}

Influencer candidates:
{json.dumps(batch, indent=2)}

Score each candidate. Remember: every candidate must have a DIFFERENT composite_score.
Return the JSON array.
"""
        try:
            raw = await _llm_chat(SCORING_SYSTEM_PROMPT, user_message)
            scored = _parse_json(raw)
            all_scored.extend(scored)
        except Exception as e:
            print(f"  [SCORING] Batch {i // BATCH_SIZE + 1} failed: {e}")
            # Add fallback scores so pipeline doesn't break
            for p in batch:
                all_scored.append({
                    "handle": p.get("handle"),
                    "platform": p.get("platform"),
                    "composite_score": 50.0,
                    "risk_flag": p.get("risk_flag", "green"),
                    "ai_summary": f"{p.get('handle')} is a candidate in the {brand_brief.get('niche')} niche.",
                    "score_breakdown": {
                        "engagement": 50,
                        "authenticity": 50,
                        "relevance": 50,
                        "safety": 80
                    }
                })

    all_scored.sort(key=lambda x: x.get("composite_score", 0), reverse=True)
    return all_scored[:10]


async def expand_keywords(brief: dict) -> list:
    user_message = f"""
Given this brand brief, generate 5-8 search keywords to find relevant influencers.
Return ONLY a JSON array of strings. No explanation.

Niche: {brief.get('niche')}
Target audience: {brief.get('target_audience')}
"""
    raw = await _llm_chat("You are a helpful assistant.", user_message)
    return _parse_json(raw)


async def draft_outreach(influencer: dict, brief: dict) -> str:
    user_message = f"""
Write a short outreach DM to @{influencer.get('handle')} on behalf of a brand.

Brand details:
- Niche: {brief.get('niche')}
- Target audience: {brief.get('target_audience')}
- Budget: ${brief.get('budget_min')}–${brief.get('budget_max')}

Influencer details:
- Platform: {influencer.get('platform')}
- Followers: {influencer.get('followers')}
- Engagement rate: {influencer.get('engagement_rate')}%
- About them: {influencer.get('ai_summary')}

Rules:
- Max 80 words
- Mention something SPECIFIC about their content or audience
- Include the budget range naturally
- End with a clear question to start conversation
- Sound like a real human, not a template
- No emojis, no corporate speak
- Return ONLY the message, nothing else
"""
    return await _llm_chat(
        "You are a brand partnerships manager who writes personalized, genuine outreach messages.",
        user_message
    )