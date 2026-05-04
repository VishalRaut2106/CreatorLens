"""
Chain 3 — Filtering
===================
Applies hard filtering to discovered candidates based on the single source of truth:
the ICP Profile generated in Chain 0.

Filters out candidates that do not meet the minimum benchmarks for followers,
engagement rate, view-to-sub ratio, and healthy activity patterns.
"""

import logging
from typing import List, Dict, Any

from chain_0_ICP import ICPProfile

logger = logging.getLogger(__name__)


def run_filtering(icp: ICPProfile, candidates: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Filters candidates based on the hard constraints defined in the ICP Profile.
    """
    logger.info(f"Starting Chain 3: Filtering (Initial candidates: {len(candidates)})")
    
    filtered_candidates = []
    benchmarks = icp.benchmarks
    
    # We allow a wider tolerance (e.g. 50%) on followers to avoid missing good creators
    # who are just slightly under/over the tier limits.
    f_min = int(benchmarks.follower_min * 0.5)
    f_max = int(benchmarks.follower_max * 1.5)

    for candidate in candidates:
        name = candidate.get("channel_title", "Unknown")
        followers = candidate.get("followers", 0)
        engagement_rate = candidate.get("engagement_rate", 0.0)
        avg_views = candidate.get("avg_views", 0)
        avg_likes = candidate.get("avg_likes", 0)
        avg_comments = candidate.get("avg_comments", 0)
        
        # 1. Follower constraints
        if not (f_min <= followers <= f_max):
            logger.info(f"Dropped {name}: Followers {followers:,} outside tolerated range [{f_min:,}, {f_max:,}]")
            continue
            
        # 2. Engagement Rate constraint
        if engagement_rate < benchmarks.min_engagement_rate:
            logger.info(f"Dropped {name}: ER {engagement_rate}% below minimum {benchmarks.min_engagement_rate}%")
            continue
            
        # 3. View to Sub ratio constraint
        view_to_sub_ratio = avg_views / followers if followers > 0 else 0
        if view_to_sub_ratio < benchmarks.min_view_to_sub_ratio:
            logger.info(f"Dropped {name}: View/Sub ratio {view_to_sub_ratio:.4f} below minimum {benchmarks.min_view_to_sub_ratio}")
            continue
            
        # 4. Like to Comment ratio constraint
        if avg_comments > 0:
            like_to_comment_ratio = avg_likes / avg_comments
            min_ratio, max_ratio = benchmarks.healthy_like_comment_ratio
            if not (min_ratio <= like_to_comment_ratio <= max_ratio):
                logger.info(f"Dropped {name}: Like/Comment ratio {like_to_comment_ratio:.1f} outside healthy range [{min_ratio}, {max_ratio}]")
                continue
        elif avg_likes > 100:
            # If they have lots of likes but 0 comments, that's highly suspicious
            logger.info(f"Dropped {name}: Zero comments but {avg_likes:,} likes. Highly suspicious.")
            continue
                
        # Candidate passed all filters
        filtered_candidates.append(candidate)
        
    logger.info(f"Filtering complete. Kept {len(filtered_candidates)} out of {len(candidates)} candidates.")
    return filtered_candidates


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

    # Ensure backend is in the path for module resolution if run as standalone
    import sys
    sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
    
    load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    sample_brief = BrandBrief(
        brand_name         = "Dot & Key",
        product_description= "Vitamin C serum for hyperpigmentation targeting Indian women",
        campaign_goal      = CampaignGoal.CONVERSION,
        niche              = "skincare",
        platforms          = [Platform.YOUTUBE],
        follower_tier      = FollowerTier.MICRO, # 10k - 100k
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
            print("ERROR: GROQ_API_KEY not found in environment.")
            return

        print("=== RUNNING CHAIN 0 (ICP) ===")
        icp = await run_icp_chain(sample_brief, api_key)
        
        print("\n=== RUNNING CHAIN 1 (Keyword Expansion) ===")
        keywords = run_keyword_expansion(icp)
        keywords.youtube_queries = keywords.youtube_queries[:2] # limit
        
        print("\n=== RUNNING CHAIN 2 (Discovery) ===")
        candidates = await run_discovery(icp, keywords)
        
        print("\n=== RUNNING CHAIN 3 (Filtering) ===")
        
        # Loosen benchmarks for the local test so we actually get some results
        icp.benchmarks.follower_min = 1_000
        icp.benchmarks.follower_max = 5_000_000
        icp.benchmarks.min_engagement_rate = 0.5
        icp.benchmarks.healthy_like_comment_ratio = (5, 150)
        
        filtered = run_filtering(icp, candidates)
        
        print("\n=== FINAL RESULTS ===")
        print(f"Passed filters: {len(filtered)} / {len(candidates)}")
        for c in filtered:
            print(f"- {c['channel_title']} ({c['followers']:,} followers, ER: {c['engagement_rate']}%)")

    asyncio.run(main())
