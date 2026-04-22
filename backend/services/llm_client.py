"""
llm_client.py — Unified LLM client
Handles Gemini (primary) and Ollama (fallback) API calls,
plus JSON response parsing.
"""

import os
import re
import json
import asyncio
import httpx

# ── LLM Provider Config ──────────────────────────────────────
# Set LLM_PROVIDER to "gemini" (default) or "ollama"
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "gemini").lower()

# Gemini config
GEMINI_API_KEY  = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL    = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")
GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"

# Ollama config (optional fallback)
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_MODEL    = os.getenv("OLLAMA_MODEL", "llama3.2")

print(f"[LLM_CLIENT] Provider: {LLM_PROVIDER.upper()} "
      f"({'model=' + GEMINI_MODEL if LLM_PROVIDER == 'gemini' else 'model=' + OLLAMA_MODEL})")


# ============================================================
# OLLAMA
# ============================================================

async def ollama_chat(system: str, user: str) -> str:
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


# ============================================================
# GEMINI
# ============================================================

async def gemini_chat(system: str, user: str, retries: int = 5) -> str:
    """Call Google Gemini API via REST with exponential backoff on 429."""
    if not GEMINI_API_KEY or "your_api_key" in GEMINI_API_KEY:
        raise ValueError("GEMINI_API_KEY is not set correctly. Add it to your .env file.")

    # Try primary model first, fall back to alternative on persistent 429
    models_to_try = [GEMINI_MODEL, "gemini-1.5-flash", "gemini-1.5-flash-8b"]
    base_delay = 2

    for model in models_to_try:
        url = f"{GEMINI_BASE_URL}/models/{model}:generateContent?key={GEMINI_API_KEY}"
        payload = {
            "system_instruction": {"parts": [{"text": system}]},
            "contents": [{"parts": [{"text": user}]}],
            "generationConfig": {"temperature": 0.7, "maxOutputTokens": 4096}
        }

        for attempt in range(retries):
            try:
                async with httpx.AsyncClient(timeout=120) as client:
                    resp = await client.post(url, json=payload)

                    # Handle 429 specifically with backoff
                    if resp.status_code == 429:
                        if attempt < retries - 1:
                            wait = base_delay * (2 ** attempt)
                            print(f"  [LLM] {model} 429 (Rate Limit). Retrying in {wait}s (attempt {attempt+1}/{retries})...")
                            await asyncio.sleep(wait)
                            continue
                        else:
                            print(f"  [LLM] {model} rate limit exhausted, trying next model...")
                            break  # Break retry loop to try next model

                    if resp.status_code == 200:
                        data = resp.json()
                        if not data.get("candidates"):
                            print(f"  [LLM] {model} blocked/empty response: {data}")
                            return ""

                        parts = data["candidates"][0].get("content", {}).get("parts", [])
                        if not parts:
                            return ""

                        return parts[0].get("text", "").strip()
                    else:
                        print(f"  [LLM] {model} error {resp.status_code}, trying next model...")
                        break  # Break retry loop to try next model

            except Exception as e:
                print(f"  [LLM] {model} request failed: {e}")
                if attempt < retries - 1:
                    await asyncio.sleep(base_delay)
                    continue
                else:
                    break

    raise Exception("All Gemini models rate limited or failed. Try again in a minute.")


# ============================================================
# UNIFIED ROUTER
# ============================================================

async def llm_chat(system: str, user: str) -> str:
    """Unified LLM router — Gemini only with retry."""
    return await gemini_chat(system, user)


# ============================================================
# JSON PARSING
# ============================================================

def parse_json(raw: str):
    """Parse JSON from LLM response, handling common issues like markdown blocks or chatty text."""
    if not raw:
        return []

    # 1. Try direct parse
    try:
        return json.loads(raw, strict=False)
    except json.JSONDecodeError:
        pass

    # 2. Extract content between first [ or { and last ] or }
    pattern = re.compile(r'(\[.*\]|\{.*\})', re.DOTALL)
    match = pattern.search(raw)

    if match:
        clean = match.group(1)
        # Remove potential markdown debris
        clean = clean.replace("```json", "").replace("```", "").strip()
        try:
            return json.loads(clean, strict=False)
        except json.JSONDecodeError:
            # Last ditch: remove control characters
            clean = re.sub(r'[\x00-\x09\x0b\x0c\x0e-\x1f]', '', clean)
            try:
                return json.loads(clean, strict=False)
            except json.JSONDecodeError:
                pass

    # 3. Last fallback: return empty structure
    print(f"  [LLM] FAILED TO PARSE JSON: {raw[:200]}...")
    return []
