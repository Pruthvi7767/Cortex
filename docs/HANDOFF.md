# Handoff (Post-Phase 7 Maintenance)

## What THIS phase built
- `main.py` rewritten with the `/v1/complete` endpoint integrating Auth, Quota/Rate Limiting, Idempotency, Pulse, and UCB1 Racing with retry mechanism.
- `execute_with_retry` wrapper in `main.py` ensuring a single full retry after a 2s backoff on cascade failure.
- Config updates including `MAX_PROMPT_CHARS = 500000` to prevent payload DoS.
- Docker Compose updates (Redis persistence via `redis-server --save 60 1`, postgres 16 added, `restart: unless-stopped`).
- Healthcheck endpoint `/health` expanded with circuit breaker status tracking.
- Test suite `test_phase7_chaos.py` simulating 8 core failure scenarios.
- Database layer fully migrated from Supabase SDK to raw Postgres via `asyncpg` connection pooling in `db.py`. New telemetry tracking tokens and nvidia prioritization integrated into `requests_log`.

## What THIS phase explicitly did NOT do
- Did not implement streaming support.
- Did not implement semantic exact-match caching.
- Did not implement cost-based routing (only handles free tiers).
- Did not implement `/complete-verified` best-of-N+judge endpoint.

## Exact next step for Maintainers
- Load test the production deployment under real usage.
- Monitor `requests_log` on Supabase to ensure all asynchronous fire-and-forget logging handles production throughput without data loss.
- Observe Redis memory usage to ensure sliding window rate limits (ZSETs) and idempotency keys do not cause unchecked growth.

## Environment/config notes
- New `MAX_PROMPT_CHARS` parameter introduced in `config.py`.
- Redis now saves to disk via the `./data` volume mount (`redis_data` in Docker Compose). Ensure volume backups are configured in production.
