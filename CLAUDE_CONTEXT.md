# PROJECT CONTEXT — CreatorLens

## What I'm building
An influencer marketing intelligence platform that lets brands submit a brief, then automatically discovers, vets, audits, prices, and ranks influencers using parallel TinyFish browser agents + Ollama LLM scoring.

Originally pitched as **InfluenceFit** for the TinyFish × HackerEarth $2M Pre-Accelerator Hackathon 2026. Renamed to **CreatorLens**.

---

## Tech Stack
- Frontend: React (Vite) — not yet built
- Backend: FastAPI (Python)
- DB: SQLite (`influencefit.db`) → Postgres for production
- APIs/Services: TinyFish API (parallel browser agents), Ollama (llama3.2, local LLM)
- Package manager: pip

---

## Project Structure
```
CreatorLens/
├── backend/
│   ├── main.py
│   ├── .env
│   ├── routes/
│   │   ├── __init__.py
│   │   └── campaign.py
│   ├── services/
│   │   ├── __init__.py
│   │   ├── tinyfish.py
│   │   └── scoring.py
│   ├── models/
│   │   ├── __init__.py
│   │   └── schemas.py
│   └── db/
│       ├── __init__.py
│       └── database.py
├── test_tinyfish.py
├── CLAUDE_CONTEXT.md
└── README.md
```

---

## Key Files

| File | Purpose |
|---|---|
| `backend/main.py` | FastAPI entry point, CORS, router registration, `load_dotenv()` at top |
| `backend/.env` | `TINYFISH_API_KEY`, `OLLAMA_BASE_URL`, `OLLAMA_MODEL` |
| `backend/routes/campaign.py` | `POST /api/run-campaign`, `GET /api/status/{job_id}`, pipeline with step logging |
| `backend/services/tinyfish.py` | TinyFish SSE agent calls: discovery, qualification, audit, pricing (parallel via asyncio) |
| `backend/services/scoring.py` | Ollama calls: keyword expansion + influencer scoring/summarization |
| `backend/models/schemas.py` | Pydantic models: BrandBrief, InfluencerDossier, CampaignResponse, enums |
| `backend/db/database.py` | SQLite CRUD: init_db, create_job, update_job_status, save_results, get_job |
| `test_tinyfish.py` | Standalone script to test TinyFish API independently (run from backend/) |

---

## Architecture

```
User submits brief
      │
      ▼
FastAPI POST /api/run-campaign → returns job_id immediately
      │
      ▼ (background task)
Ollama (llama3.2) → keyword expansion
      │
      ▼
TinyFish discovery agents (parallel, per platform per keyword)
      │
      ▼
TinyFish full audit batch (qual + audit + pricing — all parallel via asyncio.gather)
      │
      ▼
Ollama scoring → ranked top-10 dossiers
      │
      ▼
SQLite → job marked complete
      │
GET /api/status/{job_id} → returns results
```

---

## TinyFish Integration

- **Endpoint:** `https://agent.tinyfish.ai/v1/automation/run-sse`
- **Auth:** `X-API-Key` header (not `Authorization: Bearer`)
- **Key format:** `sk-tinyfish-...`
- **Response format:** SSE stream, listen until `type: COMPLETE`
- **Result key:** `event["result"]` (NOT `event["resultJson"]` — this was a bug, already fixed)
- **Parallelism:** `asyncio.gather()` fires all agents simultaneously
- **Browser profiles:** `stealth` for Instagram/TikTok, `lite` for Google/Collabstr
- **Agent timeout:** 60 seconds per agent
- **Free tier:** 500 steps. Builder tier available via credit request form at tinyfish.ai/accelerator

### Known issues / TODOs
- TinyFish returns result under a dynamic key (e.g. `"fitness_influencers"`, `"profiles"`) — code grabs `result[keys[0]]` to handle this
- Discovery goal string must include `"Return ONLY a raw JSON array, no markdown, no table, no explanation"` — otherwise TinyFish formats as HTML table. **This fix is pending — not yet applied to tinyfish.py**
- Pipeline was hanging on Step 3 (full audit) — root cause not yet confirmed. Step logging added to campaign.py to diagnose

---

## Ollama Integration

- **Endpoint:** `http://localhost:11434/api/chat`
- **Model:** `llama3.2` (pulled and confirmed working)
- **No API key needed** — runs locally
- **Common issue:** `ollama serve` throws bind error if already running — that's fine, means it's already up
- **Model name in .env must match exactly:** `OLLAMA_MODEL=llama3.2`

---

## Environment Variables (backend/.env)
```
TINYFISH_API_KEY=sk-tinyfish-xxxx
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=llama3.2
```

---

## API Endpoints

### POST /api/run-campaign
Request body:
```json
{
  "niche": "fitness supplements",
  "target_audience": "men 18-35 India",
  "budget_min": 500,
  "budget_max": 5000,
  "platforms": ["instagram", "youtube"],
  "keywords": ["protein shake", "gym workout", "bodybuilding"]
}
```
Response: `{ "job_id": "uuid", "status": "pending", "results": null }`

### GET /api/status/{job_id}
Response when complete:
```json
{
  "job_id": "uuid",
  "status": "complete",
  "results": [ ...list of InfluencerDossier objects... ]
}
```
Status values: `pending` → `running` → `complete` | `failed`

---

## Database Schema (SQLite)

```sql
jobs (job_id, status, brief_json, created_at, completed_at)

influencer_results (
  id, job_id, handle, platform,
  followers, engagement_rate,
  risk_flag, risk_evidence,
  price_low, price_high,
  composite_score, ai_summary,
  fetched_at
)
```
Results cached by handle+platform. 24h TTL recommended (not yet implemented).

---

## LLM Scoring Weights
| Signal | Weight |
|---|---|
| Engagement quality | 35% |
| Brand fit | 30% |
| Risk score (100 = safe) | 25% |
| Price fairness | 10% |

---

## Current State
- ✅ Backend fully scaffolded end-to-end
- ✅ TinyFish API confirmed working (tested via playground + test_tinyfish.py)
- ✅ Ollama confirmed working (llama3.2 pulled and running)
- ✅ SQLite DB initializing correctly
- ✅ Step-by-step logging added to pipeline
- ⚠️ Pipeline hanging on Step 3 (audit) — under investigation
- ⚠️ Discovery goal string missing "raw JSON only" instruction — pending fix
- ❌ Frontend not yet built (CORS ready for localhost:5173)

---

## Active Task
Debugging pipeline hang at Step 3 (TinyFish full audit). Next steps:
1. Apply "Return ONLY raw JSON" fix to all goal strings in `tinyfish.py`
2. Confirm pipeline runs end-to-end to `complete`
3. Build React frontend (Vite, two screens: brief form + results dashboard)

---

## Rules & Conventions
- `load_dotenv()` must be the first two lines of `main.py` before any other imports
- All API routes use `/api` prefix
- Background tasks handle the pipeline (non-blocking POST)
- Pydantic models define all request/response shapes in `models/schemas.py`
- TinyFish agents stream SSE, parsed until `COMPLETE` event, result at `event["result"]`
- LLM calls go through `services/scoring.py` via Ollama `/api/chat`
- Goal strings must always end with "Return ONLY a raw JSON array, no markdown, no table"
- Platform enum values: `"instagram"`, `"tiktok"`, `"youtube"` (lowercase only)