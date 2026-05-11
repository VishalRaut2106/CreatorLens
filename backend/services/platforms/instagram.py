"""
tavily.py - Tavily web-search API client.

Focused on reliable Instagram creator discovery:
- retries transient Tavily/network failures
- supports current Tavily search parameters
- uses Instagram domain filters in addition to query operators
- avoids treating missing follower counts as real data
- keeps brand-safety findings conservative when evidence is ambiguous
"""

from __future__ import annotations

import asyncio
import logging
import os
import random
import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Iterable
from urllib.parse import urlparse

import httpx

logger = logging.getLogger(__name__)

TAVILY_SEARCH_URL = "https://api.tavily.com/search"
DEFAULT_TIMEOUT_SECONDS = 30
MAX_TAVILY_RESULTS = 20
RETRYABLE_STATUS_CODES = {408, 409, 425, 429, 500, 502, 503, 504}
VALID_SEARCH_DEPTHS = {"basic", "advanced", "fast", "ultra-fast"}
VALID_TOPICS = {"general", "news", "finance"}

INSTAGRAM_PLATFORM = "instagram"
INSTAGRAM_DOMAINS = ("instagram.com",)

PLATFORM_DOMAINS: dict[str, tuple[str, ...]] = {
    INSTAGRAM_PLATFORM: INSTAGRAM_DOMAINS,
}

PROFILE_PATH_SKIP = {
    "",
    "about",
    "account",
    "accounts",
    "channel",
    "channels",
    "collabs",
    "explore",
    "feed",
    "hashtag",
    "hashtags",
    "help",
    "i",
    "intent",
    "login",
    "p",
    "privacy",
    "reel",
    "results",
    "search",
    "share",
    "shorts",
    "stories",
    "tag",
    "tags",
    "terms",
    "tv",
    "watch",
}


class TavilySearchError(RuntimeError):
    """Raised when Tavily cannot return usable search results."""


def _api_key() -> str:
    key = os.getenv("TAVILY_API_KEY", "").strip()
    if not key:
        raise ValueError("TAVILY_API_KEY is not set in environment.")
    return key


def _normalize_search_depth(search_depth: str) -> str:
    depth = (search_depth or "basic").strip().lower()
    if depth not in VALID_SEARCH_DEPTHS:
        raise ValueError(
            f"Unsupported search_depth={search_depth!r}. "
            f"Use one of {sorted(VALID_SEARCH_DEPTHS)}."
        )
    return depth


def _normalize_topic(topic: str) -> str:
    normalized = (topic or "general").strip().lower()
    if normalized not in VALID_TOPICS:
        raise ValueError(f"Unsupported topic={topic!r}. Use one of {sorted(VALID_TOPICS)}.")
    return normalized


def _clean_domain(domain: str) -> str:
    parsed = urlparse(domain if "://" in domain else f"https://{domain}")
    return parsed.netloc.lower().removeprefix("www.")


def _platform_include_domains(platform: str) -> list[str]:
    _ensure_instagram_platform(platform)
    return list(PLATFORM_DOMAINS.get(platform.lower(), ()))


def _ensure_instagram_platform(platform: str) -> None:
    if platform.lower() != INSTAGRAM_PLATFORM:
        raise ValueError("This Tavily client is configured for Instagram discovery only.")


def _content_text(result: dict[str, Any]) -> str:
    parts = [result.get("title", ""), result.get("content", ""), result.get("raw_content", "")]
    return " ".join(str(part) for part in parts if part)


async def tavily_search(
    query: str,
    max_results: int = 5,
    search_depth: str = "basic",
    include_domains: list[str] | None = None,
    exclude_domains: list[str] | None = None,
    *,
    topic: str = "general",
    time_range: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    chunks_per_source: int | None = None,
    include_answer: bool | str = False,
    include_raw_content: bool | str = False,
    include_favicon: bool = False,
    exact_match: bool = False,
    auto_parameters: bool = False,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    retries: int = 2,
    client: httpx.AsyncClient | None = None,
    raise_on_error: bool = False,
) -> list[dict[str, Any]]:
    """
    Search the web via Tavily and return result dictionaries.

    By default this function preserves the old forgiving behavior and returns []
    after logging an error. Set raise_on_error=True in tests or pipelines where a
    Tavily failure should stop the chain.
    """
    if not query or not query.strip():
        return []

    depth = _normalize_search_depth(search_depth)
    payload: dict[str, Any] = {
        "api_key": _api_key(),
        "query": query.strip(),
        "max_results": max(0, min(int(max_results), MAX_TAVILY_RESULTS)),
        "search_depth": depth,
        "topic": _normalize_topic(topic),
        "include_answer": include_answer,
        "include_raw_content": include_raw_content,
        "include_favicon": include_favicon,
        "exact_match": exact_match,
        "auto_parameters": auto_parameters,
    }

    if include_domains:
        payload["include_domains"] = [_clean_domain(d) for d in include_domains]
    if exclude_domains:
        payload["exclude_domains"] = [_clean_domain(d) for d in exclude_domains]
    if time_range:
        payload["time_range"] = time_range
    if start_date:
        payload["start_date"] = start_date
    if end_date:
        payload["end_date"] = end_date
    if chunks_per_source is not None and depth == "advanced":
        payload["chunks_per_source"] = max(1, min(int(chunks_per_source), 3))

    owns_client = client is None
    if owns_client:
        client = httpx.AsyncClient(timeout=timeout_seconds)

    assert client is not None
    try:
        for attempt in range(retries + 1):
            try:
                resp = await client.post(TAVILY_SEARCH_URL, json=payload)
                if resp.status_code in RETRYABLE_STATUS_CODES and attempt < retries:
                    retry_after = resp.headers.get("Retry-After")
                    await _sleep_before_retry(attempt, retry_after)
                    continue
                resp.raise_for_status()
                data = resp.json()
                results = data.get("results", [])
                if not isinstance(results, list):
                    logger.warning("Tavily returned non-list results for %r: %r", query[:80], results)
                    return []
                logger.debug("tavily_search(%r): %d results", query[:80], len(results))
                return results
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                if attempt < retries:
                    await _sleep_before_retry(attempt)
                    continue
                raise TavilySearchError(f"Tavily transport failure for {query[:80]!r}: {exc}") from exc
            except httpx.HTTPStatusError as exc:
                body = exc.response.text[:500] if exc.response is not None else ""
                raise TavilySearchError(
                    f"Tavily HTTP {exc.response.status_code} for {query[:80]!r}: {body}"
                ) from exc
            except ValueError as exc:
                raise TavilySearchError(f"Tavily returned invalid JSON for {query[:80]!r}") from exc
    except Exception as exc:
        if raise_on_error:
            raise
        logger.error("Tavily search failed for %r: %s", query[:80], exc)
        return []
    finally:
        if owns_client:
            await client.aclose()

    return []


async def _sleep_before_retry(attempt: int, retry_after: str | None = None) -> None:
    if retry_after:
        try:
            await asyncio.sleep(min(float(retry_after), 10))
            return
        except ValueError:
            pass
    await asyncio.sleep(min(0.5 * (2**attempt) + random.uniform(0, 0.25), 5))


def extract_followers(text: str) -> int | None:
    """
    Parse follower/subscriber count from text.

    Returns None when no count is found. A missing count is unknown, not zero.
    """
    text = (text or "").lower()
    patterns = [
        (r"([\d]+(?:[.,]\d+)?)\s*(?:m|mn|million)\s*(?:followers|subscribers|subs)", 1_000_000),
        (r"([\d]+(?:[.,]\d+)?)\s*k\s*(?:followers|subscribers|subs)", 1_000),
        (r"([\d][\d,.\s]+)\s*(?:followers|subscribers|subs)", 1),
    ]
    for pattern, multiplier in patterns:
        match = re.search(pattern, text)
        if not match:
            continue
        raw_number = match.group(1).replace(" ", "").replace(",", "")
        try:
            return int(float(raw_number) * multiplier)
        except ValueError:
            logger.debug("Could not parse follower count from %r", raw_number)
    return None


def extract_handle_from_url(url: str, platform: str) -> str:
    """Pull a username/handle out of a profile URL."""
    if not url:
        return ""

    platform = platform.lower()
    _ensure_instagram_platform(platform)
    parsed = urlparse(url)
    host = parsed.netloc.lower().removeprefix("www.").removeprefix("m.")
    path_parts = [part for part in parsed.path.split("/") if part]

    if not any(host == d or host.endswith(f".{d}") for d in PLATFORM_DOMAINS.get(platform, ())):
        return ""
    if not path_parts:
        return ""

    candidate = path_parts[0]

    candidate = candidate.strip().lstrip("@")
    if not candidate or candidate.lower() in PROFILE_PATH_SKIP:
        return ""
    if len(candidate) <= 1:
        return ""
    return candidate


def parse_tavily_profiles(results: list[dict[str, Any]], platform: str) -> list[dict[str, Any]]:
    """
    Convert raw Tavily search results into profile dictionaries.

    Only returns profiles where a real handle can be derived from a known
    platform profile URL.
    """
    platform = platform.lower()
    profiles: list[dict[str, Any]] = []
    seen: set[str] = set()

    for result in results:
        url = str(result.get("url", ""))
        handle = extract_handle_from_url(url, platform)
        dedupe_key = handle.lower()
        if not handle or dedupe_key in seen:
            continue

        seen.add(dedupe_key)
        profiles.append(
            {
                "handle": handle,
                "platform": platform,
                "followers": extract_followers(_content_text(result)),
                "profile_url": url,
                "source_title": result.get("title", ""),
                "source_score": result.get("score"),
                "data_confidence": "estimated",
            }
        )

    return profiles


async def discover_social_from_queries(
    boolean_queries: list[str],
    platform: str = INSTAGRAM_PLATFORM,
    max_results_per_query: int = 5,
    *,
    fallback_queries: list[str] | None = None,
    concurrency: int = 4,
) -> list[dict[str, Any]]:
    """
    Discover social profiles using pre-formatted Boolean queries.

    If the strict Boolean queries return no profiles, the optional fallback
    queries are tried with the same platform domain filter.
    """
    platform = platform.lower()
    _ensure_instagram_platform(platform)
    queries = [q for q in boolean_queries if q and q.strip()]
    include_domains = _platform_include_domains(platform)
    profiles = await _discover_profiles_for_query_batch(
        queries,
        platform,
        include_domains,
        max_results_per_query,
        concurrency,
    )

    if not profiles and fallback_queries:
        logger.info("[Tavily] No %s profiles found; trying fallback queries", platform)
        profiles = await _discover_profiles_for_query_batch(
            fallback_queries,
            platform,
            include_domains,
            max_results_per_query,
            concurrency,
        )

    logger.info("[Tavily] %s discovery: %d profiles found", platform, len(profiles))
    return profiles


async def discover_social(
    keywords_or_queries: Iterable[str],
    platform: str = INSTAGRAM_PLATFORM,
    max_results_per_query: int = 5,
    *,
    preformatted_queries: bool | None = None,
) -> list[dict[str, Any]]:
    """
    Discover social profiles from either plain keywords or Boolean queries.

    Set preformatted_queries=True when Chain 1 already built queries such as:
    '"vitamin c serum honest review" ("collab" OR "gifted") site:instagram.com'
    """
    platform = platform.lower()
    _ensure_instagram_platform(platform)
    terms = [str(term).strip() for term in keywords_or_queries if term and str(term).strip()]
    if preformatted_queries is None:
        preformatted_queries = any(_looks_like_boolean_query(term) for term in terms)

    if preformatted_queries:
        queries = terms
        fallback_queries = [_fallback_profile_query(term, platform) for term in terms]
    else:
        queries = [
            f'"{term}" (creator OR influencer OR review OR collab OR gifted)'
            for term in terms
        ]
        fallback_queries = [f"{term} {platform} creator profile followers" for term in terms]

    return await discover_social_from_queries(
        queries,
        platform,
        max_results_per_query=max_results_per_query,
        fallback_queries=fallback_queries,
    )


async def _discover_profiles_for_query_batch(
    queries: list[str],
    platform: str,
    include_domains: list[str],
    max_results_per_query: int,
    concurrency: int,
) -> list[dict[str, Any]]:
    _ensure_instagram_platform(platform)
    semaphore = asyncio.Semaphore(max(1, concurrency))

    async def run_query(query: str) -> list[dict[str, Any]]:
        async with semaphore:
            return await tavily_search(
                query,
                max_results=max_results_per_query,
                search_depth="advanced",
                chunks_per_source=3,
                include_domains=include_domains or None,
            )

    all_results = await asyncio.gather(*(run_query(q) for q in queries), return_exceptions=True)
    return _dedupe_profiles(
        profile
        for results in all_results
        if not isinstance(results, Exception)
        for profile in parse_tavily_profiles(results, platform)
    )


def _dedupe_profiles(profiles: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[str, str]] = set()
    unique: list[dict[str, Any]] = []
    for profile in profiles:
        key = (str(profile.get("platform", "")).lower(), str(profile.get("handle", "")).lower())
        if key in seen:
            continue
        seen.add(key)
        unique.append(profile)
    return unique


def _looks_like_boolean_query(query: str) -> bool:
    lowered = query.lower()
    return any(token in lowered for token in (" or ", " and ", "site:", '"', "(", ")"))


def _fallback_profile_query(query: str, platform: str) -> str:
    cleaned = re.sub(r"\bsite:\S+", "", query, flags=re.IGNORECASE)
    cleaned = re.sub(r"\b(?:OR|AND|NOT)\b", " ", cleaned)
    cleaned = cleaned.replace("(", " ").replace(")", " ").replace('"', " ")
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return f"{cleaned} {platform} creator profile followers"


@dataclass
class BrandSafetyFindings:
    """Structured findings from a three-tier brand safety scan."""

    tier1_findings: list[str] = field(default_factory=list)
    tier2_findings: list[str] = field(default_factory=list)
    tier3_findings: list[str] = field(default_factory=list)
    ftc_compliant: bool | None = None
    raw_results: dict[str, list[dict[str, Any]]] = field(default_factory=dict)

    @property
    def has_hard_disqualifier(self) -> bool:
        return bool(self.tier1_findings)

    @property
    def risk_level(self) -> str:
        if self.has_hard_disqualifier:
            return "high_risk"
        if self.tier2_findings:
            return "risk"
        if self.tier3_findings:
            return "review"
        return "safe"

    def to_dict(self) -> dict[str, Any]:
        return {
            "risk_level": self.risk_level,
            "tier1_findings": self.tier1_findings,
            "tier2_findings": self.tier2_findings,
            "tier3_findings": self.tier3_findings,
            "ftc_compliant": self.ftc_compliant,
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
    Run a three-tier brand-safety scan for a creator handle.

    Tier 1: hard disqualifiers.
    Tier 2: high-risk flags and competitor conflicts.
    Tier 3: potential ad-disclosure issues.
    """
    handle = handle.strip().lstrip("@")
    findings = BrandSafetyFindings()
    if not handle:
        return findings

    start = _lookback_start_date(lookback_months)
    disqualifiers = hard_disqualifiers or ["hate speech", "racist", "slur", "fraud"]
    high_risk = high_risk_flags or ["political", "scandal", "apology", "lawsuit"]

    tier1_query = (
        f'"{handle}" '
        f'({_or_terms(["controversy", "cancelled", "backlash", "banned", *disqualifiers[:6]])})'
    )
    tier2_query = (
        f'"{handle}" '
        f'({_or_terms(["arrested", "lawsuit", "apology", "scandal", *high_risk[:6]])} '
        f'OR {_or_terms([*competitor_brands[:6], "ambassador", "partnership"])})'
    )
    tier3_query = (
        f'"{handle}" '
        '("sponsored" OR "gifted" OR "paid partnership" OR "brand deal" OR "collab")'
    )

    logger.info("[Brand Safety] Scanning %s (3-tier)", handle)

    tier1_results, tier2_results, tier3_results = await asyncio.gather(
        tavily_search(tier1_query, max_results=5, search_depth="advanced", start_date=start),
        tavily_search(tier2_query, max_results=5, search_depth="advanced", start_date=start),
        tavily_search(tier3_query, max_results=5, search_depth="basic", start_date=start),
        return_exceptions=True,
    )

    findings.raw_results = {
        "tier1": [] if isinstance(tier1_results, Exception) else tier1_results,
        "tier2": [] if isinstance(tier2_results, Exception) else tier2_results,
        "tier3": [] if isinstance(tier3_results, Exception) else tier3_results,
    }

    for result in findings.raw_results["tier1"]:
        content = _content_text(result).lower()
        if _contains_any(content, ["controversy", "cancel", "hate", "racist", "banned", "lawsuit", "arrested", *disqualifiers]):
            findings.tier1_findings.append(_extract_finding(result))

    for result in findings.raw_results["tier2"]:
        content = _content_text(result).lower()
        for brand in competitor_brands:
            if brand.lower() in content and ("partner" in content or "ambassador" in content or "collab" in content):
                findings.tier2_findings.append(f"Possible {brand} partnership: {_extract_finding(result)}")
        if _contains_any(content, high_risk):
            findings.tier2_findings.append(_extract_finding(result))

    for result in findings.raw_results["tier3"]:
        content = _content_text(result).lower()
        has_sponsored_signal = _contains_any(content, ["sponsored", "gifted", "paid partnership", "brand deal", "collab"])
        has_disclosure = _contains_any(content, ["#ad", "#sponsored", "paid partnership", "affiliate", "gifted"])
        if has_sponsored_signal and not has_disclosure:
            findings.tier3_findings.append(f"Potential disclosure review needed: {_extract_finding(result)}")

    if findings.tier3_findings:
        findings.ftc_compliant = False
    elif findings.raw_results["tier3"]:
        findings.ftc_compliant = None
    else:
        findings.ftc_compliant = None

    logger.info(
        "[Brand Safety] %s -> risk=%s | tier1=%d | tier2=%d | tier3=%d",
        handle,
        findings.risk_level,
        len(findings.tier1_findings),
        len(findings.tier2_findings),
        len(findings.tier3_findings),
    )
    return findings


def _lookback_start_date(lookback_months: int) -> str:
    days = max(1, int(lookback_months)) * 30
    return (date.today() - timedelta(days=days)).isoformat()


def _or_terms(terms: Iterable[str]) -> str:
    clean_terms = [str(term).strip() for term in terms if str(term).strip()]
    return " OR ".join(f'"{term}"' if " " in term else term for term in clean_terms)


def _contains_any(text: str, keywords: Iterable[str]) -> bool:
    lowered = text.lower()
    return any(str(keyword).lower() in lowered for keyword in keywords if keyword)


def _extract_finding(result: dict[str, Any]) -> str:
    title = str(result.get("title", "")).strip()
    content = str(result.get("content", "")).strip()
    url = str(result.get("url", "")).strip()
    summary = f"{title} - {content[:180]}".strip(" -")
    return f"{summary} ({url})" if url else summary


async def find_competitor_influencers(
    competitor_brand: str,
    competitor_queries: list[str] | None = None,
) -> list[dict[str, Any]]:
    """
    Find influencers working with a competitor brand.

    Accepts pre-formatted competitor queries and falls back to generated queries.
    """
    competitor_brand = competitor_brand.strip()
    if not competitor_brand and not competitor_queries:
        return []

    queries = (
        [q for q in competitor_queries[:3] if q.strip()]
        if competitor_queries
        else [
            f'"{competitor_brand}" influencer ambassador "brand deal" site:instagram.com',
            f'"{competitor_brand}" sponsored creator partnership site:instagram.com',
            f'"{competitor_brand}" "paid partnership" instagram creator site:instagram.com',
        ]
    )

    all_results = await asyncio.gather(
        *[
            tavily_search(
                query,
                max_results=8,
                search_depth="advanced",
                chunks_per_source=3,
                include_domains=list(INSTAGRAM_DOMAINS),
            )
            for query in queries
        ],
        return_exceptions=True,
    )

    partnerships: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()

    for results in all_results:
        if isinstance(results, Exception):
            continue
        for result in results:
            url = str(result.get("url", ""))
            title = str(result.get("title", ""))
            handle = extract_handle_from_url(url, INSTAGRAM_PLATFORM)
            key = (INSTAGRAM_PLATFORM, handle.lower())
            if not handle or key in seen:
                continue
            seen.add(key)
            partnerships.append(
                {
                    "handle": handle,
                    "platform": INSTAGRAM_PLATFORM,
                    "evidence": title[:100],
                    "evidence_url": url,
                    "competitor_brand": competitor_brand,
                    "data_confidence": "estimated",
                }
            )

    logger.info(
        "[Competitor] Found %d potential partnerships for %s",
        len(partnerships),
        competitor_brand,
    )
    return partnerships
