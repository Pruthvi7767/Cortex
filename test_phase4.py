"""
test_phase4.py — Phase 4 validation using mocked HTTP responses.

All 8 tests use unittest.mock to intercept httpx calls so zero real API quota
is consumed. Tests verify:
  1. Successful call → record_success + update_latency called
  2. 429 → quota path, record_failure NOT called
  3. Timeout → record_failure + circuit breaker eventually trips
  4. HTTP 200 but empty content → validation failure, NOT treated as success
  5. execute_race() NVIDIA success → other providers never called
  6. execute_race() NVIDIA timeout → falls through to other candidates
  7. execute_race() parallel race → loser tasks are genuinely cancelled
  8. execute_race() all fail → clean failure result, no hang
"""

import asyncio
import sys
import unittest
from unittest.mock import AsyncMock, MagicMock, patch
import httpx

# ── Helpers ────────────────────────────────────────────────────────────────

def make_http_response(status_code: int, body: dict) -> httpx.Response:
    """Builds a minimal fake httpx.Response for mocking."""
    import json
    content = json.dumps(body).encode()
    return httpx.Response(
        status_code=status_code,
        headers={"content-type": "application/json"},
        content=content,
        request=httpx.Request("POST", "https://fake.provider/v1/chat/completions"),
    )


def success_body(content_text: str = "Hello, world!") -> dict:
    return {
        "choices": [{"message": {"content": content_text, "tool_calls": None}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 20},
    }


def empty_body() -> dict:
    """HTTP 200 but empty content — should fail validation."""
    return {
        "choices": [{"message": {"content": "", "tool_calls": None}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 0},
    }


FAKE_MESSAGES = [{"role": "user", "content": "Say hello"}]

# ── Test runner helpers ────────────────────────────────────────────────────

_results = []

def report(n: int, ok: bool, msg: str):
    status = "OK" if ok else "FAIL"
    line = f"{n}. [{status}] {msg}"
    _results.append((ok, line))
    print(line)


# ── Individual tests ───────────────────────────────────────────────────────

async def test1_successful_call():
    """call_candidate() parses success, calls record_success + update_latency."""
    from race import call_candidate, get_http_client

    call_count = {"record_success": 0, "update_latency": 0}

    async def fake_record_success(p, m):
        call_count["record_success"] += 1

    async def fake_update_latency(p, m, lat):
        call_count["update_latency"] += 1

    fake_response = make_http_response(200, success_body("Hello from nvidia"))

    with patch("race.get_http_client") as mock_client_factory, \
         patch("race.record_success", side_effect=fake_record_success), \
         patch("race.update_latency", side_effect=fake_update_latency), \
         patch("race.record_failure", new_callable=AsyncMock), \
         patch("race.record_circuit_failure", new_callable=AsyncMock), \
         patch("race.increment_rpm", new_callable=AsyncMock), \
         patch("race.increment_tier_requests", new_callable=AsyncMock), \
         patch("race.reset_circuit", new_callable=AsyncMock), \
         patch("race._get_dynamic_timeout", new_callable=AsyncMock, return_value=5.0):

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=fake_response)
        mock_client_factory.return_value = mock_client

        result = await call_candidate("nvidia", "openai/gpt-oss-120b", FAKE_MESSAGES, 800, "strong")

    ok = (result.success
          and result.content == "Hello from nvidia"
          and call_count["record_success"] == 1
          and call_count["update_latency"] == 1)
    report(1, ok, f"Success path: success={result.success}, record_success={call_count['record_success']}, update_latency={call_count['update_latency']}")


async def test2_rate_limit_429():
    """call_candidate() on 429: record_failure must NOT be called."""
    from race import call_candidate

    failure_call_count = {"n": 0}

    async def fake_record_failure(p, m, err):
        failure_call_count["n"] += 1

    fake_response = make_http_response(429, {"error": "rate limited"})

    with patch("race.get_http_client") as mock_client_factory, \
         patch("race.record_failure", side_effect=fake_record_failure), \
         patch("race.record_success", new_callable=AsyncMock), \
         patch("race.update_latency", new_callable=AsyncMock), \
         patch("race.record_circuit_failure", new_callable=AsyncMock), \
         patch("race.increment_rpm", new_callable=AsyncMock), \
         patch("race.increment_tier_requests", new_callable=AsyncMock), \
         patch("race.reset_circuit", new_callable=AsyncMock), \
         patch("race._get_dynamic_timeout", new_callable=AsyncMock, return_value=5.0):

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=fake_response)
        mock_client_factory.return_value = mock_client

        result = await call_candidate("groq", "llama-3.3-70b-versatile", FAKE_MESSAGES, 800, "strong")

    ok = (not result.success
          and result.error_type == "rate_limit"
          and failure_call_count["n"] == 0)
    report(2, ok, f"429 path: error_type={result.error_type}, record_failure calls={failure_call_count['n']} (must be 0)")


async def test3_timeout_trips_circuit():
    """call_candidate() on repeated timeouts eventually trips the circuit breaker."""
    from race import call_candidate
    from redis_store import client as redis_client
    from circuit_breaker import get_circuit_state, reset_circuit as cb_reset

    provider, model_id = "groq", "timeout-test-model"

    # Clean up
    keys = await redis_client.keys(f"*{provider}:{model_id}*")
    if keys:
        await redis_client.delete(*keys)

    async def raise_timeout(*args, **kwargs):
        raise asyncio.TimeoutError()

    with patch("race.get_http_client") as mock_client_factory, \
         patch("race.increment_rpm", new_callable=AsyncMock), \
         patch("race.increment_tier_requests", new_callable=AsyncMock), \
         patch("race.reset_circuit", new_callable=AsyncMock), \
         patch("race._get_dynamic_timeout", new_callable=AsyncMock, return_value=0.001):

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(side_effect=asyncio.TimeoutError())
        mock_client_factory.return_value = mock_client

        # FAILURE_THRESHOLD in circuit_breaker.py is 2
        for _ in range(3):
            await call_candidate(provider, model_id, FAKE_MESSAGES, 800, "strong")

    state = await get_circuit_state(provider, model_id)
    ok = (state == "OPEN")
    report(3, ok, f"Timeout trips circuit: state={state} (expected OPEN)")

    # cleanup
    await cb_reset(provider, model_id)


async def test4_empty_200_is_failure():
    """HTTP 200 with empty content fails validation and is NOT a success."""
    from race import call_candidate

    failure_called = {"n": 0}

    async def fake_record_failure(p, m, err):
        failure_called["n"] += 1

    fake_response = make_http_response(200, empty_body())

    with patch("race.get_http_client") as mock_client_factory, \
         patch("race.record_failure", side_effect=fake_record_failure), \
         patch("race.record_success", new_callable=AsyncMock) as mock_success, \
         patch("race.update_latency", new_callable=AsyncMock), \
         patch("race.record_circuit_failure", new_callable=AsyncMock), \
         patch("race.increment_rpm", new_callable=AsyncMock), \
         patch("race.increment_tier_requests", new_callable=AsyncMock), \
         patch("race.reset_circuit", new_callable=AsyncMock), \
         patch("race._get_dynamic_timeout", new_callable=AsyncMock, return_value=5.0):

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=fake_response)
        mock_client_factory.return_value = mock_client

        result = await call_candidate("groq", "llama-3.3-70b-versatile", FAKE_MESSAGES, 800, "strong")

    ok = (not result.success
          and result.error_type == "empty"
          and failure_called["n"] == 1
          and mock_success.call_count == 0)
    report(4, ok, f"Empty 200: success={result.success}, error_type={result.error_type}, record_failure={failure_called['n']}, record_success={mock_success.call_count}")


async def test5_nvidia_success_no_other_calls():
    """execute_race() NVIDIA wins → other providers are never called."""
    from race import execute_race

    call_log = []

    async def fake_call_candidate(provider, model_id, *args, **kwargs):
        from race import RaceResult
        call_log.append(provider)
        if provider == "nvidia":
            return RaceResult(
                success=True, content="NVIDIA answer", tool_calls=None,
                model_used=model_id, provider_used="nvidia",
                latency_ms=120.0, error_type=None,
            )
        # Should never be reached
        return RaceResult(
            success=False, content=None, tool_calls=None,
            model_used=model_id, provider_used=provider,
            latency_ms=0.0, error_type="server_error",
        )

    fake_candidates = [
        {"provider": "nvidia", "model_id": "openai/gpt-oss-120b", "max_context": 128000},
        {"provider": "groq",   "model_id": "llama-3.3-70b-versatile", "max_context": 128000},
        {"provider": "mistral","model_id": "mistral-large-latest", "max_context": 128000},
    ]

    from router import CandidateResult
    with patch("race.get_candidates_with_cascade",
               new_callable=AsyncMock,
               return_value=CandidateResult(tier="strong", candidates=fake_candidates)), \
         patch("race.call_candidate", side_effect=fake_call_candidate):

        result = await execute_race("strong", FAKE_MESSAGES)

    nvidia_calls = call_log.count("nvidia")
    other_calls = sum(1 for p in call_log if p != "nvidia")
    ok = (result.success and nvidia_calls == 1 and other_calls == 0)
    report(5, ok, f"NVIDIA wins first: success={result.success}, nvidia_calls={nvidia_calls}, other_calls={other_calls} (must be 0)")


async def test6_nvidia_timeout_falls_through():
    """execute_race() NVIDIA times out → races other candidates."""
    from race import execute_race, RaceResult

    call_log = []

    async def fake_call_candidate(provider, model_id, *args, **kwargs):
        call_log.append(provider)
        if provider == "nvidia":
            return RaceResult(
                success=False, content=None, tool_calls=None,
                model_used=model_id, provider_used="nvidia",
                latency_ms=2000.0, error_type="timeout",
            )
        return RaceResult(
            success=True, content="Non-NVIDIA fallback answer", tool_calls=None,
            model_used=model_id, provider_used=provider,
            latency_ms=300.0, error_type=None,
        )

    fake_candidates = [
        {"provider": "nvidia", "model_id": "openai/gpt-oss-120b", "max_context": 128000},
        {"provider": "groq",   "model_id": "llama-3.3-70b-versatile", "max_context": 128000},
    ]

    from router import CandidateResult
    with patch("race.get_candidates_with_cascade",
               new_callable=AsyncMock,
               return_value=CandidateResult(tier="strong", candidates=fake_candidates)), \
         patch("race.call_candidate", side_effect=fake_call_candidate):

        result = await execute_race("strong", FAKE_MESSAGES)

    ok = (result.success and "nvidia" in call_log and "groq" in call_log)
    report(6, ok, f"NVIDIA timeout fallthrough: success={result.success}, providers tried={call_log}")


async def test7_parallel_race_losers_cancelled():
    """execute_race() parallel race: losing tasks are actually cancelled."""
    from race import _race_parallel, RaceResult

    cancel_log = []
    started_log = []

    async def make_candidate_fn(provider, delay, should_succeed):
        async def fake_call(prov, model_id, *args, **kwargs):
            started_log.append(prov)
            try:
                await asyncio.sleep(delay)
                return RaceResult(
                    success=should_succeed, content="Answer" if should_succeed else None,
                    tool_calls=None, model_used=model_id, provider_used=prov,
                    latency_ms=delay * 1000, error_type=None if should_succeed else "server_error",
                )
            except asyncio.CancelledError:
                cancel_log.append(prov)
                raise  # must re-raise
        return fake_call

    # candidate A: wins quickly, B and C: slow (should be cancelled)
    candidates = [
        {"provider": "groq",    "model_id": "llama-3.3-70b-versatile", "max_context": 128000},
        {"provider": "mistral", "model_id": "mistral-large-latest",     "max_context": 128000},
        {"provider": "cerebras","model_id": "gpt-oss-120b",             "max_context": 128000},
    ]

    call_counts = {"groq": 0, "mistral": 0, "cerebras": 0}

    async def fake_call_candidate(provider, model_id, *args, **kwargs):
        started_log.append(provider)
        call_counts[provider] = call_counts.get(provider, 0) + 1
        try:
            if provider == "groq":
                await asyncio.sleep(0.05)  # wins fast
                return RaceResult(
                    success=True, content="Groq wins", tool_calls=None,
                    model_used=model_id, provider_used=provider,
                    latency_ms=50.0, error_type=None,
                )
            else:
                await asyncio.sleep(10.0)  # slow — should be cancelled
                return RaceResult(
                    success=True, content="slow answer", tool_calls=None,
                    model_used=model_id, provider_used=provider,
                    latency_ms=10000.0, error_type=None,
                )
        except asyncio.CancelledError:
            cancel_log.append(provider)
            raise

    with patch("race.call_candidate", side_effect=fake_call_candidate):
        result = await _race_parallel(candidates, FAKE_MESSAGES, 800, "strong", None, None, None)

    cancelled_count = len(cancel_log)
    ok = (result.success
          and result.provider_used == "groq"
          and cancelled_count >= 1)  # at least the slow ones should be cancelled
    report(7, ok, f"Parallel race cancellation: winner={result.provider_used}, cancelled={cancel_log} (must have >=1)")


async def test8_all_fail_clean_result():
    """execute_race() all candidates fail → returns clean failure, no hang."""
    from race import execute_race, RaceResult

    async def fake_call_candidate(provider, model_id, *args, **kwargs):
        return RaceResult(
            success=False, content=None, tool_calls=None,
            model_used=model_id, provider_used=provider,
            latency_ms=100.0, error_type="server_error",
        )

    fake_candidates = [
        {"provider": "groq",    "model_id": "llama-3.3-70b-versatile", "max_context": 128000},
        {"provider": "mistral", "model_id": "mistral-large-latest",     "max_context": 128000},
    ]

    from router import CandidateResult
    with patch("race.get_candidates_with_cascade",
               new_callable=AsyncMock,
               return_value=CandidateResult(tier="strong", candidates=fake_candidates)), \
         patch("race.call_candidate", side_effect=fake_call_candidate):

        result = await execute_race("strong", FAKE_MESSAGES)

    ok = (not result.success and result.error_type is not None)
    report(8, ok, f"All fail: success={result.success}, error_type={result.error_type}")


# ── Main ───────────────────────────────────────────────────────────────────

async def run_all():
    print("=== Phase 4 Test Script ===\n")

    # Check Redis is live (tests 1, 2, 4 mock HTTP but still use Redis for state)
    from redis_store import client as redis_client
    try:
        await redis_client.ping()
        print("[setup] Redis connection OK\n")
    except Exception as e:
        print(f"[FAIL] Redis unreachable: {e}")
        return

    await test1_successful_call()
    await test2_rate_limit_429()
    await test3_timeout_trips_circuit()
    await test4_empty_200_is_failure()
    await test5_nvidia_success_no_other_calls()
    await test6_nvidia_timeout_falls_through()
    await test7_parallel_race_losers_cancelled()
    await test8_all_fail_clean_result()

    print("\n=== Summary ===")
    passed = sum(1 for ok, _ in _results if ok)
    failed = sum(1 for ok, _ in _results if not ok)
    for _, line in _results:
        print(line)
    print(f"\n{passed}/{len(_results)} tests passed")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(run_all())
