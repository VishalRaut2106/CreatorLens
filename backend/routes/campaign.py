from fastapi import APIRouter, BackgroundTasks, HTTPException
from models.schemas import BrandBrief, CampaignResponse, JobStatus
from db.database import create_job, update_job_status, save_results, get_job
from services.tinyfish import discover_influencers, run_full_audit
from services.scoring import score_influencers, expand_keywords
import uuid
import json

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
# Background pipeline
# -----------------------------------------------------------
async def execute_pipeline(job_id: str, brief: BrandBrief):
    try:
        update_job_status(job_id, "running")
        brief_dict = brief.model_dump()

        # Step 1: Expand keywords via Claude
        keywords = await expand_keywords(brief_dict)
        if brief.keywords:
            keywords = list(set(keywords + brief.keywords))

        # Step 2: Discover influencer profiles
        profiles = await discover_influencers(
            keywords=keywords,
            platforms=[p.value for p in brief.platforms]
        )

        if not profiles:
            update_job_status(job_id, "failed")
            return

        # Step 3: Qualify + audit + pricing (parallel batch)
        enriched = await run_full_audit(profiles)

        # Step 4: Claude scoring + summarization
        scored = await score_influencers(enriched, brief_dict)

        # Step 5: Save to DB
        save_results(job_id, scored)
        update_job_status(job_id, "complete")

    except Exception as e:
        print(f"Pipeline error for job {job_id}: {e}")
        update_job_status(job_id, "failed")