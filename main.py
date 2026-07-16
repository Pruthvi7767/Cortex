import asyncio
import json
import uuid
import time
import datetime
from typing import Optional, List, Dict, Any
from contextlib import asynccontextmanager

from fastapi import FastAPI, Depends, HTTPException, Security, Request, Header
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel, Field

from config import get_active_providers, assert_providers_configured, environment, MAX_PROMPT_CHARS, MODEL_REGISTRY
from auth import verify_api_key, check_caller_rate_limit
from classifier import resolve_tier, VALID_TIERS
from race import execute_race, close_http_client
from logger import log_request
from redis_store import client as redis_client
from db import init_db, close_db
from circuit_breaker import get_circuit_state
from background_prober import prober_loop

# --- Pydantic Models ---
class ToolSchema(BaseModel):
    name: str
    description: Optional[str] = None
    parameters: Optional[Dict[str, Any]] = None

class CompleteRequest(BaseModel):
    prompt: str
    tier: Optional[str] = None
    context_history: Optional[List[Dict[str, str]]] = None
    system_prompt: Optional[str] = None
    max_tokens: Optional[int] = None
    tools: Optional[List[ToolSchema]] = None
    tool_whitelist: Optional[List[str]] = None
    expected_tool_schema: Optional[Dict[str, Any]] = None
    idempotency_key: Optional[str] = None

security = HTTPBearer()

async def get_caller(credentials: HTTPAuthorizationCredentials = Security(security)):
    raw_key = credentials.credentials
    try:
        caller_info = await verify_api_key(raw_key)
    except Exception as e:
        logger.error(f"Infrastructure error during auth: {e}")
        raise HTTPException(status_code=503, detail="Authentication service unavailable")

    if not caller_info:
        raise HTTPException(status_code=401, detail="Invalid API Key")
    return caller_info

async def check_rate_limit(caller_info: dict = Depends(get_caller)):
    caller_id = caller_info["caller_id"]
    rpm = caller_info["rate_limit_per_minute"]
    is_allowed = await check_caller_rate_limit(caller_id, rpm)
    if not is_allowed:
        raise HTTPException(status_code=429, detail="Rate limit exceeded")
    return caller_info

async def require_admin(caller_info: dict = Depends(get_caller)):
    """
    BUG-13 FIX: Dependency that enforces admin privilege.
    Previously admin endpoints only required a valid API key (any caller).
    Now checks the is_admin field returned by verify_api_key().
    """
    if not caller_info.get("is_admin", False):
        raise HTTPException(status_code=403, detail="Admin privilege required")
    return caller_info

async def quota_reset_loop():
    logger.info("Background quota reset loop started.")
    from redis_store import set_quota_reset_time
    while True:
        try:
            for tier, models in MODEL_REGISTRY.items():
                for m in models:
                    await set_quota_reset_time(m["provider"], m["model_id"])
            
            now = datetime.datetime.now(datetime.timezone.utc)
            tomorrow = now + datetime.timedelta(days=1)
            midnight = datetime.datetime(tomorrow.year, tomorrow.month, tomorrow.day, tzinfo=datetime.timezone.utc)
            sleep_secs = (midnight - now).total_seconds() + 1
            await asyncio.sleep(sleep_secs)
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Quota loop error: {e}")
            await asyncio.sleep(3600)

async def thresholds_update_loop():
    logger.info("Background thresholds update loop started.")
    from pulse_learner import update_thresholds
    while True:
        try:
            await update_thresholds()
            await asyncio.sleep(6 * 3600)
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Thresholds update loop error: {e}")
            await asyncio.sleep(3600)

@asynccontextmanager
async def lifespan(app: FastAPI):
    assert_providers_configured()
    await init_db()
    # Start the background tasks
    prober_task = asyncio.create_task(prober_loop())
    quota_task = asyncio.create_task(quota_reset_loop())
    threshold_task = asyncio.create_task(thresholds_update_loop())
    yield
    # Graceful shutdown
    prober_task.cancel()
    quota_task.cancel()
    threshold_task.cancel()
    try:
        await asyncio.gather(prober_task, quota_task, threshold_task, return_exceptions=True)
    except Exception:
        pass
    await close_db()
    await close_http_client()

app = FastAPI(lifespan=lifespan)

@app.get("/health")
async def health_check():
    active = get_active_providers()

    # Get circuit breaker summary for any non-CLOSED breakers
    breaker_summary = {}
    for tier, models in MODEL_REGISTRY.items():
        for m in models:
            provider = m["provider"]
            model_id = m["model_id"]
            state = await get_circuit_state(provider, model_id)
            if state != "CLOSED":
                breaker_summary[f"{provider}:{model_id}"] = state

    return {
        "status": "ok",
        "environment": environment,
        "active_providers_count": len(active),
        "active_providers": [p["id"] for p in active],
        "circuit_breakers_open": breaker_summary,
    }

@app.post("/admin/reload-config")
async def reload_config(caller_info: dict = Depends(require_admin)):
    """
    BUG-13 FIX: Now requires is_admin=true on the API key.
    Old version accepted any valid API key for this admin operation.
    """
    assert_providers_configured()
    return {"status": "reloaded", "active_providers": [p["id"] for p in get_active_providers()]}


async def execute_with_retry(
    tier: str,
    messages: list,
    max_tokens: Optional[int],
    estimated_tokens: int,
    tools: Optional[list],
    tool_whitelist: Optional[list],
    expected_tool_schema: Optional[dict],
) -> object:
    # Pass 1
    result = await execute_race(
        tier, messages, max_tokens, estimated_tokens,
        tools, tool_whitelist, expected_tool_schema,
    )
    if result.success:
        return result

    # Full cascade failed — wait 2s then retry once
    await asyncio.sleep(2.0)

    # Pass 2
    retry_result = await execute_race(
        tier, messages, max_tokens, estimated_tokens,
        tools, tool_whitelist, expected_tool_schema,
    )
    # BUG-08 FIX: retry_triggered is now a proper dataclass field — no more
    # dynamic attribute assignment.
    retry_result.retry_triggered = True
    return retry_result


@app.post("/v1/complete")
async def complete_endpoint(req: CompleteRequest, caller_info: dict = Depends(check_rate_limit)):
    request_id = str(uuid.uuid4())

    # ── Input validation ──────────────────────────────────────────────────────
    if not req.prompt or not req.prompt.strip():
        raise HTTPException(status_code=400, detail="Prompt is missing or empty")

    if len(req.prompt) > MAX_PROMPT_CHARS:
        raise HTTPException(
            status_code=413,
            detail=f"Prompt exceeds maximum character limit of {MAX_PROMPT_CHARS}",
        )

    # BUG-11 FIX: Validate the manual tier override before it reaches resolve_tier().
    # Old code silently passed any string through; get_candidates_with_cascade would
    # log a warning and default to "strong", but the caller got back an unexpected tier.
    if req.tier is not None and req.tier not in VALID_TIERS:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid tier '{req.tier}'. Must be one of: {sorted(VALID_TIERS)}",
        )

    # ── Idempotency check ─────────────────────────────────────────────────────
    # BUG-07 FIX: Store and replay the completed response on duplicate calls.
    # Old code stored "processing" and never updated it, so the second call with
    # the same idempotency_key always got a 409 even after the first completed.
    idem_key = None
    if req.idempotency_key:
        idem_key = f"idempotency:{req.idempotency_key}"
        try:
            existing_raw = await redis_client.get(idem_key)

            if existing_raw:
                if existing_raw == "processing":
                    # First request is still in-flight — reject duplicate correctly
                    raise HTTPException(
                        status_code=409,
                        detail="Request with this idempotency_key is currently being processed",
                    )
                # First request completed successfully — replay the cached response
                try:
                    cached_response = json.loads(existing_raw)
                    cached_response["_cached"] = True
                    return cached_response
                except (json.JSONDecodeError, TypeError):
                    # Corrupted cache entry — proceed as fresh request
                    pass

            # Mark as in-flight (short TTL to handle crashes)
            await redis_client.set(idem_key, "processing", ex=30)
        except HTTPException:
            raise
        except Exception as e:
            # Graceful degradation: if Redis is down, log error and proceed
            import logging
            logging.getLogger(__name__).warning(f"Idempotency Redis error (get/set): {e}")

    # ── Tier resolution ───────────────────────────────────────────────────────
    context_dict = {"history": req.context_history} if req.context_history else {}
    
    # Tool formatting: convert to dict early for classifier and downstream execution
    tools_list = [t.model_dump(exclude_none=True) for t in req.tools] if req.tools else None
    
    resolved_tier, decision_score, used_llm_classifier = await resolve_tier(
        prompt=req.prompt,
        explicit_tier=req.tier,
        context=context_dict,
        tools=tools_list,
        caller_id=caller_info["caller_id"]
    )
    tier_source = "manual" if req.tier else "auto"

    # Token estimation: divide by 3 (safer than /4) to prevent underestimating
    context_str = " ".join(
        [m.get("content", "") for m in (req.context_history or []) if isinstance(m, dict)]
    )
    estimated_tokens = len(req.prompt + context_str) // 3

    # ── Message construction ──────────────────────────────────────────────────
    messages = []
    if req.system_prompt:
        messages.append({"role": "system", "content": req.system_prompt})
    if req.context_history:
        messages.extend(req.context_history)
    messages.append({"role": "user", "content": req.prompt})

    # ── Execute ───────────────────────────────────────────────────────────────
    result = await execute_with_retry(
        tier=resolved_tier,
        messages=messages,
        max_tokens=req.max_tokens,
        estimated_tokens=estimated_tokens,
        tools=tools_list,
        tool_whitelist=req.tool_whitelist,
        expected_tool_schema=req.expected_tool_schema,
    )

    # ── Fire-and-forget logging ───────────────────────────────────────────────
    asyncio.create_task(log_request(
        request_id=request_id,
        caller_id=caller_info["caller_id"],
        tier_requested=resolved_tier,
        tier_source=tier_source,
        provider_used=result.provider_used,
        model_used=result.model_used,
        latency_ms=int(result.latency_ms),
        success=result.success,
        error_type=result.error_type,
        prompt_tokens=result.prompt_tokens,
        completion_tokens=result.completion_tokens,
        total_tokens=result.total_tokens,
        decision_score=decision_score,
        nvidia_attempted=result.nvidia_attempted,
        nvidia_succeeded=result.nvidia_succeeded,
        validation_rejections=result.validation_rejections,
    ))

    if not result.success:
        raise HTTPException(
            status_code=500,
            detail={
                "error": "All candidates failed",
                "error_type": result.error_type,
            },
        )

    # ── Build response ────────────────────────────────────────────────────────
    response_payload = {
        "request_id": request_id,
        "content": result.content,
        "model_used": result.model_used,
        "provider_used": result.provider_used,
        "tier_used": resolved_tier,
        "telemetry": {
            "decision_score": decision_score,
            "used_llm_classifier": used_llm_classifier,
            "retry_triggered": result.retry_triggered,
        },
    }

    if result.tool_calls:
        response_payload["tool_calls"] = result.tool_calls

    # BUG-07 FIX: Cache the successful response for idempotency replay.
    # Store with a 24-hour TTL — long enough to handle retries, short enough to
    # not accumulate stale entries. Use a separate longer TTL than the "processing"
    # marker (30s) to avoid eviction before clients can retry.
    if idem_key:
        try:
            await redis_client.set(idem_key, json.dumps(response_payload), ex=86400)
        except Exception:
            pass  # Caching failure must never block the response

    return response_payload
