from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from routes.campaign import router as campaign_router
from db.database import init_db

app = FastAPI(title="CreatorLens API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(campaign_router, prefix="/api")

@app.on_event("startup")
async def startup():
    init_db()

@app.get("/")
def root():
    return {"status": "CreatorLens API running"}