# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

LLM Council is a deliberation system where multiple LLMs collaboratively answer user questions through a 3-stage pipeline: independent answers → anonymized peer ranking → chairman synthesis. A Phase 0 scrubber neutralizes framing bias before any model sees the question.

## Running the Project

**Primary (Docker):**
```bash
docker compose up --build   # rebuild and start everything
docker compose up           # start without rebuilding
```
Frontend → `http://localhost:3000`, Backend → `http://localhost:8001`

**Local development:**
```bash
# Backend (from project root)
python -m backend.main

# Frontend
cd frontend && npm run dev   # http://localhost:5173
cd frontend && npm run lint
```

**Tests:**
```bash
pip install httpx
python test_phase0.py        # runs 10 adversarial scrubber test cases against live API
```

**Dependencies:** `OPENROUTER_API_KEY` must be set in `.env` at project root (read by docker-compose and python-dotenv).

## Architecture

### Docker networking
`VITE_API_BASE` is intentionally empty in `docker-compose.yml`. The Nginx container proxies `/api/` to the backend container — never set it to `http://localhost:8001` inside Docker.

### Data flow
```
User Query
    ↓
Phase 0 (optional): Scrubber → user reviews diff → accept/decline
    ↓
Stage 1: Parallel queries to all council models
         Each response parsed into prose + JSON metadata block
    ↓
Stage 2: Anonymize as "Response A/B/C..." → parallel peer rankings
         Rankers see confidence scores but only COUNTS of assumptions (not text)
    ↓
Aggregate Rankings: avg position per model across all rankings
    ↓
Stage 3: Chairman synthesizes final answer (sees full metadata)
    ↓
SSE stream: {stage1_complete, stage2_complete, stage3_complete, title_complete}
```

### Backend (`backend/`)

**`config.py`** — all model identifiers and feature flags.
- `COUNCIL_MODELS`: list of 4 OpenRouter model IDs
- `CHAIRMAN_MODEL`: synthesis model (currently Gemini 3 Pro)
- `SCRUBBER_MODEL`: Phase 0 model (Gemini 2.5 Flash — fast/cheap)
- `PHASE0_ENABLED`: **currently `False`** — set to `True` once scrubber prompt is tuned

**`council.py`** — core logic, most frequently modified file.
- `SCRUBBER_SYSTEM_PROMPT`: Phase 0 scrubber instructions (fully iterated)
- `STAGE1_SYSTEM_PROMPT`: instructions given to every council model before their answer. Defines confidence calibration rules (hard ceilings: recalled ≤90, reasoned ≤75, speculative ≤60), the "recalled a debate" rule, and anonymity notice.
- `phase0_scrub_prompt()`: calls scrubber, extracts JSON with `find/rfind`, graceful fallback
- `parse_stage1_metadata()`: splits model response into prose + metadata dict. Uses `rfind` (not `find`) because prose may contain JSON-like text — the metadata block is always last.
- `stage1_collect_responses()`: sends `[system: STAGE1_SYSTEM_PROMPT, user: query]` to all models in parallel; calls `parse_stage1_metadata()` on each
- `stage2_collect_rankings()`: anonymizes responses; shows confidence + source + **counts only** for assumptions/unknowns (not text, to prevent de-anonymization by phrasing style)
- `stage3_synthesize_final()`: chairman sees full metadata including assumption text
- `calculate_aggregate_rankings()`: avg rank position across all peer evaluations

**`main.py`** — FastAPI app, ports 8001.
- Primary path: `POST /api/conversations/{id}/message/stream` — SSE streaming, yields `stage1_complete`, `stage2_complete`, `stage3_complete`, `title_complete`, `complete`
- Phase 0 path: `POST /api/conversations/{id}/phase0` — returns `{original, scrubbed, reasoning}`, does NOT write to storage
- `SendMessageRequest.scrubbed_content`: optional; if provided, council uses it instead of `content`; storage always saves original `content`

**`storage.py`** — JSON files in `data/conversations/`. Metadata (`label_to_model`, `aggregate_rankings`) is **not persisted** — only returned via API response and held in frontend state.

### Frontend (`frontend/src/`)

**`App.jsx`** — Phase 0 state machine: `PHASE0_IDLE → scrubbing → pending → idle`. `handleSendMessage` triggers scrub; `handlePhase0Accept(scrubbedContent)` calls `runCouncilStream`; `handlePhase0Decline` resets to idle.

**`components/Phase0Review.jsx`** — shows spinner during scrub, two-column original/scrubbed diff when pending, "no changes" tag when identical.

**`components/Stage1.jsx`** — tab view per model. Below each prose response shows: confidence badge (green ≥70 / amber 50–69 / red <50) + source pill (recalled/reasoned/speculative) + assumption chips (blue) + unknown chips (purple). All hidden if metadata is null (backward compatible with old conversations).

**`components/Stage2.jsx`** — tab view of raw ranking text per model; de-anonymization client-side; shows extracted ranking for user validation.

## Key Invariants

- **Relative imports**: all `backend/` modules use `from .config import ...`. Run as `python -m backend.main` from project root.
- **All ReactMarkdown** must be wrapped in `<div className="markdown-content">` — class defined in `index.css`.
- **Confidence ceilings** are enforced by prompt instruction, not code. The system trusts model compliance; `parse_stage1_metadata()` normalises `confidence_source` to `{"recalled","reasoned","speculative"}` or `None`.
- **Anonymity rule**: Stage 2 must never show assumption/unknown text — only counts. Breaking this lets rankers identify models by phrasing style.
- **Phase 0 storage rule**: scrubbed prompt goes to council; original prompt goes to `storage`. Users always see what they typed.

## System Prompt Locations

Both system prompts live as module-level constants in `council.py` and were designed iteratively by the council itself:
- `SCRUBBER_SYSTEM_PROMPT` (~60 lines): full ruleset including EXCEPTIONS & OVERRIDES section
- `STAGE1_SYSTEM_PROMPT` (~30 lines): transparency framing + calibration rules + JSON schema

## Testing Scrubber Behavior

`test_phase0.py` at project root runs 10 adversarial cases (lived experience, code refactoring, crisis language, contradictions, etc.) against the live API. Requires the server running and `OPENROUTER_API_KEY` set. Output is raw — no pass/fail verdicts, intended for human review and council discussion.
