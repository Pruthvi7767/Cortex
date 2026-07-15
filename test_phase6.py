"""
test_phase6.py — Phase 6 testing for Auth & Logging.

Mocks the asyncpg queries to avoid external network dependencies.
Runs check_caller_rate_limit on a live local Redis instance.
"""

import asyncio
import hashlib
import sys
from unittest.mock import AsyncMock, patch, MagicMock

from auth import generate_api_key, verify_api_key, check_caller_rate_limit
from logger import log_request, get_recent_failures
from redis_store import client as redis_client

_results = []

def report(n: int, ok: bool, msg: str):
    status = "OK" if ok else "FAIL"
    line = f"{n}. [{status}] {msg}"
    _results.append((ok, line))
    print(line)


async def test1_generate_api_key():
    """generate_api_key produces unique keys and deterministic hashes."""
    key1, hash1 = generate_api_key()
    key2, hash2 = generate_api_key()

    ok1 = (key1 != key2)
    ok2 = (hash1 != hash2)
    
    # Deterministic check
    hash_check = hashlib.sha256(key1.encode()).hexdigest()
    ok3 = (hash_check == hash1)

    ok = ok1 and ok2 and ok3
    report(1, ok, f"generate_api_key works: unique={ok1}, deterministic={ok3}")


async def test2_verify_api_key_valid():
    """verify_api_key returns correct caller_id for valid active key."""
    raw_key, key_hash = generate_api_key()
    
    mock_pool = AsyncMock()
    mock_pool.fetchrow = AsyncMock(return_value={"caller_id": "alice", "rate_limit_per_minute": 100})
    
    with patch("auth.get_pool", return_value=mock_pool):
        result = await verify_api_key(raw_key)

    ok = (result is not None and result["caller_id"] == "alice" and result["rate_limit_per_minute"] == 100)
    report(2, ok, f"verify_api_key valid: result={result}")


async def test3_verify_api_key_invalid():
    """verify_api_key returns None for nonexistent key."""
    mock_pool = AsyncMock()
    mock_pool.fetchrow = AsyncMock(return_value=None)
    
    with patch("auth.get_pool", return_value=mock_pool):
        result = await verify_api_key("sk-cortex-doesnotexist")

    ok = (result is None)
    report(3, ok, f"verify_api_key invalid: result={result}")


async def test4_check_caller_rate_limit():
    """check_caller_rate_limit applies sliding window correctly via Redis."""
    caller_id = "test-caller-6"
    
    # Clean up any previous test state
    await redis_client.delete(f"caller:{caller_id}:rate_limit")
    
    rpm = 2
    
    res1 = await check_caller_rate_limit(caller_id, rpm)
    res2 = await check_caller_rate_limit(caller_id, rpm)
    res3 = await check_caller_rate_limit(caller_id, rpm)
    
    # 1 and 2 should pass, 3 should fail
    ok = (res1 is True) and (res2 is True) and (res3 is False)
    report(4, ok, f"check_caller_rate_limit: {res1}, {res2}, {res3} (expected True, True, False)")


async def test5_log_request():
    """log_request inserts record asynchronously without raising."""
    mock_pool = AsyncMock()
    mock_pool.execute = AsyncMock(return_value=None)
    
    with patch("logger.get_pool", return_value=mock_pool):
        await log_request(
            request_id="req-123",
            caller_id="test-caller-6",
            tier_requested="fast",
            tier_source="auto",
            provider_used="groq",
            model_used="llama-3-8b",
            latency_ms=450,
            success=True
        )
        
    # Give the task a moment to execute if it was launched via create_task in the real world
    # Since we await it directly in the test, it finishes synchronously
    called = mock_pool.execute.called
    report(5, called, f"log_request triggered db execute: called={called}")


async def test6_log_request_handles_failure():
    """log_request swallows database errors gracefully."""
    mock_pool = AsyncMock()
    mock_pool.execute = AsyncMock(side_effect=Exception("Simulated DB failure"))
    
    raised = False
    try:
        with patch("logger.get_pool", return_value=mock_pool):
            await log_request(
                request_id="req-fail-123",
                caller_id="test",
                tier_requested="strong",
                tier_source="manual",
                provider_used=None,
                model_used=None,
                latency_ms=None,
                success=False,
                error_type="timeout"
            )
    except Exception:
        raised = True
        
    ok = not raised
    report(6, ok, f"log_request swallows errors: raised={raised}")


async def test7_get_recent_failures():
    """get_recent_failures retrieves properly ordered failed logs."""
    mock_pool = AsyncMock()
    # Mocking rows directly; they need to act like dicts, so we can just return standard dicts.
    mock_pool.fetch = AsyncMock(return_value=[
        {"request_id": "req-1", "success": False, "error_type": "timeout"},
        {"request_id": "req-2", "success": False, "error_type": "parse_error"}
    ])
    
    with patch("logger.get_pool", return_value=mock_pool):
        failures = await get_recent_failures(5)
        
    ok = (len(failures) == 2 and failures[0]["error_type"] == "timeout")
    report(7, ok, f"get_recent_failures retrieved {len(failures)} logs")


async def run_all():
    print("=== Running Phase 6 Tests ===")
    await test1_generate_api_key()
    await test2_verify_api_key_valid()
    await test3_verify_api_key_invalid()
    await test4_check_caller_rate_limit()
    await test5_log_request()
    await test6_log_request_handles_failure()
    await test7_get_recent_failures()
    
    failures = [r for r in _results if not r[0]]
    if failures:
        print(f"\\n[FAIL] {len(failures)} tests failed.")
        sys.exit(1)
    else:
        print("\\n[SUCCESS] All 7 tests passed.")
        sys.exit(0)


if __name__ == "__main__":
    asyncio.run(run_all())
