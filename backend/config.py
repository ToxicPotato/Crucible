"""Configuration for the LLM Council."""

import os
from dotenv import load_dotenv

load_dotenv()

# OpenRouter API key
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")

# Council members - list of OpenRouter model identifiers
COUNCIL_MODELS = [
    "openai/gpt-5.1",
    "google/gemini-3-pro-preview",
    "anthropic/claude-sonnet-4.5",
    "x-ai/grok-4",
]

# Chairman model - synthesizes final response
CHAIRMAN_MODEL = "google/gemini-3-pro-preview"

# Scrubber model - Phase 0 prompt neutralization (fast + cheap)
SCRUBBER_MODEL = "google/gemini-2.5-flash"

# Set to False to disable Phase 0 scrubbing without removing code
PHASE0_ENABLED = True

# OpenRouter API endpoint
OPENROUTER_API_URL = "https://openrouter.ai/api/v1/chat/completions"

# Data directory for conversation storage
DATA_DIR = "data/conversations"

# ---------------------------------------------------------------------------
# Stage 2.5 — Spot-check Verifier
# ---------------------------------------------------------------------------
# Model used for claim extraction, search query generation, and validation.
# Gemini Flash is fast/cheap — validation tasks are simple, latency matters.
VERIFIER_MODEL = "google/gemini-2.5-flash"

# Set to False to disable Stage 2.5 verification without removing code
STAGE25_ENABLED = True

# Tavily Search API key — get a free key at https://tavily.com
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY", "")
