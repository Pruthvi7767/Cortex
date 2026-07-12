# Handoff to Phase 7

## What THIS phase built
- `auth.py` — `generate_api_key()`, `verify_api_key(raw_key) -> caller_info`, `check_caller_rate_limit(caller_id, limit_per_minute) -> bool`.
- `create_api_key.py` — CLI key generator.
- `logger.py` — `log_request(...)` (safe database insertion), `get_recent_failures(limit) -> list`.
- `supabase_schema.sql` — database schema with Row Level Security (RLS) policies.

## What THIS phase explicitly did NOT build
- Wiring of auth and logging into the final `/v1/complete` endpoint.
- Integrating tool-calling validation/whitelisting.
- Docker Compose orchestration config.

## Key interface for Phase 7 to understand

### `verify_api_key(raw_key: str) -> Optional[dict]`
- Takes the incoming authorization header token (from FastAPI dependency).
- Returns `{"caller_id": str, "rate_limit_per_minute": int}` if validated, or `None`.

### `check_caller_rate_limit(caller_id: str, limit_per_minute: int) -> bool`
- Checks caller's sliding window rate limit using local Redis.
- Returns `True` (under limit) or `False` (rate-limited, return HTTP 429).
- Check this immediately after authenticating the request.

### `log_request(...)` (in logger.py)
- Call this inside the endpoint handler using `asyncio.create_task(log_request(...))` so that it is fire-and-forget and does not block the response loop.
- Parameters: `request_id`, `caller_id`, `tier_requested`, `tier_source` ("manual" or "auto"), `provider_used`, `model_used`, `latency_ms`, `success`, `error_type`.

## Exact next step for Phase 7
1. Set up the final public `/v1/complete` (POST) endpoint in `main.py`.
2. Add FastAPI authorization header parsing (dependency) and call `verify_api_key()`.
3. Add caller rate limit check using `check_caller_rate_limit()`.
4. Run Pulse's `resolve_tier()` to compute target tier if none is explicitly specified.
5. Invoke Phase 4's `execute_race()` with the determined tier and request payload.
6. Enforce a retry mechanism wrapping `execute_race()`: if the race fails, perform exactly 1 full retry of the entire tier cascade after a 2-3s backoff.
7. Wrap response generation with async fire-and-forget `log_request()` using `asyncio.create_task()`.
8. Integrate tool whitelisting and argument schema verification via `validation.py`.
9. Ensure `close_http_client()` from `race.py` is registered in FastAPI lifespan shutdown.

## Watch out for
- The "1 full retry" must only be done once (total 2 attempts max).
- If the request fails both attempts, return HTTP 500 or appropriate error code containing the final failure's `error_type` in the payload.
