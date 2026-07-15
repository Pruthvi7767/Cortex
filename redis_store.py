"""
redis_store.py — Redis-backed model metrics, rate limiting, and quota tracking.

BUG-03 FIX: record_success() and record_failure() now use EMA (alpha=0.3) for the
success rate, consistent with update_latency(). The old arithmetic mean approach
caused weights to shrink near-zero after early requests, permanently anchoring the
metric to early history and making UCB1 routing learn stale/wrong data.

BUG-09 FIX: increment_rpm() now uses an atomic SET NX + EXPIRE approach to initialize
the key, eliminating the TOCTOU window where the TTL could be missed if two coroutines
raced at the exact same moment on a fresh key.
"""

import datetime
import logging
import hashlib
import redis.asyncio as redis
from redis.exceptions import ConnectionError, TimeoutError
from config import REDIS_URL, RPM_LIMITS

logger = logging.getLogger("cortex.redis_store")

# EMA smoothing factor — same alpha used for latency, now also used for success rate.
_EMA_ALPHA = 0.3


class RedisConnectionError(Exception):
    """Custom exception raised when Redis is unreachable, allowing callers to handle gracefully."""
    pass


# Single connection pool reused across the application lifetime
pool = redis.ConnectionPool.from_url(
    REDIS_URL,
    decode_responses=True,
    socket_timeout=2.0,
    socket_connect_timeout=2.0,
)
client = redis.Redis(connection_pool=pool)


async def _safe_execute(coro):
    """Executes a Redis coroutine, converting connection/timeout errors to a typed exception."""
    try:
        return await coro
    except (ConnectionError, TimeoutError) as e:
        logger.error(f"Redis connection error: {e}")
        raise RedisConnectionError(f"Redis is unreachable: {e}")


# ── Latency EMA ──────────────────────────────────────────────────────────────

async def update_latency(provider: str, model_id: str, latency_ms: float):
    """Updates the exponential moving average of response latency for a model."""
    key = f"model:{provider}:{model_id}:latency_ema"

    async def _update():
        current_ema = await client.get(key)
        if current_ema is None:
            new_ema = latency_ms
        else:
            new_ema = _EMA_ALPHA * latency_ms + (1 - _EMA_ALPHA) * float(current_ema)
        await client.set(key, new_ema)

    await _safe_execute(_update())


# ── Success Rate EMA ─────────────────────────────────────────────────────────

async def record_success(provider: str, model_id: str):
    """
    Records a successful response and updates the success-rate EMA.

    BUG-03 FIX: Replaced the old arithmetic mean formula:
        new_sr = current_sr + (1.0 - current_sr) / req_count
    with a proper EMA (alpha=0.3). The old formula assigns weight 1/req_count to the
    new observation — after ~10 requests this is <10%, making the metric permanently
    anchored to early history and unable to reflect recent reliability changes.
    EMA gives a consistent ~30% weight to the latest observation regardless of history.
    """
    success_rate_key = f"model:{provider}:{model_id}:success_rate"
    request_count_key = f"model:{provider}:{model_id}:request_count"

    async def _update():
        await client.incr(request_count_key)
        current_sr = await client.get(success_rate_key)

        if current_sr is None:
            new_sr = 1.0  # first request was a success
        else:
            # EMA: new observation = 1.0 (success)
            new_sr = _EMA_ALPHA * 1.0 + (1 - _EMA_ALPHA) * float(current_sr)

        await client.set(success_rate_key, new_sr)

    await _safe_execute(_update())


async def record_failure(provider: str, model_id: str, error_type: str):
    """
    Records a real failure (timeout, 5xx, etc.) and updates the success-rate EMA.
    Do NOT call this for quota/rate-limit hits — those don't reflect model quality.

    BUG-03 FIX: Same EMA formula fix as record_success(). New observation = 0.0 (failure).
    """
    success_rate_key = f"model:{provider}:{model_id}:success_rate"
    request_count_key = f"model:{provider}:{model_id}:request_count"

    async def _update():
        await client.incr(request_count_key)
        current_sr = await client.get(success_rate_key)

        if current_sr is None:
            new_sr = 0.0  # first request was a failure
        else:
            # EMA: new observation = 0.0 (failure)
            new_sr = _EMA_ALPHA * 0.0 + (1 - _EMA_ALPHA) * float(current_sr)

        await client.set(success_rate_key, new_sr)

    await _safe_execute(_update())


# ── RPM Tracking ─────────────────────────────────────────────────────────────

async def increment_rpm(provider: str, model_id: str):
    """
    Increments the per-model per-minute request counter using a fixed 60-second window.

    BUG-09 FIX: Old code ran INCR+TTL in one pipeline then called EXPIRE in a separate
    await if TTL was -1. Under concurrent load two coroutines could both see TTL=-1 and
    both try to set EXPIRE, OR more critically: a coroutine could see TTL=-1 on a key
    that a concurrent coroutine already set EXPIRE on — resulting in double-expiry logic.
    Worst case: two concurrent first-time calls where neither sees TTL=-1 post-pipeline
    (due to ordering) could leave the key without an expiry, living forever.

    Fix: Use SET ... NX EX 60 to atomically create the key with a TTL only if it does
    not already exist, then always INCR. This way the TTL is set exactly once, atomically,
    with no race window.
    """
    counter_key = f"model:{provider}:{model_id}:rpm_used"

    async def _update():
        # Atomically create the key with a 60-second TTL if it doesn't exist yet.
        # NX = only set if Not eXists; EX = expire in seconds.
        # If the key already exists, SET NX does nothing — the existing TTL is preserved.
        await client.set(counter_key, 0, ex=60, nx=True)
        # Always increment; the key is guaranteed to have a TTL from the SET NX above
        # (or from a previous call that already initialized it).
        await client.incr(counter_key)

    await _safe_execute(_update())


async def is_rate_limited(provider: str, model_id: str) -> bool:
    """Returns True if the model has hit its RPM cap in the current window."""
    key = f"model:{provider}:{model_id}:rpm_used"
    limit = RPM_LIMITS.get(provider, RPM_LIMITS["default"])

    async def _check():
        val = await client.get(key)
        if not val:
            return False
        return int(val) >= limit

    return await _safe_execute(_check())


# ── Quota Tracking ───────────────────────────────────────────────────────────

async def increment_quota_usage(provider: str, model_id: str, tokens_used: int):
    """Adds tokens_used to the daily quota counter for a model."""
    key = f"quota:{provider}:{model_id}:used_today"

    async def _update():
        await client.incrby(key, tokens_used)

    await _safe_execute(_update())


async def is_quota_exhausted(provider: str, model_id: str) -> bool:
    """Returns True if the model's daily token quota has been consumed."""
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
    """
    Sets a TTL on the used_today counter so it auto-resets at the next UTC midnight.
    Also records the reset timestamp for diagnostic visibility.
    """
    reset_key = f"quota:{provider}:{model_id}:reset_at"
    used_key = f"quota:{provider}:{model_id}:used_today"

    async def _update():
        now = datetime.datetime.now(datetime.timezone.utc)
        tomorrow = now + datetime.timedelta(days=1)
        midnight = datetime.datetime(
            tomorrow.year, tomorrow.month, tomorrow.day, tzinfo=datetime.timezone.utc
        )

        # Seconds until the next UTC midnight
        ttl_seconds = int((midnight - now).total_seconds())

        # Store the scheduled reset time for visibility
        await client.set(reset_key, midnight.isoformat())

        # Ensure the counter key exists before setting its TTL
        val = await client.get(used_key)
        if val is None:
            await client.set(used_key, 0)

        await client.expire(used_key, ttl_seconds)
        await client.expire(reset_key, ttl_seconds)

    await _safe_execute(_update())


# ── Tier Request Counter ─────────────────────────────────────────────────────

async def increment_tier_requests(tier: str):
    """Increments the all-time request counter for a tier (used by UCB1 N_total)."""
    key = f"tier:{tier}:total_requests"

    async def _update():
        await client.incr(key)

    await _safe_execute(_update())


# ── Last-Used Timestamp ──────────────────────────────────────────────────────

async def update_last_used(provider: str, model_id: str):
    """Records the current UTC timestamp as the last time this model received a real request."""
    key = f"model:{provider}:{model_id}:last_used_at"

    async def _update():
        now_ts = datetime.datetime.now(datetime.timezone.utc).timestamp()
        await client.set(key, now_ts)

    await _safe_execute(_update())


async def is_stale(provider: str, model_id: str, staleness_threshold_seconds: int = 300) -> bool:
    """
    Returns True if the model hasn't been called in staleness_threshold_seconds.
    Also returns True if the model has never been called (no last_used_at key).
    """
    key = f"model:{provider}:{model_id}:last_used_at"

    async def _check():
        val = await client.get(key)
        if not val:
            return True  # Never called → treat as stale

        last_used_ts = float(val)
        now_ts = datetime.datetime.now(datetime.timezone.utc).timestamp()
        return (now_ts - last_used_ts) >= staleness_threshold_seconds

    return await _safe_execute(_check())


# ── Pulse Cache ───────────────────────────────────────────────────────────────

PULSE_CACHE_TTL = 3600  # 1 hour

async def get_pulse_cache(prompt: str) -> str | None:
    """Returns cached tier or None. Key = SHA256(prompt.strip().lower())."""
    key = f"pulse:cache:{hashlib.sha256(prompt.strip().lower().encode()).hexdigest()}"
    async def _check():
        return await client.get(key)
    try:
        return await _safe_execute(_check())
    except RedisConnectionError:
        return None

async def set_pulse_cache(prompt: str, tier: str) -> None:
    """Caches tier for 1 hour."""
    key = f"pulse:cache:{hashlib.sha256(prompt.strip().lower().encode()).hexdigest()}"
    async def _set():
        await client.set(key, tier, ex=PULSE_CACHE_TTL)
    try:
        await _safe_execute(_set())
    except RedisConnectionError:
        pass


async def get_caller_thresholds(caller_id: str) -> tuple[float, float]:
    """
    Returns the custom fast and strong thresholds for a caller.
    Defaults to (2.0, 5.0) if not found or Redis is down.
    """
    key = f"pulse:profile:{caller_id}"
    async def _get():
        raw = await client.get(key)
        if raw:
            fast_t, strong_t = raw.split(",")
            return float(fast_t), float(strong_t)
        return 2.0, 5.0
    try:
        return await _safe_execute(_get())
    except (RedisConnectionError, ValueError, AttributeError):
        return 2.0, 5.0

async def set_caller_thresholds(caller_id: str, fast_t: float, strong_t: float) -> None:
    """Caches thresholds for 24 hours."""
    key = f"pulse:profile:{caller_id}"
    val = f"{fast_t},{strong_t}"
    async def _set():
        await client.set(key, val, ex=86400)
    try:
        await _safe_execute(_set())
    except RedisConnectionError:
        pass
