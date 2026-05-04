<div align="center">
  <h1>🎯 CreatorLens</h1>
  <p><b>Find the Right Influencer. Verify Them. Know What to Pay.</b></p>
</div>

An influencer marketing intelligence platform that automates the entire pre-campaign workflow — discovery, qualification, brand safety audit, and pricing — using the **YouTube Data API**, **Tavily web search**, and **Gemini LLM scoring**.

### Built With

![React](https://img.shields.io/badge/React-18.3-61DAFB?style=for-the-badge&logo=react&logoColor=white)
![Vite](https://img.shields.io/badge/Vite-5.4-646CFF?style=for-the-badge&logo=vite&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-0.110+-009688?style=for-the-badge&logo=fastapi&logoColor=white)
![Python](https://img.shields.io/badge/Python-3.11+-3776AB?style=for-the-badge&logo=python&logoColor=white)
![Gemini](https://img.shields.io/badge/Gemini-2.0_Flash-4285F4?style=for-the-badge&logo=googlegemini&logoColor=white)
![YouTube API](https://img.shields.io/badge/YouTube-Data_API_v3-FF0000?style=for-the-badge&logo=youtube&logoColor=white)
![Tavily](https://img.shields.io/badge/Tavily-Web_Search-5A67D8?style=for-the-badge)
![SQLite](https://img.shields.io/badge/SQLite-003B57?style=for-the-badge&logo=sqlite&logoColor=white)
![Pydantic](https://img.shields.io/badge/Pydantic-E92063?style=for-the-badge&logo=pydantic&logoColor=white)
![HTTPX](https://img.shields.io/badge/HTTPX-Async_HTTP-3B3B3B?style=for-the-badge)
![CSS](https://img.shields.io/badge/Vanilla_CSS-1572B6?style=for-the-badge&logo=css3&logoColor=white)
![JavaScript](https://img.shields.io/badge/JavaScript-ES6+-F7DF1E?style=for-the-badge&logo=javascript&logoColor=black)

---

## ✨ Features

- **Automated Discovery**: Submit a brand brief, and template-based keyword expansion finds the best-fit creators across YouTube, Instagram, and Twitter.
- **YouTube Data API Integration**: Directly queries YouTube's official API for accurate channel search, subscriber counts, view statistics, and engagement data.
- **Tavily Web Search**: Uses Tavily's web search API to discover social media profiles and gather brand safety intelligence.
- **Brand Safety Audit**: Automatically scans for controversy, scandals, and reputation risks linked to each influencer.
- **Fair Pricing Engine**: Estimates influencer rates using platform-specific cost-per-post benchmarks based on follower tiers.
- **Intelligent Scoring**: Ranks candidates using Gemini 2.0 Flash on engagement quality, audience authenticity, niche relevance, and brand safety.
- **Competitor Intel**: Discover influencers and brand ambassadors already working with competitor brands.
- **Campaign History**: Automatically stores previous pipeline runs, accessible via a dedicated history dashboard.
- **AI Outreach Drafting**: Generates personalized influencer outreach messages based on performance data and brand brief context.
- **Lightning Fast**: Generates a comprehensive, ranked top-5 dossier of vetted influencers in under 2 minutes.

---

## 🏗️ Architecture & Tech Stack

### Tech Stack

| Component | Technology |
|---|---|
| **Frontend** | React + Vite + Vanilla CSS |
| **Backend** | FastAPI (Python 3.11+) |
| **Influencer Discovery** | YouTube Data API v3 + Tavily Web Search |
| **LLM Engine** | Google Gemini 2.0 Flash (Ollama as optional fallback) |
| **Database** | SQLite |

### Project Structure

```
CreatorLens/
├── backend/
│   ├── main.py                       # FastAPI app entry point, CORS, router registration
│   ├── chains/
│   │   ├── chain_0_ICP.py            # Converts brand briefs into Ideal Creator Profiles (ICP)
│   │   ├── chain_1_keywordExpansion.py # Maps ICP into executable platform search queries
│   │   └── chain_2_discovery.py      # Orchestrates platform searches to discover raw candidates
│   ├── routes/
│   │   └── campaign.py               # API route definitions (no business logic)
│   ├── services/
│   │   ├── platforms/
│   │   │   ├── youtube.py            # YouTube Data API v3 client (search, stats, discovery)
│   │   │   └── tavily.py             # Tavily web search client (search, parsing, competitor intel)
│   │   ├── discovery.py              # Multi-platform discovery aggregator
│   │   ├── auditor.py                # Profile qualification, brand safety audit, pricing
│   │   ├── llm_client.py             # LLM API calls, retry logic, JSON parsing
│   │   ├── scoring.py                # Pre-filter, missing data estimates, LLM scoring
│   │   ├── outreach.py               # Personalized outreach message drafting
│   │   ├── pipeline.py               # Full campaign execution pipeline (background task)
│   │   └── agents.py                 # Backward-compatibility shim (re-exports only)
│   ├── models/
│   │   └── schemas.py                # Pydantic request/response models
│   ├── db/
│   │   └── database.py               # SQLite database init, CRUD operations
│   └── creatorlens.db                # SQLite database file
├── frontend/
│   └── src/
│       ├── App.jsx                   # Root component, screen routing
│       ├── App.css                   # Global styles & design system
│       └── components/
│           ├── BriefForm.jsx         # Brand brief input form
│           ├── Dashboard.jsx         # Results dashboard with dossier cards
│           └── CampaignHistory.jsx   # Past campaign history viewer
└── docker-compose.yml                # Container orchestration
```

### System Components

```
┌──────────────────────────────────────────────────────────────────────┐
│  FRONTEND (React + Vite)       http://localhost:5173                 │
│  ┌─────────────┐ ┌──────────────────┐ ┌───────────────────────┐     │
│  │  BriefForm   │ │    Dashboard     │ │  CampaignHistory      │     │
│  │ (Submit Brief)│ │ (Polling + Cards)│ │ (Past Campaigns)      │     │
│  └──────┬───────┘ └────────┬─────────┘ └──────────┬────────────┘     │
│         │  POST /run-campaign  GET /status/:id     │ GET /campaigns  │
└─────────┼──────────────────┼──────────────────────┼─────────────────┘
          │    REST API      │                      │
┌─────────▼──────────────────▼──────────────────────▼─────────────────┐
│  BACKEND (FastAPI)             http://localhost:8000                 │
│  ┌──────────────────────────────────────────────────────────┐       │
│  │  Routes (campaign.py)                                     │       │
│  │  • POST /api/run-campaign    → launch background pipeline │       │
│  │  • GET  /api/status/:id      → poll job status + results  │       │
│  │  • GET  /api/campaigns       → list campaign history      │       │
│  │  • POST /api/outreach/:id/:h → generate outreach message  │       │
│  │  • POST /api/competitor-intel→ find competitor influencers │       │
│  │  • POST /api/cancel-agents   → emergency stop             │       │
│  └──────────────────────────────────────────────────────────┘       │
│                              │                                      │
│  ┌───────────────────────────▼──────────────────────────────┐       │
│  │  Background Pipeline (pipeline.py)                        │       │
│  │  Step 1 → Step 2 → Step 2b → Step 3 → Step 4 → Step 5   │       │
│  └──────────────────────────────────────────────────────────┘       │
│         │                    │                    │                  │
│  ┌──────▼───────┐  ┌────────▼────────┐  ┌───────▼────────┐         │
│  │  scoring.py   │  │  platforms/     │  │  database.py   │         │
│  │  llm_client.py│  │  youtube.py     │  │  (SQLite CRUD) │         │
│  │  (Gemini LLM) │  │  tavily.py     │  └───────┬────────┘         │
│  └──────┬────────┘  └────────┬────────┘          │                  │
└─────────┼────────────────────┼───────────────────┼─────────────────┘
          │                    │                   │
  ┌───────▼────────┐  ┌───────▼─────────────┐  ┌──▼──────────┐
  │  Gemini API     │  │ YouTube Data API v3  │  │  SQLite DB  │
  │  (2.0 Flash)    │  │ Tavily Web Search    │  │             │
  └─────────────────┘  │  → YouTube channels  │  └─────────────┘
                       │  → Instagram profiles│
                       │  → Twitter profiles  │
                       │  → Competitor intel   │
                       └──────────────────────┘
```

### Pipeline Data Flow

The core pipeline runs as a **background task** after a brand brief is submitted. The new chained architecture handles reasoning first, then data execution:

```
Brand Brief
    │
    ▼
┌──────────────────────────────────────────────────────────────┐
│ Chain 0: ICP Generation (LLM)                                │
│ Transforms raw brief into a structured Ideal Creator Profile │
│ including psychographics, competitor sets, and strict bounds.│
└──────────────────────┬───────────────────────────────────────┘
                       ▼
┌──────────────────────────────────────────────────────────────┐
│ Chain 1: Keyword Expansion (LLM)                             │
│ Generates highly-specific search query objects tailored for  │
│ YouTube Data API (e.g. "skincare review 2025") and Tavily.   │
└──────────────────────┬───────────────────────────────────────┘
                       ▼
┌──────────────────────────────────────────────────────────────┐
│ Chain 2: Discovery (Platform APIs)                           │
│ Executes queries against YouTube/Tavily concurrently.        │
│ Deduplicates and builds rich, unfiltered creator profiles    │
│ complete with recent video engagement stats.                 │
└──────────────────────┬───────────────────────────────────────┘
                       ▼
┌──────────────────────────────────────────────────────────────┐
│ Step 3: Full Audit (Parallel — 3 tasks per profile)          │
│ For each profile, runs 3 tasks in parallel via asyncio:      │
│   • Qualification → YouTube API stats / Tavily engagement    │
│   • Brand Safety  → Tavily controversy scan                  │
│   • Pricing       → Platform-specific cost benchmarks        │
│                                                              │
│ Hard filters applied: min engagement rate, follower mismatch │
│ Post-audit re-ranking by engagement + risk + price fit       │
└──────────────────────┬───────────────────────────────────────┘
                       ▼
┌──────────────────────────────────────────────────────────────┐
│ Step 4: LLM Scoring & Summarization (Gemini 2.0 Flash)       │
│ Scores each candidate on 4 weighted dimensions:              │
│   • Engagement Quality (40%)                                 │
│   • Audience Authenticity (30%)                              │
│   • Niche Relevance (20%)                                    │
│   • Brand Safety (10%)                                       │
│ Generates AI summary + composite score per influencer        │
└──────────────────────┬───────────────────────────────────────┘
                       ▼
┌──────────────────────────────────────────────────────────────┐
│ Step 5: Persist Results (SQLite)                             │
│ Ranked dossiers saved to DB, job marked complete             │
└──────────────────────┬───────────────────────────────────────┘
                       ▼
              Ranked Influencer Dossier
              (Dashboard renders results)
```

---

## 🚀 Getting Started

### Prerequisites

- **Node.js** (v18+)
- **Python** (3.11+)
- **YouTube Data API Key** from [Google Cloud Console](https://console.cloud.google.com/)
- **Tavily API Key** from [tavily.com](https://tavily.com)
- **Gemini API Key** from [Google AI Studio](https://aistudio.google.com/)

### 1. Backend Setup

```bash
cd backend

# Create and activate virtual environment
python -m venv env
env\Scripts\activate  # Windows
# source env/bin/activate  # macOS/Linux

# Install dependencies
pip install -r requirements.txt
```

Create a `.env` file in the `backend/` directory:

```env
YOUTUBE_API_KEY=your-youtube-api-key
TAVILY_API_KEY=your-tavily-api-key
GEMINI_API_KEY=your-gemini-api-key
```

Start the backend server:

```bash
uvicorn main:app --reload --port 8000
```
*API docs will be available at [http://localhost:8000/docs](http://localhost:8000/docs)*

### 2. Frontend Setup

In a new terminal, configure and start the React frontend.

```bash
cd frontend

# Install dependencies
npm install

# Start the Vite development server
npm run dev
```
*The web interface will be available at [http://localhost:5173](http://localhost:5173)*

---

## 📖 How to Use

1. Navigate to **[http://localhost:5173](http://localhost:5173)**.
2. Fill out the **Brand Brief** with your niche, target audience, budget, platforms, and keywords.
3. Submit the brief. The platform will search YouTube and the web to discover, audit, and price influencers.
4. Once completed (usually ~1-2 mins), view your **Influencer Dashboard**, complete with detailed dossiers, safety flags, and AI-generated summaries.

---

## 🔌 API Documentation

### `POST /api/run-campaign`
Submits a brand brief. Returns a `job_id` to poll for results. The pipeline runs asynchronously in the background.

**Request Body:**
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

**Response:**
```json
{
  "job_id": "b7f239c6-1234-...",
  "status": "pending",
  "results": null
}
```

### `GET /api/status/{job_id}`
Poll this endpoint to check job status (`pending` → `running` → `complete` | `failed`).

**Response (when complete):**
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
      "risk_sources": [],
      "price_low": 50000,
      "price_high": 150000,
      "composite_score": 87.4,
      "ai_summary": "Chris Bumstead is a dominant figure in the fitness..."
    }
  ]
}
```

### `GET /api/campaigns`
Fetches a list of the 20 most recent campaign jobs and their statuses.

### `POST /api/outreach/{job_id}/{handle}`
Generates a personalized outreach message for a specific influencer using their stats and the original brand brief.

### `POST /api/competitor-intel`
Searches for influencers with sponsored partnerships with a competitor brand.

**Request Body:**
```json
{
  "competitor_brand": "Gymshark"
}
```

### `POST /api/cancel-agents`
Emergency stop endpoint that clears all active runs.

---

## 🤖 Service Architecture

The backend is organized into focused, single-responsibility modules:

| Module | Responsibility | External APIs |
|---|---|---|
| **platforms/youtube.py** | YouTube channel search & statistics | YouTube Data API v3 |
| **platforms/tavily.py** | Web search, profile parsing, competitor intel | Tavily Search API |
| **discovery.py** | Multi-platform discovery aggregator | — |
| **auditor.py** | Qualification, brand safety audit, pricing | YouTube API, Tavily |
| **llm_client.py** | LLM API calls, retry logic, JSON parsing | Google Gemini API |
| **scoring.py** | Pre-filtering, estimates, LLM scoring, keywords | — |
| **outreach.py** | Personalized DM message drafting | Google Gemini API |
| **pipeline.py** | Full campaign pipeline orchestration | — |

---

## 🛡️ The Moat

Manually vetting 20 influencers across multiple platforms takes **3 days** of human labor. CreatorLens does it in **under 2 minutes** by running all tasks concurrently with `asyncio`. The audit step alone — cross-referencing each influencer against news, controversy reports, and social signals — is impossible at this speed without automated parallel processing.

CreatorLens doesn't just find influencers; it guarantees they align with your brand's reputation and budget instantly.