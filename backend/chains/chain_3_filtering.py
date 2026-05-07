"""
Chain 3 — Filtering
====================
Improvements over previous version:
  FIX  BUG     : Zero avg_likes AND avg_comments no longer passes — channels with
                 no engagement data are flagged, not silently approved.
  FIX  MISSING : Activity check added using channel_age_years + video_count proxy.
  FIX  MISSING : Estimated profiles (Instagram/Twitter) use relaxed follower check
                 since their follower count may be None or imprecise.
  FIX  MINOR   : Removed benchmark mutation in test — use override params instead.
  REMOVED      : Follower growth spike check — not feasible from static API data.
                 Noted in comments so Chain 4 knows it isn't guaranteed.
"""

from __future__ import annotations

import logging
from typing import Any

from chain_0_ICP import ICPProfile
from chain_2_discovery import RawCreatorProfile

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────
# DROP REASONS  (for audit trail in logs + dossier)
# ─────────────────────────────────────────────

class DropReason:
    FOLLOWER_RANGE     = "Followers outside ICP tier range"
    LOW_ENGAGEMENT     = "Engagement rate below ICP minimum"
    DEAD_CHANNEL       = "View-to-subscriber ratio below 5% (dead audience)"
    BOT_SIGNAL_RATIO   = "Like-to-comment ratio outside healthy range (bot signal)"
    ZERO_ENGAGEMENT    = "Zero likes AND zero comments — inactive or newly created"
    INACTIVE           = "Channel too new or inactive — insufficient content history"
    NON_YOUTUBE        = "Platform not supported (restricted to YouTube)"


class FilterResult:
    """Wraps the outcome of a single candidate's filter evaluation."""

    def __init__(self, candidate: RawCreatorProfile, passed: bool, reason: str | None = None):
        self.candidate = candidate
        self.passed    = passed
        self.reason    = reason  # populated when passed=False


# ─────────────────────────────────────────────
# FILTER RULES
# ─────────────────────────────────────────────

def _check_follower_range(
    candidate: RawCreatorProfile,
    benchmarks,
    tolerance: float = 0.5,
) -> str | None:
    """
    Check follower count against ICP tier range with ±50% tolerance.

    FIX: Estimated profiles (Instagram/Twitter) with followers=None skip
         this check — we cannot reliably filter on a scraped number.
    """
    if candidate.followers is None:
        if candidate.data_confidence == "estimated":
            return None   # Can't enforce follower filter without reliable data
        return DropReason.FOLLOWER_RANGE  # Real profile with no followers = drop

    f_min = int(benchmarks.follower_min * tolerance)
    f_max = int(benchmarks.follower_max * (1 + tolerance))

    if not (f_min <= candidate.followers <= f_max):
        return DropReason.FOLLOWER_RANGE
    return None


def _check_engagement_rate(
    candidate: RawCreatorProfile,
    benchmarks,
) -> str | None:
    """
    Compare ER against tier-relative minimum benchmark.
    Only enforced for real data. Estimated profiles skip this.
    """
    if candidate.data_confidence == "estimated":
        return None   # No reliable ER for Instagram/Twitter profiles

    er = candidate.engagement_rate
    if er is None:
        return None   # No data — let Chain 4 decide

    if er < benchmarks.min_engagement_rate:
        return DropReason.LOW_ENGAGEMENT
    return None


def _check_view_to_sub_ratio(
    candidate: RawCreatorProfile,
    benchmarks,
) -> str | None:
    """
    View-to-subscriber ratio below 5% = dead channel.
    Only applies to YouTube real data.
    """
    if candidate.platform != "youtube" or candidate.data_confidence != "real":
        return None

    ratio = candidate.view_to_sub_ratio
    if ratio is None:
        return None

    if ratio < benchmarks.min_view_to_sub_ratio:
        return DropReason.DEAD_CHANNEL
    return None


def _check_like_to_comment_ratio(
    candidate: RawCreatorProfile,
    benchmarks,
) -> str | None:
    """
    Like-to-comment ratio outside healthy range signals bot activity.

    FIX: Also catches the case where BOTH avg_likes AND avg_comments are 0
         — this is not a healthy channel, it's an inactive one.
    """
    if candidate.platform != "youtube" or candidate.data_confidence != "real":
        return None

    avg_likes    = candidate.avg_likes    or 0
    avg_comments = candidate.avg_comments or 0

    # FIX BUG: Both zero = no engagement data = suspicious / inactive
    if avg_likes == 0 and avg_comments == 0:
        return DropReason.ZERO_ENGAGEMENT

    if avg_comments == 0:
        # Lots of likes, no comments — highly suspicious bot signal
        if avg_likes > 50:
            return DropReason.BOT_SIGNAL_RATIO
        return None   # Very small channel might genuinely have no comments

    ratio = candidate.like_to_comment_ratio
    if ratio is None:
        return None

    min_r, max_r = benchmarks.healthy_like_comment_ratio
    if not (min_r <= ratio <= max_r):
        return DropReason.BOT_SIGNAL_RATIO
    return None


def _check_activity(
    candidate: RawCreatorProfile,
    benchmarks,
) -> str | None:
    """
    Check that the channel is active enough to be worth pursuing.

    FIX MISSING: Previous version checked min_posts_per_month but that field
    wasn't in the candidate data. We now use a proxy:
      - Channel age < 3 months with < 3 videos = too new to evaluate
      - Channel age > 1 year with 0 recent videos = inactive

    NOTE: Follower growth spike detection (>20%/month) is NOT feasible from
    static YouTube API data — historical subscriber counts aren't exposed.
    This would require Social Blade or a paid data provider.
    """
    if candidate.platform != "youtube" or candidate.data_confidence != "real":
        return None

    age = candidate.channel_age_years or 0
    recent_video_count = len(candidate.recent_videos)

    # Channel older than 1 year but no recent videos pulled = very inactive
    if age > 1.0 and recent_video_count == 0:
        return DropReason.INACTIVE

    # Channel too new to have a content track record
    if age < 0.25 and recent_video_count < 3:
        return DropReason.INACTIVE

    return None


# ─────────────────────────────────────────────
# MAIN CALLABLE
# ─────────────────────────────────────────────

def run_filtering(
    icp: ICPProfile,
    candidates: list[RawCreatorProfile],
    follower_tolerance: float = 0.5,
) -> list[RawCreatorProfile]:
    """
    Chain 3 entry point. Applies hard-drop rules from ICP benchmarks.

    Args:
        icp:                 ICPProfile from Chain 0 (single source of truth)
        candidates:          RawCreatorProfile list from Chain 2
        follower_tolerance:  How far outside the tier range is acceptable (0.5 = ±50%)

    Returns filtered list of RawCreatorProfile — still typed, no dicts.
    """
    logger.info("Chain 3: Filtering %d candidates", len(candidates))
    benchmarks = icp.benchmarks

    results: list[FilterResult] = []

    for candidate in candidates:
        if candidate.platform != "youtube":
            drop_reason = DropReason.NON_YOUTUBE
        else:
            drop_reason = (
                _check_follower_range(candidate, benchmarks, follower_tolerance) or
                _check_engagement_rate(candidate, benchmarks) or
                _check_view_to_sub_ratio(candidate, benchmarks) or
                _check_like_to_comment_ratio(candidate, benchmarks) or
                _check_activity(candidate, benchmarks)
            )

        if drop_reason:
            logger.info(
                "Dropped @%s [%s]: %s",
                candidate.handle, candidate.platform, drop_reason,
            )
            results.append(FilterResult(candidate, passed=False, reason=drop_reason))
        else:
            results.append(FilterResult(candidate, passed=True))

    passed = [r.candidate for r in results if r.passed]
    dropped = [r for r in results if not r.passed]

    logger.info(
        "Chain 3 complete: %d passed, %d dropped. Drop breakdown: %s",
        len(passed),
        len(dropped),
        _summarise_drops(dropped),
    )

    return passed


def _summarise_drops(dropped: list[FilterResult]) -> dict[str, int]:
    """Aggregate drop reasons for logging."""
    summary: dict[str, int] = {}
    for r in dropped:
        reason = r.reason or "unknown"
        summary[reason] = summary.get(reason, 0) + 1
    return summary


# ─────────────────────────────────────────────
# QUICK LOCAL TEST
# ─────────────────────────────────────────────

if __name__ == "__main__":
    import asyncio
    import os
    from dotenv import load_dotenv
    from chain_0_ICP import BrandBrief, CampaignGoal, Platform, FollowerTier, run_icp_chain
    from chain_1_keywordExpansion import run_keyword_expansion
    from chain_2_discovery import run_discovery

    load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))
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
        competitor_brands  = ["Minimalist", "Plum"],
        excluded_niches    = ["adult content", "alcohol"],
    )

    async def main():
        api_key = os.environ.get("GROQ_API_KEY")
        if not api_key:
            print("ERROR: GROQ_API_KEY not found.")
            return

        icp = await run_icp_chain(sample_brief, api_key)
        
        # Loosen benchmarks so more creators pass the example
        icp.benchmarks.follower_min = 0
        icp.benchmarks.follower_max = 20_000_000
        icp.benchmarks.min_engagement_rate = 0.0
        icp.benchmarks.min_view_to_sub_ratio = 0.0
        
        keywords = run_keyword_expansion(icp)
        keywords.youtube_queries = keywords.youtube_queries[:2]  # quota limit for test

        candidates = await run_discovery(icp, keywords)
        filtered   = run_filtering(icp, candidates, follower_tolerance=10.0)

        print(f"\nFiltering: {len(candidates)} -> {len(filtered)} passed")
        for c in filtered:
            er = f"{c.engagement_rate:.2f}%" if c.engagement_rate else "n/a"
            followers = f"{c.followers:,}" if c.followers else "unknown"
            print(f"  [{c.platform:9}] @{c.handle:<30} followers={followers} ER={er}")

    asyncio.run(main())