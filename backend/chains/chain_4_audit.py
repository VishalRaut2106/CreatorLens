"""
Chain 4 — Audit
================
Improvements over previous version:
  FIX  CRITICAL : LLM skeleton now uses pre-filled placeholder JSON, not raw Pydantic
                  model_json_schema() which sent $defs/$ref/anyOf to the LLM.
  FIX  MISSING  : Three-tier brand safety scan (tier1/tier2/tier3) fully implemented.
  FIX  MISSING  : Pricing sub-chain added with CPM estimate.
  FIX  MISSING  : Pre-computed ratios (view_to_sub, like_comment, median_er) passed
                  to LLM so it scores from data, not from raw numbers it must compute.
  FIX  BAD PATTERN: run_audit() now accepts groq_api_key as parameter.
  FIX  MISSING  : Tier 2 and Tier 3 are separate Tavily queries, not merged.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from typing import Literal, Any

from langchain_core.output_parsers import JsonOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import RunnableSerializable
from langchain_groq import ChatGroq
from pydantic import BaseModel, Field
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from chain_0_ICP import ICPProfile
from chain_2_discovery import RawCreatorProfile
from services.platforms.instagram import three_tier_brand_safety_search

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────
# NICHE + PLATFORM PRICING MULTIPLIERS
# ─────────────────────────────────────────────

NICHE_MULTIPLIERS: dict[str, float] = {
    "finance":       1.4, "investing":    1.4, "tech":      1.4,
    "legal":         1.4, "crypto":       1.3,
    "fitness":       1.2, "health":       1.2, "wellness":  1.2,
    "skincare":      1.1, "beauty":       1.1,
    "lifestyle":     1.0, "general":      1.0,
    "food":          0.95, "travel":      0.95,
    "entertainment": 0.9, "gaming":       0.9, "meme":      0.8,
}

PLATFORM_MULTIPLIERS: dict[str, float] = {
    "youtube":           1.8,
    "instagram_reel":    1.0,
    "instagram_story":   0.7,
    "instagram_post":    0.85,
    "twitter":           0.5,
}


# ─────────────────────────────────────────────
# PRICING SUB-CHAIN  (FIX MISSING)
# ─────────────────────────────────────────────

class PricingEstimate(BaseModel):
    base_rate_inr:       float  = Field(description="Base rate before multipliers (INR)")
    estimated_rate_inr:  float  = Field(description="Final rate after niche + platform multipliers (INR)")
    estimated_rate_usd:  float  = Field(description="Final rate in USD (approx)")
    estimated_reach:     int    = Field(description="Estimated unique reach (followers × 0.30)")
    cpm_usd:             float  = Field(description="Cost per 1000 impressions in USD")
    is_overpriced:       bool   = Field(description="True if CPM > $40 (above market rate)")
    niche_multiplier:    float
    platform_multiplier: float
    pricing_tier:        Literal["budget", "fair_value", "premium", "overpriced"]


INR_TO_USD = 0.012   # Update periodically

def compute_pricing(
    candidate: RawCreatorProfile,
    icp: ICPProfile,
) -> PricingEstimate:
    """
    Compute pricing estimate using the real agency formula.
    base_rate = followers × ER × 0.14
    """
    followers = candidate.followers or 0
    er        = (candidate.engagement_rate or 0) / 100

    # Base rate
    base_rate = followers * er * 0.14

    # Niche multiplier — match against primary niches
    niche_mult = 1.0
    for niche in icp.primary_niches:
        niche_lower = niche.lower()
        for key, mult in NICHE_MULTIPLIERS.items():
            if key in niche_lower:
                niche_mult = max(niche_mult, mult)
                break

    # Platform multiplier
    platform_key = candidate.platform
    if candidate.platform == "instagram":
        platform_key = "instagram_reel"   # Assume Reel format as primary
    platform_mult = PLATFORM_MULTIPLIERS.get(platform_key, 1.0)

    estimated_rate_inr = base_rate * niche_mult * platform_mult
    estimated_rate_usd = estimated_rate_inr * INR_TO_USD

    # Reach and CPM
    estimated_reach = int(followers * 0.30)
    cpm_usd = (estimated_rate_usd / estimated_reach * 1000) if estimated_reach > 0 else 0

    # Pricing tier
    if cpm_usd > 40:
        tier = "overpriced"
    elif cpm_usd > 25:
        tier = "premium"
    elif cpm_usd >= 5:
        tier = "fair_value"
    else:
        tier = "budget"

    return PricingEstimate(
        base_rate_inr=round(base_rate, 2),
        estimated_rate_inr=round(estimated_rate_inr, 2),
        estimated_rate_usd=round(estimated_rate_usd, 2),
        estimated_reach=estimated_reach,
        cpm_usd=round(cpm_usd, 2),
        is_overpriced=cpm_usd > 40,
        niche_multiplier=niche_mult,
        platform_multiplier=platform_mult,
        pricing_tier=tier,
    )


# ─────────────────────────────────────────────
# LLM AUDIT OUTPUT SCHEMA
# ─────────────────────────────────────────────

class AudienceQuality(BaseModel):
    audience_fit:        float | None = Field(default=None, ge=0, le=1, description="Age, location, language, interest alignment with ICP")
    authenticity_score:  float | None = Field(default=None, ge=0, le=1, description="Penalise fake followers, bot comments, zero engagement")
    sentiment_score:     float | None = Field(default=None, ge=-1, le=1, description="Positive vs negative audience sentiment")

class EngagementMetrics(BaseModel):
    engagement_rate:      float | None  = Field(default=None, description="Actual median ER from recent videos")
    engagement_vs_tier:   Literal["above_benchmark", "at_benchmark", "below_benchmark"] | None = Field(default=None)
    engagement_consistency: float | None = Field(default=None, ge=0, le=1, description="Stability — no sudden drops or suspicious spikes")
    top_content_type:     str | None    = Field(default=None, description="Best-performing content format e.g. 'tutorial', 'review', 'routine'")

class BrandSafetyAssessment(BaseModel):
    risk_level:           Literal["safe", "risk", "high_risk"]
    tier1_triggered:      bool   = Field(description="Hard disqualifier found (cancel/hate/legal)")
    tier2_triggered:      bool   = Field(description="High risk flag found (political/competitor/deleted posts)")
    tier3_triggered:      bool   = Field(description="Soft flag found (FTC compliance, old mentions)")
    ftc_compliant:        bool | None = Field(default=None, description="None = unknown, True = compliant, False = violations found")
    partnership_conflicts: list[str] = Field(default_factory=list, description="Competitor brands found in Tavily results")
    rationale:            str    = Field(description="1-2 sentence explanation of risk assessment")

class Credibility(BaseModel):
    credibility_score:    float | None = Field(default=None, ge=0, le=1, description="Content accuracy, expertise signals, credentials")
    niche_authority:      float | None = Field(default=None, ge=0, le=1, description="Topical depth — do they truly own this niche?")

class Compliance(BaseModel):
    disclosure_compliance: float | None = Field(default=None, ge=0, le=1, description="Ad disclosure rate (#ad/#sponsored/#gifted)")
    professionalism:       float | None = Field(default=None, ge=0, le=1, description="PR email in bio, works with contracts, responds timely")

class CreatorAudit(BaseModel):
    """Complete multi-dimensional audit produced by Chain 4 for each candidate."""
    audience_quality:   AudienceQuality
    engagement_metrics: EngagementMetrics
    brand_safety:       BrandSafetyAssessment
    credibility:        Credibility
    compliance:         Compliance
    audit_rationale:    str | None = Field(default=None, description="2-3 sentence summary of key factors driving these scores")


# ─────────────────────────────────────────────
# PROMPT  (FIX CRITICAL: pre-filled skeleton, not model_json_schema)
# ─────────────────────────────────────────────

AUDIT_SYSTEM_PROMPT = """\
You are an elite Influencer Marketing Auditor.
Your job: evaluate a creator candidate against the ICP and assign precise scores.

## Rules
1. Output ONLY valid JSON matching the skeleton. No preamble, no markdown.
2. Use the pre-computed metrics provided — do NOT recalculate them yourself.
3. If median_er is above strong_engagement_rate benchmark → engagement_vs_tier = "above_benchmark"
   If median_er is above min but below strong → "at_benchmark"
   If median_er is below min → "below_benchmark"
4. If brand_safety_tier1_findings is non-empty → tier1_triggered = true, risk_level = "high_risk".
5. If like_to_comment_ratio > 100 or == null with likes > 50 → set authenticity_score ≤ 0.3.
6. base the top_content_type on their recent video titles, not just categories.
7. CRITICAL: For numeric fields (floats), output raw numbers ONLY (e.g., 0.5, 0.0). DO NOT append '%' and DO NOT output `null`. If a value is unknown, estimate it based on available context or default to 0.0.
"""

AUDIT_HUMAN_PROMPT = """\
## Candidate Metrics (pre-computed — use these directly)
Handle: {handle}
Platform: {platform}
Followers: {followers}
Country: {country}

Engagement (MEDIAN-based):
  median_er:             {median_er}%  (tier min: {min_er}%, tier strong: {strong_er}%)
  view_to_sub_ratio:     {view_to_sub_ratio}  (healthy: >5%)
  like_to_comment_ratio: {like_to_comment_ratio}  (healthy: 20–50)
  engagement_source:     {engagement_source}

Channel context:
  Age:         {channel_age_years} years
  Description: {description}
  Categories:  {topic_categories}
  Keywords:    {channel_keywords}

Recent video titles (for niche relevance):
{recent_video_titles}

## ICP Context
Primary niches: {primary_niches}
Target audience: {target_audience}
Min ER benchmark: {min_er}%
Strong ER benchmark: {strong_er}%

## Brand Safety Findings (from 3-tier Tavily scan)
Risk level: {safety_risk_level}
Tier 1 (hard disqualifiers): {safety_tier1}
Tier 2 (high risk flags): {safety_tier2}
Tier 3 (soft flags / FTC): {safety_tier3}
FTC compliant: {ftc_compliant}
Partnership conflicts: {partnership_conflicts}

## JSON Skeleton — fill every field
```json
{json_skeleton}
```

Output only the completed JSON.
"""


def _build_audit_skeleton() -> str:
    """
    FIX CRITICAL: Pre-filled placeholder skeleton replaces model_json_schema().
    The LLM fills in values — it never sees $defs, $ref, or anyOf.
    """
    skeleton = {
        "audience_quality": {
            "audience_fit":       "<0.0–1.0: how well does creator's audience match ICP demographics>",
            "authenticity_score": "<0.0–1.0: penalise bots, fake followers, zero comments>",
            "sentiment_score":    "<-1.0 to 1.0: audience tone in comments and replies>",
        },
        "engagement_metrics": {
            "engagement_rate":       "<actual median ER from data>",
            "engagement_vs_tier":    "<above_benchmark | at_benchmark | below_benchmark>",
            "engagement_consistency": "<0.0–1.0: consistency across last 10 videos>",
            "top_content_type":      "<tutorial | review | routine | haul | vlog | etc.>",
        },
        "brand_safety": {
            "risk_level":            "<safe | risk | high_risk>",
            "tier1_triggered":       "<true | false>",
            "tier2_triggered":       "<true | false>",
            "tier3_triggered":       "<true | false>",
            "ftc_compliant":         "<true | false | null>",
            "partnership_conflicts": ["<competitor brand name if found, else empty list>"],
            "rationale":             "<1-2 sentences explaining risk level>",
        },
        "credibility": {
            "credibility_score": "<0.0–1.0: expertise, accuracy, credentials>",
            "niche_authority":   "<0.0–1.0: topical depth and ownership of niche>",
        },
        "compliance": {
            "disclosure_compliance": "<0.0–1.0: how consistently they disclose paid posts>",
            "professionalism":       "<0.0–1.0: contact info, contract readiness, responsiveness signals>",
        },
        "audit_rationale": "<2-3 sentences: key factors driving this overall assessment>",
    }
    return json.dumps(skeleton, indent=2)


# ─────────────────────────────────────────────
# CHAIN FACTORY
# ─────────────────────────────────────────────

def build_audit_chain(groq_api_key: str) -> RunnableSerializable:
    llm = ChatGroq(
        model="llama-3.1-8b-instant",
        api_key=groq_api_key,
        temperature=0.1,
        max_tokens=2048,
    )
    parser = JsonOutputParser(pydantic_object=CreatorAudit)
    prompt = ChatPromptTemplate.from_messages([
        ("system", AUDIT_SYSTEM_PROMPT),
        ("human",  AUDIT_HUMAN_PROMPT),
    ])
    return prompt | llm.with_retry(stop_after_attempt=3) | parser


# ─────────────────────────────────────────────
# SINGLE CANDIDATE AUDIT
# ─────────────────────────────────────────────

async def audit_one_candidate(
    candidate: RawCreatorProfile,
    icp: ICPProfile,
    chain: RunnableSerializable,
) -> dict[str, Any]:
    """
    Full audit for one candidate:
    1. Run three-tier brand safety Tavily scan (concurrent)
    2. Compute pricing estimate
    3. Run LLM scoring with all real data injected

    Returns enriched candidate dict with audit + pricing attached.
    """
    handle = candidate.handle

    logger.info("Auditing @%s [%s]", handle, candidate.platform)

    # Sub-tasks run concurrently
    safety_task  = three_tier_brand_safety_search(
        handle=handle,
        hard_disqualifiers=icp.brand_safety.hard_disqualifiers,
        high_risk_flags=icp.brand_safety.high_risk_flags,
        competitor_brands=icp.brand_safety.competitor_brands_to_check,
        lookback_months=icp.brand_safety.lookback_months,
    )

    safety_findings, = await asyncio.gather(safety_task)
    pricing = compute_pricing(candidate, icp)

    # Prepare video titles for niche relevance
    titles_str = "\n".join(
        f"  - {t}" for t in (candidate.recent_video_titles or [])[:10]
    ) or "No video titles available"

    prompt_vars: dict[str, Any] = {
        "handle":              handle,
        "platform":            candidate.platform,
        "followers":           f"{candidate.followers:,}" if candidate.followers else "unknown",
        "country":             candidate.country or "unknown",

        # FIX MISSING: pass pre-computed ratios, not raw numbers
        "median_er":           candidate.median_er or candidate.engagement_rate or 0,
        "min_er":              icp.benchmarks.min_engagement_rate,
        "strong_er":           icp.benchmarks.strong_engagement_rate,
        "view_to_sub_ratio":   candidate.view_to_sub_ratio or "n/a",
        "like_to_comment_ratio": candidate.like_to_comment_ratio or "n/a",
        "engagement_source":   candidate.engagement_source,

        "channel_age_years":   candidate.channel_age_years or "unknown",
        "description":         (candidate.description or "")[:300],
        "topic_categories":    ", ".join(candidate.topic_categories or []),
        "channel_keywords":    candidate.channel_keywords or "none",
        "recent_video_titles": titles_str,

        "primary_niches":      ", ".join(icp.primary_niches),
        "target_audience":     (
            f"{icp.audience.location}, age {icp.audience.age_range}, "
            f"{icp.audience.gender_skew}, language: {icp.audience.language}"
        ),

        # FIX MISSING: three-tier brand safety findings as structured input
        "safety_risk_level":   safety_findings.risk_level,
        "safety_tier1":        "; ".join(safety_findings.tier1_findings) or "None found",
        "safety_tier2":        "; ".join(safety_findings.tier2_findings) or "None found",
        "safety_tier3":        "; ".join(safety_findings.tier3_findings) or "None found",
        "ftc_compliant":       str(safety_findings.ftc_compliant),
        "partnership_conflicts": ", ".join(
            [f for f in safety_findings.tier2_findings if any(
                b.lower() in f.lower() for b in icp.brand_safety.competitor_brands_to_check
            )]
        ) or "None detected",

        "json_skeleton": _build_audit_skeleton(),  # FIX CRITICAL
    }

    try:
        raw_audit = await chain.ainvoke(prompt_vars)
        audit_obj = CreatorAudit.model_validate(raw_audit)

        return {
            **candidate.model_dump(),
            "audit":          audit_obj.model_dump(),
            "brand_safety_raw": safety_findings.to_dict(),
            "pricing":        pricing.model_dump(),
            "audit_error":    None,
        }

    except Exception as e:
        logger.error("Audit failed for @%s: %s", handle, e)
        return {
            **candidate.model_dump(),
            "audit":          None,
            "brand_safety_raw": safety_findings.to_dict(),
            "pricing":        pricing.model_dump(),
            "audit_error":    str(e),
        }


# ─────────────────────────────────────────────
# MAIN CALLABLE  (FIX BAD PATTERN: api_key as param)
# ─────────────────────────────────────────────

async def run_audit(
    icp: ICPProfile,
    candidates: list[RawCreatorProfile],
    groq_api_key: str,          # FIX: was read from os.environ inside the function
) -> list[dict[str, Any]]:
    """
    Chain 4 entry point. Audits all candidates in parallel.

    Each candidate gets:
      - Three-tier Tavily brand safety scan
      - Pricing + CPM estimate
      - LLM audit score with pre-computed ratios as context

    Hard-disqualified candidates (tier1 triggered) are still returned
    with audit.brand_safety.risk_level = "high_risk" so the dossier builder
    (Chain 6) can filter them out and show the brand why they were excluded.
    """
    logger.info("Chain 4: Auditing %d candidates", len(candidates))

    chain = build_audit_chain(groq_api_key)

    tasks = [
        audit_one_candidate(c, icp, chain)
        for c in candidates
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    audited: list[dict[str, Any]] = []
    for r in results:
        if isinstance(r, Exception):
            logger.error("Audit task raised exception: %s", r)
            continue
        audited.append(r)

    successful = sum(1 for r in audited if r.get("audit") is not None)
    logger.info("Chain 4 complete: %d/%d audited successfully", successful, len(candidates))

    return audited


# ─────────────────────────────────────────────
# QUICK LOCAL TEST
# ─────────────────────────────────────────────

if __name__ == "__main__":
    from chain_0_ICP import BrandBrief, CampaignGoal, Platform, FollowerTier, run_icp_chain
    from chain_1_keywordExpansion import run_keyword_expansion
    from chain_2_discovery import run_discovery
    from chain_3_filtering import run_filtering

    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    sample_brief = BrandBrief(
        brand_name         = "Dot & Key",
        product_description= "Vitamin C serum for hyperpigmentation targeting Indian women",
        campaign_goal      = CampaignGoal.CONVERSION,
        niche              = "skincare",
        platforms          = [Platform.YOUTUBE],
        follower_tier      = FollowerTier.MICRO,
        target_audience    = "Indian women 22-35, interested in clean beauty",
        audience_location  = "India",
        audience_age_range = "22-35",
        language           = "English and Hindi",
        competitor_brands  = ["Minimalist", "Plum", "Derma Co"],
        excluded_niches    = ["adult content", "alcohol", "gambling"],
    )

    async def main():
        api_key = os.environ.get("GROQ_API_KEY")
        if not api_key:
            print("ERROR: GROQ_API_KEY not found.")
            return

        icp      = await run_icp_chain(sample_brief, api_key)
        
        # Loosen benchmarks so more creators pass the example
        icp.benchmarks.follower_min = 0
        icp.benchmarks.follower_max = 20_000_000
        icp.benchmarks.min_engagement_rate = 0.0
        icp.benchmarks.min_view_to_sub_ratio = 0.0
        
        keywords = run_keyword_expansion(icp)
        keywords.youtube_queries = keywords.youtube_queries[:2]

        candidates = await run_discovery(icp, keywords)
        filtered   = run_filtering(icp, candidates, follower_tolerance=10.0)
        filtered   = filtered[:2]   # limit for test

        audited = await run_audit(icp, filtered, groq_api_key=api_key)

        for c in audited:
            print(f"\n{'-'*50}")
            print(f"@{c['handle']} [{c['platform']}]")
            if c.get("audit"):
                a = c["audit"]
                print(f"  Brand safety:  {a['brand_safety']['risk_level']}")
                print(f"  ER vs tier:    {a['engagement_metrics']['engagement_vs_tier']}")
                print(f"  Authenticity:  {a['audience_quality']['authenticity_score']:.2f}")
                print(f"  Niche auth:    {a['credibility']['niche_authority']:.2f}")
                print(f"  Rationale:     {a['audit_rationale'][:120]}")
            if c.get("pricing"):
                p = c["pricing"]
                print(f"  Est. rate:     INR {p['estimated_rate_inr']:,.0f} | CPM: ${p['cpm_usd']:.2f} | {p['pricing_tier']}")
            if c.get("audit_error"):
                print(f"  ERROR: {c['audit_error']}")

    asyncio.run(main())