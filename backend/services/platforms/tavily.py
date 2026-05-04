"""
tavily.py — Tavily web-search API client
=========================================
Improvements over previous version:
  FIX  BUG     : search_depth now respected (was hardcoded to "basic")
  FIX  BUG     : Removed 50_000 placeholder follower count — None used instead
  FIX  MISSING : three_tier_brand_safety_search() added (tier1/tier2/tier3 queries)
  FIX  MISSING : discover_social() now accepts pre-formatted Boolean queries from Chain 1
"""

import asyncio
import logging
import os
import re
from typing import Any

import httpx

logger = logging.getLogger(__name__)


def _api_key() -> str:
    key = os.getenv("TAVILY_API_KEY", "")
    if not key:
        raise ValueError("TAVILY_API_KEY is not set in environment.")
    return key


# ─────────────────────────────────────────────
# LOW-LEVEL API CALL
# ─────────────────────────────────────────────

async def tavily_search(
    query: str,
    max_results: int = 5,
    search_depth: str = "basic",          # FIX: now respected, was hardcoded
    include_domains: list[str] | None = None,
    exclude_domains: list[str] | None = None,
) -> list[dict]:
    """
    Search the web via Tavily. Returns list of result dicts.
    Each dict has: url, title, content, score.

    FIX: search_depth parameter now actually sent to API (was always "basic").
    """
    payload: dict[str, Any] = {
        "api_key":      _api_key(),
        "query":        query,
        "max_results":  max_results,
        "search_depth": search_depth,
    }
    if include_domains:
        payload["include_domains"] = include_domains
    if exclude_domains:
        payload["exclude_domains"] = exclude_domains

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post("https://api.tavily.com/search", json=payload)
            resp.raise_for_status()
            results = resp.json().get("results", [])
            logger.debug("tavily_search('%s'): %d results", query[:60], len(results))
            return results
    except httpx.HTTPStatusError as e:
        logger.error("Tavily HTTP %d for query '%s': %s",
                     e.response.status_code, query[:60], str(e)[:200])
        return []
    except Exception as e:
        logger.error("Tavily search failed for '%s': %s", query[:60], e)
        return []


# ─────────────────────────────────────────────
# PARSING HELPERS
# ─────────────────────────────────────────────

def extract_followers(text: str) -> int | None:
    """
    Parse follower count from a text snippet.

    FIX: Returns None (not 50_000) when no count found.
    A missing follower count should be treated as unknown, not assumed.
    Chain 3 will skip the follower filter for Instagram/Twitter profiles
    that have data_confidence="estimated".
    """
    text = text.lower()
    patterns = [
        (r"([\d.]+)\s*m(?:illion)?\s*(?:followers|subscribers|subs)", 1_000_000),
        (r"([\d.]+)\s*k\s*(?:followers|subscribers|subs)",             1_000),
        (r"([\d,]+)\s*(?:followers|subscribers|subs)",                  1),
    ]
    for pattern, multiplier in patterns:
        match = re.search(pattern, text)
        if match:
            try:
                num = float(match.group(1).replace(",", ""))
                return int(num * multiplier)
            except (ValueError, AttributeError):
                pass
    return None   # FIX: was returning 50_000


def extract_handle_from_url(url: str, platform: str) -> str:
    """Pull the username/handle out of a profile URL."""
    SKIP = {
        "p", "reel", "stories", "search", "explore",
        "watch", "results", "channel", "shorts", "feed",
        "hashtag", "tags", "about",
    }
    patterns = {
        "instagram": r"instagram\.com/([^/?#\s]+)",
        "twitter":   r"(?:twitter|x)\.com/([^/?#\s]+)",
        "youtube":   r"youtube\.com/(?:@|c/|user/)?([^/?#\s]+)",
    }
    pat = patterns.get(platform, "")
    if pat:
        m = re.search(pat, url or "")
        if m:
            handle = m.group(1).strip("/")
            if handle.lower() not in SKIP and len(handle) > 1:
                return handle
    return ""


def parse_tavily_profiles(results: list[dict], platform: str) -> list[dict]:
    """
    Convert raw Tavily search results into profile dicts.
    Only returns profiles where we found a real handle from a profile URL.
    """
    platform_domains = {
        "instagram": ("instagram.com",),
        "twitter":   ("twitter.com", "x.com"),
        "youtube":   ("youtube.com",),
    }
    domains = platform_domains.get(platform, ())

    profiles: list[dict] = []
    seen: set[str] = set()

    for r in results:
        url     = r.get("url", "")
        title   = r.get("title", "")
        content = r.get("content", "")

        if not any(d in url for d in domains):
            continue

        handle = extract_handle_from_url(url, platform)
        if not handle or handle in seen:
            continue

        text      = f"{title} {content}"
        followers = extract_followers(text)  # FIX: may be None

        seen.add(handle)
        profiles.append({
            "handle":          handle,
            "platform":        platform,
            "followers":       followers,     # FIX: None if unknown, not 50_000
            "profile_url":     url,
            "data_confidence": "estimated",   # Tavily-sourced = always estimated
        })

    return profiles


# ─────────────────────────────────────────────
# DISCOVERY
# ─────────────────────────────────────────────

async def discover_social_from_queries(
    boolean_queries: list[str],
    platform: str,
    max_results_per_query: int = 5,
) -> list[dict]:
    """
    FIX: Accepts pre-formatted Boolean queries from Chain 1 (ExpandedKeywordSet).

    Old behaviour: built plain-text queries like "skincare influencer instagram profile followers"
    New behaviour: uses exact Boolean strings like:
        '"vitamin c serum honest review" ("collab" OR "gifted" OR "ad") site:instagram.com'

    These are far more likely to return actual creator profiles vs. news articles.
    """
    tasks = [
        tavily_search(
            q,
            max_results=max_results_per_query,
            search_depth="advanced",
        )
        for q in boolean_queries
    ]
    all_results = await asyncio.gather(*tasks, return_exceptions=True)

    seen: set[str] = set()
    profiles: list[dict] = []

    for results in all_results:
        if isinstance(results, Exception):
            logger.error("Tavily discovery query failed: %s", results)
            continue
        for p in parse_tavily_profiles(results, platform):
            if p["handle"] not in seen:
                seen.add(p["handle"])
                profiles.append(p)

    logger.info("[Tavily] %s discovery: %d profiles found", platform, len(profiles))
    return profiles


# ─────────────────────────────────────────────
# THREE-TIER BRAND SAFETY SEARCH  (FIX MISSING)
# ─────────────────────────────────────────────

class BrandSafetyFindings:
    """Structured findings from a three-tier brand safety scan."""

    def __init__(self):
        self.tier1_findings: list[str] = []   # Hard disqualifiers (cancel/hate/legal)
        self.tier2_findings: list[str] = []   # High risk flags (political/competitor/deleted)
        self.tier3_findings: list[str] = []   # Soft flags (profanity/old mentions)
        self.ftc_compliant: bool | None = None  # True = compliant, False = violation found, None = unknown
        self.raw_results:   dict[str, list] = {}

    @property
    def has_hard_disqualifier(self) -> bool:
        return len(self.tier1_findings) > 0

    @property
    def risk_level(self) -> str:
        if self.has_hard_disqualifier:
            return "high_risk"
        if self.tier2_findings:
            return "risk"
        return "safe"

    def to_dict(self) -> dict:
        return {
            "risk_level":       self.risk_level,
            "tier1_findings":   self.tier1_findings,
            "tier2_findings":   self.tier2_findings,
            "tier3_findings":   self.tier3_findings,
            "ftc_compliant":    self.ftc_compliant,
            "has_hard_disqualifier": self.has_hard_disqualifier,
        }


async def three_tier_brand_safety_search(
    handle: str,
    hard_disqualifiers: list[str],
    high_risk_flags: list[str],
    competitor_brands: list[str],
    lookback_months: int = 6,
) -> BrandSafetyFindings:
    """
    FIX MISSING: Runs the three-tier brand safety scan from our agency research.

    Tier 1 (Hard disqualifiers — auto reject):
        controversy, cancel, hate speech, arrests, lawsuits

    Tier 2 (High risk — flag with RED warning):
        political content, competitor brand conflicts, legal issues

    Tier 3 (Soft flags — note with AMBER):
        FTC/ASCI ad disclosure check (sponsored without #ad)

    Three separate Tavily queries, one per tier. Results mapped to severity.
    """
    findings = BrandSafetyFindings()

    # Build tier-specific queries using handle as anchor
    disqualifier_terms = " OR ".join(
        f'"{d}"' for d in (hard_disqualifiers[:3] if hard_disqualifiers
                           else ["hate speech", "racist", "slur"])
    )
    tier1_query = (
        f'"{handle}" '
        f'(controversy OR "cancel" OR backlash OR banned OR {disqualifier_terms})'
    )

    competitor_terms = " OR ".join(f'"{c}"' for c in competitor_brands[:3]) \
                       if competitor_brands else '"competitor brand"'
    tier2_query = (
        f'"{handle}" '
        f'(arrested OR lawsuit OR apology OR scandal OR political OR '
        f'{competitor_terms} ambassador OR partnership)'
    )

    # Tier 3: FTC / ASCI compliance — check if they disclose ads properly
    tier3_query = (
        f'"{handle}" '
        f'(sponsored OR "#ad" OR "#gifted" OR "#collab" OR "brand deal") '
        f'-"#ad" -"#sponsored"'  # exclude posts that DO have disclosure
    )

    logger.info("[Brand Safety] Scanning %s (3-tier)", handle)

    # Run all three concurrently
    tier1_results, tier2_results, tier3_results = await asyncio.gather(
        tavily_search(tier1_query, max_results=3, search_depth="advanced"),
        tavily_search(tier2_query, max_results=3, search_depth="advanced"),
        tavily_search(tier3_query, max_results=3, search_depth="basic"),
        return_exceptions=True,
    )

    findings.raw_results = {
        "tier1": tier1_results if not isinstance(tier1_results, Exception) else [],
        "tier2": tier2_results if not isinstance(tier2_results, Exception) else [],
        "tier3": tier3_results if not isinstance(tier3_results, Exception) else [],
    }

    def _extract_finding(result: dict) -> str:
        return f"{result.get('title', '')} — {result.get('content', '')[:150]}"

    # Tier 1: High-confidence bad signals → hard disqualifier
    for r in (findings.raw_results["tier1"] or []):
        content = (r.get("content", "") + r.get("title", "")).lower()
        if any(kw in content for kw in ["controversy", "cancel", "hate", "racist",
                                         "banned", "lawsuit", "arrested"]):
            findings.tier1_findings.append(_extract_finding(r))

    # Tier 2: Medium severity signals
    for r in (findings.raw_results["tier2"] or []):
        content = (r.get("content", "") + r.get("title", "")).lower()
        for brand in competitor_brands:
            if brand.lower() in content and "partner" in content or "ambassador" in content:
                findings.tier2_findings.append(f"Possible {brand} partnership: {_extract_finding(r)}")
        if any(kw in content for kw in ["political", "apology", "scandal"]):
            findings.tier2_findings.append(_extract_finding(r))

    # Tier 3: FTC compliance check
    non_disclosed_count = len(findings.raw_results["tier3"] or [])
    if non_disclosed_count > 0:
        findings.ftc_compliant = False
        findings.tier3_findings.append(
            f"Found {non_disclosed_count} potential undisclosed brand mention(s)"
        )
    else:
        findings.ftc_compliant = True

    logger.info(
        "[Brand Safety] %s → risk=%s | tier1=%d | tier2=%d | tier3=%d",
        handle, findings.risk_level,
        len(findings.tier1_findings),
        len(findings.tier2_findings),
        len(findings.tier3_findings),
    )

    return findings


# ─────────────────────────────────────────────
# COMPETITOR INTEL
# ─────────────────────────────────────────────

async def find_competitor_influencers(
    competitor_brand: str,
    competitor_queries: list[str] | None = None,
) -> list[dict]:
    """
    Find influencers working with a competitor brand.

    FIX: Now accepts pre-formatted competitor queries from Chain 1.
    Falls back to building its own query if none provided.
    """
    if competitor_queries:
        queries = competitor_queries[:3]
    else:
        queries = [
            f'"{competitor_brand}" influencer ambassador "brand deal"',
            f'"{competitor_brand}" sponsored creator partnership',
        ]

    all_results = await asyncio.gather(
        *[tavily_search(q, max_results=5, search_depth="advanced") for q in queries],
        return_exceptions=True,
    )

    partnerships: list[dict] = []
    seen: set[str] = set()

    for results in all_results:
        if isinstance(results, Exception):
            continue
        for r in results:
            url   = r.get("url", "")
            title = r.get("title", "")
            for platform in ("instagram", "youtube", "twitter"):
                handle = extract_handle_from_url(url, platform)
                if handle and handle not in seen:
                    seen.add(handle)
                    partnerships.append({
                        "handle":   handle,
                        "platform": platform,
                        "evidence": f"{title[:100]}",
                        "competitor_brand": competitor_brand,
                        "data_confidence": "estimated",
                    })
                    break

    logger.info("[Competitor] Found %d potential partnerships for %s",
                len(partnerships), competitor_brand)
    return partnerships