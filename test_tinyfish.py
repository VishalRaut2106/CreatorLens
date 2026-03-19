import asyncio
import httpx
import json
import os
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "backend", ".env"))

TINYFISH_API_KEY = os.getenv("TINYFISH_API_KEY")
TINYFISH_URL = "https://agent.tinyfish.ai/v1/automation/run-sse"

HEADERS = {
    "X-API-Key": TINYFISH_API_KEY,
    "Content-Type": "application/json"
}

async def test_agent():
    payload = {
        "url": "https://www.instagram.com/explore/tags/fitness/",
        "goal": (
            'Find 3 fitness influencer accounts. '
            'Return a JSON array: [{"handle": str, "platform": "instagram", "followers": int}]'
        ),
        "browser_profile": "stealth"
    }

    print("Firing TinyFish agent...")
    print(f"URL: {TINYFISH_URL}")
    print(f"API Key: sk-tinyfish-{'*' * 20}")
    print("-" * 40)

    async with httpx.AsyncClient(timeout=120) as client:
        async with client.stream("POST", TINYFISH_URL, headers=HEADERS, json=payload) as resp:
            print(f"Status code: {resp.status_code}")
            if resp.status_code != 200:
                body = await resp.aread()
                print(f"Error: {body.decode()}")
                return

            async for line in resp.aiter_lines():
                if not line:
                    continue
                print(f"Event: {line}")
                if line.startswith("data: "):
                    event = json.loads(line[6:])
                    if event.get("type") == "COMPLETE":
                        print("\n✓ COMPLETE")
                        print("Result:", json.dumps(event.get("resultJson", {}), indent=2))
                        return

asyncio.run(test_agent())