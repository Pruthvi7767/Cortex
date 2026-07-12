from redis_store import is_quota_exhausted, is_rate_limited
from circuit_breaker import get_circuit_state

async def check_availability(provider: str, model_id: str) -> bool:
    """
    Returns False if:
    - Quota is exhausted OR
    - Circuit state is OPEN OR
    - Currently rate-limited (RPM exhausted).
    
    Otherwise returns True (including for HALF_OPEN).
    """
    
    # Check circuit state first (fastest, no config lookup)
    circuit_state = await get_circuit_state(provider, model_id)
    if circuit_state == "OPEN":
        return False
        
    # Check rate limit
    if await is_rate_limited(provider, model_id):
        return False
        
    # Check daily quota
    if await is_quota_exhausted(provider, model_id):
        return False
        
    return True
