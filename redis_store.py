import datetime
import logging
import redis.asyncio as redis
from redis.exceptions import ConnectionError, TimeoutError
from config import REDIS_URL, RPM_LIMITS

logger = logging.getLogger("cortex.redis_store")

class RedisConnectionError(Exception):
    """Custom exception raised when Redis is unreachable, allowing caller to handle it gracefully."""
    pass

# Create a connection pool to be reused across the application
pool = redis.ConnectionPool.from_url(REDIS_URL, decode_responses=True, socket_timeout=2.0, socket_connect_timeout=2.0)
client = redis.Redis(connection_pool=pool)

async def _safe_execute(coro):
    """Executes a Redis coroutine, catching connection errors and raising a safe custom exception."""
    try:
        return await coro
    except (ConnectionError, TimeoutError) as e:
        logger.error(f"Redis connection error: {e}")
        raise RedisConnectionError(f"Redis is unreachable: {e}")

async def update_latency(provider: str, model_id: str, latency_ms: float):
    alpha = 0.3
    key = f"model:{provider}:{model_id}:latency_ema"
    
    async def _update():
        current_ema = await client.get(key)
        if current_ema is None:
            new_ema = latency_ms
        else:
            new_ema = alpha * latency_ms + (1 - alpha) * float(current_ema)
        await client.set(key, new_ema)
    
    await _safe_execute(_update())

async def record_success(provider: str, model_id: str):
    success_rate_key = f"model:{provider}:{model_id}:success_rate"
    request_count_key = f"model:{provider}:{model_id}:request_count"
    
    async def _update():
        req_count = await client.incr(request_count_key)
        current_sr = await client.get(success_rate_key)
        
        if current_sr is None:
            new_sr = 1.0
        else:
            current_sr = float(current_sr)
            # moving average update for success rate
            new_sr = current_sr + (1.0 - current_sr) / req_count
            
        await client.set(success_rate_key, new_sr)
        
    await _safe_execute(_update())

async def record_failure(provider: str, model_id: str, error_type: str):
    """Records a real failure (timeout, 500, etc) impacting success rate. Do NOT use for quota limits."""
    success_rate_key = f"model:{provider}:{model_id}:success_rate"
    request_count_key = f"model:{provider}:{model_id}:request_count"
    
    async def _update():
        req_count = await client.incr(request_count_key)
        current_sr = await client.get(success_rate_key)
        
        if current_sr is None:
            new_sr = 0.0
        else:
            current_sr = float(current_sr)
            new_sr = current_sr + (0.0 - current_sr) / req_count
            
        await client.set(success_rate_key, new_sr)
        
    await _safe_execute(_update())

async def increment_rpm(provider: str, model_id: str):
    key = f"model:{provider}:{model_id}:rpm_used"
    
    async def _update():
        # Atomic increment and expire if first time
        async with client.pipeline(transaction=True) as pipe:
            pipe.incr(key)
            # Only set expire if we just created it or it doesn't have an expire
            # Actually, to maintain a rolling window perfectly, we'd need a sorted set or redis lists,
            # but a simple fixed window of 60s is standard for this kind of basic rate limiting.
            # INCR and EXPIRE if ttl is -1.
            # Since we can't conditionally execute expire safely without lua, we can just fetch TTL.
            pipe.ttl(key)
            res = await pipe.execute()
            
            count, ttl = res[0], res[1]
            if ttl == -1:
                await client.expire(key, 60)
                
    await _safe_execute(_update())

async def is_rate_limited(provider: str, model_id: str) -> bool:
    key = f"model:{provider}:{model_id}:rpm_used"
    limit = RPM_LIMITS.get(provider, RPM_LIMITS["default"])
    
    async def _check():
        val = await client.get(key)
        if not val:
            return False
        return int(val) >= limit
        
    return await _safe_execute(_check())

async def increment_quota_usage(provider: str, model_id: str, tokens_used: int):
    key = f"quota:{provider}:{model_id}:used_today"
    
    async def _update():
        await client.incrby(key, tokens_used)
        
    await _safe_execute(_update())

async def is_quota_exhausted(provider: str, model_id: str) -> bool:
    used_key = f"quota:{provider}:{model_id}:used_today"
    limit_key = f"quota:{provider}:{model_id}:limit_daily"
    
    async def _check():
        used = await client.get(used_key)
        limit = await client.get(limit_key)
        if not used or not limit:
            return False
        return int(used) >= int(limit)
        
    return await _safe_execute(_check())

async def set_quota_reset_time(provider: str, model_id: str):
    reset_key = f"quota:{provider}:{model_id}:reset_at"
    used_key = f"quota:{provider}:{model_id}:used_today"
    
    async def _update():
        now = datetime.datetime.now(datetime.timezone.utc)
        tomorrow = now + datetime.timedelta(days=1)
        midnight = datetime.datetime(tomorrow.year, tomorrow.month, tomorrow.day, tzinfo=datetime.timezone.utc)
        
        # Calculate seconds until midnight
        ttl_seconds = int((midnight - now).total_seconds())
        
        # Set reset_at for tracking
        await client.set(reset_key, midnight.isoformat())
        
        # Set TTL on used_today so it automatically zeroes out
        # We need to make sure the used_key exists before we can expire it,
        # but if it doesn't exist, we don't need to expire it. 
        # If it doesn't exist yet, we can set it to 0 and then expire.
        val = await client.get(used_key)
        if val is None:
            await client.set(used_key, 0)
            
        await client.expire(used_key, ttl_seconds)
        await client.expire(reset_key, ttl_seconds)
        
    await _safe_execute(_update())

async def increment_tier_requests(tier: str):
    key = f"tier:{tier}:total_requests"
    async def _update():
        await client.incr(key)
    await _safe_execute(_update())
