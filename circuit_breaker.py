"""
circuit_breaker.py — Redis-backed circuit breaker with exponential cooldown backoff.

States: CLOSED → OPEN → HALF_OPEN → CLOSED (on success)

BUG-04 FIX: trip_circuit() now deletes fail_count_key when it opens the breaker.
Previously the counter stayed at FAILURE_THRESHOLD, so the very first failure during
HALF_OPEN immediately re-tripped — making HALF_OPEN recovery impossible.
"""

import datetime
from redis_store import client, _safe_execute

MAX_COOLDOWN_SECONDS = 600  # 10 minutes maximum backoff
BASE_COOLDOWN_SECONDS = 30  # initial cooldown duration
FAILURE_THRESHOLD = 2       # consecutive failures required to trip the breaker


async def get_circuit_state(provider: str, model_id: str) -> str:
    """
    Reads circuit state. If OPEN and cooldown has passed, transitions to HALF_OPEN.
    Returns: "CLOSED", "OPEN", or "HALF_OPEN".
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
                # No cooldown set but state is OPEN — edge case. Transition to HALF_OPEN.
                state = "HALF_OPEN"
                await client.set(state_key, state)

        return state

    return await _safe_execute(_check())


async def record_circuit_failure(provider: str, model_id: str):
    """
    Increments consecutive failure counter. Trips the breaker when threshold is reached.
    """
    fail_count_key = f"model:{provider}:{model_id}:consecutive_failures"

    async def _fail():
        count = await client.incr(fail_count_key)
        if count >= FAILURE_THRESHOLD:
            await trip_circuit(provider, model_id)

    await _safe_execute(_fail())


async def trip_circuit(provider: str, model_id: str):
    """
    Opens the circuit breaker with exponential backoff cooldown.
    Doubles the previous cooldown duration each trip, up to MAX_COOLDOWN_SECONDS.

    BUG-04 FIX: Deletes fail_count_key after tripping so that when the circuit
    transitions to HALF_OPEN and gets a probe/request, the consecutive-failure
    counter starts fresh from 0 instead of being pre-loaded at the threshold.
    Without this fix, any single failure in HALF_OPEN would immediately re-trip.
    """
    state_key = f"model:{provider}:{model_id}:circuit_state"
    cooldown_key = f"model:{provider}:{model_id}:circuit_cooldown_until"
    prev_cooldown_duration_key = f"model:{provider}:{model_id}:prev_cooldown_duration"
    fail_count_key = f"model:{provider}:{model_id}:consecutive_failures"

    async def _trip():
        await client.set(state_key, "OPEN")

        # Exponential backoff: double previous duration, cap at MAX_COOLDOWN_SECONDS
        prev_duration = await client.get(prev_cooldown_duration_key)
        if prev_duration:
            new_duration = min(int(prev_duration) * 2, MAX_COOLDOWN_SECONDS)
        else:
            new_duration = BASE_COOLDOWN_SECONDS

        await client.set(prev_cooldown_duration_key, new_duration)

        now = datetime.datetime.now(datetime.timezone.utc)
        cooldown_time = now + datetime.timedelta(seconds=new_duration)
        await client.set(cooldown_key, cooldown_time.isoformat())

        # BUG-04 FIX: Reset the failure counter so HALF_OPEN probes start fresh.
        # Without this, the counter stays at FAILURE_THRESHOLD, causing the first
        # failure in HALF_OPEN to immediately re-trip the breaker.
        await client.delete(fail_count_key)

    await _safe_execute(_trip())


async def reset_circuit(provider: str, model_id: str):
    """
    Closes the circuit breaker on success. Resets cooldown history and failure counter.
    """
    state_key = f"model:{provider}:{model_id}:circuit_state"
    prev_cooldown_duration_key = f"model:{provider}:{model_id}:prev_cooldown_duration"
    fail_count_key = f"model:{provider}:{model_id}:consecutive_failures"

    async def _reset():
        await client.set(state_key, "CLOSED")
        await client.delete(prev_cooldown_duration_key)
        await client.delete(fail_count_key)

    await _safe_execute(_reset())
