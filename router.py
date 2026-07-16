import asyncio
import math
import logging
from typing import Optional, NamedTuple

from config import MODEL_REGISTRY, get_active_providers
from redis_store import client, _safe_execute
from quota_tracker import check_availability

logger = logging.getLogger("cortex.router")

# UCB1 exploration constant — tunable without changing the formula.
UCB1_C = 1.4

TIER_ORDER = ["strong", "mid", "fast"]


class CandidateResult(NamedTuple):
    """Structured return type for get_candidates_with_cascade."""
    tier: str
    candidates: list


async def ucb1_score(provider: str, model_id: str, tier: str) -> float:
    """
    UCB1 score = mean_reward + C * sqrt(ln(N_total) / N_model)
    mean_reward = 1 / latency_ema  (faster = higher reward)

    Cold start rule: if N_model == 0, the model has never been tried.
    Per design, untried models MUST get a mandatory first attempt — returning inf
    guarantees they are always ranked first until they have at least one data point.
    We do NOT add a small epsilon to N_model, which would incorrectly deprioritise
    new models by applying the formula before any data exists.
    """
    async def _score():
        n_model_raw = await client.get(f"model:{provider}:{model_id}:request_count")
        n_model = int(n_model_raw) if n_model_raw else 0

        if n_model == 0:
            # Mandatory free first try — no data yet, give maximum priority
            return float("inf")

        n_total_raw = await client.get(f"tier:{tier}:total_requests")
        # Guard against ln(0); if tier has no requests recorded yet treat as 1
        n_total = max(int(n_total_raw) if n_total_raw else 0, 1)

        latency_raw = await client.get(f"model:{provider}:{model_id}:latency_ema")
        # Pessimistic default if we have request_count but no latency yet
        latency = float(latency_raw) if latency_raw else 10000.0

        mean_reward = 1.0 / latency
        exploration = UCB1_C * math.sqrt(math.log(n_total) / n_model)

        return mean_reward + exploration

    return await _safe_execute(_score())


async def get_candidates(
    tier: str,
    k: int = 3,
    estimated_tokens: int = 0,
    registry_override: Optional[list] = None,
    active_providers_override: Optional[set] = None,
) -> list:
    """
    Returns top-k ranked candidates for a given tier after applying all filters.

    Filters applied in this exact order (order matters — do not reorder):
      1. Tier membership (from MODEL_REGISTRY or registry_override)
      2. Active provider (has a configured API key)
      3. Availability (circuit not OPEN, not rate-limited, not quota-exhausted)
      4. Context window (model max_context >= estimated_tokens * 1.15)
      5. UCB1 ranking (descending)
      6. Top-k slice

    registry_override and active_providers_override exist solely for unit-test
    injection; production callers should leave them as None.
    """
    # Step 1 — tier membership
    tier_models: list = registry_override if registry_override is not None else MODEL_REGISTRY.get(tier, [])
    if not tier_models:
        return []

    # Step 2 — active providers only
    if active_providers_override is not None:
        active_ids: set = active_providers_override
    else:
        active_ids = {p["id"] for p in get_active_providers()}

    tier_models = [m for m in tier_models if m["provider"] in active_ids]

    # BUG-30 Fix: Use asyncio.gather for concurrent availability checks
    availability_results = await asyncio.gather(
        *(check_availability(m["provider"], m["model_id"]) for m in tier_models)
    )
    available = [m for m, is_avail in zip(tier_models, availability_results) if is_avail]

    # Step 4 — context window filter (15% safety margin)
    if estimated_tokens > 0:
        min_context = int(estimated_tokens * 1.15)
        available = [m for m in available if m.get("max_context", 0) >= min_context]

    if not available:
        return []

    # BUG-30 Fix: Use asyncio.gather for concurrent scoring
    scores = await asyncio.gather(
        *(ucb1_score(m["provider"], m["model_id"], tier) for m in available)
    )
    scored = list(zip(scores, available))

    scored.sort(key=lambda x: x[0], reverse=True)

    # Step 6 — top k
    return [m for _, m in scored[:k]]


def split_nvidia_first(candidates: list) -> tuple:
    """
    Splits a ranked candidate list into (nvidia_candidates, other_candidates),
    preserving relative rank order within each group.

    Phase 4 will attempt nvidia_candidates first (single shot, short timeout)
    before fanning out to other_candidates — that execution logic lives in Phase 4,
    not here. This function only re-organises the list.
    """
    nvidia = [m for m in candidates if m["provider"] == "nvidia"]
    others = [m for m in candidates if m["provider"] != "nvidia"]
    return (nvidia, others)


async def get_candidates_with_cascade(
    tier: str,
    k: int = 3,
    estimated_tokens: int = 0,
    registry_override: Optional[dict] = None,
    active_providers_override: Optional[set] = None,
) -> CandidateResult:
    """
    Wraps get_candidates() with automatic tier cascading:
      strong → mid → fast

    If the requested tier returns an empty candidate list (all filtered out),
    the next tier down is tried automatically. The caller receives both the
    candidates AND which tier was actually used, since Phase 4 needs the tier
    name to look up the correct TIER_TIMEOUTS budget.

    Returns CandidateResult(tier=actual_tier_used, candidates=list).
    """
    if tier not in TIER_ORDER:
        logger.warning(f"Unknown tier '{tier}' — defaulting to 'strong'")
        tier = "strong"

    start_index = TIER_ORDER.index(tier)

    for current_tier in TIER_ORDER[start_index:]:
        reg = registry_override.get(current_tier) if registry_override else None
        candidates = await get_candidates(
            current_tier,
            k=k,
            estimated_tokens=estimated_tokens,
            registry_override=reg,
            active_providers_override=active_providers_override,
        )
        if candidates:
            if current_tier != tier:
                logger.info(f"Tier cascade: '{tier}' exhausted, using '{current_tier}'")
            return CandidateResult(tier=current_tier, candidates=candidates)

    # All tiers exhausted — return empty with original tier for caller to handle
    logger.warning(f"No candidates found across all tiers starting from '{tier}'")
    return CandidateResult(tier=tier, candidates=[])
