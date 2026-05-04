"""
Chain 0 — ICP Builder
=====================
Converts a raw BrandBrief into a fully structured Ideal Creator Profile (ICP).
This ICP becomes the single source of truth for every downstream chain.

Design decisions vs the original attempt:
  - JsonOutputParser with pydantic_object forces the LLM to match your schema exactly
  - The prompt embeds the full JSON skeleton so the model has no ambiguity
  - with_retry() handles transient 429s / 503s automatically
  - Two Pydantic models: BrandBrief (input) and ICPProfile (output)
  - Auto-derives follower benchmarks and ER thresholds from follower_tier
  - Uses Groq (Llama 3) for fast, free inference
"""

from __future__ import annotations

import json
import logging
from enum import Enum
from typing import Literal

from langchain_core.output_parsers import JsonOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import RunnableSerializable
from langchain_groq import ChatGroq
from pydantic import BaseModel, Field, field_validator, model_validator

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────
# ENUMS
# ─────────────────────────────────────────────

class Platform(str, Enum):
    YOUTUBE  = "youtube"
    INSTAGRAM = "instagram"
    TWITTER  = "twitter"


class CampaignGoal(str, Enum):
    AWARENESS   = "awareness"
    CONVERSION  = "conversion"
    ENGAGEMENT  = "engagement"
    LEAD_GEN    = "lead_generation"


class FollowerTier(str, Enum):
    NANO  = "nano"    # 1K – 10K
    MICRO = "micro"   # 10K – 100K
    MID   = "mid"     # 100K – 500K
    MACRO = "macro"   # 500K – 2M
    MEGA  = "mega"    # 2M+


# ─────────────────────────────────────────────
# INPUT SCHEMA  (what the brand submits)
# ─────────────────────────────────────────────

class BrandBrief(BaseModel):
    """Raw campaign brief submitted by the brand manager via BriefForm.jsx"""

    # Brand identity
    brand_name:          str  = Field(..., description="Name of the brand, e.g. 'Mamaearth'")
    product_description: str  = Field(..., description="What the product is and what it does, 2–4 sentences")
    campaign_goal:       CampaignGoal = Field(..., description="Primary campaign objective")

    # Creator targeting
    niche:               str             = Field(..., description="Primary niche, e.g. 'skincare', 'personal finance', 'gaming'")
    platforms:           list[Platform]  = Field(..., min_length=1)
    follower_tier:       FollowerTier    = Field(..., description="Target creator tier")

    # Audience targeting
    target_audience:     str  = Field(..., description="Who the brand wants to reach, e.g. 'Indian women 22-35 interested in clean beauty'")
    audience_location:   str  = Field(..., description="Country or region, e.g. 'India', 'United States'")
    audience_age_range:  str  = Field(..., description="e.g. '18-24', '25-35', '35-50'")
    language:            str  = Field(default="English")

    # Optional enrichment
    competitor_brands:   list[str] = Field(default_factory=list, description="Brands whose ambassadors we want to study")
    budget_inr:          int | None = Field(default=None, description="Total campaign budget in INR (optional)")
    excluded_niches:     list[str]  = Field(default_factory=list, description="Niches to avoid, e.g. ['alcohol', 'adult content']")
    additional_context:  str | None = Field(default=None, description="Any other notes the brand wants the system to know")

    @field_validator("platforms", mode="before")
    @classmethod
    def normalise_platforms(cls, v: list) -> list:
        return [p.lower() if isinstance(p, str) else p for p in v]


# ─────────────────────────────────────────────
# OUTPUT SUB-SCHEMAS
# ─────────────────────────────────────────────

class KeywordBuckets(BaseModel):
    """
    Three purpose-built search buckets.
    Each is used differently in Chain 2 (Discovery).

    - discovery         → YouTube video search, Tavily content search
    - audience_intent   → Tavily audience-side queries (what fans search)
    - competitor        → Tavily Boolean queries for competitor ambassador discovery
    """
    discovery:          list[str] = Field(..., min_length=5, max_length=12,
                                          description="Queries targeting what the creator POSTS")
    audience_intent:    list[str] = Field(..., min_length=3, max_length=8,
                                          description="Queries targeting what the AUDIENCE searches")
    competitor:         list[str] = Field(..., min_length=1, max_length=8,
                                          description="Queries to find competitor brand ambassadors")


class AudienceCriteria(BaseModel):
    location:        str
    age_range:       str
    gender_skew:     Literal["any", "majority_female", "majority_male", "balanced"]
    top_interests:   list[str] = Field(..., min_length=3, max_length=8)
    language:        str


class PerformanceBenchmarks(BaseModel):
    """
    Tier-relative thresholds — not flat numbers.
    Chain 3 uses these for hard-drop decisions.
    """
    follower_tier:              FollowerTier
    follower_min:               int
    follower_max:               int

    # Engagement rate thresholds (as %)
    # These are industry standards per tier — the LLM should not override these
    min_engagement_rate:        float = Field(..., description="Drop creators below this ER%")
    strong_engagement_rate:     float = Field(..., description="ER% that signals a high-quality creator")

    # YouTube-specific
    min_view_to_sub_ratio:      float = Field(default=0.05,
                                              description="Views/subscribers — below 5% is a dead channel")
    healthy_like_comment_ratio: tuple[int, int] = Field(
        default=(20, 50),
        description="Healthy likes:comments range. Outside this signals fake activity."
    )

    # Activity
    min_posts_per_month:        int  = Field(default=2, description="Minimum posting cadence")
    max_follower_growth_pct:    float = Field(default=20.0,
                                             description="Monthly follower growth above this % = suspicious")


class BrandSafetyRules(BaseModel):
    """
    Three-tier severity model — mirrors how real agencies triage risk.
    Chain 4b runs three separate Tavily searches, one per tier.
    """
    # Hard drop — do not present to brand under any circumstances
    hard_disqualifiers: list[str] = Field(
        ...,
        description="Behaviours that auto-reject a creator: hate speech, FTC violations, active legal issues, etc."
    )
    # Flag — present to brand with a red warning, brand decides
    high_risk_flags: list[str] = Field(
        ...,
        description="Behaviours that get a RED flag: political content, competitor lock-in, deleted post history"
    )
    # Note — present to brand with an amber note, low priority
    soft_flags: list[str] = Field(
        ...,
        description="Behaviours that get an AMBER note: occasional profanity, old competitor mentions"
    )
    competitor_brands_to_check: list[str] = Field(
        default_factory=list,
        description="Brands to search for when checking exclusivity conflicts"
    )
    # Lookback window the brand cares about
    lookback_months: int = Field(default=6, description="How many months of history to audit")


class ContentCriteria(BaseModel):
    primary_formats:    list[str] = Field(..., description="e.g. ['YouTube long-form review', 'Instagram Reel', 'Twitter thread']")
    preferred_tone:     str       = Field(..., description="e.g. 'authentic and conversational'")
    integration_style:  str       = Field(..., description="e.g. 'natural product mention in existing content format'")
    avoid_styles:       list[str] = Field(..., description="e.g. ['overly salesy', 'clickbait thumbnails']")


class ScoringWeights(BaseModel):
    """
    Explicit weights for Chain 5 LLM scoring.
    ICP builder sets these based on campaign goal — conversion campaigns weight
    audience authenticity more, awareness campaigns weight reach more.
    """
    engagement_quality:    float = Field(..., ge=0, le=1)
    audience_authenticity: float = Field(..., ge=0, le=1)
    niche_relevance:       float = Field(..., ge=0, le=1)
    brand_safety:          float = Field(..., ge=0, le=1)

    @model_validator(mode="after")
    def weights_must_sum_to_one(self) -> ScoringWeights:
        total = (
            self.engagement_quality +
            self.audience_authenticity +
            self.niche_relevance +
            self.brand_safety
        )
        if not (0.99 <= total <= 1.01):
            raise ValueError(f"Scoring weights must sum to 1.0, got {total:.2f}")
        return self


# ─────────────────────────────────────────────
# OUTPUT SCHEMA  (what Chain 0 produces)
# ─────────────────────────────────────────────

class ICPProfile(BaseModel):
    """
    Ideal Creator Profile — the complete filter spec for this campaign.
    Every downstream chain reads from this object, never from BrandBrief directly.
    """
    # Human-readable summary for the dashboard
    icp_summary:         str = Field(..., description="2–3 sentence plain-English summary of the ideal creator")

    # Creator definition
    primary_niches:      list[str] = Field(..., min_length=1, max_length=4)
    secondary_niches:    list[str] = Field(..., max_length=6)
    excluded_niches:     list[str]

    # The three keyword buckets (drives Chain 2)
    keyword_buckets:     KeywordBuckets

    # Platform-specific hashtags (used in Tavily Boolean queries)
    hashtags:            list[str] = Field(..., min_length=5, max_length=20)

    # Audience criteria
    audience:            AudienceCriteria

    # Performance benchmarks (tier-relative)
    benchmarks:          PerformanceBenchmarks

    # Brand safety rules
    brand_safety:        BrandSafetyRules

    # Content criteria
    content:             ContentCriteria

    # Positive / negative signals for Chain 4 qualitative check
    positive_signals:    list[str] = Field(..., min_length=4, max_length=10,
                                           description="Signals that CONFIRM a creator is a strong match")
    negative_signals:    list[str] = Field(..., min_length=4, max_length=10,
                                           description="Signals that DISQUALIFY a creator beyond hard metrics")

    # Scoring weights for Chain 5
    scoring_weights:     ScoringWeights


# ─────────────────────────────────────────────
# TIER BENCHMARKS  (hardcoded — not LLM-derived)
# ─────────────────────────────────────────────
# These are non-negotiable industry standards.
# The LLM must NOT override them — we inject them into the prompt
# so the model references them rather than inventing its own numbers.

TIER_BENCHMARKS: dict[FollowerTier, dict] = {
    FollowerTier.NANO:  {"min": 1_000,       "max": 10_000,      "min_er": 5.0,  "strong_er": 10.0},
    FollowerTier.MICRO: {"min": 10_000,      "max": 100_000,     "min_er": 3.0,  "strong_er": 8.0},
    FollowerTier.MID:   {"min": 100_000,     "max": 500_000,     "min_er": 1.5,  "strong_er": 4.0},
    FollowerTier.MACRO: {"min": 500_000,     "max": 2_000_000,   "min_er": 0.5,  "strong_er": 2.0},
    FollowerTier.MEGA:  {"min": 2_000_000,   "max": 100_000_000, "min_er": 0.3,  "strong_er": 1.0},
}

CAMPAIGN_GOAL_WEIGHTS: dict[CampaignGoal, dict] = {
    CampaignGoal.AWARENESS:   {"engagement_quality": 0.30, "audience_authenticity": 0.20, "niche_relevance": 0.30, "brand_safety": 0.20},
    CampaignGoal.CONVERSION:  {"engagement_quality": 0.25, "audience_authenticity": 0.35, "niche_relevance": 0.25, "brand_safety": 0.15},
    CampaignGoal.ENGAGEMENT:  {"engagement_quality": 0.40, "audience_authenticity": 0.25, "niche_relevance": 0.20, "brand_safety": 0.15},
    CampaignGoal.LEAD_GEN:    {"engagement_quality": 0.25, "audience_authenticity": 0.30, "niche_relevance": 0.25, "brand_safety": 0.20},
}


# ─────────────────────────────────────────────
# PROMPT
# ─────────────────────────────────────────────

# We give the LLM the full JSON skeleton with every key pre-populated
# with a placeholder string. This is the single most important prompt
# engineering decision — the model fills in values, not structure.

ICP_SYSTEM_PROMPT = """\
You are a senior influencer marketing strategist with 10 years of experience \
building creator programs for D2C and FMCG brands in India and globally. \
You think like a talent scout, not a keyword tool.

Your job: convert a brand brief into a precise Ideal Creator Profile (ICP) \
that a junior analyst can use to shortlist creators without guesswork.

## Rules you must follow
1. Output ONLY valid JSON — no markdown, no preamble, no explanation.
2. Your JSON must exactly match the schema skeleton provided. \
   Do not add or remove any keys.
3. Keyword buckets must be SPECIFIC and SEARCHABLE. \
   BAD: "fitness influencer" — too generic, matches 10 million results. \
   GOOD: "whey protein taste test honest opinion 2024" — topically precise.
4. Competitor keyword bucket must include the exact brand name in each query \
   so Tavily can find confirmed ambassadors.
5. The benchmarks section already has follower_min/max and ER values injected below. \
   Copy them verbatim into your output — do not invent your own numbers.
6. Brand safety rules must be specific to this brand's category. \
   A children's brand has stricter hard disqualifiers than an adult gaming brand.
7. Scoring weights have been pre-calculated from the campaign goal. \
   Copy them verbatim into your output.

## Pre-calculated values (copy these verbatim into benchmarks and scoring_weights)
{pre_calculated_values}
"""

ICP_HUMAN_PROMPT = """\
## Brand Brief
Brand: {brand_name}
Product: {product_description}
Campaign goal: {campaign_goal}
Primary niche: {niche}
Platforms: {platforms}
Follower tier: {follower_tier}
Target audience: {target_audience}
Location: {audience_location}
Age range: {audience_age_range}
Language: {language}
Competitor brands: {competitor_brands}
Excluded niches: {excluded_niches}
Additional context: {additional_context}

## JSON Schema Skeleton
Fill EVERY field. Never leave a placeholder string in the output.

```json
{json_skeleton}
```

Output only the completed JSON object.
"""

# ─────────────────────────────────────────────
# SKELETON BUILDER
# ─────────────────────────────────────────────

def _build_json_skeleton(brief: BrandBrief) -> str:
    """
    Pre-populate the skeleton with hardcoded benchmark values and
    campaign-goal-derived scoring weights so the LLM cannot fabricate them.
    """
    tier   = TIER_BENCHMARKS[brief.follower_tier]
    weights = CAMPAIGN_GOAL_WEIGHTS[brief.campaign_goal]

    skeleton = {
        "icp_summary": "<2-3 sentences: who is the ideal creator for this campaign>",
        "primary_niches":   ["<niche 1>", "<niche 2>"],
        "secondary_niches": ["<niche 1>", "<niche 2>", "<niche 3>"],
        "excluded_niches":  ["<fill from brand brief + category-specific exclusions>"],
        "keyword_buckets": {
            "discovery": [
                "<specific video/post topic + platform context>",
                "<specific video/post topic + honest/review angle>",
                "<niche + product type + year>",
                "<routine/lifestyle query relevant to niche>",
                "<comparison or haul query relevant to niche>"
            ],
            "audience_intent": [
                "<what the TARGET AUDIENCE searches on YouTube/Google>",
                "<product category + 'worth it' or 'review'>",
                "<problem the product solves + 'how to'>"
            ],
            "competitor": [
                f"<{c} ambassador OR partner OR gifted>" for c in (brief.competitor_brands or ["<competitor brand>"])
            ]
        },
        "hashtags": [
            "<#niche_hashtag_1>", "<#niche_hashtag_2>", "<#niche_hashtag_3>",
            "<#product_hashtag>", "<#brand_hashtag>"
        ],
        "audience": {
            "location":      brief.audience_location,
            "age_range":     brief.audience_age_range,
            "gender_skew":   "<any | majority_female | majority_male | balanced>",
            "top_interests": ["<interest 1>", "<interest 2>", "<interest 3>"],
            "language":      brief.language
        },
        "benchmarks": {
            "follower_tier":              brief.follower_tier.value,
            "follower_min":               tier["min"],
            "follower_max":               tier["max"],
            "min_engagement_rate":        tier["min_er"],
            "strong_engagement_rate":     tier["strong_er"],
            "min_view_to_sub_ratio":      0.05,
            "healthy_like_comment_ratio": [20, 50],
            "min_posts_per_month":        2,
            "max_follower_growth_pct":    20.0
        },
        "brand_safety": {
            "hard_disqualifiers": [
                "<specific to brand category — not just generic hate speech>",
                "<FTC / ASCI compliance failure>",
                "<legal issues or public scandal>",
                "<content that directly contradicts brand values>"
            ],
            "high_risk_flags": [
                "<strong political content>",
                f"<active partnership with {brief.competitor_brands[0] if brief.competitor_brands else 'a direct competitor'}>",
                "<deleted or heavily edited post history>",
                "<sudden follower spike suggesting purchased followers>"
            ],
            "soft_flags": [
                "<occasional profanity — brand-dependent>",
                "<old competitor mentions (>6 months)>",
                "<niche crossover that might confuse audience>"
            ],
            "competitor_brands_to_check": brief.competitor_brands,
            "lookback_months": 6
        },
        "content": {
            "primary_formats":   ["<format for platform 1>", "<format for platform 2>"],
            "preferred_tone":    "<describe the ideal creator's voice and energy>",
            "integration_style": "<how the brand should appear in content naturally>",
            "avoid_styles":      ["<style 1>", "<style 2>"]
        },
        "positive_signals": [
            "<specific behaviour that confirms creator genuinely uses this product category>",
            "<engagement signal: audience asks creator for recommendations>",
            "<content signal: creator has done similar brand collabs that performed well>",
            "<community signal: creator responds to comments thoughtfully>"
        ],
        "negative_signals": [
            "<posts too many sponsored posts — more than 1 in 4 posts is an ad>",
            "<comments are mostly generic emoji with no substantive discussion>",
            "<creator switches niches frequently — no topical authority>",
            "<follower count inconsistent with engagement volume>"
        ],
        "scoring_weights": {
            "engagement_quality":    weights["engagement_quality"],
            "audience_authenticity": weights["audience_authenticity"],
            "niche_relevance":       weights["niche_relevance"],
            "brand_safety":          weights["brand_safety"]
        }
    }

    return json.dumps(skeleton, indent=2)


def _build_pre_calculated_block(brief: BrandBrief) -> str:
    tier    = TIER_BENCHMARKS[brief.follower_tier]
    weights = CAMPAIGN_GOAL_WEIGHTS[brief.campaign_goal]
    return (
        f"Follower range: {tier['min']:,} - {tier['max']:,}\n"
        f"Min engagement rate: {tier['min_er']}%\n"
        f"Strong engagement rate: {tier['strong_er']}%\n"
        f"Min view-to-subscriber ratio: 5%\n"
        f"Max monthly follower growth: 20%\n"
        f"Scoring weights (campaign goal = {brief.campaign_goal.value}):\n"
        f"  engagement_quality:    {weights['engagement_quality']}\n"
        f"  audience_authenticity: {weights['audience_authenticity']}\n"
        f"  niche_relevance:       {weights['niche_relevance']}\n"
        f"  brand_safety:          {weights['brand_safety']}\n"
    )


# ─────────────────────────────────────────────
# CHAIN FACTORY
# ─────────────────────────────────────────────

def build_icp_chain(groq_api_key: str) -> RunnableSerializable:
    """
    Returns a LangChain LCEL chain:

        BrandBrief → (prompt formatting) → Groq (Llama 3) → JsonOutputParser → ICPProfile

    Usage:
        chain  = build_icp_chain(api_key)
        result = await chain.ainvoke(brief)          # returns ICPProfile dict
        icp    = ICPProfile.model_validate(result)   # validate into Pydantic object
    """

    llm = ChatGroq(
        model="llama-3.3-70b-versatile",
        api_key=groq_api_key,
        temperature=0.2,        # low — we want consistent structured output, not creativity
        max_tokens=4096,
    )

    parser = JsonOutputParser(pydantic_object=ICPProfile)

    prompt = ChatPromptTemplate.from_messages([
        ("system", ICP_SYSTEM_PROMPT),
        ("human",  ICP_HUMAN_PROMPT),
    ])

    chain = prompt | llm.with_retry(
        stop_after_attempt=3,
        wait_exponential_jitter=True,
    ) | parser

    return chain


# ─────────────────────────────────────────────
# MAIN CALLABLE
# ─────────────────────────────────────────────

async def run_icp_chain(brief: BrandBrief, groq_api_key: str) -> ICPProfile:
    """
    The function called by pipeline.py.

    Returns a validated ICPProfile.
    Raises ValueError with a clear message if LLM output fails validation.
    """
    chain = build_icp_chain(groq_api_key)

    prompt_vars = {
        "brand_name":           brief.brand_name,
        "product_description":  brief.product_description,
        "campaign_goal":        brief.campaign_goal.value,
        "niche":                brief.niche,
        "platforms":            ", ".join(p.value for p in brief.platforms),
        "follower_tier":        brief.follower_tier.value,
        "target_audience":      brief.target_audience,
        "audience_location":    brief.audience_location,
        "audience_age_range":   brief.audience_age_range,
        "language":             brief.language,
        "competitor_brands":    ", ".join(brief.competitor_brands) if brief.competitor_brands else "none specified",
        "excluded_niches":      ", ".join(brief.excluded_niches) if brief.excluded_niches else "none specified",
        "additional_context":   brief.additional_context or "none",
        "pre_calculated_values": _build_pre_calculated_block(brief),
        "json_skeleton":        _build_json_skeleton(brief),
    }

    logger.info("Running ICP chain for brand=%s tier=%s goal=%s",
                brief.brand_name, brief.follower_tier.value, brief.campaign_goal.value)

    raw: dict = await chain.ainvoke(prompt_vars)

    # Validate the LLM output against the full Pydantic schema
    # This will raise ValidationError if the LLM skipped a required field
    try:
        icp = ICPProfile.model_validate(raw)
    except Exception as exc:
        logger.error("ICP validation failed: %s\nRaw output: %s", exc, raw)
        raise ValueError(f"ICP chain produced invalid output: {exc}") from exc

    logger.info("ICP built successfully. Primary niches: %s", icp.primary_niches)
    return icp


# ─────────────────────────────────────────────
# QUICK LOCAL TEST  (python chain_0_ICP.py)
# ─────────────────────────────────────────────

if __name__ == "__main__":
    import asyncio, os
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

    sample_brief = BrandBrief(
        brand_name         = "Dot & Key",
        product_description= (
            "Dot & Key is an Indian skincare brand known for science-backed, "
            "dermatologist-tested formulations. We are launching a new Vitamin C "
            "serum targeting hyperpigmentation and dull skin."
        ),
        campaign_goal      = CampaignGoal.CONVERSION,
        niche              = "skincare",
        platforms          = [Platform.INSTAGRAM, Platform.YOUTUBE],
        follower_tier      = FollowerTier.MICRO,
        target_audience    = "Indian women aged 22-35 interested in clean beauty and skincare routines",
        audience_location  = "India",
        audience_age_range = "22-35",
        language           = "English and Hindi",
        competitor_brands  = ["Minimalist", "Plum", "mCaffeine"],
        excluded_niches    = ["adult content", "alcohol", "tobacco"],
        budget_inr         = 500_000,
        additional_context = "Prefer creators who already incorporate serums in their existing content",
    )

    async def main():
        api_key = os.environ["GROQ_API_KEY"]
        icp = await run_icp_chain(sample_brief, api_key)
        print(icp.model_dump_json(indent=2))

    asyncio.run(main())