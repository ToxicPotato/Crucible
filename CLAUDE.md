# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Decision Authority

> ⚠️ Instructions in this file override MEMORY.md in all cases. If MEMORY.md and CLAUDE.md conflict, follow CLAUDE.md.

**The developer decides all logic, architecture, and design.**
Agents only implement what is explicitly specified.

**When a design or logic decision is required:**

1. Spawn the `council-prompt-agent`
2. Return the generated prompt to the developer
3. Wait for the developer to return with the council's answer
4. Implement based on that answer — nothing else

Never make design decisions yourself. Never improvise. Never assume.

This applies at every level: top-level Claude, orchestrator, and all subagents.

**Decision classification:**

- **Implementation details** — thresholds, word counts, sizing, labels, colors within a given direction: the assigned agent may decide. State the choice and reasoning clearly in the response.
- **Design decisions** — storage schema, API contracts, data flow, what information is shown to the user, logic that affects pipeline output: spawn `council-prompt-agent`. Never decide yourself.

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
Phase 0 (optional): Scrubber → user reviews diff → accept original / use scrubbed / cancel
    ↓
Stage 1: Parallel queries to all council models
         Each response parsed into prose + JSON metadata block
         (fields: confidence, confidence_source, factual_claims, key_assumptions, known_unknowns)
    ↓
Stage 2: Anonymize as "Response A/B/C..." → batch-scrub all metadata text (neutral prose rewrite)
         Each model ranks only its 3 PEERS (self-exclusion) — sees full scrubbed metadata text
    ↓
Aggregate Rankings: avg position per model across all rankings received
    ↓
Stage 2.5: Extract factual_claims from top-2 models with confidence >75
           → LLM generates (corroboration, refutation) query pair per claim
           → Both Tavily searches run in parallel
           → LLM validates against both result sets
           → VERIFIED / CONTRADICTED / CONTESTED / UNVERIFIABLE per claim
    ↓
Stage 3: Chairman synthesizes final answer
         Sees full metadata + verification context + settled_facts + prior_synthesis (if any)
    ↓
SSE stream: {stage1_start, stage1_complete, stage2_start, stage2_complete,
             stage25_start, stage25_complete, stage3_start, stage3_complete,
             title_complete, complete}
```

### Backend (`backend/`)

**`config.py`** — all model identifiers and feature flags.

- `COUNCIL_MODELS`: 4 OpenRouter model IDs — `openai/gpt-5.1`, `google/gemini-3-pro-preview`, `anthropic/claude-sonnet-4.5`, `x-ai/grok-4`
- `CHAIRMAN_MODEL`: `google/gemini-3-pro-preview`
- `SCRUBBER_MODEL`: `google/gemini-2.5-flash` (Phase 0 scrubbing and metadata style-scrubbing)
- `PHASE0_ENABLED`: currently `True`
- `VERIFIER_MODEL`: `google/gemini-2.5-flash` (Stage 2.5)
- `STAGE25_ENABLED`: currently `True` — set to `False` to disable verification without removing code
- `TAVILY_API_KEY`: read from `.env`; empty string disables search (graceful degradation)

**`council.py`** — core logic, most frequently modified file.

- `SCRUBBER_SYSTEM_PROMPT`: Phase 0 scrubber instructions — ANALYZE → REWRITE → OUTPUT sections plus EXCEPTIONS & OVERRIDES (crisis override, preserve simulation parameters, do not resolve contradictions)
- `STAGE1_SYSTEM_PROMPT`: instructions given to every council model. Defines confidence calibration rules (hard ceilings: recalled ≤90, reasoned ≤75, speculative ≤60), the "recalled a debate" rule, anonymity notice, and JSON schema.
- `phase0_scrub_prompt()`: calls scrubber, extracts JSON with `find/rfind`, graceful fallback
- `parse_stage1_metadata()`: splits model response into prose + metadata dict. Uses `rfind` (not `find`) because prose may contain JSON-like text — the metadata block is always last. Parses `factual_claims` (specific, falsifiable facts) and `key_assumptions` (framing premises) as distinct fields.
- `_scrub_metadata_texts(texts)`: batch-rewrites all claim/assumption text in neutral third-person prose. Single LLM call, positional reconstruction. Removes stylistic fingerprints to prevent de-anonymization by phrasing style.
- `_build_scrubbed_metadata(stage1_results)`: collects all factual claims and key assumptions across all models into a flat list, calls `_scrub_metadata_texts()` once, reconstructs per-model data using positional indices.
- `stage1_collect_responses()`: sends `[system: STAGE1_SYSTEM_PROMPT, user: query]` to all models in parallel; calls `parse_stage1_metadata()` on each
- `stage2_collect_rankings()`: calls `_build_scrubbed_metadata()` first; each model ranks only its 3 peers (self-exclusion); rankers see full **scrubbed** factual claims and key assumptions text (not counts), plus confidence score + unknowns count. All rankers queried in parallel via `asyncio.gather()`.
- `stage3_synthesize_final(query, stage1, stage2, verification_results=None, settled_facts=None, prior_synthesis=None)`: chairman sees full metadata + verification context from `format_verification_context()`. Injects `[PRIOR COUNCIL CONTEXT]` block when `settled_facts` or `prior_synthesis` present. Synthesis protocol: External Primacy → Diagnostic Mode → Settled Facts → Consensus.
- `calculate_aggregate_rankings()`: avg rank position per model across only the rankings it received (self-exclusion safe)

**`verifier.py`** — Stage 2.5 spot-check verifier (isolated module).

- `VERIFIER_SYSTEM_PROMPT`: verdict logic — `UNVERIFIABLE` is the safe default; absence of evidence ≠ contradiction; opinion/predictive claims always `UNVERIFIABLE`. Four verdicts: VERIFIED, CONTRADICTED, CONTESTED, UNVERIFIABLE.
- `extract_verifiable_claims(top2_results)`: pulls `factual_claims` (primary) or `key_assumptions` (fallback for old conversations) from top-2 models with confidence >75; capped at 4 claims to bound API cost
- `_generate_search_queries(claim, user_query)`: single LLM call generates a (corroboration, refutation) query pair (max 10 words each)
- `stage25_verify_claims(top2_results, user_query)`: orchestrates pipeline — extract → generate query pairs → all searches interleaved `[corr0, refu0, corr1, refu1, ...]` in single `asyncio.gather()` (zero extra latency) → validate each claim against both result sets
- `format_verification_context(verification_results)`: groups results **by source model** for chairman accountability. `CONTRADICTED` → `[!]` strong correction instruction. `CONTESTED` → `[~]` unresolved debate, report controversy. `VERIFIED` → `[✓]` settled fact. `UNVERIFIABLE` → excluded (noise reduction). Header: "### EXTERNAL FACT-CHECK RESULTS (grouped by source model)"

**`main.py`** — FastAPI app, port 8001.

- Primary path: `POST /api/conversations/{id}/message/stream` — SSE streaming, yields `stage1_start`, `stage1_complete`, `stage2_start`, `stage2_complete`, `stage25_start`, `stage25_complete`, `stage3_start`, `stage3_complete`, `title_complete`, `complete`
- Phase 0 path: `POST /api/conversations/{id}/phase0` — returns `{original, scrubbed, reasoning}`, does NOT write to storage
- `SendMessageRequest.scrubbed_content`: optional; if provided, council uses it instead of `content`; storage always saves original `content`
- Stage 2.5 is wrapped in an isolated `try/except` — a verifier failure never kills Stage 3
- After Stage 3: calls `build_new_settled_facts()` and `add_settled_facts()` to persist VERIFIED claims; loads `settled_facts` and `prior_synthesis` from conversation before calling `stage3_synthesize_final()`

**`storage.py`** — JSON files in `data/conversations/`. Metadata (`label_to_model`, `aggregate_rankings`) is **not persisted** — only returned via API response and held in frontend state.

- `create_conversation()`: initializes with `settled_facts: []` top-level field (backward-compatible; old conversations default to `[]`)
- `add_assistant_message(conversation_id, stage1, stage2, stage3, stage25=None)`: saves all stage data per message
- `add_settled_facts(conversation_id, new_facts)`: appends newly VERIFIED facts; deduplicates by claim text. Each fact: `{text, source, source_turn}`.
- `get_prior_synthesis(conversation)`: returns Stage 3 response text from most recent assistant message; `None` if none exists
- `build_new_settled_facts(verification_results, conversation)`: builds list of VERIFIED facts from current turn's results, assigning `source_turn` number

### Frontend (`frontend/src/`)

**`App.jsx`** — Phase 0 state machine: `idle → scrubbing → pending → idle`. `handleSendMessage` triggers scrub; user can then: `handlePhase0UseScrubbed(scrubbedContent)` (council uses scrubbed, shows badge on message), `handlePhase0UseOriginal()` (council uses original text unchanged), or `handlePhase0Decline()` (removes optimistic message, resets to idle). `runCouncilStream(originalContent, scrubbedContent)` handles SSE event processing.

**`components/ChatInterface.jsx`** — renders the full message thread. Shows `ScrubIndicator` badge on user messages where `usedScrubbed === true` (expandable, shows original + reasoning). Input form only shown when `conversation.messages.length === 0` (no persistent input bar). Error banner at bottom, dismissible. Shows stage-specific loading spinners between SSE events. Manages `drawerOpenForIndex` state for `EpistemicDrawer`. When `msg.stage3` is present, renders the composite `EpistemicSummary + Stage3Annotated + MinorityReport` in place of the old `Stage3`. `EpistemicDrawer` is rendered outside `messages-container` (always mounted, visibility controlled by `open` prop) so it overlays the full viewport.

**`components/Phase0Review.jsx`** — SCRUBBING state: spinner. PENDING state: two-column original/scrubbed diff, "no changes" tag when identical; three action buttons if changed: "Cancel" / "Use Original" / "Use Scrubbed →"; single "Continue →" if unchanged.

**`components/Stage1.jsx`** — tab view per model. Below each prose response shows: confidence badge (green ≥70 / amber 50–69 / red <50) + source pill (recalled/reasoned/speculative) + teal "Facts" chips (`factual_claims`) + blue assumption chips + purple unknown chips. All hidden if metadata is null (backward compatible with old conversations).

**`components/Stage2.jsx`** — tab view of raw ranking text per model (de-anonymized client-side). Shows "Metadata texts were style-scrubbed before evaluation to prevent de-anonymization." notice. Shows aggregate rankings section (average rank per model across all peer votes received).

**`components/Stage25.jsx`** — verdict cards between Stage 2 and Stage 3. Sorted `CONTRADICTED → CONTESTED → VERIFIED → UNVERIFIABLE`. `CONTRADICTED`: red. `CONTESTED`: amber (new verdict — credible evidence on both sides). `VERIFIED`: green. `UNVERIFIABLE`: gray/muted (0.75 opacity). Summary pills show counts for CONTRADICTED, CONTESTED, VERIFIED only (UNVERIFIABLE excluded from pills). Each card shows: model short name, original confidence, claim text, delta explanation, source URL.

**`components/Stage3.jsx`** — minimal wrapper around chairman synthesis. Not used directly in `ChatInterface` — wrapped by `Stage3Annotated` (which imports `Stage3.css`). Session memory (`settled_facts`, `prior_synthesis`) is backend-only — not rendered in UI.

**`utils/epistemic.js`** — pure utility functions (no React deps):
- `calcVerdict(stage25, stage1)`: Epistemic Waterfall — Step 1: any CONTRADICTED→`Disputed`, all actionable VERIFIED→`Verified`; Step 2: avgConf<50→`Uncertain`, claim overlap≥0.5 + avgConf≥65→`Consensus`, else `Split`/`Uncertain`. Returns `Verified|Disputed|Consensus|Split|Uncertain|Unknown`.
- `calcPeerAlignment(aggregateRankings)`: spread-based — spread≤0.3→`Unanimous`, ≤0.8→`Majority`, else `Split`.
- `calcConfidenceRange(stage1)`: `{min, max}` or null.
- `getMinorityModel(aggregateRankings)`: worst-ranked model if gap vs second-worst > 0.5, else null.
- `groupClaimsByVerdict(stage25)`: `{CONTRADICTED, CONTESTED, VERIFIED, UNVERIFIABLE}`.
- `annotateChairmanText(responseText, stage25)`: injects HTML `<span>` markers into markdown for VERIFIED/CONTRADICTED/CONTESTED claims (end-to-start string surgery).

**`components/EpistemicSummary.jsx`** — collapsible bar between Stage 2.5 and Stage 3. Collapsed header: mode badge + Reliability badge (`calcVerdict`) + verdict pills (CONTRADICTED/CONTESTED/VERIFIED counts) + confidence range. Expanded: Reliability (primary) + Peer Alignment (`calcPeerAlignment`, secondary, drawer/expanded only). Collapsed by default.

**`components/Stage3Annotated.jsx`** — replaces `Stage3` in ChatInterface. Imports `Stage3.css` + `Stage3Annotated.css`. Adds "⊞ Epistemic View" button (calls `onOpenDrawer`). Injects colored underlines via `annotateChairmanText` + `rehype-raw`. Click delegation on `.claim-annotation` spans → `ClaimPopover`. Falls back to plain `ReactMarkdown` when `stage25` is null/empty (no regression for old conversations).

**`components/ClaimPopover.jsx`** — `ReactDOM.createPortal` to `document.body`. `position: fixed`, edge-clamped to viewport. Shows verdict badge + model + confidence + delta + source URL. Dismissed on outside click or Escape.

**`components/MinorityReport.jsx`** — amber collapsible below Stage 3. Only renders when `getMinorityModel()` returns a model. Shows that model's Stage 1 response (clamped 80 words), factual_claims chips, confidence badge.

**`components/EpistemicDrawer.jsx`** — fixed slide-in panel (380px, right edge). Imports `Stage25.css` for verdict card styles. Shows Reliability + Peer Alignment summary + claims grouped by verdict (CONTRADICTED→CONTESTED→VERIFIED→UNVERIFIABLE). Backdrop click or Escape closes.

**`components/Sidebar.jsx`** — conversation list with title and message count. "+ New Conversation" button.

**`api.js`** — HTTP/SSE client. `API_BASE` from `VITE_API_BASE` env var, defaults to `http://localhost:8001`. Methods: `listConversations`, `createConversation`, `getConversation`, `scrubPhase0`, `sendMessageStream`.

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
**Key files:** `App.jsx`, `ChatInterface.jsx`, `components/Stage*.jsx`, `components/Phase0Review.jsx`, `components/Epistemic*.jsx`, `components/Stage3Annotated.jsx`, `components/ClaimPopover.jsx`, `components/MinorityReport.jsx`, `utils/epistemic.js`
**Never touch:** `backend/`, `data/`, docker files

**Frontend update trigger:** Any time the backend changes its API response shape, adds SSE events, or exposes new data fields — the frontend-agent must be run to reflect those changes.

### infra-agent

**Scope:** `docker-compose.yml`, nginx config, `.env` structure only
**Responsibility:** Implement infrastructure changes as specified. Do not change networking logic without explicit instruction.
**Key invariant:** `VITE_API_BASE` must always be empty in `docker-compose.yml`
**Never touch:** `backend/`, `frontend/src/`

## How to Use Agents

**Top-level Claude is orchestrator only.**

It must never write implementation code directly — even for small or "obvious" changes. All code must be written by spawning the appropriate agent. Top-level Claude reads the task, identifies which agents are needed, coordinates between them, and presents results to the developer.

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

## When No Role Is Specified

If no role is specified in the prompt, Claude is top-level orchestrator by default.

As top-level orchestrator, Claude MUST:

1. Identify whether the task crosses domain boundaries (backend + frontend)
2. Identify whether it contains any design or logic decisions
3. Pause and confirm which agent to spawn. The answers to 1 and 2 determine which agent is appropriate — not whether one is needed at all. Spawn the appropriate agent regardless.
4. Never silently assume "this is small enough to do directly"

## Post-Task Review Protocol

After completing any non-trivial task, top-level Claude must check:

1. Were any rules in this file violated during the session?
2. Did any instruction in this file fail to prevent the violation?
3. If yes to either: propose a concrete, minimal addition or edit to this file and present it to the developer for approval

**Never self-apply changes to CLAUDE.md — only propose them.**

When the developer asks for a post-task review:

- Go through the session and list rule violations
- For each violation, propose a specific fix to this file
- Present all proposals at once for developer approval before making any edits
- After approval, apply the changes and clear the log from MEMORY.md

## Key Invariants

- **Relative imports**: all `backend/` modules use `from .config import ...`. Run as `python -m backend.main` from project root.
- **All ReactMarkdown** must be wrapped in `<div className="markdown-content">` — class defined in `index.css`.
- **Confidence ceilings** are enforced by prompt instruction, not code. The system trusts model compliance; `parse_stage1_metadata()` normalises `confidence_source` to `{"recalled","reasoned","speculative"}` or `None`.
- **Stage 2 de-anonymization**: rankers see full metadata text, but it must be style-scrubbed first via `_scrub_metadata_texts()` before showing to rankers. The scrubbing (not count-limiting) is what prevents de-anonymization by phrasing style.
- **Stage 2 self-exclusion**: each model ranks only 3 peers, never itself. `calculate_aggregate_rankings()` averages only ranks actually received.
- **Phase 0 storage rule**: scrubbed prompt goes to council; original prompt goes to `storage`. Users always see what they typed.
- **Stage 2.5 isolation**: the verifier block in `main.py` is wrapped in `try/except`; `verification_results` defaults to `[]` on failure. Stage 3 always runs.
- **factual_claims vs key_assumptions**: `factual_claims` are specific falsifiable facts (primary verification target); `key_assumptions` are framing/interpretation premises. The verifier uses `factual_claims` first and falls back to `key_assumptions` only when the field is absent (backward compatibility with pre-Stage-2.5 conversations).
- **Stage 2.5 verdicts**: four types — VERIFIED, CONTRADICTED, CONTESTED, UNVERIFIABLE. CONTESTED means credible evidence on both sides; Chairman must report the controversy, not resolve it.
- **Session memory**: `settled_facts` persisted per conversation in JSON. Injected into Stage 3 as `[PRIOR COUNCIL CONTEXT]`. Stage 3 UI does not display these — backend-only.
- **Epistemic Waterfall** (`calcVerdict`): primary reliability signal shown to user. Step 1 uses Stage 2.5 external verdicts (CONTRADICTED beats everything → Disputed; all VERIFIED → Verified). Step 2 falls back to Stage 1 claim overlap + avg confidence. Never changes pipeline output — display only.
- **`reliability-badge` vs `verdict-badge`**: epistemic verdict CSS uses `reliability-badge--{state}` (in `EpistemicSummary.css`). Do not use `verdict-badge--{state}` for this — that class is already used in `Stage25.css` for claim-level verdicts and the names would conflict.
- **`rehype-raw`**: only used in `Stage3Annotated` when `stage25` is non-empty. When `stage25` is null/empty, plain `ReactMarkdown` is used (no `rehype-raw`). This prevents any regression on old conversations.
- **Peer Alignment vs Reliability**: two separate signals. Peer Alignment (spread of `aggregate_rankings`) measures response-quality agreement. Reliability (`calcVerdict`) measures factual correctness. They are independent and can diverge — e.g., all models agree on facts (Verified) but peers rate one response much lower (Split alignment).

## System Prompt Locations

System prompts live as module-level constants and were designed iteratively by the council itself:

- `council.py` → `SCRUBBER_SYSTEM_PROMPT` (~60 lines): full ruleset including EXCEPTIONS & OVERRIDES section
- `council.py` → `STAGE1_SYSTEM_PROMPT` (~30 lines): transparency framing + calibration rules + JSON schema
- `verifier.py` → `VERIFIER_SYSTEM_PROMPT`: verdict rules with explicit bias guards (absence of evidence ≠ contradiction); four-verdict schema including CONTESTED
