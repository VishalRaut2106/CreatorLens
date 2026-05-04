import asyncio
import json
import sys
import os
from dotenv import load_dotenv

# Ensure backend is in the path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

# Load API keys from .env before importing modules that use them
load_dotenv()

from services.platforms.youtube import (
    youtube_search, 
    build_channel_profile,
    youtube_channel_stats,
    youtube_recent_videos,
    youtube_video_stats
)


async def main():
    query = "Ishan Sharma"
    print(f"Searching YouTube for: {query}")
    search_results = await youtube_search(query, max_results=3)
    
    if not search_results:
        print("No search results found.")
        return
        
    print(f"Search found {len(search_results)} results. Picking the first one...")
    
    # We want to make sure we find the right one, though search should give it as top result
    target_channel_id = None
    for item in search_results:
        cid = item.get("id", {}).get("channelId")
        title = item.get("snippet", {}).get("title", "")
        print(f"Found Channel ID: {cid} | Title: {title}")
        if target_channel_id is None:
            target_channel_id = cid # fallback to first
    
    if not target_channel_id:
        print("Could not extract a channel ID from search results.")
        return
        
    print(f"\nBuilding profile for Channel ID: {target_channel_id}")
    profile = await build_channel_profile(target_channel_id)
    
    print("\n--- FINAL PROFILE ---")
    print(json.dumps(profile, indent=2))

    print("\n--- RECENT 10 VIDEOS ENGAGEMENT ---")
    channel_data = await youtube_channel_stats(target_channel_id)
    uploads_playlist_id = channel_data.get("contentDetails", {}).get("relatedPlaylists", {}).get("uploads", "")
    
    if uploads_playlist_id:
        video_ids = await youtube_recent_videos(uploads_playlist_id, max_videos=10)
        if video_ids:
            video_items = await youtube_video_stats(video_ids)
            for v in video_items:
                vid = v.get("id")
                stats = v.get("statistics", {})
                views = stats.get("viewCount", 0)
                likes = stats.get("likeCount", 0)
                comments = stats.get("commentCount", 0)
                print(f"Video ID: {vid} | Views: {views} | Likes: {likes} | Comments: {comments}")
        else:
            print("No recent videos found.")
    else:
        print("Could not find uploads playlist ID.")

if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv() # Load API keys from .env
    
    asyncio.run(main())
