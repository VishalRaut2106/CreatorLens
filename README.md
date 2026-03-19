# InfluenceFit — System Architecture

> Find the Right Influencer. Verify Them. Know What to Pay.

---

## Overview

InfluenceFit runs one core loop:

**Brand brief → Parallel TinyFish agents → LLM scoring → Ranked dossier**

Everything else is UI and storage around that loop.

---

## Stack at a Glance

| Layer | Technology | Role |
|---|---|---|
| Frontend | React (Vite) | Brand brief input + results dashboard |
| Backend | FastAPI (Python) | Orchestration, job management |
| Agent infra | TinyFish API | 100+ parallel browser agents |
| LLM scoring | Anthropic Claude API | Ranking, summarization |
| Storage | SQLite → Postgres | Results cache, job state |

---

## Layer 1 — Frontend (React)

Two screens only.

**Screen 1 — Brief form**
- Niche / keywords
- Target audience description
- Budget range
- Platform preference (Instagram / TikTok / YouTube)

**Screen 2 — Results dashboard**
- Ranked shortlist of 10 influencers
- Per-influencer dossier card: score, engagement stats, risk flag, pricing estimate, AI summary
- Loading/polling state while agents run (expect 1–2 min)

---

## Layer 2 — FastAPI Backend

The orchestration brain. Two core endpoints:

```
POST /run-campaign       → accepts brand brief, spawns TinyFish jobs, returns job_id
GET  /status/{job_id}    → frontend polls this until status = "complete"
```

**Job lifecycle:**
1. Receive brand brief
2. Generate search keywords from brief (Claude call #1)
3. Dispatch all TinyFish agent batches in parallel
4. Poll / receive webhooks until all agents complete
5. Pass raw data to Claude scoring layer (Claude call #2)
6. Store results, return final dossier

---

## Layer 3 — TinyFish Agents (Core Value)

Four agent types run **simultaneously** — not sequentially.

### Discovery agents
- Platforms: Instagram, TikTok, YouTube
- Task: search niche keywords, return matching profile URLs
- Scale: 3–4 agents per platform in parallel

### Qualification agents
- For each discovered profile
- Pull: follower count, post frequency, avg likes, avg comments, engagement rate
- Filters out vanity accounts before audit stage

### Audit agents
- Search each influencer's name + "controversy / scandal / drama"
- Sources: news sites, Twitter/X, Reddit
- Returns: risk flag (green / amber / red) + evidence links

### Pricing agents
- Sources: Collabstr + niche-specific rate sites
- Match by: follower tier + niche category
- Output: estimated rate range (post, story, reel)

> **Key architecture decision:** all four agent types launch in a single TinyFish batch call. Discovery finishes first, qualification + audit + pricing fire immediately after using discovery output as input.

---

## Layer 4 — LLM Scoring (Claude)

Two Claude calls in the pipeline:

**Call #1 — Keyword expansion** (before agents run)
- Input: raw brand brief
- Output: structured search terms per platform

**Call #2 — Scoring + summarization** (after agents return)
- Input: raw agent data for all candidates
- Output per influencer:
  - Composite score (0–100)
  - Breakdown: engagement quality, brand fit, risk, price fairness
  - 2–3 sentence natural-language summary for the dossier card

**Scoring weights (recommended starting point):**

| Signal | Weight |
|---|---|
| Engagement rate (real vs inflated) | 35% |
| Brand fit (content alignment) | 30% |
| Risk score (audit result) | 25% |
| Price fairness | 10% |

---

## Layer 5 — Storage

**SQLite** for hackathon speed. **Postgres** for production.

Two tables:

```sql
jobs (job_id, status, brand_brief, created_at, completed_at)

influencer_results (
  id, job_id, handle, platform,
  followers, engagement_rate,
  risk_flag, risk_evidence,
  price_low, price_high,
  composite_score, ai_summary,
  fetched_at
)
```

Cache by `(handle, platform)` — repeated audits of the same influencer skip the agent re-run if data is less than 24 hours old.

---

## Data Flow

```
User submits brief
        │
        ▼
FastAPI → Claude (keyword expansion)
        │
        ▼
TinyFish batch dispatch ──────────────────────────┐
        │                                          │
   Discovery agents                                │
   (Instagram / TikTok / YouTube)                  │
        │                                          │
        ▼                                          │
   Profile URLs (20–50 candidates)                 │
        │                                          │
   ┌────┴──────────────────────────┐               │
   │                               │               │
Qualification agents          Audit agents         │
(engagement stats)         (brand safety check)    │
   │                               │               │
   └────────────┬──────────────────┘               │
                │                                  │
           Pricing agents                          │
           (rate benchmarks)                       │
                │                                  │
                ▼                                  │
        Raw data bundle ◄─────────────────────────┘
                │
                ▼
        Claude scoring layer
        (rank + summarize)
                │
                ▼
        Store in DB
                │
                ▼
        Dashboard output
        (top 10 ranked dossiers)
```

---

## Key Architecture Decisions

**Polling vs webhooks**
- Hackathon: poll `GET /status/{job_id}` every 3s from frontend — simple, no infra needed
- Production: TinyFish webhook → FastAPI callback → push update via WebSocket

**Parallelism strategy**
- Don't run discovery → qualification → audit → pricing in sequence
- Discovery feeds directly into a second parallel batch (qual + audit + pricing fire together)
- Total expected runtime: under 2 minutes for 20 candidates

**Caching**
- Cache influencer results by handle + platform with 24h TTL
- Saves agent cost on repeat queries for the same influencer

---

## What Makes This Defensible

This product is not technically possible without parallel browser agents. The audit step alone — cross-referencing an influencer's name across news, Reddit, and Twitter simultaneously — would take 45+ minutes sequentially. TinyFish runs it across all 20 candidates in under 2 minutes.

That's the demo moment. That's the moat.