# Cortex Project Checkpoint

## Phases completed
- [x] Phase 1 — Foundation (completed: 2026-07-12)
- [x] Phase 2 — State layer (completed: 2026-07-12)
- [x] Phase 3 — Router (completed: 2026-07-12)
- [ ] Phase 4 — Race execution
- [ ] Phase 5 — Pulse (auto-classifier)
- [ ] Phase 6 — Auth & logging
- [ ] Phase 7 — Tool-calling & integration

## Files that exist so far
- `requirements.txt` — contains fastapi, pydantic, uvicorn, redis, httpx, python-dotenv, supabase.
- `.env.example` — template with all 24 provider key names across 3 tiers, plus ENVIRONMENT, REDIS_URL, SUPABASE_URL, SUPABASE_KEY.
- `config.py` — provider registry, tier timeouts, quota/RPM limits, environment loader, and hot-reload compatible `get_active_providers` function.
- `main.py` — minimal FastAPI app with lifespan context manager for startup check, and `/health` endpoint.
- `Dockerfile` — Python 3.11-slim setup.
- `docker-compose.yml` — services for `app` and `redis`.
- `test_phase1.py` & `test_phase2.py` — basic pytest tests and test scripts for the config and state layers.
- `redis_store.py` — connection pool management, latency/success tracking, and atomic increment functions.
- `circuit_breaker.py` — CLOSED/OPEN/HALF_OPEN state machine implementation handling cooling off and failure thresholds.
- `quota_tracker.py` — `check_availability()` filter based on quota, rate limits, and circuit state.
- `router.py` — UCB1 scoring, `get_candidates()`, `split_nvidia_first()`, `get_candidates_with_cascade()` with tier cascade.
- `MODEL_REGISTRY` added to `config.py` — all confirmed model IDs for strong/mid/fast tiers across 7 providers (NVIDIA, Groq, Cerebras, Google, Mistral, Cloudflare, Ollama).

## Known issues / TODOs left for later phases
- Model race execution (HTTP calls, timeouts, response validation gate) — Phase 4.
- Pulse Tier auto-classifier (classifier.py) — Phase 5.
- Authentication, logging, Supabase integrations — Phases 6-7.
- Authentication, logging, and Supabase integrations (Phases 6-7).

## Decisions made (that future phases must respect)
- Algorithm: UCB1, not Thompson Sampling or LinUCB.
- No `/complete-verified` endpoint.
- Tiers: strong/mid/fast only.
- Providers ranked: NVIDIA (top priority) > Groq > Cerebras > Google > Mistral > Cloudflare > Ollama Cloud > Tier 2/3 bonus providers.
- Environment switching supported via `ENVIRONMENT` (loads `.env.development`, etc.).
- Loud failure on startup if ZERO providers are configured.
- Replaced deprecated `@app.on_event("startup")` with `@asynccontextmanager` lifespan function in `main.py`.
