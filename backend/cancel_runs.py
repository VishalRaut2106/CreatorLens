"""
Cancel all running TinyFish agents.
Run from backend/ with: python cancel_runs.py

Usage:
  Cancel all running:   python cancel_runs.py
  Cancel specific IDs:  python cancel_runs.py run_id1 run_id2
"""
import httpx
import os
import sys
import asyncio
from dotenv import load_dotenv
load_dotenv()

BASE_URL = "https://agent.tinyfish.ai/v1"

def _get_headers():
    return {
        "X-API-Key": os.getenv("TINYFISH_API_KEY"),
        "Content-Type": "application/json"
    }


async def get_running_runs() -> list:
    """Fetch all currently running AND pending runs — tries multiple API patterns."""
    all_runs = []
    endpoints = [
        f"{BASE_URL}/runs",
        f"{BASE_URL}/automation/runs",
        "https://agent.tinyfish.ai/v1/runs",
        "https://agent.tinyfish.ai/v1/automation/runs",
    ]
    async with httpx.AsyncClient(timeout=30) as client:
        for endpoint in endpoints:
            # Try without status filter first (get all)
            for params in [None, {"status": "RUNNING"}, {"status": "PENDING"},
                           {"status": "running"}, {"status": "pending"}]:
                try:
                    resp = await client.get(endpoint, headers=_get_headers(), params=params)
                    print(f"  [{resp.status_code}] GET {endpoint} params={params}")
                    if resp.status_code == 200:
                        data = resp.json()
                        print(f"       Response keys: {list(data.keys()) if isinstance(data, dict) else 'array'}")
                        if isinstance(data, list):
                            runs = data
                        elif isinstance(data, dict):
                            # Try common response shapes
                            runs = data.get("runs", data.get("data", data.get("results", [])))
                        else:
                            runs = []
                        if runs:
                            print(f"       Found {len(runs)} runs!")
                            all_runs.extend(runs)
                            return all_runs  # Found working endpoint, return
                except Exception as e:
                    print(f"  [ERR] {endpoint} params={params}: {e}")
    return all_runs


async def cancel_run(run_id: str) -> bool:
    """Cancel a single run by ID — tries multiple cancel endpoints."""
    cancel_endpoints = [
        f"{BASE_URL}/runs/{run_id}/cancel",
        f"{BASE_URL}/automation/runs/{run_id}/cancel",
        f"https://agent.tinyfish.ai/v1/runs/{run_id}/cancel",
        f"https://agent.tinyfish.ai/v1/automation/runs/{run_id}/cancel",
    ]
    async with httpx.AsyncClient(timeout=30) as client:
        for endpoint in cancel_endpoints:
            for method in [client.post, client.delete]:
                try:
                    resp = await method(endpoint, headers=_get_headers())
                    print(f"  [{resp.status_code}] {method.__name__.upper()} {endpoint}")
                    if resp.status_code in (200, 204):
                        return True
                except Exception:
                    pass
    return False


async def main():
    api_key = os.getenv("TINYFISH_API_KEY")
    if not api_key:
        print("✗ TINYFISH_API_KEY not set in .env")
        return

    print(f"API Key: {api_key[:20]}...")

    # If run IDs passed as args, cancel those directly
    if len(sys.argv) > 1:
        run_ids = sys.argv[1:]
        print(f"Cancelling {len(run_ids)} specified runs...")
        for run_id in run_ids:
            ok = await cancel_run(run_id)
            print(f"  {'✓' if ok else '✗'} {run_id}")
        return

    # Otherwise fetch and cancel all running
    print("Probing TinyFish API endpoints...\n")
    runs = await get_running_runs()

    if not runs:
        print("\nNo runs found via API. You can still cancel by ID:")
        print("  python cancel_runs.py <run_id1> <run_id2> ...")
        return

    print(f"\nFound {len(runs)} runs. Cancelling...")
    for run in runs:
        run_id = run.get("run_id", run.get("id", "")) if isinstance(run, dict) else run
        if run_id:
            ok = await cancel_run(run_id)
            print(f"  {'✓' if ok else '✗'} {run_id}")

    print("Done.")


if __name__ == "__main__":
    asyncio.run(main())
