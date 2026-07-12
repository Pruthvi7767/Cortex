# Cortex Project Checkpoint

## Phases completed
- [x] Phase 1 — Foundation (completed: 2026-07-12)
- [ ] Phase 2 — State layer
- [ ] Phase 3 — Router
- [ ] Phase 4 — Race execution
- [ ] Phase 5 — Pulse (auto-classifier)
- [ ] Phase 6 — Auth & logging
- [ ] Phase 7 — Tool-calling & integration

## Files that exist so far
- `requirements.txt` — contains fastapi, pydantic, uvicorn, redis, httpx, python-dotenv, supabase.
- `.env.example` — template with all 24 provider key names across 3 tiers, plus ENVIRONMENT, REDIS_URL, SUPABASE_URL, SUPABASE_KEY.
- `config.py` — provider registry, tier timeouts, environment loader, and hot-reload compatible `get_active_providers` function.
- `main.py` — minimal FastAPI app with lifespan context manager for startup check, and `/health` endpoint.
- `Dockerfile` — Python 3.11-slim setup.
- `docker-compose.yml` — services for `app` and `redis`.
- `test_phase1.py` — basic pytest tests for the config layer (not strictly required for runtime but useful for validation).

## Known issues / TODOs left for later phases
- Redis state management, quota tracking, and circuit breakers (Phase 2).
- The actual model race execution and validation logic (Phases 3-4).
- Pulse Tier auto-classifier (Phase 5).
- Authentication, logging, and Supabase integrations (Phase 6-7).

## Decisions made (that future phases must respect)
- Algorithm: UCB1, not Thompson Sampling or LinUCB.
- No `/complete-verified` endpoint.
- Tiers: strong/mid/fast only.
- Providers ranked: NVIDIA (top priority) > Groq > Cerebras > Google > Mistral > Cloudflare > Ollama Cloud > Tier 2/3 bonus providers.
- Environment switching supported via `ENVIRONMENT` (loads `.env.development`, etc.).
- Loud failure on startup if ZERO providers are configured.
- Replaced deprecated `@app.on_event("startup")` with `@asynccontextmanager` lifespan function in `main.py`.
