import asyncio
import json
import sys
import os

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from db.database import get_conn, get_job
from services.scoring import draft_outreach

async def main():
    conn = get_conn()
    job_row = conn.execute("SELECT job_id FROM jobs LIMIT 1").fetchone()
    conn.close()
    
    output = []

    if job_row:
        job_id = job_row["job_id"]
        job, results = get_job(job_id)
        if results:
            influencer = results[0]
            brief = json.loads(job["brief_json"])
            output.append(f"Testing draft_outreach for influencer: {influencer['handle']}")
            output.append(f"Brand brief niche: {brief.get('niche')}")
            
            try:
                draft = await draft_outreach(influencer, brief)
                output.append("\n--- GENERATED DRAFT ---")
                output.append(draft)
                output.append("-----------------------\n")
            except Exception as e:
                output.append(f"Error generating draft: {e}")
        else:
            output.append("Job found, but no results/influencers available.")
    else:
        output.append("No jobs found in the database. Using mock data.")
        mock_influencer = {
            "handle": "tech_guru_99",
            "platform": "youtube",
            "followers": 150000,
            "engagement_rate": 5.2,
            "ai_summary": "tech_guru_99 is a prominent tech reviewer specializing in mechanical keyboards and productivity setups."
        }
        mock_brief = {
            "niche": "Technology and Peripherals",
            "target_audience": "Software engineers and desk setup enthusiasts"
        }
        
        output.append(f"Testing draft_outreach for mock influencer: {mock_influencer['handle']}")
        try:
            draft = await draft_outreach(mock_influencer, mock_brief)
            output.append("\n--- GENERATED DRAFT ---")
            output.append(draft)
            output.append("-----------------------\n")
        except Exception as e:
            output.append(f"Error generating draft: {e}")

    with open("draft_result.txt", "w", encoding="utf-8") as f:
        f.write("\n".join(output))

if __name__ == "__main__":
    asyncio.run(main())
