"""
outreach.py — Outreach message drafting
Generates personalized DM outreach messages for influencers using LLM.
"""

from services.llm_client import llm_chat


async def draft_outreach(influencer: dict, brief: dict) -> str:
    """Draft a personalized outreach DM for an influencer."""
    user_message = f"""
Write a short outreach DM to @{influencer.get('handle')} on behalf of a brand.

Brand details:
- Niche: {brief.get('niche')}
- Target audience: {brief.get('target_audience')}
- Budget: ${brief.get('budget_min')}–${brief.get('budget_max')}

Influencer details:
- Platform: {influencer.get('platform')}
- Followers: {influencer.get('followers')}
- Engagement rate: {influencer.get('engagement_rate')}%
- About them: {influencer.get('ai_summary')}

Rules:
- Max 80 words
- Mention something SPECIFIC about their content or audience
- Include the budget range naturally
- End with a clear question to start conversation
- Sound like a real human, not a template
- No emojis, no corporate speak
- Return ONLY the message, nothing else
"""
    return await llm_chat(
        "You are a brand partnerships manager who writes personalized, genuine outreach messages.",
        user_message
    )
