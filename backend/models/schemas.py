from pydantic import BaseModel
from typing import Optional, List
from enum import Enum

class Platform(str, Enum):
    instagram = "instagram"
    tiktok = "tiktok"
    youtube = "youtube"
    twitter = "twitter"

class RiskFlag(str, Enum):
    green = "green"
    amber = "amber"
    red = "red"

# --- Request ---

class BrandBrief(BaseModel):
    niche: str                          # e.g. "fitness supplements"
    target_audience: str                # e.g. "men 18-35 India"
    budget_min: int                     # in USD
    budget_max: int
    platforms: List[Platform]
    keywords: Optional[List[str]] = []  # optional extra keywords

# --- Response ---

class InfluencerDossier(BaseModel):
    handle: str
    platform: Platform
    followers: Optional[int] = 0
    engagement_rate: Optional[float] = None
    risk_flag: RiskFlag = RiskFlag.green
    risk_evidence: Optional[str] = None
    risk_sources: Optional[List] = []
    price_low: Optional[int] = 0
    price_high: Optional[int] = 0
    composite_score: Optional[float] = 0.0
    ai_summary: Optional[str] = ""

class JobStatus(str, Enum):
    pending = "pending"
    running = "running"
    complete = "complete"
    failed = "failed"

class CampaignResponse(BaseModel):
    job_id: str
    status: JobStatus
    results: Optional[List[InfluencerDossier]] = None
    error: Optional[str] = None