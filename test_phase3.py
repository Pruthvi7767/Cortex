"""
Phase 3 test script — validates UCB1 scoring, candidate filtering, split logic,
and tier cascading. All tests use fake/injected data so they don't depend on
the real MODEL_REGISTRY being fully live.
"""
import asyncio
import datetime

from redis_store import client, update_latency, record_success
from circuit_breaker import trip_circuit, reset_circuit
from router import get_candidates, split_nvidia_first, get_candidates_with_cascade

# ---------------------------------------------------------------------------
# Fake registry fixtures used across tests
# ---------------------------------------------------------------------------

FAKE_STRONG = [
    {"provider": "nvidia",  "model_id": "fast-model",   "max_context": 128000},
    {"provider": "nvidia",  "model_id": "slow-model",   "max_context": 128000},
    {"provider": "groq",    "model_id": "mid-model",    "max_context": 128000},
    {"provider": "mistral", "model_id": "inactive-prov","max_context": 128000},
]

FAKE_MID = [
    {"provider": "groq",   "model_id": "mid-fallback",  "max_context": 128000},
]


async def clean_keys(*model_specs):
    """Delete all Redis keys for the given (provider, model_id) pairs."""
    for provider, model_id in model_specs:
        keys = await client.keys(f"*{provider}:{model_id}*")
        if keys:
            await client.delete(*keys)


async def seed_latency(provider, model_id, latency_ms, request_count):
    """Directly set latency EMA and request count in Redis for deterministic ranking."""
    await client.set(f"model:{provider}:{model_id}:latency_ema",    latency_ms)
    await client.set(f"model:{provider}:{model_id}:request_count",  request_count)
    await client.set(f"model:{provider}:{model_id}:circuit_state",  "CLOSED")


async def run_tests():
    print("=== Phase 3 Test Script ===\n")

    # Ensure Redis is reachable
    try:
        await client.ping()
        print("[setup] Redis connection OK")
    except Exception as e:
        print(f"[FAIL] Redis unreachable: {e}")
        return

    # -----------------------------------------------------------------------
    # TEST 1 — UCB1 ranking: faster latency → higher score
    # -----------------------------------------------------------------------
    print("\n--- Test 1: UCB1 ranking ---")
    for provider, model_id in [("nvidia","fast-model"),("nvidia","slow-model"),("groq","mid-model")]:
        await clean_keys((provider, model_id))

    # fast-model: 100ms latency, slow-model: 900ms, mid-model: 400ms
    await seed_latency("nvidia", "fast-model",  100,  10)
    await seed_latency("nvidia", "slow-model",  900,  10)
    await seed_latency("groq",   "mid-model",   400,  10)
    await client.set("tier:strong:total_requests", 30)

    active = {"nvidia", "groq", "mistral"}  # mistral is "active" here
    candidates = await get_candidates(
        "strong", k=3,
        registry_override=FAKE_STRONG[:3],  # exclude inactive-prov entry
        active_providers_override=active,
    )

    provider_order = [c["model_id"] for c in candidates]
    expected_order = ["fast-model", "mid-model", "slow-model"]
    if provider_order == expected_order:
        print(f"1. [OK] Candidates ranked correctly: {provider_order}")
    else:
        print(f"1. [FAIL] Unexpected order: {provider_order} (expected {expected_order})")

    # -----------------------------------------------------------------------
    # TEST 2 — Cold-start: untried model (N=0) is prioritised
    # -----------------------------------------------------------------------
    print("\n--- Test 2: Cold-start model gets mandatory first try ---")
    await clean_keys(("nvidia","brand-new"))
    # Don't set any Redis keys for brand-new → request_count = 0

    fake_with_new = FAKE_STRONG[:3] + [
        {"provider": "nvidia", "model_id": "brand-new", "max_context": 128000}
    ]
    active2 = {"nvidia", "groq"}
    candidates2 = await get_candidates(
        "strong", k=4,
        registry_override=fake_with_new,
        active_providers_override=active2,
    )

    ids2 = [c["model_id"] for c in candidates2]
    if ids2 and ids2[0] == "brand-new":
        print(f"2. [OK] Cold-start model ranked first: {ids2}")
    else:
        print(f"2. [FAIL] Cold-start model not first: {ids2}")

    # -----------------------------------------------------------------------
    # TEST 3 — Inactive provider is excluded
    # -----------------------------------------------------------------------
    print("\n--- Test 3: Inactive provider filtered out ---")
    active3 = {"nvidia", "groq"}  # mistral NOT in active set
    await seed_latency("mistral", "inactive-prov", 50, 5)  # very fast, should still be excluded

    candidates3 = await get_candidates(
        "strong", k=10,
        registry_override=FAKE_STRONG,
        active_providers_override=active3,
    )
    providers_in_result = {c["provider"] for c in candidates3}
    if "mistral" not in providers_in_result:
        print(f"3. [OK] Inactive provider 'mistral' excluded. Found providers: {providers_in_result}")
    else:
        print(f"3. [FAIL] Inactive provider 'mistral' appeared in results")

    # -----------------------------------------------------------------------
    # TEST 4 — Circuit-OPEN model is excluded
    # -----------------------------------------------------------------------
    print("\n--- Test 4: Circuit-OPEN model excluded ---")
    await trip_circuit("groq", "mid-model")

    candidates4 = await get_candidates(
        "strong", k=5,
        registry_override=FAKE_STRONG[:3],
        active_providers_override={"nvidia", "groq"},
    )
    ids4 = [c["model_id"] for c in candidates4]
    if "mid-model" not in ids4:
        print(f"4. [OK] Circuit-OPEN model 'mid-model' excluded. Results: {ids4}")
    else:
        print(f"4. [FAIL] Circuit-OPEN model appeared in results: {ids4}")

    await reset_circuit("groq", "mid-model")  # restore for subsequent tests

    # -----------------------------------------------------------------------
    # TEST 5 — Context window filter
    # -----------------------------------------------------------------------
    print("\n--- Test 5: Context window filtering ---")
    small_context_registry = [
        {"provider": "nvidia", "model_id": "fast-model", "max_context": 4096},   # too small
        {"provider": "nvidia", "model_id": "slow-model", "max_context": 128000}, # large enough
    ]
    # Request with 5000 tokens → need 5000 * 1.15 = 5750 tokens minimum
    candidates5 = await get_candidates(
        "strong", k=5,
        estimated_tokens=5000,
        registry_override=small_context_registry,
        active_providers_override={"nvidia"},
    )
    ids5 = [c["model_id"] for c in candidates5]
    if "fast-model" not in ids5 and "slow-model" in ids5:
        print(f"5. [OK] Small-context model excluded, large-context model present: {ids5}")
    else:
        print(f"5. [FAIL] Context filter failed: {ids5}")

    # -----------------------------------------------------------------------
    # TEST 6 — split_nvidia_first preserves rank order
    # -----------------------------------------------------------------------
    print("\n--- Test 6: split_nvidia_first ---")
    mixed_candidates = [
        {"provider": "nvidia", "model_id": "fast-model"},
        {"provider": "groq",   "model_id": "mid-model"},
        {"provider": "nvidia", "model_id": "slow-model"},
        {"provider": "mistral","model_id": "some-model"},
    ]
    nvidia_list, others_list = split_nvidia_first(mixed_candidates)
    nvidia_ids = [m["model_id"] for m in nvidia_list]
    others_ids = [m["model_id"] for m in others_list]

    if nvidia_ids == ["fast-model", "slow-model"] and others_ids == ["mid-model", "some-model"]:
        print(f"6. [OK] split_nvidia_first correct. NVIDIA: {nvidia_ids}, Others: {others_ids}")
    else:
        print(f"6. [FAIL] NVIDIA: {nvidia_ids}, Others: {others_ids}")

    # -----------------------------------------------------------------------
    # TEST 7 — Tier cascade: strong exhausted → falls through to mid
    # -----------------------------------------------------------------------
    print("\n--- Test 7: Tier cascade strong -> mid ---")
    # Trip ALL strong-tier fake models so none are available
    await trip_circuit("nvidia", "fast-model")
    await trip_circuit("nvidia", "slow-model")

    fake_mid_registry = [
        {"provider": "groq", "model_id": "mid-fallback", "max_context": 128000},
    ]
    await seed_latency("groq", "mid-fallback", 200, 5)

    result7 = await get_candidates_with_cascade(
        "strong", k=3,
        registry_override={
            "strong": [
                {"provider": "nvidia", "model_id": "fast-model", "max_context": 128000},
                {"provider": "nvidia", "model_id": "slow-model", "max_context": 128000},
            ],
            "mid": fake_mid_registry,
            "fast": [],
        },
        active_providers_override={"nvidia", "groq"},
    )

    if result7.tier == "mid" and result7.candidates and result7.candidates[0]["model_id"] == "mid-fallback":
        print(f"7. [OK] Cascaded from 'strong' to 'mid'. Used tier='{result7.tier}', candidates={[c['model_id'] for c in result7.candidates]}")
    else:
        print(f"7. [FAIL] Cascade failed: tier={result7.tier}, candidates={result7.candidates}")

    # Cleanup
    await reset_circuit("nvidia", "fast-model")
    await reset_circuit("nvidia", "slow-model")

    print("\n=== Test Complete ===")


if __name__ == "__main__":
    asyncio.run(run_tests())
