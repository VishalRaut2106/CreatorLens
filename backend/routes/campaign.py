from fastapi import APIRouter, BackgroundTasks, HTTPException
from models.schemas import BrandBrief, CampaignResponse, JobStatus
from db.database import create_job, update_job_status, save_results, get_job, get_conn
from services.tinyfish import discover_influencers, run_full_audit, cancel_all_runs, active_runs
from services.scoring import score_influencers, expand_keywords, draft_outreach
import uuid
import json
import traceback

router = APIRouter()


# -----------------------------------------------------------
# POST /api/run-campaign
# -----------------------------------------------------------
@router.post("/run-campaign", response_model=CampaignResponse)
async def run_campaign(brief: BrandBrief, background_tasks: BackgroundTasks):
    job_id = str(uuid.uuid4())
    create_job(job_id, brief.model_dump_json())

    background_tasks.add_task(execute_pipeline, job_id, brief)

    return CampaignResponse(job_id=job_id, status=JobStatus.pending)


# -----------------------------------------------------------
# GET /api/campaigns
# -----------------------------------------------------------
@router.get("/campaigns")
def get_campaigns():
    conn = get_conn()
    jobs = conn.execute(
        "SELECT job_id, status, brief_json, created_at, completed_at FROM jobs ORDER BY created_at DESC LIMIT 20"
    ).fetchall()
    conn.close()
    return [dict(j) for j in jobs]

# -----------------------------------------------------------
# GET /api/status/{job_id}
# -----------------------------------------------------------
@router.get("/status/{job_id}", response_model=CampaignResponse)
def get_status(job_id: str):
    job, results = get_job(job_id)

    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    dossiers = None
    if job["status"] == "complete" and results:
        dossiers = [dict(r) for r in results]

    return CampaignResponse(
        job_id=job_id,
        status=JobStatus(job["status"]),
        results=dossiers
    )


# -----------------------------------------------------------
# POST /api/outreach/{job_id}/{handle}
# -----------------------------------------------------------
@router.post("/outreach/{job_id}/{handle}")
async def generate_outreach(job_id: str, handle: str):
    job, results = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    # Find the influencer
    influencer = next((r for r in results if r.get("handle") == handle), None)
    if not influencer:
        raise HTTPException(status_code=404, detail="Influencer not found")

    # Get the brief
    import json
    brief = json.loads(job["brief_json"])

    message = await draft_outreach(influencer, brief)
    return {"handle": handle, "message": message}


# -----------------------------------------------------------
# POST /api/cancel-agents  — emergency stop
# -----------------------------------------------------------
@router.post("/cancel-agents")
async def cancel_agents():
    print(f"\n[CANCEL] Cancel requested — {len(active_runs)} active runs")
    result = await cancel_all_runs()
    print(f"[CANCEL] Done: {result}")
    return result


# -----------------------------------------------------------
# Background pipeline
# -----------------------------------------------------------
async def execute_pipeline(job_id: str, brief: BrandBrief):
    try:
        update_job_status(job_id, "running")
        brief_dict = brief.model_dump()
        print(f"\n{'='*60}")
        print(f"[PIPELINE] Job {job_id} — STARTED")
        print(f"[PIPELINE] Brief: {brief_dict}")
        print(f"{'='*60}")

        # Step 1: Expand keywords via Ollama
        print(f"\n[STEP 1] Expanding keywords via Ollama...")
        try:
            keywords = await expand_keywords(brief_dict)
            if brief.keywords:
                keywords = list(set(keywords + brief.keywords))
            print(f"[STEP 1] ✓ Keywords: {keywords}")
        except Exception as e:
            print(f"[STEP 1] ✗ FAILED: {e}")
            traceback.print_exc()
            update_job_status(job_id, "failed")
            return

        # Step 2: Discover influencer profiles
        print(f"\n[STEP 2] Discovering influencers via TinyFish...")
        try:
            profiles = await discover_influencers(
                keywords=keywords,
                platforms=[p.value for p in brief.platforms]
            )
            print(f"[STEP 2] ✓ Found {len(profiles)} profiles")
            for p in profiles[:5]:
                print(f"  - {p.get('handle')} ({p.get('platform')})")
        except Exception as e:
            print(f"[STEP 2] ✗ FAILED: {e}")
            traceback.print_exc()
            update_job_status(job_id, "failed")
            return

        if not profiles:
            print(f"[STEP 2] ✗ No profiles found — marking job as failed")
            update_job_status(job_id, "failed")
            return

        # Cap to top 1 by followers before audit — saves agent calls
        profiles = sorted(profiles, key=lambda x: x.get("followers", 0), reverse=True)[:1]
        print(f"[STEP 2] Capped to top {len(profiles)} profile by followers")

        # Step 3: Qualify + audit + pricing (parallel batch)
        print(f"\n[STEP 3] Running full audit (qual + audit + pricing)...")
        try:
            enriched = await run_full_audit(profiles)
            print(f"[STEP 3] ✓ Enriched {len(enriched)} profiles")
        except Exception as e:
            print(f"[STEP 3] ✗ FAILED: {e}")
            traceback.print_exc()
            update_job_status(job_id, "failed")
            return

        # Step 4: Ollama scoring + summarization
        print(f"\n[STEP 4] Scoring via Ollama...")
        try:
            scored = await score_influencers(enriched, brief_dict)

            # Merge raw stats back onto scored results
            enriched_map = {p["handle"]: p for p in enriched}
            for s in scored:
                raw = enriched_map.get(s.get("handle"), {})
                s.setdefault("followers", raw.get("followers", 0))
                s.setdefault("engagement_rate", raw.get("engagement_rate", 0.0))
                s.setdefault("price_low", raw.get("price_low", 0))
                s.setdefault("price_high", raw.get("price_high", 0))
                s.setdefault("risk_flag", raw.get("risk_flag", "green"))
                s.setdefault("risk_evidence", raw.get("risk_evidence"))

            print(f"[STEP 4] ✓ Scored {len(scored)} influencers")
            for s in scored[:3]:
                print(f"  - {s.get('handle')}: score={s.get('composite_score')}")
        except Exception as e:
            print(f"[STEP 4] ✗ FAILED: {e}")
            traceback.print_exc()
            update_job_status(job_id, "failed")
            return

        # Step 5: Save to DB
        print(f"\n[STEP 5] Saving results to DB...")
        try:
            save_results(job_id, scored)
            update_job_status(job_id, "complete")
            print(f"[STEP 5] ✓ Saved. Job COMPLETE.")
        except Exception as e:
            print(f"[STEP 5] ✗ FAILED: {e}")
            traceback.print_exc()
            update_job_status(job_id, "failed")
            return

        print(f"\n{'='*60}")
        print(f"[PIPELINE] Job {job_id} — COMPLETE ✓")
        print(f"{'='*60}\n")

    except Exception as e:
        print(f"\n[PIPELINE] UNHANDLED ERROR for job {job_id}:")
        traceback.print_exc()
        update_job_status(job_id, "failed")