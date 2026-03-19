import httpx
import json
import os

OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.2")

SCORING_SYSTEM_PROMPT = """
You are an influencer marketing analyst. Given raw data about influencer candidates,
score each one and write a brief dossier summary.

Return ONLY a valid JSON array. No markdown, no explanation, no backticks.

Each object must have:
- handle (string)
- platform (string)
- composite_score (float 0-100)
- score_breakdown (object with: engagement_quality, brand_fit, risk_score, price_fairness — each 0-100)
- ai_summary (string, 2-3 sentences, professional tone)
- risk_flag (string: "green", "amber", or "red")

Scoring weights:
- engagement_quality: 35%
- brand_fit: 30%
- risk_score: 25% (100 = no risk, 0 = high risk)
- price_fairness: 10%

composite_score = weighted average of the four components.
"""


async def _ollama_chat(system: str, user: str) -> str:
    payload = {
        "model": OLLAMA_MODEL,
        "stream": False,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user",   "content": user}
        ]
    }

    async with httpx.AsyncClient(timeout=120) as client:
        resp = await client.post(
            f"{OLLAMA_BASE_URL}/api/chat",
            json=payload
        )
        resp.raise_for_status()
        return resp.json()["message"]["content"].strip()


def _parse_json(raw: str):
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        clean = raw.replace("```json", "").replace("```", "").strip()
        return json.loads(clean)


async def score_influencers(enriched_profiles: list, brand_brief: dict) -> list:
    user_message = f"""
Brand brief:
{json.dumps(brand_brief, indent=2)}

Influencer candidate data:
{json.dumps(enriched_profiles, indent=2)}

Score each candidate and return the JSON array.
"""
    raw = await _ollama_chat(SCORING_SYSTEM_PROMPT, user_message)
    scored = _parse_json(raw)
    scored.sort(key=lambda x: x.get("composite_score", 0), reverse=True)
    return scored[:10]


async def expand_keywords(brief: dict) -> list:
    user_message = f"""
Given this brand brief, generate 5-8 search keywords to find relevant influencers.
Return ONLY a JSON array of strings. No explanation.

Niche: {brief.get('niche')}
Target audience: {brief.get('target_audience')}
"""
    raw = await _ollama_chat("You are a helpful assistant.", user_message)
    return _parse_json(raw)