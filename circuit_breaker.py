import datetime
from redis_store import client, _safe_execute

MAX_COOLDOWN_SECONDS = 600  # 10 minutes
BASE_COOLDOWN_SECONDS = 30
FAILURE_THRESHOLD = 2 # e.g. 2 consecutive failures trip the breaker

async def get_circuit_state(provider: str, model_id: str) -> str:
    """
    Reads state. If OPEN and cooldown passed, transitions to HALF_OPEN.
    Returns: CLOSED, OPEN, or HALF_OPEN.
    """
    state_key = f"model:{provider}:{model_id}:circuit_state"
    cooldown_key = f"model:{provider}:{model_id}:circuit_cooldown_until"
    
    async def _check():
        state = await client.get(state_key)
        if not state:
            state = "CLOSED"
            await client.set(state_key, state)
            
        if state == "OPEN":
            cooldown = await client.get(cooldown_key)
            if cooldown:
                now = datetime.datetime.now(datetime.timezone.utc)
                cooldown_time = datetime.datetime.fromisoformat(cooldown)
                if now >= cooldown_time:
                    state = "HALF_OPEN"
                    await client.set(state_key, state)
            else:
                # No cooldown set but OPEN, edge case. Transition to HALF_OPEN.
                state = "HALF_OPEN"
                await client.set(state_key, state)
                
        return state
        
    return await _safe_execute(_check())

async def record_circuit_failure(provider: str, model_id: str):
    """
    Helper to track consecutive failures. If threshold is met, it trips the circuit.
    """
    fail_count_key = f"model:{provider}:{model_id}:consecutive_failures"
    
    async def _fail():
        count = await client.incr(fail_count_key)
        if count >= FAILURE_THRESHOLD:
            await trip_circuit(provider, model_id)
            
    await _safe_execute(_fail())

async def trip_circuit(provider: str, model_id: str):
    """
    Sets state to OPEN. Calculates new cooldown (double the previous, up to 10 mins).
    """
    state_key = f"model:{provider}:{model_id}:circuit_state"
    cooldown_key = f"model:{provider}:{model_id}:circuit_cooldown_until"
    prev_cooldown_duration_key = f"model:{provider}:{model_id}:prev_cooldown_duration"
    
    async def _trip():
        await client.set(state_key, "OPEN")
        
        prev_duration = await client.get(prev_cooldown_duration_key)
        if prev_duration:
            new_duration = min(int(prev_duration) * 2, MAX_COOLDOWN_SECONDS)
        else:
            new_duration = BASE_COOLDOWN_SECONDS
            
        await client.set(prev_cooldown_duration_key, new_duration)
        
        now = datetime.datetime.now(datetime.timezone.utc)
        cooldown_time = now + datetime.timedelta(seconds=new_duration)
        await client.set(cooldown_key, cooldown_time.isoformat())
        
    await _safe_execute(_trip())

async def reset_circuit(provider: str, model_id: str):
    """
    Sets state to CLOSED. Resets cooldown duration base and consecutive failures.
    """
    state_key = f"model:{provider}:{model_id}:circuit_state"
    prev_cooldown_duration_key = f"model:{provider}:{model_id}:prev_cooldown_duration"
    fail_count_key = f"model:{provider}:{model_id}:consecutive_failures"
    
    async def _reset():
        await client.set(state_key, "CLOSED")
        await client.delete(prev_cooldown_duration_key)
        await client.delete(fail_count_key)
        
    await _safe_execute(_reset())
