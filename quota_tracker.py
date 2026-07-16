from redis_store import is_quota_exhausted, is_rate_limited, client as redis_client, _safe_execute
from circuit_breaker import get_circuit_state

async def check_availability(provider: str, model_id: str) -> bool:
    """
    Returns False if:
    - Quota is exhausted OR
    - Circuit state is OPEN OR
    - Currently rate-limited (RPM exhausted).
    - BUG-26 Fix: Recently hit a 429 Rate Limit (TTL 60s).
    
    Otherwise returns True (including for HALF_OPEN).
    """
    
    # Check circuit state first (fastest, no config lookup)
    circuit_state = await get_circuit_state(provider, model_id)
    if circuit_state == "OPEN":
        return False
        
    # Check rate limit (RPM limits config)
    if await is_rate_limited(provider, model_id):
        return False
        
    # Check daily quota
    if await is_quota_exhausted(provider, model_id):
        return False

    # BUG-26 Fix: Check if model recently hit an HTTP 429 rate limit
    async def _check_429():
        return await redis_client.exists(f"model:{provider}:{model_id}:recent_429")
    if await _safe_execute(_check_429()):
        return False
        
    return True
