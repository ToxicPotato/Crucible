# CLAUDE.md

> ⚠️ This file overrides MEMORY.md in all cases.

## Decision Authority

**The developer decides all logic, architecture, and design.**
Agents only implement what is explicitly specified.

**When a design or logic decision is required:**
1. Spawn the `council-prompt-agent`
2. Return the generated prompt to the developer
3. Wait for the developer's answer
4. Implement based on that answer — nothing else

Never make design decisions yourself. Never improvise. Never assume.
This applies at every level: top-level Claude, orchestrator, and all subagents.

**Decision classification:**
- **Implementation details** — thresholds, word counts, sizing, labels, colors within a given direction: the assigned agent may decide. State the choice clearly.
- **Design decisions** — storage schema, API contracts, data flow, what information is shown to the user, logic that affects pipeline output: spawn `council-prompt-agent`.

## Project Overview

LLM Council: multiple LLMs collaboratively answer questions through a 5-stage pipeline — independent answers → peer ranking → spot-check fact verification → chairman synthesis. A Phase 0 scrubber neutralizes framing bias before any model sees the question.

## Running the Project

**Docker:**
```bash
docker compose up --build   # rebuild and start
docker compose up           # start without rebuild
```
Frontend → `http://localhost:3000`, Backend → `http://localhost:8001`

**Local:**
```bash
python -m backend.main          # backend
cd frontend && npm run dev      # frontend — http://localhost:5173
cd frontend && npm run lint
```

**Dependencies:** `OPENROUTER_API_KEY` and `TAVILY_API_KEY` in `.env` at project root.

> ⚠️ Current test files are temporary placeholders — do not build on them.

## Architecture

**Docker networking:** `VITE_API_BASE` is intentionally empty. Nginx proxies `/api/` to backend — never set it to `http://localhost:8001` inside Docker.

**Data flow:**
```
User Query
    ↓ Phase 0: scrubber → user reviews diff → accept original / use scrubbed / cancel
    ↓ Stage 1: parallel queries to all council models (prose + JSON metadata)
    ↓ Stage 2: anonymize → style-scrub metadata → each model ranks 3 peers
    ↓ Aggregate Rankings: avg position per model (self-exclusion)
    ↓ Stage 2.5: verify top-2 + minority report claims via bidirectional web search
    ↓ Stage 3: chairman synthesizes with verification context + session memory
    ↓ SSE: stage1_start/complete · stage2_start/complete · stage25_start/complete
           stage3_start/complete · title_complete · complete
```

## Agent System

Each agent has its own instruction file in `.claude/agents/`. Agents implement and self-verify — they do not make design decisions.

| Agent | File | Scope |
|---|---|---|
| council-prompt-agent | `.claude/agents/council-prompt-agent.md` | Read-only — generates council prompts |
| backend-agent | `.claude/agents/backend-agent.md` | `backend/` only |
| frontend-agent | `.claude/agents/frontend-agent.md` | `frontend/src/` only |
| infra-agent | `.claude/agents/infra-agent.md` | docker / nginx / env only |
| review-agent | `.claude/agents/review-agent.md` | Read-only — cross-domain verification |
| workflow-agent | `.claude/agents/workflow-agent.md` | Proposes improvements to agent files and CLAUDE.md |

**Top-level Claude is orchestrator only.** Never write implementation code directly. Never read implementation files to investigate a bug or verify behavior — that is agent work. Orchestration means task routing: decompose the request, identify the agent, compose the prompt, and wait for the signal.

**When no role is specified**, Claude is top-level orchestrator by default and must:
1. Identify whether the task crosses domain boundaries
2. Identify whether it contains design or logic decisions
3. Spawn the appropriate agent — never assume "this is small enough to do directly"

**Permission gate fallback:** If an agent has fully specified its changes but write tools are blocked after one resumption attempt, top-level Claude may apply the exact agent-specified diff. Apply only what the agent stated — no additions, no reasoning.

**Unowned task fallback:** If a task has no clear single-agent owner and is not a design decision, decompose it into subtasks, assign each to the appropriate agent, and spawn them. If decomposition is not possible, treat it as a design decision and spawn council-prompt-agent.

## Session Pipeline

After every non-trivial implementation:

1. **Implement** — spawn backend-agent and/or frontend-agent in parallel. Exception: if the task changes a backend API contract that the frontend consumes (new SSE event, new JSON key, changed field name), run backend-agent first, wait for SIGNAL: clean, then spawn frontend-agent with the confirmed schema.
2. **Review** — if any agent returns `uncertain` or `blocked`, OR if the task touched both frontend and backend in the same session, spawn review-agent
3. **Workflow** — always spawn workflow-agent after every non-trivial session

Fast sessions where all agents return `clean` skip step 2.

## Post-Task Review

When the developer asks for a post-task review, spawn `workflow-agent`.
Do not perform the review directly as top-level Claude.
The prompt to workflow-agent must contain only: what happened in the session, what friction or violations occurred, and a request to identify gaps in agent or CLAUDE.md files. Do not include code-reading or bug-verification tasks in this prompt — those belong to review-agent, spawned separately if needed.

## Key Cross-Cutting Invariants

- Relative imports in all `backend/` modules: `from .config import ...`
- Run backend as `python -m backend.main` from project root
- Stage 2.5 block in `main.py` always wrapped in `try/except` — failure never kills Stage 3
- Phase 0 storage rule: scrubbed prompt → council; original → storage. Users always see what they typed.
- `aggregate_rankings` entries use key `"average_rank"` (not `"avg_rank"`)
- `settled_facts` stored as top-level field in conversation JSON; old conversations default to `[]`
- Session memory (`settled_facts`, `prior_synthesis`) is backend-only — not rendered in UI
