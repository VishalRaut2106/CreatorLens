import sqlite3
import os
import json

DB_PATH = "creatorlens.db"

def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_conn()
    cur = conn.cursor()

    cur.executescript("""
        CREATE TABLE IF NOT EXISTS jobs (
            job_id      TEXT PRIMARY KEY,
            status      TEXT NOT NULL DEFAULT 'pending',
            brief_json  TEXT,
            created_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
            completed_at DATETIME
        );

        CREATE TABLE IF NOT EXISTS influencer_results (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id           TEXT,
            handle           TEXT,
            platform         TEXT,
            followers        INTEGER,
            engagement_rate  REAL,
            risk_flag        TEXT,
            risk_evidence    TEXT,
            risk_sources     TEXT,
            price_low        INTEGER,
            price_high       INTEGER,
            composite_score  REAL,
            ai_summary       TEXT,
            fetched_at       DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (job_id) REFERENCES jobs(job_id)
        );
    """)

    conn.commit()
    conn.close()
    print("DB initialized.")

def create_job(job_id: str, brief_json: str):
    conn = get_conn()
    conn.execute(
        "INSERT INTO jobs (job_id, status, brief_json) VALUES (?, 'pending', ?)",
        (job_id, brief_json)
    )
    conn.commit()
    conn.close()

def update_job_status(job_id: str, status: str):
    conn = get_conn()
    if status == "complete":
        conn.execute(
            "UPDATE jobs SET status=?, completed_at=CURRENT_TIMESTAMP WHERE job_id=?",
            (status, job_id)
        )
    else:
        conn.execute("UPDATE jobs SET status=? WHERE job_id=?", (status, job_id))
    conn.commit()
    conn.close()

VALID_PLATFORMS = {"instagram", "tiktok", "youtube", "twitter"}

def save_results(job_id: str, results: list):
    conn = get_conn()
    for r in results:
        platform = r.get("platform") or "instagram"
        platform = platform.lower()
        if platform not in VALID_PLATFORMS:
            platform = "instagram"
            
        risk_flag = r.get("risk_flag") or "green"
        if risk_flag not in ("green", "amber", "red"):
            risk_flag = "green"

        conn.execute("""
            INSERT INTO influencer_results
            (job_id, handle, platform, followers, engagement_rate,
             risk_flag, risk_evidence, risk_sources, price_low, price_high, composite_score, ai_summary)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            job_id, r.get("handle"), platform, 
            r.get("followers", 0), r.get("engagement_rate", 0.0),
            risk_flag, r.get("risk_evidence"),
            json.dumps(r.get("risk_sources", [])),
            r.get("price_low", 0), r.get("price_high", 0),
            r.get("composite_score", 0), r.get("ai_summary", "")
        ))
    conn.commit()
    conn.close()

def get_job(job_id: str):
    conn = get_conn()
    job = conn.execute("SELECT * FROM jobs WHERE job_id=?", (job_id,)).fetchone()
    results = conn.execute(
        "SELECT * FROM influencer_results WHERE job_id=? ORDER BY composite_score DESC",
        (job_id,)
    ).fetchall()
    conn.close()
    # Parse risk_sources JSON string back to list
    parsed = []
    for r in results:
        d = dict(r)
        try:
            d["risk_sources"] = json.loads(d.get("risk_sources") or "[]")
        except Exception:
            d["risk_sources"] = []
        parsed.append(d)
    return job, parsed