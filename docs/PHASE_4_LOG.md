# Phase 4: Race Execution — Build Log

## Goal for this phase
Build the HTTP execution layer that calls LLM providers using the NVIDIA-first sequencing rule, races candidates with proper timeouts and real asyncio cancellation, and validates every response through a strict gate before declaring a winner.

## Files created/modified (full list)
- `config.py` (modified — added `NVIDIA_FIRST_TIMEOUT = 2.0`, `TIER_MAX_TOKENS`)
- `provider_adapters.py` (created)
- `validation.py` (created)
- `race.py` (created)
- `test_phase4.py` (created)
- `docs/CHECKPOINT.md` (updated)
- `docs/HANDOFF.md` (overwritten for Phase 5)
- `docs/PHASE_4_LOG.md` (created)

## Key design decisions made during this phase

**httpx cancellation via `asyncio.wait_for()`:**
Verified via context7 that httpx has no built-in "total wall-clock timeout" — only per-chunk read timeouts. A `asyncio.wait_for(http.post(...), timeout=T)` wrapper is the correct pattern: when it fires, `CancelledError` propagates into the httpx coroutine, causing httpcore to close the underlying TCP connection. This is REAL cancellation, not "ignore and let it run."

**Shared `AsyncClient` with connection pool:**
A single `httpx.AsyncClient` is created once (lazy, on first call) and reused across all requests. Configured with `max_connections=50`, `max_keepalive_connections=20`. `close_http_client()` is exported for FastAPI lifespan shutdown.

**Dynamic timeout per model:**
If a model has historical EMA latency data in Redis, uses `min(tier_timeout, ema_ms / 1000 * 2.0)` as a cheap p90 proxy — historically slow models get cut off faster. Cold-start falls back to flat `TIER_TIMEOUTS` value.

**429 vs real errors:**
- `429` → only `increment_rpm` logged (already done pre-call). `record_failure()` is NOT called. Quality score is preserved. Per AGENT.md Section 5.
- All other errors (timeout, 5xx, parse failure, empty, refused) → `record_failure()` + `record_circuit_failure()`.
- `asyncio.CancelledError` during parallel race → re-raised, NOT recorded as a failure (expected normal race behaviour).

**Validation gate:**
Refusal detection uses a dominance threshold (60%) to avoid false-positives on legitimate long answers that happen to mention refusal phrases in passing.

**`TIER_MAX_TOKENS`:**
`{fast: 300, mid: 800, strong: 2000}` — enforces per-tier response size cap to prevent runaway generation consuming quota.

## Problems encountered and how they were solved
- `NameError: call_candidate` in test3 — the function was imported at the wrong scope level inside the `with patch(...)` context. Fixed by moving the `from race import call_candidate` inside the test function body, before the patch block.
- Unicode arrow `→` in log messages caused mojibake on Windows console output (`racing 1 other candidates` displayed with garbled character). This is a cosmetic Windows cp1252 codec issue only — all logic ran correctly. Not fixed (non-critical); would need `sys.stdout.reconfigure(encoding='utf-8')` to fix.

## Testing done before marking phase complete

All 8 tests executed via `python test_phase4.py` — zero real API quota spent (all HTTP mocked):

```
=== Phase 4 Test Script ===

[setup] Redis connection OK

1. [OK] Success path: success=True, record_success=1, update_latency=1
2. [OK] 429 path: error_type=rate_limit, record_failure calls=0 (must be 0)
3. [OK] Timeout trips circuit: state=OPEN (expected OPEN)
4. [OK] Empty 200: success=False, error_type=empty, record_failure=1, record_success=0
5. [OK] NVIDIA wins first: success=True, nvidia_calls=1, other_calls=0 (must be 0)
6. [OK] NVIDIA timeout fallthrough: success=True, providers tried=['nvidia', 'groq']
7. [OK] Parallel race cancellation: winner=groq, cancelled=['mistral', 'cerebras'] (must have >=1)
8. [OK] All fail: success=False, error_type=server_error

=== Summary ===
8/8 tests passed
```

## Confirmed working (yes/no)
Yes — all 8 required tests pass.
