# Handoff to Phase 5

## What THIS phase built
- `provider_adapters.py` — `build_request()`, `parse_response()` (raises `ParseError` on schema drift), `get_endpoint_url()`, `get_auth_headers()`. All providers use the OpenAI-compat shape; endpoint URL is resolved from config.py's registry.
- `validation.py` — `validate_response(parsed, expected_tool_schema, tool_whitelist) -> (bool, reason_str)`. Fixed failure reason strings (used by Phase 6 logging): `empty`, `refused`, `invalid_tool_schema`, `hallucinated_tool`.
- `race.py` — `call_candidate()`, `execute_race()` (main entry point), `_race_parallel()`, `RaceResult` dataclass, `close_http_client()`.
- `config.py` additions: `NVIDIA_FIRST_TIMEOUT = 2.0`, `TIER_MAX_TOKENS = {fast:300, mid:800, strong:2000}`.

## What THIS phase explicitly did NOT build
- Pulse auto-classifier (Phase 5) — tier selection before calling execute_race().
- The "1 full retry after 2-3s backoff" wrapper (Phase 6/7) — execute_race() handles one pass only.
- Auth, logging, Supabase integrations (Phases 6-7).

## Key interface for Phase 5 to understand

### `execute_race(tier, messages, max_tokens=None, estimated_tokens=0, tools=None, tool_whitelist=None, expected_tool_schema=None) -> RaceResult`
- **This is the function Phase 6/7's endpoint handler calls.** Phase 5 (Pulse) decides the tier BEFORE this is called, not inside it.
- `tier` is one of `"strong"` | `"mid"` | `"fast"`.
- Returns `RaceResult(success, content, tool_calls, model_used, provider_used, latency_ms, error_type)`.

### `RaceResult` (dataclass in race.py)
```python
@dataclass
class RaceResult:
    success:       bool
    content:       str | None
    tool_calls:    list | None
    model_used:    str | None
    provider_used: str | None
    latency_ms:    float
    error_type:    str | None   # None on success
```

## Exact next step for Phase 5
Phase 5 builds `classifier.py` (Pulse). Its output is a tier string (`"strong"` | `"mid"` | `"fast"`) that gets passed directly to `execute_race(tier=..., messages=...)`. Phase 5 does NOT modify execute_race or race.py — it only builds the classifier logic that sits in front of the execution layer.

## Watch out for
- The "1 retry" logic belongs in Phase 6/7's endpoint handler — NOT inside execute_race(). The endpoint calls execute_race() once, checks if result.success, waits 2-3s, then calls execute_race() at most once more.
- `close_http_client()` from race.py must be called in FastAPI's lifespan shutdown handler (add this to main.py in Phase 6 or 7).
- Error type strings from validation.py (`"empty"`, `"refused"`, `"invalid_tool_schema"`, `"hallucinated_tool"`) must be used as-is for Phase 6 Supabase logging columns — do not rename them.

## Environment/config notes
- No new `.env` variables added this phase.
- No new pip dependencies added this phase (httpx was already in requirements.txt from Phase 1).
