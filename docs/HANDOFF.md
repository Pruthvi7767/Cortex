# Handoff to Phase 4

## What THIS phase built
- `router.py` — UCB1 candidate selection with full filtering pipeline and tier cascade.
- `MODEL_REGISTRY` added to `config.py` — all confirmed model IDs for strong/mid/fast tiers across NVIDIA, Groq, Cerebras, Google Gemini, Mistral, Cloudflare, and Ollama, with `max_context` token sizes.

## Key functions available to Phase 4

### `get_candidates_with_cascade(tier, k=3, estimated_tokens=0) -> CandidateResult`
- **Primary entry point for Phase 4.** Call this to get a ranked, filtered candidate list.
- Returns `CandidateResult(tier: str, candidates: list)` — a NamedTuple.
- `tier` on return is the **actual tier used** (may differ from requested if cascade fired).
- `candidates` is a list of dicts: `{"provider": str, "model_id": str, "max_context": int}`.
- Already handles: active-provider filtering, circuit-breaker exclusion, quota/RPM gating, context-window filtering, UCB1 ranking, and tier fallback.

### `split_nvidia_first(candidates: list) -> tuple`
- Takes the candidates list and returns `(nvidia_candidates, others)`.
- **Call this immediately after `get_candidates_with_cascade`** — Phase 4's execution loop tries `nvidia_candidates` first (single shot, short NVIDIA timeout from `TIER_TIMEOUTS`), then fans out to `others` only if needed.

### `ucb1_score(provider, model_id, tier) -> float`
- Not needed directly by Phase 4 (called internally by get_candidates). Listed for awareness.

## What THIS phase explicitly did NOT build
- No HTTP calls to any LLM provider.
- No timeout/race execution logic.
- No response validation gate.
- No Pulse classifier (that is Phase 5).

## Exact next step for Phase 4
1. Import `get_candidates_with_cascade` and `split_nvidia_first` from `router.py`.
2. Import `TIER_TIMEOUTS` from `config.py` for per-tier HTTP timeout budgets.
3. Import `update_latency`, `record_success`, `record_failure`, `increment_rpm`, `increment_tier_requests` from `redis_store.py` to update state after each attempt.
4. Import `record_circuit_failure`, `reset_circuit` from `circuit_breaker.py` to update breaker state based on outcomes.
5. Build `race.py` which: calls candidates in NVIDIA-first order, enforces TIER_TIMEOUTS, passes responses through a validation gate (non-empty, not refusal pattern), and returns the first winner. Implements exactly 1 full retry on total tier failure with 2-3s backoff.

## Watch out for
- The retry ceiling from AGENT.md Section 5: **exactly 1 full retry** of the entire tier cascade — never more than 2 total passes.
- `record_failure` vs quota events are SEPARATE — Phase 4 must call `record_failure` only on real errors (timeout/500/malformed), never on 429. On 429 call `increment_quota_usage` instead (which pauses the model without touching quality score).
- `TIER_TIMEOUTS` = `{"fast": 1.5, "mid": 3.0, "strong": 5.0}` seconds — these are the **per-model attempt** timeouts.

## Environment/config notes
- No new `.env` variables added this phase.
- No new dependencies added this phase.
