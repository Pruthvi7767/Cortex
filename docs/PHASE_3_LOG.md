# Phase 3: Router (UCB1 candidate selection) — Build Log

## Goal for this phase
Build the candidate selection engine that decides which models to try for a given request, in what order, using UCB1 scoring. No network calls are made in this phase.

## Files created/modified (full list)
- `config.py` (modified — added `MODEL_REGISTRY` with all confirmed model IDs)
- `router.py` (created)
- `test_phase3.py` (created)
- `docs/CHECKPOINT.md` (updated)
- `docs/HANDOFF.md` (overwritten for Phase 4)
- `docs/PHASE_3_LOG.md` (created)

## Key design decisions made during this phase

**UCB1 formula implemented:**
```
score = (1 / latency_ema) + 1.4 * sqrt(ln(N_total) / N_model)
```
- `UCB1_C = 1.4` defined as a named constant for easy tuning.
- Cold-start returns `float("inf")` — not epsilon — ensuring untried models always rank first.

**Filtering order is strict (by design from AGENT.md):**
1. Tier membership
2. Active provider (has API key)
3. `check_availability()` — handles circuit/quota/rate-limit in one call
4. Context window (estimated_tokens * 1.15 safety margin)
5. UCB1 rank
6. Top-k slice

**`registry_override` / `active_providers_override` parameters** added to `get_candidates()` and `get_candidates_with_cascade()` for testability without needing to alter the real registry or environment.

**`CandidateResult` NamedTuple** used for `get_candidates_with_cascade()` return type — confirmed via context7 that `typing.NamedTuple` is the correct, stable stdlib pattern (no pydantic dependency needed for a simple value object).

**MODEL_REGISTRY notes:**
- Ordered by provider priority within each tier per AGENT.md Section 5.
- Vision models deliberately excluded (separate pool, future phase).
- `whisper-large-v3-turbo` excluded from Groq fast per user direction — not a general reasoning model.
- Context window sizes use official specs where known; conservative placeholders marked in comments.

## Problems encountered and how they were solved
- Test 7 print statement used a Unicode arrow `→` which caused `UnicodeEncodeError` on Windows console (cp1252 codec). Fixed by replacing with ASCII `->`.
- Tests 1-6 passed on first run; only Test 7 was affected by the encoding issue.

## Testing done before marking phase complete

All 7 tests executed via `python test_phase3.py` against a live Redis container:

```
=== Phase 3 Test Script ===

[setup] Redis connection OK

--- Test 1: UCB1 ranking ---
1. [OK] Candidates ranked correctly: ['fast-model', 'mid-model', 'slow-model']

--- Test 2: Cold-start model gets mandatory first try ---
2. [OK] Cold-start model ranked first: ['brand-new', 'fast-model', 'mid-model', 'slow-model']

--- Test 3: Inactive provider filtered out ---
3. [OK] Inactive provider 'mistral' excluded. Found providers: {'groq', 'nvidia'}

--- Test 4: Circuit-OPEN model excluded ---
4. [OK] Circuit-OPEN model 'mid-model' excluded. Results: ['fast-model', 'slow-model']

--- Test 5: Context window filtering ---
5. [OK] Small-context model excluded, large-context model present: ['slow-model']

--- Test 6: split_nvidia_first ---
6. [OK] split_nvidia_first correct. NVIDIA: ['fast-model', 'slow-model'], Others: ['mid-model', 'some-model']

--- Test 7: Tier cascade strong -> mid ---
INFO:cortex.router:Tier cascade: 'strong' exhausted, using 'mid'
7. [OK] Cascaded from 'strong' to 'mid'. Used tier='mid', candidates=['mid-fallback']

=== Test Complete ===
```

## Confirmed working (yes/no)
Yes — all 7 required tests pass.
