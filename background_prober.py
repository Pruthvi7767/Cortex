"""
background_prober.py — Periodic background prober for stale model health-checks.

Iterates the MODEL_REGISTRY cross-referenced with currently active providers.
A model is probed when it hasn't received real traffic for STALENESS_THRESHOLD_SECONDS
and its circuit isn't OPEN and its quota isn't exhausted.

BUG-01 FIX: Old code iterated provider_data.get("models", {}) which is always {}
because the provider registry has no 'models' key. This caused zero models to ever
be probed. Now correctly iterates MODEL_REGISTRY from config.
"""

import asyncio
import logging
from config import MODEL_REGISTRY, get_active_providers
from redis_store import is_stale, is_quota_exhausted
from circuit_breaker import get_circuit_state
from race import call_candidate

logger = logging.getLogger("cortex.background_prober")

PROBE_INTERVAL_SECONDS = 30
STALENESS_THRESHOLD_SECONDS = 300


async def probe_model(provider: str, model_id: str, tier: str):
    """Probes a single model if it's stale and hasn't exhausted its quota."""

    # 1. Skip if quota is exhausted
    if await is_quota_exhausted(provider, model_id):
        return

    # 2. Skip if circuit breaker is OPEN (it will transition to HALF_OPEN automatically
    #    over time via get_circuit_state). Don't probe OPEN circuits — useless until cool-off.
    state = await get_circuit_state(provider, model_id)
    if state == "OPEN":
        return

    # 3. Skip if not stale (regular traffic is keeping it fresh)
    if not await is_stale(provider, model_id, STALENESS_THRESHOLD_SECONDS):
        return

    logger.info(f"Probing stale model: {provider}/{model_id} (tier={tier})")

    messages = [{"role": "user", "content": "ping"}]
    # call_candidate automatically updates latency, success/failure, circuit, and rpm.
    try:
        await call_candidate(
            provider=provider,
            model_id=model_id,
            messages=messages,
            max_tokens=10,
            tier=tier,
            timeout_override=5.0  # short timeout for probing
        )
    except Exception as e:
        logger.warning(f"Probe failed for {provider}/{model_id}: {e}")


async def prober_loop():
    """
    Background loop that periodically probes stale models.

    BUG-01 FIX: Iterates MODEL_REGISTRY (the real source of model/tier membership),
    cross-referenced with get_active_providers() so we only probe models whose
    provider has a configured API key.
    """
    logger.info("Background prober started.")

    while True:
        try:
            # Build set of active provider IDs for quick lookup
            active_ids = {p["id"] for p in get_active_providers()}

            tasks = []
            # MODEL_REGISTRY: {"strong": [...], "mid": [...], "fast": [...]}
            for tier, models in MODEL_REGISTRY.items():
                for model_entry in models:
                    provider_id = model_entry["provider"]
                    model_id = model_entry["model_id"]

                    # Only probe models whose provider is currently active
                    if provider_id not in active_ids:
                        continue

                    tasks.append(probe_model(provider_id, model_id, tier))

            if tasks:
                # BUG-21 Fix: Use a semaphore to prevent thundering herds on prober
                sem = asyncio.Semaphore(5)
                async def _sem_probe(t):
                    async with sem:
                        return await t
                
                # Run all probes concurrently; gather exceptions so the loop never dies
                results = await asyncio.gather(*[_sem_probe(t) for t in tasks], return_exceptions=True)
                errors = [r for r in results if isinstance(r, Exception)]
                if errors:
                    logger.warning(f"Prober: {len(errors)} probe(s) raised exceptions this cycle")

        except asyncio.CancelledError:
            logger.info("Background prober stopped.")
            break
        except Exception as e:
            logger.error(f"Unexpected error in prober_loop: {e}", exc_info=True)

        await asyncio.sleep(PROBE_INTERVAL_SECONDS)
