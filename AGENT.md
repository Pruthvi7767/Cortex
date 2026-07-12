# AGENT.md — Cortex (with Pulse) Build Guide

This file is the single source of truth for any AI agent (Claude, Gemini, or other)
working on this project inside Antigravity. Read this file COMPLETELY before writing
any code, on every session, even if you've read it before in this conversation.

---

## 1. Project Overview

**Project name:** Cortex
**Auto-routing subsystem name:** Pulse
**What it is:** A multi-provider LLM proxy that races requests across 20+ free-tier
LLM providers (NVIDIA NIM, Groq, Cerebras, Google Gemini, Mistral, Cloudflare Workers AI,
Ollama Cloud, and others), automatically picks the fastest healthy model per tier
(strong/mid/fast), and returns a single validated response to the caller.

**Core algorithm:** UCB1 (Upper Confidence Bound) bandit for model ranking.
**Core safety pattern:** Circuit breaker (CLOSED/OPEN/HALF_OPEN) per model.
**State layer:** Redis (all scores, quotas, circuit states — never in-memory only).
**Persistence/logging:** Supabase (request logs, API keys).
**Language/framework:** Python 3.11+, FastAPI, async/await throughout.
**Deployment:** Docker + Docker Compose (must run identically on VPS, local, or cloud).

**What this project explicitly does NOT include (do not build these unless told):**
- No `/complete-verified` best-of-N+judge endpoint (deliberately dropped).
- No streaming support yet (deferred to a future frontend phase).
- No cost-based routing yet (all providers are free-tier; deferred until paid providers added).
- No semantic/exact-match caching (deliberately rejected — risk of stale answers in agent chains).

---

## 2. How This Project Is Built — Phases

This project is built in **7 phases**, one at a time, in separate sessions. Each phase
has its own git branch off `develop`. NEVER attempt to build multiple phases in one session
unless explicitly instructed. NEVER skip ahead to a later phase's work.

```
Phase 1 — Foundation: config.py, provider registry, .env structure, hot-reload design
Phase 2 — State layer: redis_store.py, quota tracker, circuit breaker state machine
Phase 3 — Router: router.py, UCB1 scoring, candidate selection/filtering
Phase 4 — Race execution: race.py, per-tier timeouts, response validation gate
Phase 5 — Pulse (auto-classifier): classifier.py, Layer 1-4 tier auto-detection
Phase 6 — Auth & logging: auth.py, API key system, Supabase request logging
Phase 7 — Tool-calling & integration: tool-call validation/whitelisting, Docker Compose, final wiring
```

Branch naming: `phase-1-foundation`, `phase-2-redis-state`, `phase-3-router`,
`phase-4-race-execution`, `phase-5-classifier`, `phase-6-auth-logging`,
`phase-7-tools-integration`.

---

## 3. MANDATORY: Checkpoint & Handoff Files

**This is not optional. Every phase MUST produce these files before being considered done.**
These exist so that if the conversation/session ends, crashes, or a different agent/model
picks up the work later, nothing is lost and nothing is guessed at.

### 3.1 At the START of every phase, the agent must:
1. Read `/docs/HANDOFF.md` (created by the previous phase) if it exists.
2. Read `/docs/CHECKPOINT.md` to see the current state of the whole project.
3. Read this `AGENT.md` file in full.
4. Confirm understanding by summarizing, in its own first message, what phase it is
   starting and what the previous phase left behind.

### 3.2 At the END of every phase, the agent must create/update:

**`/docs/CHECKPOINT.md`** — cumulative project state, overwritten/appended each phase:
```markdown
# Cortex Project Checkpoint

## Phases completed
- [x] Phase 1 — Foundation (completed: <date>)
- [ ] Phase 2 — State layer
...

## Files that exist so far
- config.py — provider registry, hot-reload, env loading
- .env.example — template with all provider key names
...

## Known issues / TODOs left for later phases
- ...

## Decisions made (that future phases must respect)
- Algorithm: UCB1, not Thompson Sampling or LinUCB
- No /complete-verified endpoint
- Tiers: strong/mid/fast only
- Providers ranked: NVIDIA (top priority) > Groq > Cerebras > Google > Mistral > Cloudflare > Ollama Cloud > Tier 2/3 bonus providers
...
```

**`/docs/HANDOFF.md`** — overwritten fresh each phase, specifically for the NEXT phase:
```markdown
# Handoff to Phase <N+1>

## What THIS phase built
- ...

## What THIS phase explicitly did NOT do (left for next phase)
- ...

## Exact next step
- Start by creating <file>, which needs to import from <existing file>
- Watch out for: <specific gotcha discovered this phase>

## Environment/config notes
- New .env variables added this phase: <list>
- New dependencies added this phase: <list, with versions>
```

**`/docs/PHASE_<N>_LOG.md`** — one per phase, permanent record, never overwritten:
```markdown
# Phase <N>: <name> — Build Log

## Goal for this phase
## Files created/modified (full list)
## Key design decisions made during this phase
## Problems encountered and how they were solved
## Testing done before marking phase complete
## Confirmed working (yes/no) — if no, explain what's broken
```

**Do not mark a phase complete, do not tell the user "done," and do not merge to
`develop` until all three of the above files are written or updated for that phase.**

---

## 4. context7 MCP — When and How to Use It

**What context7 is:** an MCP tool that fetches current, accurate, up-to-date documentation
for libraries/frameworks directly — instead of relying on training data that may be stale
or wrong about API signatures, parameter names, or recent breaking changes.

**MANDATORY use of context7 in these situations, every phase:**
1. Before writing any code that imports a third-party library for the first time in this
   project (`fastapi`, `redis`, `supabase`, `httpx`, `pydantic`, etc.) — verify current
   syntax via context7 rather than assuming from memory.
2. Before using any FastAPI feature that involves async lifecycle, dependency injection,
   or middleware — these have changed across FastAPI versions.
3. Before writing Redis client code (`redis.asyncio`, connection pooling syntax) — verify
   the current recommended async pattern via context7, since this has shifted across
   `redis-py` versions.
4. Before writing Supabase client code (Python SDK) — verify current method names and
   auth patterns via context7.
5. Whenever a library call fails or behaves unexpectedly and you suspect the syntax/API
   may have changed since training data — check context7 before guessing a fix.

**Do NOT use context7 for:**
- Pure business logic that's yours (UCB1 formula, circuit breaker state machine, tier
  definitions) — this isn't library documentation, it's project-specific design already
  defined in this file and CHECKPOINT.md.
- Simple, stable, unchanging standard-library Python (`os`, `json`, `datetime`, `asyncio`
  basics) — not worth the lookup, low risk of drift.

**How to use it:** call context7 to fetch the relevant library's current docs BEFORE
writing the import/integration code, not after something breaks. This is a proactive
step, not a debugging-only tool.

---

## 5. Non-negotiable Design Decisions (do not re-litigate these)

These were decided after extensive design discussion. Do not suggest alternatives
unless the user explicitly reopens the topic.

- **Algorithm:** UCB1 for model ranking. Not Thompson Sampling, not LinUCB.
- **Language:** Python + FastAPI. Not Rust, not Go.
- **Tiers:** exactly three — `strong`, `mid`, `fast`. No sub-tiers unless instructed.
- **Provider priority order (highest first):** NVIDIA NIM > Groq > Cerebras > Google
  Gemini > Mistral AI > Cloudflare Workers AI > Ollama Cloud (Tier 1, core) — then
  GitHub Models, HuggingFace, SiliconFlow, ModelScope, Alibaba, Zhipu, SambaNova (Tier 2)
  — then Kilo Code, OpenCode Zen, LLM7, Chutes, Glhf, AionLabs, Agnes, Nscale, Neibius,
  OVHcloud (Tier 3, bonus/lowest trust).
- **NVIDIA-first execution rule:** within a tier, try NVIDIA's top-ranked model FIRST
  (single attempt, not raced), with a short timeout. Only expand the race to other
  providers if NVIDIA doesn't respond in time. This protects other providers' limited
  daily/monthly quotas since NVIDIA has no daily cap.
- **Both manual AND auto routing must coexist.** If caller specifies `tier` explicitly,
  use it directly (skip Pulse's classifier). If omitted, Pulse's classifier decides.
  Never auto-only, never manual-only.
- **Provider pool is auto-detected from `.env`.** A provider is only "active" if its
  API key exists in the environment. The system must run correctly with 1 provider
  configured or all 21+, with zero code changes — this is tested and confirmed working
  behavior, not optional.
- **Retry/failure ceiling:** exactly 1 full retry of the entire tier cascade after the
  first full pass fails, with a 2-3 second backoff between passes. Never more than 2
  total passes. Then return a clear error — never hang indefinitely.
- **Quota exhaustion vs real errors are tracked SEPARATELY.** A 429 or daily-quota-hit
  NEVER lowers a model's quality/UCB1 score — it only pauses that model until quota
  resets. Only real errors (timeout, 500, malformed output, etc.) affect quality scoring.
- **Every response passes through a validation gate** before being declared a winner:
  non-empty, not a refusal pattern, matches expected tool-call schema if applicable,
  tool name exists in caller's whitelist if applicable. HTTP 200 alone is never enough.
- **Only the winning model's tool-call is ever returned to the caller.** Cortex never
  executes tools itself — it only validates and returns a decision. The calling agent
  (Markly, etc.) executes tools and sends results back in a follow-up request.
- **Statelessness:** Cortex does not store conversation history. The caller sends full
  context every request (Option A, confirmed design). Cortex only tracks operational
  state (scores, quotas, circuit breakers), never conversation content long-term.
- **Security:** treat all secrets like a banking system. `.env` never committed to git,
  `chmod 600` on `.env`, API keys hashed (never stored raw) in Supabase, HTTPS only in
  production, Row Level Security enabled on Supabase tables.
- **API versioning:** all endpoints live under `/v1/` (e.g. `/v1/complete`) from day one,
  so future breaking changes get `/v2/` instead of breaking existing callers.
- **Timezone:** all internal timestamps and quota-reset logic use UTC. Never local
  server time.

---

## 6. Testing Philosophy

- Do not write elaborate test suites during Phases 1–6 — basic sanity checks only
  (does the function run without crashing, does it return the expected shape).
- Phase 7 includes a basic chaos/failure test: simulate a provider timeout, simulate
  all providers in a tier failing, simulate a malformed response — confirm the system
  degrades gracefully and never hangs.
- Full production load-testing is explicitly OUT of scope for all 7 phases — this
  happens after Phase 7, with real usage, not before.

---

## 7. Communication Rules for the Agent

- Never say a phase is "done" without having created/updated the 3 checkpoint files
  described in Section 3.
- Never silently make a design decision that contradicts Section 5 — if something in
  Section 5 seems wrong or needs to change, STOP and flag it to the user rather than
  quietly deviating.
- If a phase's scope seems to require work that belongs to a different phase, do NOT
  do that other phase's work — note it in `HANDOFF.md` instead and stay within scope.
- Keep code comments professional and minimal — explain WHY a non-obvious decision was
  made (e.g. "# 429 does not lower score — quota exhaustion is not a quality signal"),
  not WHAT the code obviously does.

---

## 8. Directory Structure (target, built up across phases)

```
/cortex
  /docs
    CHECKPOINT.md
    HANDOFF.md
    PHASE_1_LOG.md
    PHASE_2_LOG.md
    ... (one per phase)
  config.py
  redis_store.py
  circuit_breaker.py
  quota_tracker.py
  classifier.py          (Pulse)
  router.py
  race.py
  auth.py
  logger.py
  background_prober.py
  main.py
  .env.example
  requirements.txt
  Dockerfile
  docker-compose.yml
  AGENT.md               (this file, stays at project root)
```

---

*This file must be re-read at the start of every phase, regardless of which model or
agent is executing it. When in doubt about a design decision, check Section 5 first,
then CHECKPOINT.md, then ask the user — never assume or invent new architecture.*
