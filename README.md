# CreatorLens

> Find the Right Influencer. Verify Them. Know What to Pay.

An influencer marketing intelligence platform that automates the entire pre-campaign workflow — discovery, qualification, brand safety audit, and pricing — using parallel TinyFish browser agents and local LLM scoring.

Built for the **TinyFish × HackerEarth $2M Pre-Accelerator Hackathon 2026**.

---

## How It Works

**Brand brief → Parallel TinyFish agents → LLM scoring → Ranked dossier**

1. Submit a brand brief (niche, audience, budget, platforms)
2. Ollama expands it into search keywords
3. TinyFish fires 100+ parallel browser agents across Instagram, TikTok, YouTube
4. Agents discover profiles, pull engagement stats, audit brand safety, and benchmark pricing — all simultaneously
5. Ollama scores and ranks candidates into a top-10 dossier
6. Results stored and returned via polling endpoint

Total runtime: under 2 minutes for 20 candidates.

---

## Stack

| Layer | Technology |
|---|---|
| Frontend | React + Vite (coming soon) |
| Backend | FastAPI (Python) |
| Agent infra | TinyFish API |
| LLM scoring | Ollama (llama3.2, local) |
| Storage | SQLite → Postgres |

---

## Project Structure

```
CreatorLens/
├── backend/
│   ├── main.py              # FastAPI entry point
│   ├── .env                 # API keys and config
│   ├── routes/
│   │   └── campaign.py      # POST /api/run-campaign, GET /api/status/{job_id}
│   ├── services/
│   │   ├── tinyfish.py      # Parallel browser agent calls
│   │   └── scoring.py       # Ollama keyword expansion + scoring
│   ├── models/
│   │   └── schemas.py       # Pydantic models
│   └── db/
│       └── database.py      # SQLite CRUD
├── test_tinyfish.py          # Standalone TinyFish API test
└── CLAUDE_CONTEXT.md         # AI assistant session context
```

---

## Setup

### Prerequisites
- Python 3.11+
- [Ollama](https://ollama.ai) installed and running
- TinyFish API key from [tinyfish.ai](https://tinyfish.ai)

### 1. Clone and install

```powershell
cd backend
python -m venv venv
venv\Scripts\activate
pip install fastapi uvicorn python-dotenv httpx
```

### 2. Configure environment

Create `backend/.env`:

```
TINYFISH_API_KEY=sk-tinyfish-your-key-here
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=llama3.2
```

### 3. Pull the model

```powershell
ollama pull llama3.2
```

### 4. Run the server

```powershell
cd backend
uvicorn main:app --reload --port 8000
```

API docs available at `http://localhost:8000/docs`

---

## API

### POST `/api/run-campaign`

Submit a brand brief and get a `job_id` back immediately. The pipeline runs in the background.

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

Response:
```json
{
  "job_id": "b7f239c6-...",
  "status": "pending",
  "results": null
}
```

### GET `/api/status/{job_id}`

Poll this every few seconds. Status flows: `pending → running → complete | failed`

```json
{
  "job_id": "b7f239c6-...",
  "status": "complete",
  "results": [
    {
      "handle": "cbum",
      "platform": "instagram",
      "followers": 26000000,
      "engagement_rate": 0.58,
      "risk_flag": "green",
      "price_low": 50000,
      "price_high": 150000,
      "composite_score": 87.4,
      "ai_summary": "Chris Bumstead is a dominant figure in the fitness supplement niche..."
    }
  ]
}
```

---

## Agent Pipeline

Four agent types run in parallel via `asyncio.gather`:

| Agent | Task | Sources |
|---|---|---|
| Discovery | Find matching profiles by keyword | Instagram, TikTok, YouTube |
| Qualification | Pull engagement stats per profile | Platform profile pages |
| Audit | Brand safety check | Google, Reddit, Twitter/X |
| Pricing | Rate benchmarks | Collabstr |

---

## Scoring Weights

| Signal | Weight |
|---|---|
| Engagement quality | 35% |
| Brand fit | 30% |
| Risk score (100 = safe) | 25% |
| Price fairness | 10% |

---

## Testing TinyFish

Run the standalone test script from `backend/`:

```powershell
python test_tinyfish.py
```

This fires a single agent at Instagram and prints all SSE events live.

---

## What Makes This Defensible

Manually vetting 20 influencers across 6 platforms takes 3 days of human labor. CreatorLens does it in under 2 minutes by running all agents simultaneously. The audit step alone — cross-referencing each influencer across news, Reddit, and Twitter — is impossible at this speed without parallel browser agents.

That's the moat.