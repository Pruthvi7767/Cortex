"""
auth.py — API key generation, asyncpg key verification, and caller-level rate limiting.

BUG-02 FIX: check_caller_rate_limit() now uses a single atomic Lua script instead of
two separate Redis pipelines with a gap between them. The old two-pipeline approach had
a TOCTOU race: two concurrent requests from the same caller could both read count < limit,
both decide "allowed", and both add — making the rate limiter bypassable under load.

The Lua script runs atomically on the Redis server, so the check-and-add is guaranteed
to be indivisible.
"""

import hashlib
import logging
import secrets
import time
import uuid
from typing import Optional

from db import get_pool
from redis_store import client as redis_client, _safe_execute

logger = logging.getLogger("cortex.auth")

# Lua script for atomic sliding-window rate limit check-and-increment.
# Args: KEYS[1] = rate-limit sorted-set key
#       ARGV[1] = current timestamp (float, as string)
#       ARGV[2] = window start timestamp (now - 60s, as string)
#       ARGV[3] = rpm limit (int, as string)
#       ARGV[4] = unique member value (string)
#
# Returns: 1 if the request is allowed (and was recorded), 0 if rate-limited.
_RATE_LIMIT_LUA = """
local key       = KEYS[1]
local now       = tonumber(ARGV[1])
local window_start = tonumber(ARGV[2])
local limit     = tonumber(ARGV[3])
local member    = ARGV[4]

-- Remove entries outside the 60-second sliding window
redis.call('ZREMRANGEBYSCORE', key, 0, window_start)

-- Count remaining entries in the window
local count = redis.call('ZCARD', key)

if count >= limit then
    return 0
end

-- Under limit: record this request and refresh the key TTL
redis.call('ZADD', key, now, member)
redis.call('EXPIRE', key, 60)
return 1
"""


def generate_api_key() -> tuple[str, str]:
    """
    Generates a cryptographically secure API key and its SHA-256 hash.
    Format: sk-cortex- + 32 URL-safe characters (from 24 random bytes).

    Returns:
        (raw_key, key_hash)  — raw_key is shown to the user ONCE; key_hash is stored.
    """
    # secrets.token_urlsafe(24) produces exactly 32 URL-safe base64 characters
    random_part = secrets.token_urlsafe(24)
    raw_key = f"sk-cortex-{random_part}"
    key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
    return raw_key, key_hash


async def verify_api_key(raw_key: str) -> Optional[dict]:
    """
    Hashes raw_key and queries the Postgres api_keys table for an active match.

    Returns:
        {"caller_id": str, "rate_limit_per_minute": int, "is_admin": bool}
        or None if the key is invalid or inactive.
    """
    if not raw_key:
        return None

    key_hash = hashlib.sha256(raw_key.encode()).hexdigest()

    try:
        pool = get_pool()
        query = (
            "SELECT caller_id, rate_limit_per_minute, is_admin "
            "FROM api_keys WHERE key_hash = $1 AND active = true"
        )
        row = await pool.fetchrow(query, key_hash)
        if row:
            return dict(row)

    except Exception as e:
        logger.error(f"Error during API key verification: {e}")

    return None


async def check_caller_rate_limit(caller_id: str, limit_per_minute: int) -> bool:
    """
    Atomic sliding-window rate limiter using a Redis Lua script.

    BUG-02 FIX: The old implementation used two separate pipeline blocks with a
    gap between them. Under concurrent requests from the same caller, two requests
    could both read count < limit before either added its entry, causing both to be
    allowed — defeating the limiter.

    Now uses a single Lua script executed atomically on the Redis server: the prune,
    check, and add are all one indivisible operation.

    Returns True if the caller is under their RPM limit (request allowed).
    Returns True on Redis failure (fail-open) to avoid blocking legitimate traffic.
    """
    now = time.time()
    window_start = now - 60.0
    key = f"caller:{caller_id}:rate_limit"
    unique_member = f"{now}:{uuid.uuid4().hex}"

    async def _check():
        result = await redis_client.eval(
            _RATE_LIMIT_LUA,
            1,                      # number of KEYS arguments
            key,                    # KEYS[1]
            str(now),               # ARGV[1]
            str(window_start),      # ARGV[2]
            str(limit_per_minute),  # ARGV[3]
            unique_member,          # ARGV[4]
        )
        return bool(result)

    try:
        return await _safe_execute(_check())
    except Exception as e:
        # Fail-open: log but allow the request if Redis is down
        logger.error(f"Redis rate limiting failed for caller '{caller_id}': {e}")
        return True
