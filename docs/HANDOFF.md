# Handoff to Phase 3

## What THIS phase built
- `redis_store.py`: Contains a shared async connection pool and functions to update state for UCB1 and circuit breaker components. Keys track request count, success rate (EMA based), latency (EMA), RPM used, circuit breaker status, and daily quota.
- `circuit_breaker.py`: Manages the state machine for each model (CLOSED, OPEN, HALF_OPEN) and controls lazy transitions on read (`get_circuit_state`). Backoff is capped at 10 minutes.
- `quota_tracker.py`: Exposes a single unified boolean check function `check_availability(provider, model_id) -> bool` that verifies the model is not rate limited, not quota exhausted, and its circuit breaker is not OPEN.

## What THIS phase explicitly did NOT do (left for next phase)
- Did not build the UCB1 scoring logic (the actual math and ranking).
- Did not implement candidate candidate selection algorithms or the router endpoint.
- Did not define how a candidate race will be executed (Phase 4).

## Exact next step
- Read `docs/CHECKPOINT.md` and this `HANDOFF.md`.
- Build the UCB1 routing logic inside `router.py`.
- You will need to use `check_availability()` from `quota_tracker.py` to filter candidates.
- You will also need to read `model:{provider}:{model_id}:success_rate` and `model:{provider}:{model_id}:request_count` to apply the UCB1 math.
- You will also need the global `tier:{tier}:total_requests` count from Redis to compute UCB1 properly.

## Environment/config notes
- You can find updated `QUOTA_LIMITS` and `RPM_LIMITS` inside `config.py`.
- Any Redis interaction you do should wrap around `_safe_execute` in `redis_store.py` (or handle `RedisConnectionError`) to gracefully degrade if the database falls over.
