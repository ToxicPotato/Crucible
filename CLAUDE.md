# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Decision Authority

**The developer decides all logic, architecture, and design.**
Agents only implement what is explicitly specified.

**When a design or logic decision is required:**

1. Spawn the `council-prompt-agent`
2. Return the generated prompt to the developer
3. Wait for the developer to return with the council's answer
4. Implement based on that answer — nothing else

Never make design decisions yourself. Never improvise. Never assume.

This applies at every level: top-level Claude, orchestrator, and all subagents.

## Project Overview

LLM Council is a deliberation system where multiple LLMs collaboratively answer user questions through a 5-stage pipeline: independent answers → anonymized peer ranking → spot-check fact verification → chairman synthesis. A Phase 0 scrubber neutralizes framing bias before any model sees the question.

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

> ⚠️ Current test files (`test_phase0.py`, `test_stage25.py`) are **temporary placeholders**.
> They will be deleted or replaced with proper test files later. Do not build on them.

```bash
pip install httpx
python test_phase0.py        # 10 adversarial scrubber cases — requires live API, human review
python test_stage25.py --direct   # Layer 1: 6 direct verifier tests, no server required
python test_stage25.py            # Layer 1 + Layer 3 adversarial pipeline tests (server required)
```

**Dependencies:** `OPENROUTER_API_KEY` must be set in `.env` at project root (read by docker-compose and python-dotenv). `TAVILY_API_KEY` is required for Stage 2.5 web search; if absent, all claims degrade to `UNVERIFIABLE`.

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
         (fields: confidence, confidence_source, factual_claims, key_assumptions, known_unknowns)
    ↓
Stage 2: Anonymize as "Response A/B/C..." → parallel peer rankings
         Rankers see confidence scores but only COUNTS of assumptions (not text)
    ↓
Aggregate Rankings: avg position per model across all rankings
    ↓
Stage 2.5: Extract factual_claims from top-2 models with confidence >75
           → LLM generates search queries → Tavily web search → LLM validates
           → VERIFIED / CONTRADICTED / UNVERIFIABLE per claim
    ↓
Stage 3: Chairman synthesizes final answer (sees full metadata + verification context)
    ↓
SSE stream: {stage1_complete, stage2_complete, stage25_start, stage25_complete,
             stage3_complete, title_complete, complete}
```

### Backend (`backend/`)

**`config.py`** — all model identifiers and feature flags.

- `COUNCIL_MODELS`: list of 4 OpenRouter model IDs
- `CHAIRMAN_MODEL`: synthesis model (currently Gemini 3 Pro)
- `SCRUBBER_MODEL`: Phase 0 model (Gemini 2.5 Flash — fast/cheap)
- `PHASE0_ENABLED`: currently `True`
- `VERIFIER_MODEL`: Stage 2.5 model (Gemini 2.5 Flash)
- `STAGE25_ENABLED`: currently `True` — set to `False` to disable verification without removing code
- `TAVILY_API_KEY`: read from `.env`; empty string disables search (graceful degradation)

**`council.py`** — core logic, most frequently modified file.

- `SCRUBBER_SYSTEM_PROMPT`: Phase 0 scrubber instructions (fully iterated)
- `STAGE1_SYSTEM_PROMPT`: instructions given to every council model before their answer. Defines confidence calibration rules (hard ceilings: recalled ≤90, reasoned ≤75, speculative ≤60), the "recalled a debate" rule, and anonymity notice.
- `phase0_scrub_prompt()`: calls scrubber, extracts JSON with `find/rfind`, graceful fallback
- `parse_stage1_metadata()`: splits model response into prose + metadata dict. Uses `rfind` (not `find`) because prose may contain JSON-like text — the metadata block is always last. Parses `factual_claims` (specific, falsifiable facts) and `key_assumptions` (framing premises) as distinct fields.
- `stage1_collect_responses()`: sends `[system: STAGE1_SYSTEM_PROMPT, user: query]` to all models in parallel; calls `parse_stage1_metadata()` on each
- `stage2_collect_rankings()`: anonymizes responses; shows confidence + source + **counts only** for assumptions/unknowns (not text, to prevent de-anonymization by phrasing style)
- `stage3_synthesize_final(query, stage1, stage2, verification_results)`: chairman sees full metadata + verification context from `format_verification_context()`
- `calculate_aggregate_rankings()`: avg rank position across all peer evaluations

**`verifier.py`** — Stage 2.5 spot-check verifier (isolated module).

- `VERIFIER_SYSTEM_PROMPT`: verdict logic — `UNVERIFIABLE` is the safe default; absence of evidence ≠ contradiction; opinion/predictive claims always `UNVERIFIABLE`
- `extract_verifiable_claims(top2_results)`: pulls `factual_claims` (primary) or `key_assumptions` (fallback for old conversations) from top-2 models with confidence >75; capped at 4 claims to bound API cost
- `stage25_verify_claims(top2_results, user_query)`: orchestrates 3 parallel pipelines: query generation → Tavily search → LLM validation
- `format_verification_context(verification_results)`: formats results for the Chairman prompt. `CONTRADICTED` → strong `[!]` flag with correction instruction. `VERIFIED` → `[✓]` settled fact. `UNVERIFIABLE` → excluded (noise reduction).

**`main.py`** — FastAPI app, port 8001.

- Primary path: `POST /api/conversations/{id}/message/stream` — SSE streaming, yields `stage1_complete`, `stage2_complete`, `stage25_start`, `stage25_complete`, `stage3_complete`, `title_complete`, `complete`
- Phase 0 path: `POST /api/conversations/{id}/phase0` — returns `{original, scrubbed, reasoning}`, does NOT write to storage
- `SendMessageRequest.scrubbed_content`: optional; if provided, council uses it instead of `content`; storage always saves original `content`
- Stage 2.5 is wrapped in an isolated `try/except` — a verifier failure never kills Stage 3

**`storage.py`** — JSON files in `data/conversations/`. Metadata (`label_to_model`, `aggregate_rankings`) is **not persisted** — only returned via API response and held in frontend state.

### Frontend (`frontend/src/`)

**`App.jsx`** — Phase 0 state machine: `PHASE0_IDLE → scrubbing → pending → idle`. `handleSendMessage` triggers scrub; `handlePhase0Accept(scrubbedContent)` calls `runCouncilStream`; `handlePhase0Decline` resets to idle.

**`components/Phase0Review.jsx`** — shows spinner during scrub, two-column original/scrubbed diff when pending, "no changes" tag when identical.

**`components/Stage1.jsx`** — tab view per model. Below each prose response shows: confidence badge (green ≥70 / amber 50–69 / red <50) + source pill (recalled/reasoned/speculative) + teal "Facts" chips (`factual_claims`) + blue assumption chips + purple unknown chips. All hidden if metadata is null (backward compatible with old conversations).

**`components/Stage2.jsx`** — tab view of raw ranking text per model; de-anonymization client-side; shows extracted ranking for user validation.

**`components/Stage25.jsx`** — verdict cards between Stage 2 and Stage 3. Sorted `CONTRADICTED → VERIFIED → UNVERIFIABLE`. `CONTRADICTED`: red background + badge. `VERIFIED`: green. `UNVERIFIABLE`: gray/muted (0.75 opacity). Summary pills with counts per verdict type.

## Agent Roles

This project uses specialized agents with strict scopes. Each agent must stay within its domain.
**No agent makes design or logic decisions. If something is unclear, ask the developer.**

### council-prompt-agent (`.claude/agents/council-prompt-agent.md`)

**Scope:** Read-only access to entire codebase
**Responsibility:** When a design decision is needed, read the relevant files, understand the context, and generate a neutral and precise prompt for the developer to send to the LLM Council. Returns the prompt to the developer — never answers the question itself.
**Spawned by:** Top-level Claude or any agent that encounters a design decision

### backend-agent

**Scope:** `backend/` only
**Responsibility:** Implement exactly what is specified. Do not make logic or design decisions — if something is unclear, ask before implementing.
**Entry:** `python -m backend.main`
**Key files:** `council.py`, `verifier.py`, `config.py`, `main.py`, `storage.py`
**Never touch:** `frontend/`, `docker-compose.yml`, `nginx/`, test files

### frontend-agent

**Scope:** `frontend/src/` only
**Responsibility:** Mirror what the backend exposes. If the backend adds a new stage, SSE event, or data field — the frontend must show it clearly to the user. UI should always reflect the current state of the backend pipeline. Prioritize clarity and usability.
**Rule:** Frontend makes no assumptions about backend logic. It only renders what the backend sends.
**Entry:** `cd frontend && npm run dev`
**Key files:** `App.jsx`, `components/Stage*.jsx`, `components/Phase0Review.jsx`
**Never touch:** `backend/`, `data/`, docker files

**Frontend update trigger:** Any time the backend changes its API response shape, adds SSE events, or exposes new data fields — the frontend-agent must be run to reflect those changes.

### infra-agent

**Scope:** `docker-compose.yml`, nginx config, `.env` structure only
**Responsibility:** Implement infrastructure changes as specified. Do not change networking logic without explicit instruction.
**Key invariant:** `VITE_API_BASE` must always be empty in `docker-compose.yml`
**Never touch:** `backend/`, `frontend/src/`

## How to Use Agents

Always start a Claude Code session by specifying the role and the no-decisions rule explicitly.

**Backend task:**

```
You are the backend-agent. Read CLAUDE.md.
Do not make any design or logic decisions — if anything is unclear, ask me first.
Task: [beskriv tasken]
```

**Frontend update after backend change:**

```
You are the frontend-agent. Read CLAUDE.md.
Do not make any design decisions — if anything is unclear, ask me first.
The backend just added [beskriv endring]. Update the frontend to display it clearly.
```

**Orchestrator (spawner subagents):**

```
You are the orchestrator. Read CLAUDE.md.
Do not make any design or logic decisions at any level — if anything is unclear, ask me first.
Spawn the appropriate agents to implement: [beskriv tasken]
```

> ⚠️ Without an explicit role in the prompt, Claude Code may take design decisions on its own.
> Always specify the role and the no-decisions rule at the start of every session.

## Key Invariants

- **Relative imports**: all `backend/` modules use `from .config import ...`. Run as `python -m backend.main` from project root.
- **All ReactMarkdown** must be wrapped in `<div className="markdown-content">` — class defined in `index.css`.
- **Confidence ceilings** are enforced by prompt instruction, not code. The system trusts model compliance; `parse_stage1_metadata()` normalises `confidence_source` to `{"recalled","reasoned","speculative"}` or `None`.
- **Anonymity rule**: Stage 2 must never show assumption/unknown text — only counts. Breaking this lets rankers identify models by phrasing style.
- **Phase 0 storage rule**: scrubbed prompt goes to council; original prompt goes to `storage`. Users always see what they typed.
- **Stage 2.5 isolation**: the verifier block in `main.py` is wrapped in `try/except`; `verification_results` defaults to `[]` on failure. Stage 3 always runs.
- **factual_claims vs key_assumptions**: `factual_claims` are specific falsifiable facts (primary verification target); `key_assumptions` are framing/interpretation premises. The verifier uses `factual_claims` first and falls back to `key_assumptions` only when the field is absent (backward compatibility with pre-Stage-2.5 conversations).

## System Prompt Locations

System prompts live as module-level constants and were designed iteratively by the council itself:

- `council.py` → `SCRUBBER_SYSTEM_PROMPT` (~60 lines): full ruleset including EXCEPTIONS & OVERRIDES section
- `council.py` → `STAGE1_SYSTEM_PROMPT` (~30 lines): transparency framing + calibration rules + JSON schema
- `verifier.py` → `VERIFIER_SYSTEM_PROMPT`: verdict rules with explicit bias guards (absence of evidence ≠ contradiction)
