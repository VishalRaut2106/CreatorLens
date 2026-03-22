import httpx
import json
import os
import re

OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.2")

SCORING_SYSTEM_PROMPT = """
You are an influencer marketing analyst. Score each influencer candidate based on available data.

CRITICAL: Only score the exact influencers provided in the candidate data. 
Do NOT add, invent, or include any influencers not in the input list.
Return ONLY the influencers from the provided data.

Return ONLY a valid JSON array. No markdown, no explanation, no backticks.

Each object must have:
- handle (string)
- platform (string)
- composite_score (float 0-100)
- score_breakdown (object with: engagement_quality, brand_fit, risk_score, price_fairness — each 0-100)
- ai_summary (string, 2-3 sentences, professional tone mentioning their niche relevance)
- risk_flag (string: "green", "amber", or "red")

Scoring rules:
- engagement_quality: use engagement_rate if available. If missing, estimate from follower count (larger accounts typically have lower engagement). Weight: 35%
- brand_fit: score based on how well the handle/niche matches the brand brief. Weight: 30%
- risk_score: 100 = no risk, 0 = high risk. Use risk_flag if provided, otherwise default to 80. Weight: 25%
- price_fairness: compare price range to budget. If price data missing, score 50. Weight: 10%
- composite_score = (engagement_quality * 0.35) + (brand_fit * 0.30) + (risk_score * 0.25) + (price_fairness * 0.10)

IMPORTANT: Every candidate must get a DIFFERENT composite_score. Do not give identical scores.
"""


async def _ollama_chat(system: str, user: str) -> str:
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
            raw = await _ollama_chat(SCORING_SYSTEM_PROMPT, user_message)
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
                        "engagement_quality": 50,
                        "brand_fit": 50,
                        "risk_score": 80,
                        "price_fairness": 50
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
    raw = await _ollama_chat("You are a helpful assistant.", user_message)
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
    return await _ollama_chat(
        "You are a brand partnerships manager who writes personalized, genuine outreach messages.",
        user_message
    )