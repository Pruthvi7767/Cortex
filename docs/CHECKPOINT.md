# Cortex Project Checkpoint

## Phases completed
- [x] Phase 1 — Foundation (completed: 2026-07-12)
- [x] Phase 2 — State layer (completed: 2026-07-12)
- [x] Phase 3 — Router (completed: 2026-07-12)
- [x] Phase 4 — Race execution (completed: 2026-07-12)
- [x] Phase 5 — Pulse (auto-classifier) (completed: 2026-07-12)
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
- `provider_adapters.py` — `build_request()`, `parse_response()`, `get_endpoint_url()`, `get_auth_headers()`, `ParseError`.
- `validation.py` — `validate_response()` gate; failure reasons: `empty`, `refused`, `invalid_tool_schema`, `hallucinated_tool`.
- `race.py` — `call_candidate()`, `execute_race()`, `_race_parallel()`, `RaceResult` dataclass, `close_http_client()`.
- `config.py` now has `NVIDIA_FIRST_TIMEOUT = 2.0` and `TIER_MAX_TOKENS = {fast:300, mid:800, strong:2000}`.
- `classifier.py` — Pulse auto-classifier with `extract_features()`, `needs_llm_classification()`, `llm_classify_confidence()`, `decision_score()`, `classify_tier()`, and `resolve_tier()`. Layer 4 adaptive thresholds left as documented placeholder.

## Known issues / TODOs left for later phases
- The "1 full retry after 2-3s backoff" wrapper around execute_race() — to be added in Phase 6/7 endpoint handler.
- Authentication, logging, Supabase integrations — Phases 6-7.

## Decisions made (that future phases must respect)
- Algorithm: UCB1, not Thompson Sampling or LinUCB.
- No `/complete-verified` endpoint.
- Tiers: strong/mid/fast only.
- Providers ranked: NVIDIA (top priority) > Groq > Cerebras > Google > Mistral > Cloudflare > Ollama Cloud > Tier 2/3 bonus providers.
- Environment switching supported via `ENVIRONMENT` (loads `.env.development`, etc.).
- Loud failure on startup if ZERO providers are configured.
- Replaced deprecated `@app.on_event("startup")` with `@asynccontextmanager` lifespan function in `main.py`.
