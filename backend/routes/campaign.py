"""
campaign.py — Campaign API routes
Clean route definitions only. All business logic lives in services/.
"""

from fastapi import APIRouter, BackgroundTasks, HTTPException
from models.schemas import BrandBrief, CampaignResponse, JobStatus
from db.database import create_job, get_job, get_conn
from services.pipeline import execute_pipeline
from services.outreach import draft_outreach
from services.platforms.instagram import find_competitor_influencers
import uuid
import json

router = APIRouter()

# Compatibility shim — kept for any code that imports active_runs
active_runs: list[str] = []


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

    influencer = next((r for r in results if r.get("handle") == handle), None)
    if not influencer:
        raise HTTPException(status_code=404, detail="Influencer not found")

    brief = json.loads(job["brief_json"])

    message = await draft_outreach(influencer, brief)
    return {"handle": handle, "message": message}


# -----------------------------------------------------------
# POST /api/cancel-agents  — emergency stop
# -----------------------------------------------------------
@router.post("/cancel-agents")
async def cancel_agents():
    print(f"\n[CANCEL] Cancel requested — {len(active_runs)} active runs")
    active_runs.clear()
    return {"cancelled": 0, "message": "No active agents (using free API stack)"}


# -----------------------------------------------------------
# POST /api/competitor-intel
# -----------------------------------------------------------
@router.post("/competitor-intel")
async def competitor_intel(body: dict):
    competitor = body.get("competitor_brand")
    if not competitor:
        raise HTTPException(status_code=400, detail="competitor_brand required")

    print(f"[COMPETITOR] Searching for {competitor} influencers...")
    results = await find_competitor_influencers(competitor)
    print(f"[COMPETITOR] Found {len(results)} partnerships")
    return {"competitor": competitor, "influencers": results}
