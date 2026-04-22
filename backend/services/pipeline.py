"""
pipeline.py — Campaign execution pipeline
Orchestrates the full influencer discovery -> audit -> scoring flow.
Runs as a background task triggered by the campaign route.
"""

import asyncio
import traceback

from models.schemas import BrandBrief
from db.database import update_job_status, save_results
from services.discovery import discover_influencers
from services.auditor import run_full_audit
from services.scoring import (
    score_influencers, expand_keywords,
    pre_filter_score, fill_missing_estimates
)
from services.platforms.tavily import find_competitor_influencers


async def execute_pipeline(job_id: str, brief: BrandBrief):
    """Background task that runs the full campaign pipeline."""
    try:
        update_job_status(job_id, "running")
        brief_dict = brief.model_dump()
        print(f"\n{'='*60}")
        print(f"[PIPELINE] Job {job_id} — STARTED")
        print(f"[PIPELINE] Brief: {brief_dict}")
        print(f"{'='*60}")

        # Step 1: Expand keywords via LLM
        print(f"\n[STEP 1] Expanding keywords via LLM...")
        try:
            keywords = await expand_keywords(brief_dict)
            if brief.keywords:
                keywords = list(set(keywords + brief.keywords))
            print(f"[STEP 1] Keywords: {keywords}")

            if not keywords:
                print(f"[STEP 1] WARNING: Generated 0 keywords. Pipeline may find fewer results.")
        except Exception as e:
            print(f"[STEP 1] FAILED: {e}")
            traceback.print_exc()
            update_job_status(job_id, "failed")
            return

        # Step 2: Discover influencer profiles + competitor intel in parallel
        print(f"\n[STEP 2] Discovering influencers...")
        try:
            competitor_task = None
            if brief.competitor_brand:
                print(f"  [COMPETITOR] Searching for {brief.competitor_brand} partnerships...")
                competitor_task = find_competitor_influencers(brief.competitor_brand)

            profiles_task = discover_influencers(
                keywords=keywords,
                platforms=[p.value for p in brief.platforms]
            )

            if competitor_task:
                profiles, competitor_profiles = await asyncio.gather(profiles_task, competitor_task)
                print(f"  [COMPETITOR] Found {len(competitor_profiles)} partnerships")

                competitor_handles = {p.get("handle", "").lower().replace("@", "") for p in competitor_profiles}
                for p in profiles:
                    handle = p.get("handle", "").lower().replace("@", "")
                    if handle in competitor_handles:
                        p["competitor_flag"] = True
                        p["competitor_evidence"] = next(
                            (c.get("evidence") for c in competitor_profiles
                             if c.get("handle", "").lower().replace("@", "") == handle),
                            None
                        )
            else:
                profiles = await profiles_task

            print(f"[STEP 2] Found {len(profiles)} profiles")
            for p in profiles[:10]:
                print(f"  - {p.get('handle')} ({p.get('platform')})")
        except Exception as e:
            print(f"[STEP 2] FAILED: {e}")
            traceback.print_exc()
            update_job_status(job_id, "failed")
            return

        if not profiles:
            print(f"[STEP 2] No profiles found — marking job as failed")
            update_job_status(job_id, "failed")
            return

        # Pre-filter and cap to top 5
        print(f"\n[STEP 2b] Pre-filtering discovered profiles...")
        valid_profiles = []
        for p in profiles:
            score = pre_filter_score(p)
            if score > 0:
                p["_pre_score"] = score
                valid_profiles.append(p)

        valid_profiles.sort(key=lambda x: x.get("_pre_score", 0), reverse=True)
        profiles = valid_profiles[:5]

        if not profiles:
            print(f"[STEP 2b] No valid profiles passed pre-filter — marking job as failed")
            update_job_status(job_id, "failed")
            return

        print(f"[STEP 2b] Passed {len(profiles)} profiles for deep audit")

        # Step 3: Qualify + audit + pricing (parallel batch)
        print(f"\n[STEP 3] Running full audit (qual + audit + pricing)...")
        try:
            enriched = await run_full_audit(profiles, brief_dict)
            print(f"[STEP 3] Enriched {len(enriched)} profiles")

            enriched = sorted(
                enriched,
                key=lambda x: x.get("followers", 0),
                reverse=True
            )[:5]

            def post_audit_score(p):
                score = 0
                engagement = p.get("engagement_rate") or 0
                score += engagement * 10
                risk = p.get("risk_flag", "green")
                if risk == "red":     score -= 50
                elif risk == "amber": score -= 10
                else:                 score += 20
                price_high = p.get("price_high") or 0
                budget_max = brief_dict.get("budget_max", 5000)
                if 0 < price_high <= budget_max: score += 30
                elif price_high > budget_max:    score -= 20
                return score

            enriched = sorted(enriched, key=post_audit_score, reverse=True)
            print(f"[STEP 3] Re-ranked by audit quality")
            for e in enriched[:10]:
                print(f"  - {e.get('handle')}: engagement={e.get('engagement_rate')}% risk={e.get('risk_flag')}")
        except Exception as e:
            print(f"[STEP 3] FAILED: {e}")
            traceback.print_exc()
            update_job_status(job_id, "failed")
            return

        # Step 3b: Fill missing estimates
        print(f"\n[STEP 3b] Filling missing data with estimates...")
        enriched = fill_missing_estimates(enriched)
        print(f"[STEP 3b] Estimates filled")

        # Step 4: LLM scoring + summarization
        print(f"\n[STEP 4] Scoring via LLM...")
        try:
            scored = await score_influencers(enriched, brief_dict)

            for s in scored:
                s["handle"] = s.get("handle", "").lower().strip().lstrip("@")

            enriched_map = {p["handle"].lower().strip().lstrip("@"): p for p in enriched}
            for s in scored:
                raw = enriched_map.get(s.get("handle", ""), {})
                s.setdefault("followers",             raw.get("followers", 0))
                s.setdefault("engagement_rate",       raw.get("engagement_rate", None))
                s.setdefault("price_low",             raw.get("price_low", 0))
                s.setdefault("price_high",            raw.get("price_high", 0))
                s.setdefault("risk_flag",             raw.get("risk_flag", "green"))
                s.setdefault("risk_evidence",         raw.get("risk_evidence", None))
                s.setdefault("risk_sources",          raw.get("risk_sources", []))
                s.setdefault("competitor_flag",       raw.get("competitor_flag", False))
                s.setdefault("competitor_evidence",   raw.get("competitor_evidence", None))
                s.setdefault("engagement_estimated",  raw.get("engagement_estimated", False))
                s.setdefault("price_estimated",       raw.get("price_estimated", False))
                valid_platforms = {"instagram", "tiktok", "youtube", "twitter"}
                if s.get("platform", "").lower() not in valid_platforms:
                    s["platform"] = raw.get("platform", "instagram")
                if s.get("risk_flag") not in ("green", "amber", "red"):
                    s["risk_flag"] = "green"

            print(f"[STEP 4] Scored {len(scored)} influencers")
            for s in scored[:10]:
                print(f"  - {s.get('handle')}: score={s.get('composite_score')}")
        except Exception as e:
            print(f"[STEP 4] FAILED: {e}")
            traceback.print_exc()
            update_job_status(job_id, "failed")
            return

        # Step 5: Save to DB
        print(f"\n[STEP 5] Saving results to DB...")
        try:
            save_results(job_id, scored[:5])
            update_job_status(job_id, "complete")
            print(f"[STEP 5] Saved. Job COMPLETE.")
        except Exception as e:
            print(f"[STEP 5] FAILED: {e}")
            traceback.print_exc()
            update_job_status(job_id, "failed")
            return

        print(f"\n{'='*60}")
        print(f"[PIPELINE] Job {job_id} — COMPLETE")
        print(f"{'='*60}\n")

    except Exception as e:
        print(f"\n[PIPELINE] UNHANDLED ERROR for job {job_id}:")
        traceback.print_exc()
        update_job_status(job_id, "failed")
