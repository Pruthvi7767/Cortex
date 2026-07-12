# Phase 1: Foundation — Build Log

## Goal for this phase
Build the foundational configuration and provider registry system for Cortex. This included defining dependencies, standardizing the environment variables across 24 providers and 3 tiers, creating a hot-reloadable configuration registry, laying out a minimal FastAPI application, and dockerizing the project with Docker and docker-compose.

## Files created/modified (full list)
- `requirements.txt`
- `.env.example`
- `config.py`
- `main.py`
- `Dockerfile`
- `docker-compose.yml`
- `test_phase1.py` (for manual verification and sanity checks)
- `docs/CHECKPOINT.md`
- `docs/HANDOFF.md`
- `docs/PHASE_1_LOG.md`

## Key design decisions made during this phase
- Replaced the deprecated FastAPI `@app.on_event("startup")` event with the currently recommended `@asynccontextmanager` `lifespan` pattern, verified via `context7`.
- Verified that the `redis` package provides `redis.asyncio` for async interaction, and it handles connection pools inherently.
- Defined `get_provider_registry` as a function in `config.py` rather than a constant, so future hot-reloading can be easily implemented by calling it again.
- Startup explicitly blocks and raises a `RuntimeError` if no active providers are found (a hard requirement).
- Included a special check for Cloudflare's `ACCOUNT_ID`, since it's required for its endpoint URL format.
- The `/health` endpoint exposes which environment the app is running in and the active provider IDs.

## Problems encountered and how they were solved
- Fast API startup events are deprecated in newer versions. Consulted `context7` which confirmed `lifespan` is the intended pattern for FastAPI 0.93+. Adopted `lifespan` properly.
- The `redis` Python client has evolved. Consulted `context7` which confirmed `redis.asyncio` is the recommended way to use async Redis in `redis-py` 4.2+.

## Testing done before marking phase complete
A test suite (`test_phase1.py`) was created and run using `pytest`, alongside mocking `os.getenv`, to manually verify the 4 required scenarios:
1. **Set only ONE provider's key**: Mocked only `NVIDIA_API_KEY`. Confirmed `get_active_providers()` returns exactly 1 provider.
2. **Set ALL provider keys**: Mocked all required keys. Confirmed all 24 providers were marked active.
3. **Set ZERO provider keys**: Mocked empty env. Confirmed it fails loudly by raising a `RuntimeError` during the `assert_providers_configured()` call.
4. **Set a provider key to empty string**: Mocked `NVIDIA_API_KEY=""`. Confirmed the logger emits a warning specifically noting the environment variable is present but empty, and skips the provider.

## Confirmed working (yes/no)
Yes, all required functionalities are implemented and tested.
