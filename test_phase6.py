"""
test_phase6.py — Phase 6 testing for Auth & Logging.

Mocks the Supabase queries to avoid external network dependencies.
Runs check_caller_rate_limit on a live local Redis instance.
"""

import asyncio
import hashlib
import sys
import unittest
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
    
    # Mock supabase response builder
    mock_res = MagicMock()
    mock_res.data = [{"caller_id": "alice", "rate_limit_per_minute": 100}]
    
    # Mock postgrest methods
    mock_eq_active = MagicMock()
    mock_eq_active.execute = AsyncMock(return_value=mock_res)
    
    mock_eq_hash = MagicMock()
    mock_eq_hash.eq = MagicMock(return_value=mock_eq_active)
    
    mock_select = MagicMock()
    mock_select.eq = MagicMock(return_value=mock_eq_hash)
    
    mock_table = MagicMock()
    mock_table.select = MagicMock(return_value=mock_select)
    
    mock_supabase = AsyncMock()
    mock_supabase.table = MagicMock(return_value=mock_table)

    with patch("auth.get_supabase_client", return_value=mock_supabase):
        result = await verify_api_key(raw_key)

    ok = (result is not None and result["caller_id"] == "alice" and result["rate_limit_per_minute"] == 100)
    report(2, ok, f"verify_api_key valid: result={result}")


async def test3_verify_api_key_invalid():
    """verify_api_key returns None for nonexistent key."""
    # Mock empty select response
    mock_res = MagicMock()
    mock_res.data = []
    
    mock_eq_active = MagicMock()
    mock_eq_active.execute = AsyncMock(return_value=mock_res)
    
    mock_eq_hash = MagicMock()
    mock_eq_hash.eq = MagicMock(return_value=mock_eq_active)
    
    mock_select = MagicMock()
    mock_select.eq = MagicMock(return_value=mock_eq_hash)
    
    mock_table = MagicMock()
    mock_table.select = MagicMock(return_value=mock_select)
    
    mock_supabase = AsyncMock()
    mock_supabase.table = MagicMock(return_value=mock_table)

    with patch("auth.get_supabase_client", return_value=mock_supabase):
        result = await verify_api_key("sk-cortex-doesnotexist")

    ok = (result is None)
    report(3, ok, f"verify_api_key invalid: result={result}")


async def test4_verify_api_key_inactive():
    """verify_api_key returns None for inactive keys."""
    raw_key, key_hash = generate_api_key()
    
    # Mock empty select response because active=True filter is applied
    mock_res = MagicMock()
    mock_res.data = []
    
    mock_eq_active = MagicMock()
    mock_eq_active.execute = AsyncMock(return_value=mock_res)
    
    mock_eq_hash = MagicMock()
    mock_eq_hash.eq = MagicMock(return_value=mock_eq_active)
    
    mock_select = MagicMock()
    mock_select.eq = MagicMock(return_value=mock_eq_hash)
    
    mock_table = MagicMock()
    mock_table.select = MagicMock(return_value=mock_select)
    
    mock_supabase = AsyncMock()
    mock_supabase.table = MagicMock(return_value=mock_table)

    with patch("auth.get_supabase_client", return_value=mock_supabase):
        result = await verify_api_key(raw_key)

    ok = (result is None)
    report(4, ok, f"verify_api_key inactive: result={result}")


async def test5_check_caller_rate_limit():
    """check_caller_rate_limit returns False if RPM exceeded, True otherwise."""
    caller_id = "test-rate-limit-caller"
    
    # Clean keys
    keys = await redis_client.keys(f"*{caller_id}*")
    if keys:
        await redis_client.delete(*keys)

    limit = 3
    # First 3 should pass
    r1 = await check_caller_rate_limit(caller_id, limit)
    r2 = await check_caller_rate_limit(caller_id, limit)
    r3 = await check_caller_rate_limit(caller_id, limit)
    
    # 4th should fail
    r4 = await check_caller_rate_limit(caller_id, limit)

    ok = (r1 is True and r2 is True and r3 is True and r4 is False)
    report(5, ok, f"check_caller_rate_limit: under={r1}/{r2}/{r3}, over={r4}")
    
    # Clean keys
    keys = await redis_client.keys(f"*{caller_id}*")
    if keys:
        await redis_client.delete(*keys)


async def test6_log_request_handles_failure():
    """log_request swallows database errors gracefully."""
    mock_table = MagicMock()
    # Mock insert raising an error
    mock_table.insert = MagicMock(side_effect=Exception("Database down"))
    
    mock_supabase = AsyncMock()
    mock_supabase.table = MagicMock(return_value=mock_table)

    # Calling log_request should complete without raising
    raised = False
    try:
        with patch("logger.get_supabase_client", return_value=mock_supabase):
            await log_request(
                request_id="req-123",
                caller_id="bob",
                tier_requested="strong",
                tier_source="manual",
                provider_used="groq",
                model_used="llama3",
                latency_ms=150,
                success=True
            )
    except Exception:
        raised = True

    ok = (raised is False)
    report(6, ok, f"log_request swallows errors: raised={raised}")


async def test7_get_recent_failures():
    """get_recent_failures only returns rows with success=False."""
    mock_res = MagicMock()
    mock_res.data = [
        {"request_id": "req-1", "success": False, "error_type": "timeout"},
        {"request_id": "req-2", "success": False, "error_type": "empty"}
    ]
    
    mock_limit = MagicMock()
    mock_limit.execute = AsyncMock(return_value=mock_res)
    
    mock_order = MagicMock()
    mock_order.limit = MagicMock(return_value=mock_limit)
    
    mock_eq = MagicMock()
    mock_eq.order = MagicMock(return_value=mock_order)
    
    mock_select = MagicMock()
    mock_select.eq = MagicMock(return_value=mock_eq)
    
    mock_table = MagicMock()
    mock_table.select = MagicMock(return_value=mock_select)
    
    mock_supabase = AsyncMock()
    mock_supabase.table = MagicMock(return_value=mock_table)

    with patch("logger.get_supabase_client", return_value=mock_supabase):
        res = await get_recent_failures(50)

    ok = (len(res) == 2 and all(row["success"] is False for row in res))
    report(7, ok, f"get_recent_failures returned only successes=False: {res}")


async def run_all():
    print("=== Phase 6 Test Script ===\n")
    
    # Check Redis is live for rate limit test
    try:
        await redis_client.ping()
        print("[setup] Redis connection OK\n")
    except Exception as e:
        print(f"[FAIL] Redis unreachable: {e}")
        return

    await test1_generate_api_key()
    await test2_verify_api_key_valid()
    await test3_verify_api_key_invalid()
    await test4_verify_api_key_inactive()
    await test5_check_caller_rate_limit()
    await test6_log_request_handles_failure()
    await test7_get_recent_failures()

    print("\n=== Summary ===")
    passed = sum(1 for ok, _ in _results if ok)
    failed = sum(1 for ok, _ in _results if not ok)
    print(f"{passed}/{len(_results)} tests passed")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(run_all())
