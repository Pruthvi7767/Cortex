"""
auth.py — API key generation, Supabase key verification, and caller-level rate limiting.
"""

import os
import hashlib
import secrets
import time
import uuid
import logging
from typing import Optional

from supabase import create_async_client, AsyncClient
from redis_store import client as redis_client, _safe_execute

logger = logging.getLogger("cortex.auth")

_supabase_client: Optional[AsyncClient] = None

async def get_supabase_client() -> AsyncClient:
    """
    Returns the shared AsyncClient, initializing it on first call.
    """
    global _supabase_client
    if _supabase_client is None:
        url = os.getenv("SUPABASE_URL", "")
        key = os.getenv("SUPABASE_KEY", "")
        if not url or not key:
            logger.warning("SUPABASE_URL or SUPABASE_KEY not set. API key verification will fail.")
        _supabase_client = await create_async_client(url, key)
    return _supabase_client


def generate_api_key() -> tuple[str, str]:
    """
    Generates a cryptographically secure API key and its SHA-256 hash.
    Format: sk-cortex- + 32 random URL-safe characters.
    
    Returns:
        (raw_key, key_hash)
    """
    # secrets.token_urlsafe(24) yields exactly 32 URL-safe characters
    random_part = secrets.token_urlsafe(24)
    raw_key = f"sk-cortex-{random_part}"
    
    # Hash the key using SHA-256
    key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
    return raw_key, key_hash


async def verify_api_key(raw_key: str) -> Optional[dict]:
    """
    Hashes the raw_key and queries the Supabase api_keys table.
    
    Returns:
        {"caller_id": str, "rate_limit_per_minute": int} if key is active and matches,
        otherwise None.
    """
    if not raw_key:
        return None
        
    key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
    
    try:
        supabase = await get_supabase_client()
        res = await supabase.table("api_keys")\
            .select("caller_id, rate_limit_per_minute")\
            .eq("key_hash", key_hash)\
            .eq("active", True)\
            .execute()
            
        if res.data and len(res.data) > 0:
            return res.data[0]
            
    except Exception as e:
        logger.error(f"Error during API key verification: {e}")
        
    return None


async def check_caller_rate_limit(caller_id: str, limit_per_minute: int) -> bool:
    """
    Uses Redis to implement a sliding window rate limiter for the caller.
    Prevents single abusive callers from exhausting the provider quota.
    
    Returns:
        True if the caller is under their RPM limit, False if rate-limited.
    """
    now = time.time()
    key = f"caller:{caller_id}:rate_limit"
    
    async def _check_and_update():
        async with redis_client.pipeline(transaction=True) as pipe:
            pipe.zremrangebyscore(key, 0, now - 60)
            pipe.zcard(key)
            res = await pipe.execute()
            
            current_count = res[1]
            if current_count >= limit_per_minute:
                return False
                
            # Under limit: add this request to the set and expire key in 60s
            unique_member = f"{now}:{uuid.uuid4().hex}"
            async with redis_client.pipeline(transaction=True) as update_pipe:
                update_pipe.zadd(key, {unique_member: now})
                update_pipe.expire(key, 60)
                await update_pipe.execute()
                
            return True
            
    try:
        return await _safe_execute(_check_and_update())
    except Exception as e:
        # Fallback to allowing request if Redis fails (fail-open for proxy rate limits,
        # but log a warning).
        logger.error(f"Redis rate limiting failed for caller '{caller_id}': {e}")
        return True
