import asyncio
import datetime
from redis_store import (
    client, update_latency, record_success, record_failure,
    increment_rpm, is_rate_limited, increment_quota_usage,
    is_quota_exhausted, set_quota_reset_time, RedisConnectionError
)
from circuit_breaker import (
    get_circuit_state, trip_circuit, reset_circuit, record_circuit_failure
)
from quota_tracker import check_availability

# We need to test against "test_provider" and "test_model"
PROVIDER = "test_provider"
MODEL = "test_model"

# We must ensure config.py knows the limits for testing, 
# but we can rely on "default" limits (RPM=30, QUOTA=5000)

async def run_tests():
    print("--- Phase 2 Test Script ---")
    
    # 1. Connects to Redis, confirms connection works
    try:
        await client.ping()
        print("1. [OK] Redis connection successful.")
    except Exception as e:
        print(f"1. [FAIL] Redis connection failed: {e}")
        return

    # Clean up keys for the test
    keys = await client.keys(f"*{PROVIDER}:{MODEL}*")
    if keys:
        await client.delete(*keys)
    
    # 2. Simulates recording 5 successes and 2 failures for a fake model, confirms success_rate calculates correctly
    for _ in range(5):
        await record_success(PROVIDER, MODEL)
    for _ in range(2):
        await record_failure(PROVIDER, MODEL, "timeout")
        
    sr = await client.get(f"model:{PROVIDER}:{MODEL}:success_rate")
    print(f"2. [OK] Recorded 5 successes, 2 failures. Success rate: {sr}")
    
    # 3. Simulates hitting the rate limit (increment_rpm past the limit), confirms is_rate_limited returns True
    for _ in range(35): # Default limit is 30
        await increment_rpm(PROVIDER, MODEL)
    
    limited = await is_rate_limited(PROVIDER, MODEL)
    if limited:
        print("3. [OK] Hit rate limit correctly.")
    else:
        print("3. [FAIL] Did not hit rate limit when expected.")
        
    # Reset RPM for later tests
    await client.delete(f"model:{PROVIDER}:{MODEL}:rpm_used")

    # 4. Simulates a failure trip, confirms circuit goes CLOSED -> OPEN, then manually advances past cooldown, confirms it transitions to HALF_OPEN on next read
    await reset_circuit(PROVIDER, MODEL)
    initial_state = await get_circuit_state(PROVIDER, MODEL)
    print(f"4. [INFO] Initial state: {initial_state}")
    
    # Simulate 2 consecutive failures to trip
    await record_circuit_failure(PROVIDER, MODEL)
    await record_circuit_failure(PROVIDER, MODEL)
    
    tripped_state = await get_circuit_state(PROVIDER, MODEL)
    if tripped_state == "OPEN":
        print("4. [OK] Circuit tripped to OPEN.")
    else:
        print(f"4. [FAIL] Circuit did not trip to OPEN. State is {tripped_state}")
        
    # Manually advance past cooldown
    past_cooldown = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(minutes=1)
    await client.set(f"model:{PROVIDER}:{MODEL}:circuit_cooldown_until", past_cooldown.isoformat())
    
    advanced_state = await get_circuit_state(PROVIDER, MODEL)
    if advanced_state == "HALF_OPEN":
        print("4. [OK] Circuit automatically transitioned to HALF_OPEN after cooldown.")
    else:
        print(f"4. [FAIL] Circuit did not transition to HALF_OPEN. State is {advanced_state}")
        
    await reset_circuit(PROVIDER, MODEL) # reset for later tests
    
    # 5. Simulates quota exhaustion, confirms is_quota_exhausted returns True, and confirms record_failure was NOT called / success_rate was NOT affected
    # Set quota limit for test provider
    await client.set(f"quota:{PROVIDER}:{MODEL}:limit_daily", 5000)
    
    old_sr = await client.get(f"model:{PROVIDER}:{MODEL}:success_rate")
    await increment_quota_usage(PROVIDER, MODEL, 5500)
    
    exhausted = await is_quota_exhausted(PROVIDER, MODEL)
    new_sr = await client.get(f"model:{PROVIDER}:{MODEL}:success_rate")
    
    if exhausted and old_sr == new_sr:
        print("5. [OK] Quota exhausted correctly and success rate unaffected.")
    else:
        print(f"5. [FAIL] Quota exhaustion failed. Exhausted: {exhausted}, Old SR: {old_sr}, New SR: {new_sr}")
        
    # Clean up quota exhaustion for test 6
    await client.delete(f"quota:{PROVIDER}:{MODEL}:used_today")
    
    # 6. Confirms check_availability() correctly returns False for a circuit-OPEN model AND for a quota-exhausted model, and True for a healthy model
    healthy_check = await check_availability(PROVIDER, MODEL)
    if healthy_check:
        print("6. [OK] Healthy model is available.")
    else:
        print("6. [FAIL] Healthy model is NOT available.")
        
    # Trip circuit
    await trip_circuit(PROVIDER, MODEL)
    open_check = await check_availability(PROVIDER, MODEL)
    if not open_check:
        print("6. [OK] Circuit-OPEN model correctly marked unavailable.")
    else:
        print("6. [FAIL] Circuit-OPEN model incorrectly marked available.")
        
    await reset_circuit(PROVIDER, MODEL)
    
    # Quota exhaust
    await increment_quota_usage(PROVIDER, MODEL, 5500)
    quota_check = await check_availability(PROVIDER, MODEL)
    if not quota_check:
        print("6. [OK] Quota-exhausted model correctly marked unavailable.")
    else:
        print("6. [FAIL] Quota-exhausted model incorrectly marked available.")
        
    print("--- Test Complete ---")

if __name__ == "__main__":
    asyncio.run(run_tests())
