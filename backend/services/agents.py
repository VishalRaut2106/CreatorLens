"""
agents.py — COMPATIBILITY SHIM
This file re-exports functions from their new locations so existing
imports (test scripts, etc.) continue to work. No logic lives here.

New module locations:
  - platforms/youtube.py  → YouTube API client
  - platforms/tavily.py   → Tavily API client + competitor intel
  - discovery.py          → Multi-platform discovery aggregator
  - auditor.py            → Qualification, audit, pricing
  - llm_client.py         → LLM client (Gemini/Ollama)
  - scoring.py            → Business scoring logic
  - outreach.py           → Outreach message drafting
  - pipeline.py           → Campaign execution pipeline
"""

# Re-exports for backward compatibility
from services.platforms.youtube import youtube_search as _youtube_search
from services.platforms.youtube import youtube_channel_stats as _youtube_channel_stats
from services.platforms.tavily import find_competitor_influencers
from services.discovery import discover_influencers
from services.auditor import run_full_audit

# Compatibility shim
active_runs: list[str] = []


async def cancel_all_runs() -> dict:
    """No real browser agents to cancel. Returns success for compatibility."""
    active_runs.clear()
    return {"cancelled": 0, "message": "No active agents (using free API stack)"}
