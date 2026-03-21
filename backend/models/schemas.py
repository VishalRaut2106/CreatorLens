from pydantic import BaseModel
from typing import Optional, List
from enum import Enum

class Platform(str, Enum):
    instagram = "instagram"
    twitter = "twitter"
    youtube = "youtube"

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
    followers: int
    engagement_rate: float
    risk_flag: RiskFlag
    risk_evidence: Optional[str]
    price_low: int
    price_high: int
    composite_score: float              # 0-100
    ai_summary: str

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