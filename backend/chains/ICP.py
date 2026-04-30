from langchain.prompts import PromptTemplate

ICP_PROMPT_TEMPLATE = """
You are an expert in creator discovery.

Your task is to generate high-quality search queries and filtering logic
to find relevant creators based on ICP.

# Campaign Context
Product: {product_focus}
Target Audience: {target_user_profile}

# Creator Definition
Primary Niches: {primary_niches}
Secondary Niches: {secondary_niches}
Excluded Niches: {excluded_niches}
Creator Roles: {creator_roles}

# Platform & Content
Platforms: {platforms}
Formats: {content_formats}
Content Types: {content_types}

# Audience Constraints
Location: {audience_location}
Age: {audience_age}
Interests: {audience_interests}
Language: {language}

# Metrics
Follower Range: {follower_range}
Min Engagement Rate: {engagement_rate}

# Task
1. Generate 10 high-quality search queries
2. Suggest relevant hashtags
3. Define quick filtering rules
4. Suggest signals to detect good creators

Output in structured format.
"""

prompt = PromptTemplate(
    input_variables=[
        "product_focus",
        "target_user_profile",
        "primary_niches",
        "secondary_niches",
        "excluded_niches",
        "creator_roles",
        "platforms",
        "content_formats",
        "content_types",
        "audience_location",
        "audience_age",
        "audience_interests",
        "language",
        "follower_range",
        "engagement_rate"
    ],
    template=template
)