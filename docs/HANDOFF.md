# Handoff to Phase 6

## What THIS phase built
- `classifier.py` — Pulse Auto-Classifier. It evaluates incoming prompts using cheap heuristic features (Layer 1) and optionally a fast LLM classification call (Layer 2) to compute a `decision_score` (Layer 3), matching it against thresholds (Layer 4 placeholder) to resolve the tier to `"fast"`, `"mid"`, or `"strong"`.
- It exports `resolve_tier(prompt, explicit_tier, context) -> str`.

## What THIS phase explicitly did NOT build
- The final public endpoint in `main.py` (Phase 7).
- Auth, logging, and Supabase integration (Phase 6).
- The "1 full retry after 2-3s backoff" wrapper.

## Key interface for Phase 6 to understand

### `resolve_tier(prompt: str, explicit_tier: str = None, context: dict = None) -> str`
- **This is the function the eventual public endpoint must call before `execute_race()`.**
- If `explicit_tier` is provided, Pulse is skipped entirely and the explicit tier is returned (manual routing).
- If `explicit_tier` is `None`, Pulse computes and returns the ideal tier (auto routing).

## Exact next step for Phase 6
Phase 6 (Auth & Logging) builds the Supabase integration. It will construct the API key authentication system and the unified logging system that captures request telemetry. The logging system needs to record whether the request tier was selected manually or via auto-routing (`pulse`), so the distinction between `explicit_tier` and the Pulse fallback is important for Phase 6 to track.

## Watch out for
- Do not integrate `resolve_tier` into a public FastAPI endpoint yet; Phase 6 focuses on the Auth & Logging infrastructure (`auth.py`, `logger.py`). The final wire-up happens in Phase 7.
- Make sure to use the exact `error_type` strings from `validation.py` for your logging schema.

## Environment/config notes
- No new `.env` variables added this phase.
- No new pip dependencies added this phase.
