"""
Chain 4 — Audit
===============
Performs a deep, LLM-powered multi-dimensional audit of candidates that passed filtering.
Evaluates 6 key categories: Audience Quality, Engagement Metrics, Brand Safety,
Credibility, Compliance, and Identity, based on the ICP single source of truth.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Literal, List, Dict, Any

from langchain_core.output_parsers import JsonOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import RunnableSerializable
from langchain_groq import ChatGroq
from pydantic import BaseModel, Field

from chain_0_ICP import ICPProfile

import sys
import os
from dotenv import load_dotenv

# Ensure backend is in the path for module resolution
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

from services.platforms.tavily import tavily_search

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────
# 1. PYDANTIC OUTPUT SCHEMA
# ─────────────────────────────────────────────

class AudienceQuality(BaseModel):
    audience_fit: float = Field(ge=0, le=1, description="Age, location, language, and interest alignment.")
    authenticity_score: float = Field(ge=0, le=1, description="Penalize fake followers/bots (e.g. likes > impressions, zero comments).")
    sentiment_score: float = Field(ge=-1, le=1, description="Positive vs negative audience sentiment.")

class EngagementMetrics(BaseModel):
    engagement_rate: float = Field(description="Actual Engagement Rate (ER).")
    engagement_consistency: float = Field(ge=0, le=1, description="Stability across recent posts without sudden drops.")
    growth_stability: float = Field(ge=0, le=1, description="Gradual growth vs sudden suspicious spikes.")
    top_content_type: str = Field(description="E.g., 'tutorial/review/meme' best performing format.")

class BrandSafety(BaseModel):
    brand_safety: Literal["safe", "risk", "high_risk"] = Field(description="Risk level based on controversy/scandal history.")
    partnership_risk: float = Field(ge=0, le=1, description="Risk of competitor conflicts or exclusivity issues.")
    reputation_score: float = Field(ge=0, le=1, description="Off-platform behavior and public discussions.")

class Credibility(BaseModel):
    credibility_score: float = Field(ge=0, le=1, description="Credentials verification (e.g. degrees, Github).")
    expertise_score: float = Field(ge=0, le=1, description="Content accuracy and depth of explanation.")

class Compliance(BaseModel):
    compliance_score: float = Field(ge=0, le=1, description="Ad disclosure compliance (#Ad / #Sponsored).")
    professionalism_score: float = Field(ge=0, le=1, description="Works with contracts, PR emails in bio.")

class Identity(BaseModel):
    identity_verified: bool = Field(description="Real name, contact details, region available.")
    account_trust_score: float = Field(ge=0, le=1, description="Account age and consistency over time.")

class CreatorAudit(BaseModel):
    """The complete 6-category audit profile for a candidate."""
    audience_quality: AudienceQuality
    engagement_metrics: EngagementMetrics
    brand_safety_risk: BrandSafety
    credibility: Credibility
    compliance: Compliance
    identity: Identity
    audit_rationale: str = Field(description="A brief 2-3 sentence summary explaining the key factors driving these scores.")


# ─────────────────────────────────────────────
# 2. PROMPT
# ─────────────────────────────────────────────

AUDIT_SYSTEM_PROMPT = """\
You are an elite Influencer Marketing Auditor and Risk Assessor.
Your job is to evaluate a creator candidate against an Ideal Creator Profile (ICP) and assign precise scores.

## Instructions
1. Output ONLY valid JSON matching the exact schema provided.
2. Read the candidate's metrics, their recent content, and the web search results.
3. Determine `sentiment_score` (-1 to 1) and `compliance_score` (0 to 1) by reading their channel description, keywords, and topics.
4. Assess `authenticity_score`. If their Like-to-Comment ratio is extremely high (>100) or they have 0 comments, penalize this score heavily.
5. If the Brand Safety search returns ANY controversy related to the `hard_disqualifiers` in the ICP, set `brand_safety` to "high_risk".
6. Base the `top_content_type` on their topic categories and recent video data.

Output your assessment strictly in the requested JSON structure.
"""

AUDIT_HUMAN_PROMPT = """\
## 1. Candidate Context
Name / Handle: {handle}
Followers: {followers}
Engagement Rate: {engagement_rate}%
Avg Views: {avg_views} | Avg Likes: {avg_likes} | Avg Comments: {avg_comments}
Channel Age: {channel_age_years} years
Country: {country}
Description: {description}
Categories: {topic_categories}
Keywords: {channel_keywords}

Recent Videos:
{recent_videos}

## 2. ICP Context (Single Source of Truth)
Primary Niches: {primary_niches}
Target Audience: {target_audience}
Brand Safety Hard Disqualifiers: {hard_disqualifiers}
Brand Safety High Risk Flags: {high_risk_flags}
Competitors to Avoid: {competitor_brands}

## 3. Web Search Intel
Brand Safety Search Results:
{safety_search_results}

Competitor / Partnership Search Results:
{partnership_search_results}

## JSON Output Schema Skeleton
```json
{json_skeleton}
```

Fill every field according to the rules and your expert assessment. Output only the JSON.
"""

# ─────────────────────────────────────────────
# 3. CHAIN & EXECUTION
# ─────────────────────────────────────────────

def build_audit_chain(groq_api_key: str) -> RunnableSerializable:
    llm = ChatGroq(
        model="llama-3.3-70b-versatile",
        api_key=groq_api_key,
        temperature=0.1, # Extremely low for analytical consistency
        max_tokens=2048,
    )
    parser = JsonOutputParser(pydantic_object=CreatorAudit)
    prompt = ChatPromptTemplate.from_messages([
        ("system", AUDIT_SYSTEM_PROMPT),
        ("human",  AUDIT_HUMAN_PROMPT),
    ])
    return prompt | llm.with_retry(stop_after_attempt=3) | parser

async def _gather_tavily_intel(handle: str, icp: ICPProfile) -> tuple[str, str]:
    """Runs concurrent Tavily searches for brand safety and competitor associations."""
    disqualifiers = " OR ".join(icp.brand_safety.hard_disqualifiers[:2]) if icp.brand_safety.hard_disqualifiers else "scandal"
    competitors = " OR ".join(icp.brand_safety.competitor_brands_to_check) if icp.brand_safety.competitor_brands_to_check else "sponsor"

    q_safety = f'"{handle}" (controversy OR fraud OR {disqualifiers})'
    q_partnership = f'"{handle}" ({competitors} OR ambassador OR partnership)'

    # Run searches concurrently
    safety_results, partnership_results = await asyncio.gather(
        tavily_search(q_safety, max_results=2),
        tavily_search(q_partnership, max_results=2),
        return_exceptions=True
    )

    def _format_results(res) -> str:
        if isinstance(res, Exception) or not res:
            return "No significant findings."
        return "\n".join([f"- {r.get('title')}: {r.get('content')}" for r in res])

    return _format_results(safety_results), _format_results(partnership_results)

async def run_candidate_audit(candidate: Dict[str, Any], icp: ICPProfile, chain: RunnableSerializable) -> Dict[str, Any]:
    """Audits a single candidate using LLM and Tavily web intel."""
    handle = candidate.get("handle") or candidate.get("channel_title", "Unknown")
    
    logger.info(f"Auditing candidate: {handle}...")
    safety_intel, partnership_intel = await _gather_tavily_intel(handle, icp)

    recent_videos_str = "No recent video data available."
    if candidate.get("recent_videos"):
        recent_videos_str = "\n".join([
            f"- Views: {v['views']:,} | Likes: {v['likes']:,} | Comments: {v['comments']:,}" 
            for v in candidate["recent_videos"][:5]
        ])

    # Pre-populate schema template for LLM
    skeleton = CreatorAudit.model_json_schema()

    prompt_vars = {
        "handle": handle,
        "followers": candidate.get("followers", 0),
        "engagement_rate": candidate.get("engagement_rate", 0),
        "avg_views": candidate.get("avg_views", 0),
        "avg_likes": candidate.get("avg_likes", 0),
        "avg_comments": candidate.get("avg_comments", 0),
        "channel_age_years": candidate.get("channel_age_years", "Unknown"),
        "country": candidate.get("country", "Unknown"),
        "description": candidate.get("description", "None"),
        "topic_categories": ", ".join(candidate.get("topic_categories", [])),
        "channel_keywords": candidate.get("channel_keywords", "None"),
        "recent_videos": recent_videos_str,
        
        "primary_niches": ", ".join(icp.primary_niches),
        "target_audience": f"{icp.audience.location}, {icp.audience.age_range}, {icp.audience.gender_skew}",
        "hard_disqualifiers": ", ".join(icp.brand_safety.hard_disqualifiers),
        "high_risk_flags": ", ".join(icp.brand_safety.high_risk_flags),
        "competitor_brands": ", ".join(icp.brand_safety.competitor_brands_to_check),
        
        "safety_search_results": safety_intel,
        "partnership_search_results": partnership_intel,
        "json_skeleton": json.dumps(skeleton, indent=2)
    }

    try:
        raw_audit = await chain.ainvoke(prompt_vars)
        audit_obj = CreatorAudit.model_validate(raw_audit)
        
        # Attach the audit to the candidate dict
        enriched_candidate = dict(candidate)
        enriched_candidate["audit"] = audit_obj.model_dump()
        return enriched_candidate
        
    except Exception as e:
        logger.error(f"Audit failed for {handle}: {e}")
        # Return candidate without audit if it fails, or mark as failed
        enriched_candidate = dict(candidate)
        enriched_candidate["audit"] = None
        enriched_candidate["audit_error"] = str(e)
        return enriched_candidate

async def run_audit(icp: ICPProfile, candidates: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Orchestrates the parallel auditing of all passed candidates."""
    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        raise ValueError("GROQ_API_KEY is required for Chain 4 Audit.")

    logger.info(f"Starting Chain 4: Audit on {len(candidates)} candidates")
    chain = build_audit_chain(api_key)

    tasks = [run_candidate_audit(c, icp, chain) for c in candidates]
    audited_candidates = await asyncio.gather(*tasks)
    
    successful = [c for c in audited_candidates if c.get("audit") is not None]
    logger.info(f"Audit complete. Successfully audited {len(successful)}/{len(candidates)} candidates.")
    
    return audited_candidates


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

        print("\n=== RUNNING CHAIN 0 (ICP) ===")
        icp = await run_icp_chain(sample_brief, api_key)
        
        print("\n=== RUNNING CHAIN 1 (Keyword Expansion) ===")
        keywords = run_keyword_expansion(icp)
        keywords.youtube_queries = keywords.youtube_queries[:2]
        
        print("\n=== RUNNING CHAIN 2 (Discovery) ===")
        candidates = await run_discovery(icp, keywords)
        
        print("\n=== RUNNING CHAIN 3 (Filtering) ===")
        # Loosen benchmarks so we get some candidates to audit
        icp.benchmarks.follower_min = 1_000
        icp.benchmarks.follower_max = 5_000_000
        icp.benchmarks.min_engagement_rate = 0.5
        icp.benchmarks.healthy_like_comment_ratio = (5, 150)
        filtered = run_filtering(icp, candidates)
        
        # Limit to 2 candidates to avoid hitting API rate limits during test
        filtered = filtered[:2]
        
        print("\n=== RUNNING CHAIN 4 (Audit) ===")
        audited = await run_audit(icp, filtered)
        
        print("\n=== FINAL AUDIT REPORTS ===")
        for c in audited:
            print(f"\n--- {c['channel_title']} ---")
            if c.get("audit"):
                print(json.dumps(c["audit"], indent=2))
            else:
                print(f"Audit failed: {c.get('audit_error')}")

    asyncio.run(main())
