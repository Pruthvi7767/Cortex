"""
race.py — HTTP execution layer for Cortex.

Implements the NVIDIA-first execution rule (AGENT.md Section 5):
  1. Try NVIDIA's top candidate first, single shot, short timeout.
  2. If NVIDIA fails/times out, race remaining candidates in parallel.
  3. First valid winner cancels all other in-flight requests immediately.

This module does NOT implement the "1 full retry after 2-3s backoff" logic —
that thin wrapper belongs in Phase 6/7's endpoint handler, which will call
execute_race() up to twice. This function covers ONE full pass only.

Cancellation note: asyncio.Task.cancel() propagates CancelledError into the
awaited httpx coroutine, which causes httpcore to close the underlying TCP
connection. This is REAL cancellation — the request stops consuming network and
quota, not just "ignored while still running".
"""

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Optional

import httpx

from config import TIER_TIMEOUTS, TIER_MAX_TOKENS, NVIDIA_FIRST_TIMEOUT
from provider_adapters import (
    build_request,
    parse_response,
    get_endpoint_url,
    get_auth_headers,
    ParseError,
)
from validation import validate_response
from redis_store import (
    client as redis_client,
    update_latency,
    record_success,
    record_failure,
    increment_rpm,
    increment_tier_requests,
    update_last_used,
    _safe_execute,
)
from circuit_breaker import record_circuit_failure, reset_circuit
from router import get_candidates_with_cascade, split_nvidia_first

logger = logging.getLogger("cortex.race")

# Shared async httpx client — created once, reused across all requests.
# Connection pool managed by httpx internally.
_http_client: Optional[httpx.AsyncClient] = None


def get_http_client() -> httpx.AsyncClient:
    """Returns the shared AsyncClient, creating it on first call."""
    global _http_client
    if _http_client is None or _http_client.is_closed:
        _http_client = httpx.AsyncClient(
            timeout=httpx.Timeout(
                connect=5.0,
                read=30.0,   # per-chunk read timeout; real deadline enforced via asyncio.wait_for
                write=10.0,
                pool=5.0,
            ),
            limits=httpx.Limits(max_connections=50, max_keepalive_connections=20),
            follow_redirects=True,
        )
    return _http_client


async def close_http_client():
    """Call at app shutdown to cleanly drain the connection pool."""
    global _http_client
    if _http_client and not _http_client.is_closed:
        await _http_client.aclose()


@dataclass
class RaceResult:
    """Structured result returned by call_candidate() and execute_race()."""
    success: bool
    content: Optional[str]
    tool_calls: Optional[list]
    model_used: Optional[str]
    provider_used: Optional[str]
    latency_ms: float
    error_type: Optional[str]   # "timeout" | "rate_limit" | "server_error" |
                                #  "auth_error" | "network_error" | "parse_error" |
                                #  "empty" | "refused" | "invalid_tool_schema" |
                                #  "hallucinated_tool" | "context_exceeded" | None

    # Telemetry fields
    prompt_tokens: Optional[int] = None
    completion_tokens: Optional[int] = None
    total_tokens: Optional[int] = None
    nvidia_attempted: bool = False
    nvidia_succeeded: bool = False
    validation_rejections: Optional[str] = None
    # BUG-08 FIX: Declared as a proper dataclass field instead of being dynamically
    # set with setattr(). Dynamic attribute setting on dataclasses is fragile and
    # breaks if __slots__ is ever added. Field defaults to False so no existing
    # code that doesn't set it is affected.
    retry_triggered: bool = False


def _classify_http_error(status_code: int, response_body: str = "") -> str:
    """Maps HTTP status codes to canonical error type strings."""
    if status_code == 429:
        return "rate_limit"
    if status_code == 401 or status_code == 403:
        return "auth_error"
    if status_code == 413 or status_code == 400:
        # Check if 400 is actually an unsupported feature (like tools on Groq)
        body_lower = response_body.lower()
        if "tool calling" in body_lower or "unsupported" in body_lower or "invalid_request_error" in body_lower:
            return "unsupported_feature"
        # 400 can be context_exceeded on some providers
        return "context_exceeded"
    if status_code >= 500:
        return "server_error"
    return "server_error"


async def _get_dynamic_timeout(provider: str, model_id: str, tier: str) -> float:
    """
    Returns the effective timeout for this model attempt.
    If the model has historical latency data, uses min(tier_timeout, p90_approx * 1.5).
    p90 approximation: we use EMA * 2.0 as a cheap proxy since we don't store full
    latency distributions in this phase. Cold-start falls back to flat tier timeout.
    """
    tier_timeout = TIER_TIMEOUTS.get(tier, 5.0)

    async def _fetch():
        val = await redis_client.get(f"model:{provider}:{model_id}:latency_ema")
        return float(val) if val else None

    try:
        latency_ema_ms = await _safe_execute(_fetch())
    except Exception:
        latency_ema_ms = None

    if latency_ema_ms and latency_ema_ms > 0:
        # Convert ms to seconds; use 2× EMA as a p90 proxy
        dynamic_timeout = (latency_ema_ms / 1000.0) * 2.0
        return min(tier_timeout, dynamic_timeout)

    return tier_timeout  # cold start — use flat tier budget


async def call_candidate(
    provider: str,
    model_id: str,
    messages: list,
    max_tokens: int,
    tier: str,
    tools: Optional[list] = None,
    tool_whitelist: Optional[list] = None,
    expected_tool_schema: Optional[dict] = None,
    timeout_override: Optional[float] = None,
    temperature: Optional[float] = None,
) -> RaceResult:
    """
    Makes a single HTTP call to one LLM candidate, validates the response, and
    updates all Redis state (latency, success/failure, circuit breaker).

    Error routing:
    - 429 / quota hits  → increment_quota_usage path; record_failure NOT called
    - All other errors  → record_failure + record_circuit_failure
    - Validation failure (HTTP 200 but bad content) → treated as a real failure
    """
    start = time.monotonic()
    http = get_http_client()

    effective_timeout = timeout_override or await _get_dynamic_timeout(provider, model_id, tier)

    # BUG-06 integration: get_endpoint_url raises ValueError for providers with
    # empty base_url (Tier 2/3 providers not yet fully configured). Catch it here
    # so it surfaces as a clean "not_configured" error rather than a crash.
    try:
        url = get_endpoint_url(provider, model_id)
    except ValueError as ve:
        logger.error(f"[{provider}/{model_id}] Configuration error: {ve}")
        return RaceResult(
            success=False, content=None, tool_calls=None,
            model_used=model_id, provider_used=provider,
            latency_ms=0.0, error_type="not_configured",
        )

    try:
        # Update last used timestamp for prober tracking
        await update_last_used(provider, model_id)

        # Track RPM usage before the call
        await increment_rpm(provider, model_id)

        headers = get_auth_headers(provider)
        headers["Content-Type"] = "application/json"
        body = build_request(provider, model_id, messages, max_tokens, tools, temperature=temperature)

        # asyncio.wait_for gives us a hard wall-clock deadline. When it fires,
        # CancelledError propagates into the httpx await, closing the TCP connection.
        raw = await asyncio.wait_for(
            http.post(url, json=body, headers=headers),
            timeout=effective_timeout,
        )

        latency_ms = (time.monotonic() - start) * 1000

        # ── Error status codes ──────────────────────────────────────────────
        if raw.status_code == 429:
            # Rate limit / quota hit — do NOT lower quality score
            logger.warning(f"[{provider}/{model_id}] 429 rate limit")
            # increment_rpm already called above; quota tracking is caller's
            # responsibility at higher level (we don't know tokens_used yet)
            return RaceResult(
                success=False, content=None, tool_calls=None,
                model_used=model_id, provider_used=provider,
                latency_ms=latency_ms, error_type="rate_limit",
            )

        if raw.status_code != 200:
            if raw.status_code == 400:
                logger.error(f"[{provider}/{model_id}] HTTP 400 Body: {raw.text}")
            error_type = _classify_http_error(raw.status_code, raw.text)
            logger.warning(f"[{provider}/{model_id}] HTTP {raw.status_code} → {error_type}")
            await record_failure(provider, model_id, error_type)
            await record_circuit_failure(provider, model_id)
            return RaceResult(
                success=False, content=None, tool_calls=None,
                model_used=model_id, provider_used=provider,
                latency_ms=latency_ms, error_type=error_type,
            )

        # ── Parse response ──────────────────────────────────────────────────
        try:
            parsed = parse_response(provider, raw.json())
        except ParseError as exc:
            logger.warning(f"[{provider}/{model_id}] ParseError: {exc}")
            await record_failure(provider, model_id, "parse_error")
            await record_circuit_failure(provider, model_id)
            return RaceResult(
                success=False, content=None, tool_calls=None,
                model_used=model_id, provider_used=provider,
                latency_ms=latency_ms, error_type="parse_error",
            )

        # ── Validation gate ─────────────────────────────────────────────────
        is_valid, failure_reason = validate_response(
            parsed,
            expected_tool_schema=expected_tool_schema,
            tool_whitelist=tool_whitelist,
        )
        
        usage = parsed.get("usage", {})
        prompt_tokens = usage.get("prompt_tokens")
        completion_tokens = usage.get("completion_tokens")
        total_tokens = None
        if prompt_tokens is not None and completion_tokens is not None:
            total_tokens = prompt_tokens + completion_tokens
            
        if not is_valid:
            logger.warning(f"[{provider}/{model_id}] Validation failed: {failure_reason}")
            # HTTP 200 but bad content → treated as a real failure
            await record_failure(provider, model_id, failure_reason)
            await record_circuit_failure(provider, model_id)
            return RaceResult(
                success=False, content=parsed.get("content"), tool_calls=None,
                model_used=model_id, provider_used=provider,
                latency_ms=latency_ms, error_type=failure_reason,
                prompt_tokens=prompt_tokens, completion_tokens=completion_tokens, total_tokens=total_tokens,
                validation_rejections=failure_reason
            )

        # ── Success ─────────────────────────────────────────────────────────
        await update_latency(provider, model_id, latency_ms)
        await record_success(provider, model_id)
        await reset_circuit(provider, model_id)
        await increment_tier_requests(tier)

        logger.info(f"[{provider}/{model_id}] SUCCESS in {latency_ms:.0f}ms")
        return RaceResult(
            success=True,
            content=parsed.get("content"),
            tool_calls=parsed.get("tool_calls"),
            model_used=model_id,
            provider_used=provider,
            latency_ms=latency_ms,
            error_type=None,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens
        )

    except asyncio.TimeoutError:
        latency_ms = (time.monotonic() - start) * 1000
        logger.warning(f"[{provider}/{model_id}] Timeout after {latency_ms:.0f}ms")
        await record_failure(provider, model_id, "timeout")
        await record_circuit_failure(provider, model_id)
        return RaceResult(
            success=False, content=None, tool_calls=None,
            model_used=model_id, provider_used=provider,
            latency_ms=latency_ms, error_type="timeout",
        )

    except asyncio.CancelledError:
        # Task was cancelled by the race winner — do not record as a failure,
        # this is expected and normal behaviour during parallel racing.
        latency_ms = (time.monotonic() - start) * 1000
        logger.debug(f"[{provider}/{model_id}] Cancelled (another candidate won)")
        raise  # must re-raise CancelledError so asyncio task machinery works correctly

    except httpx.ConnectError as exc:
        latency_ms = (time.monotonic() - start) * 1000
        logger.warning(f"[{provider}/{model_id}] Network error: {exc}")
        await record_failure(provider, model_id, "network_error")
        await record_circuit_failure(provider, model_id)
        return RaceResult(
            success=False, content=None, tool_calls=None,
            model_used=model_id, provider_used=provider,
            latency_ms=latency_ms, error_type="network_error",
        )

    except Exception as exc:
        latency_ms = (time.monotonic() - start) * 1000
        logger.error(f"[{provider}/{model_id}] Unexpected error: {exc}", exc_info=True)
        await record_failure(provider, model_id, "server_error")
        await record_circuit_failure(provider, model_id)
        return RaceResult(
            success=False, content=None, tool_calls=None,
            model_used=model_id, provider_used=provider,
            latency_ms=latency_ms, error_type="server_error",
        )


async def _race_parallel(
    candidates: list,
    messages: list,
    max_tokens: int,
    tier: str,
    tools: Optional[list],
    tool_whitelist: Optional[list],
    expected_tool_schema: Optional[dict],
) -> RaceResult:
    """
    Races up to 3 candidates in parallel. Returns the first one to succeed
    and pass validation. Immediately cancels all other in-flight tasks when a
    winner is found — this is real TCP-level cancellation, not just ignoring
    pending futures.
    """
    if not candidates:
        return RaceResult(
            success=False, content=None, tool_calls=None,
            model_used=None, provider_used=None,
            latency_ms=0.0, error_type="no_candidates",
        )

    timeout = TIER_TIMEOUTS.get(tier, 5.0)
    tasks = {}
    for c in candidates[:3]:
        task = asyncio.create_task(
            call_candidate(
                c["provider"], c["model_id"], messages, max_tokens, tier,
                tools=tools, tool_whitelist=tool_whitelist,
                expected_tool_schema=expected_tool_schema,
                timeout_override=timeout,
            )
        )
        tasks[task] = c

    winner: Optional[RaceResult] = None
    all_failures: list[RaceResult] = []
    pending = set(tasks.keys())

    try:
        while pending:
            done, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                try:
                    result = task.result()
                    if result.success:
                        winner = result
                        # Cancel all remaining in-flight requests immediately
                        for p_task in pending:
                            p_task.cancel()
                        # Wait briefly for cancellations to propagate
                        if pending:
                            await asyncio.gather(*pending, return_exceptions=True)
                        pending = set()  # exit outer while
                        break
                    else:
                        all_failures.append(result)
                except asyncio.CancelledError:
                    pass
                except Exception as exc:
                    logger.error(f"Unexpected task error in race: {exc}", exc_info=True)
    except Exception as exc:
        logger.error(f"Race orchestration error: {exc}", exc_info=True)
        # Cancel anything still running
        for p_task in pending:
            p_task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

    if winner:
        return winner

    # All candidates failed — return the last failure result for the caller to log
    if all_failures:
        return all_failures[-1]

    return RaceResult(
        success=False, content=None, tool_calls=None,
        model_used=None, provider_used=None,
        latency_ms=0.0, error_type="all_candidates_failed",
    )


async def execute_race(
    tier: str,
    messages: list,
    max_tokens: Optional[int] = None,
    estimated_tokens: int = 0,
    tools: Optional[list] = None,
    tool_whitelist: Optional[list] = None,
    expected_tool_schema: Optional[dict] = None,
) -> RaceResult:
    """
    Main entry point for Phase 6/7 to call.

    Implements the full NVIDIA-first execution sequence for ONE pass through
    one tier's candidates. Does NOT implement the "retry the whole cascade once"
    logic — the caller wraps this with a single retry if needed.

    Sequence:
      1. get_candidates_with_cascade() — filtered, UCB1-ranked candidates
      2. split_nvidia_first() — NVIDIA vs others
      3. Try NVIDIA candidates sequentially (short timeout, single shot each)
      4. If NVIDIA exhausted, race others in parallel
      5. Return first valid winner, or failure if all fail
    """
    effective_max_tokens = max_tokens or TIER_MAX_TOKENS.get(tier, 800)

    # Step 1 — get candidates (router handles filtering, UCB1, cascade)
    candidate_result = await get_candidates_with_cascade(
        tier, k=3, estimated_tokens=estimated_tokens
    )
    actual_tier = candidate_result.tier
    candidates = candidate_result.candidates

    if not candidates:
        logger.warning(f"No candidates available for tier '{tier}' (cascaded to '{actual_tier}')")
        return RaceResult(
            success=False, content=None, tool_calls=None,
            model_used=None, provider_used=None,
            latency_ms=0.0, error_type="no_candidates",
        )

    # Use the actual tier's timeout and token budget (may differ if cascade fired)
    effective_max_tokens = max_tokens or TIER_MAX_TOKENS.get(actual_tier, 800)

    # Step 2 — split by NVIDIA priority
    nvidia_candidates, other_candidates = split_nvidia_first(candidates)

    # Step 3 — NVIDIA-first: try each NVIDIA candidate sequentially
    nvidia_attempted = False
    nvidia_succeeded = False
    validation_rejections = []
    
    for nv_candidate in nvidia_candidates:
        nvidia_attempted = True
        logger.info(
            f"NVIDIA-first attempt: {nv_candidate['provider']}/{nv_candidate['model_id']}"
        )
        result = await call_candidate(
            nv_candidate["provider"],
            nv_candidate["model_id"],
            messages,
            effective_max_tokens,
            actual_tier,
            tools=tools,
            tool_whitelist=tool_whitelist,
            expected_tool_schema=expected_tool_schema,
            timeout_override=NVIDIA_FIRST_TIMEOUT,
        )
        if result.validation_rejections:
            validation_rejections.append(f"{nv_candidate['provider']}:{result.validation_rejections}")
            
        if result.success:
            nvidia_succeeded = True
            result.nvidia_attempted = nvidia_attempted
            result.nvidia_succeeded = nvidia_succeeded
            result.validation_rejections = ",".join(validation_rejections) if validation_rejections else None
            return result
        # NVIDIA failed/timed out — try next NVIDIA candidate (if any), then fall through

    # Step 4 — fan out to other providers in parallel
    if other_candidates:
        logger.info(
            f"NVIDIA exhausted — racing {len(other_candidates[:3])} other candidates"
        )
        race_res = await _race_parallel(
            other_candidates,
            messages,
            effective_max_tokens,
            actual_tier,
            tools,
            tool_whitelist,
            expected_tool_schema,
        )
        if race_res.validation_rejections:
            validation_rejections.append(race_res.validation_rejections)
            
        race_res.nvidia_attempted = nvidia_attempted
        race_res.nvidia_succeeded = nvidia_succeeded
        race_res.validation_rejections = ",".join(validation_rejections) if validation_rejections else None
        return race_res

    return RaceResult(
        success=False, content=None, tool_calls=None,
        model_used=None, provider_used=None,
        latency_ms=0.0, error_type="all_candidates_failed",
        nvidia_attempted=nvidia_attempted,
        nvidia_succeeded=nvidia_succeeded,
        validation_rejections=",".join(validation_rejections) if validation_rejections else None
    )
