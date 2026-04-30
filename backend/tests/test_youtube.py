import asyncio
import json
from dotenv import load_dotenv

# Load environment variables (like YOUTUBE_API_KEY) from .env
load_dotenv()

from services.agents import _youtube_search, _youtube_channel_stats

async def run_tests():
    print("Testing YouTube API Queries...\n")
    
    # Here are a few example queries simulating what the app searches for
    queries = [
        "fitness influencer",
        "male fitness influencer"
    ]
    
    for q in queries:
        print(f"==================================================")
        print(f"QUERY: '{q}'")
        print(f"==================================================")
        
        # Calling the same helper function used in agents.py
        results = await _youtube_search(q, max_results=2)
        
        if not results:
            print("No results returned. (Check if your YOUTUBE_API_KEY is valid!)\n")
            continue
            
        print(f"Found {len(results)} channels.")
        
        for idx, r in enumerate(results, 1):
            print(f"\nResult #{idx}:")
            
            # Fetch channel statistics
            channel_id = r.get("id", {}).get("channelId")
            if channel_id:
                stats = await _youtube_channel_stats(channel_id)
                # Attach the statistics to the result dictionary so it gets printed
                r["statistics"] = stats.get("statistics", {})
            
            # Printing the full raw JSON response so you can see all fields
            print(json.dumps(r, indent=2))
        
        print("\n")
            
if __name__ == "__main__":
    asyncio.run(run_tests())
