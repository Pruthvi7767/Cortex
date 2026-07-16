"""
classifier.py — Pulse Auto-Classifier for Tier Selection (v3).

Implements the 4-layer tier classification pipeline for deciding between
"strong", "mid", and "fast" tiers when a caller doesn't explicitly specify one.

Manual routing overrides Pulse completely: if a tier is requested, Pulse is skipped.

═══════════════════════════════════════════════════════════════════════════════
PULSE v3 PIPELINE
═══════════════════════════════════════════════════════════════════════════════

  L0: Redis cache check
        SHA256(prompt) → cached tier → return instantly (0ms, 0 tokens)

  L1: Keyword scoring (< 1ms, 0 tokens)
        Definitive score (>= 9.0 or <= -2.0) → return tier, skip LLM

  L2: Hedged LLM classifier (ambiguous prompts only)
        Send to PRIMARY provider.
        Wait 300ms hedge window.
        Primary fast → done (1 token call, ~200ms).
        Primary slow → send BACKUP simultaneously.
        First valid float wins → cancel loser immediately.
        Both fail → return 0.5 (never blocks main request).

  L3: Cache result in Redis (TTL = 1 hour)

═══════════════════════════════════════════════════════════════════════════════
PULSE v3 SCORING FORMULA
═══════════════════════════════════════════════════════════════════════════════

  stakes_count × 3.0 × irreversibility_mult  ← negation-aware, reversible-aware
  + reasoning_count × 1.0
  + user_facing × 2.5
  + has_numbers × 1.5
  + simple_penalty × -1.0
  + depth × 0.5 (capped at 5 turns = +2.5 max)
  + domain_boost (0 to +4.5)
  + intent_score (-1.5 to +4.0)
  + audience_score (-0.5 to +3.0)
  + tool_signal (0 to +3.5)
  + llm_confidence × 3.0 (ambiguous cases only)

Thresholds (from get_adaptive_threshold, future: Postgres-backed):
  score >= 5.0 → strong
  score >= 2.0 → mid
  score <  2.0 → fast
"""

import asyncio
import math
import hashlib
import re
import logging
from typing import Optional

from race import call_candidate
from config import MODEL_REGISTRY, CLASSIFIER_MODELS, get_active_providers
from quota_tracker import check_availability
from redis_store import get_pulse_cache, set_pulse_cache, get_caller_thresholds
from pulse_embedding import compute_embedding_score

logger = logging.getLogger("cortex.pulse")

# Valid tier values — used for manual-tier validation (BUG-11)
VALID_TIERS = {"strong", "mid", "fast"}

# Classifier timing constants
HEDGE_WINDOW_SECS  = 0.3   # wait 300ms before sending to backup
CLASSIFIER_TIMEOUT = 1.5   # hard per-provider deadline


# ── Keyword Sets ──────────────────────────────────────────────────────────────

# Each stakes keyword adds +3.0 × irreversibility_mult (counted, negation-aware)
STAKES_KEYWORDS = [
    "final",
    "send to client",
    "to the client",
    "to client",
    "publish",
    "report",
    "invoice",
    "quote",
    "contract",
    "production",
    "deploy",
]

# Each reasoning keyword adds +1.0 (counted)
REASONING_KEYWORDS = [
    "analyze",
    "design",
    "architecture",
    "compare",
    "explain why",
    "solve",
    "step by step",
    "evaluate",
    "synthesize",
    "debug",
    "optimize",
    "refactor",
]

# Boolean: any match → -1.0 penalty (doesn't stack)
SIMPLE_KEYWORDS = {
    "what is",
    "define",
    "translate",
    "summarize in one line",
    "say hello",
    "hello",
    "hi",
    "ping",
}

# Negation words — if found within 30 chars before a stakes keyword, cancel it
NEGATION_WORDS = {
    "don't", "dont", "do not", "not", "no", "never",
    "without", "avoid", "cancel", "stop", "undo",
    "won't", "wont", "shouldn't", "shouldnt",
}

# Reversible context modifiers — stakes score multiplied by 0.4
REVERSIBLE_MODIFIERS = {
    "draft", "preview", "stage", "staging", "test", "testing",
    "sandbox", "review", "check", "validate", "simulate",
    "mock", "temp", "temporary", "example", "sample",
}

# Domain signals — each adds +1.5, capped at +4.5
HIGH_STAKES_DOMAINS = {
    # Finance
    "payment", "billing", "bank", "wire transfer", "transaction",
    "payroll", "tax", "compliance", "audit", "invoice", "revenue",
    # Legal
    "agreement", "liability", "lawsuit", "legal", "terms of service",
    "privacy policy", "gdpr", "regulation", "violation",
    # Medical
    "patient", "diagnosis", "prescription", "dosage", "clinical", "medical",
    # Security
    "credentials", "api key", "password", "secret", "access token",
    "permission", "authentication", "breach", "vulnerability",
    # Infrastructure
    "database migration", "schema", "prod server", "infrastructure",
    "disaster recovery", "backup", "rollback",
}

# Intent signals
COMMAND_STARTERS = {
    "deploy", "send", "run", "execute", "publish", "generate",
    "create", "delete", "drop", "migrate", "update", "push",
    "submit", "charge", "pay", "transfer", "email", "blast",
}
QUESTION_STARTERS = {"what", "how", "why", "when", "where", "is", "are", "can"}
URGENCY_MARKERS   = {
    "now", "immediately", "urgent", "asap", "right away",
    "critical", "emergency", "time-sensitive",
}
HYPOTHETICAL = {
    "would", "could", "should", "might", "if i", "what if",
    "suppose", "imagine", "hypothetically",
}

# Audience signals
EXTERNAL_SIGNALS = {
    "client", "customer", "stakeholder", "board", "investor",
    "user", "public", "partner", "vendor", "executive", "ceo", "cto",
}
INTERNAL_SIGNALS = {
    "internally", "dev", "developer", "engineer", "team",
    "ourselves", "sandbox", "test environment", "local",
}

# Write tool markers for tool-call signal
WRITE_TOOL_MARKERS = {
    "write", "create", "delete", "update", "send", "execute",
    "post", "put", "patch", "remove", "insert", "modify",
}

NEGATION_WINDOW = 30  # chars before keyword to scan for negation


# ── Helper: Negation detection ────────────────────────────────────────────────

def _is_negated(prompt_lower: str, keyword_start: int) -> bool:
    """
    Returns True if a negation word appears within NEGATION_WINDOW chars
    before the keyword at keyword_start.

    Example: "don't deploy to production" — 'deploy' is negated.
    """
    window = prompt_lower[max(0, keyword_start - NEGATION_WINDOW): keyword_start]
    for neg in NEGATION_WORDS:
        if re.search(r'\b' + re.escape(neg) + r'\b', window):
            return True
    return False


# ── Layer 1: Feature Extraction ───────────────────────────────────────────────

def _is_reversible_context(prompt_lower: str, keyword_start: int, window: int = 40) -> bool:
    """Returns True if a reversible context modifier is detected near the keyword."""
    start = max(0, keyword_start - window)
    end = min(len(prompt_lower), keyword_start + window)
    local = prompt_lower[start:end]
    return any(re.search(r'\b' + re.escape(mod) + r'\b', local) for mod in REVERSIBLE_MODIFIERS)


def _count_kw(kws: list, prompt_lower: str, check_reversibility: bool = False) -> float:
    """
    Counts how many keywords from kws appear in prompt_lower (word-boundary matched),
    skipping any keyword that is preceded by a negation word within 30 chars.
    If check_reversibility is True, matches in a reversible context are discounted (0.4).
    """
    count = 0.0
    for kw in kws:
        m = re.search(r'\b' + re.escape(kw) + r'\b', prompt_lower)
        if m and not _is_negated(prompt_lower, m.start()):
            if check_reversibility and _is_reversible_context(prompt_lower, m.start()):
                count += 0.4
            else:
                count += 1.0
    return count


def _has_kw(kws, prompt_lower: str) -> bool:
    """Boolean check: at least one keyword present (no negation check — for simple/other sets)."""
    if isinstance(kws, set):
        kws = list(kws)
    for kw in kws:
        if re.search(r'\b' + re.escape(kw) + r'\b', prompt_lower):
            return True
    return False


def domain_boost(prompt_lower: str) -> float:
    """
    +1.5 per high-stakes domain signal detected, capped at +4.5 (3 signals max).
    Finance, legal, medical, security, infrastructure domains always carry inherent risk.
    """
    hits = sum(
        1 for d in HIGH_STAKES_DOMAINS
        if re.search(r'\b' + re.escape(d) + r'\b', prompt_lower)
    )
    return min(hits * 1.5, 4.5)


def intent_score(prompt_lower: str) -> float:
    """
    Scores the intent of the request:
      Command starters (deploy, send, execute...)  → +2.0
      Question starters (what, how, why...)        → -0.5
      Urgency markers (now, asap, critical...)     → +1.5
      Hypothetical framing (would, if i...)        → -1.0
      Exclamation mark at end                      → +0.5
    Range: -1.5 to +4.0
    """
    words = prompt_lower.split()
    first = words[0] if words else ""

    score = 0.0
    if first in COMMAND_STARTERS:
        score += 2.0
    if first in QUESTION_STARTERS:
        score -= 0.5
    if _has_kw(URGENCY_MARKERS, prompt_lower):
        score += 1.5
    if _has_kw(HYPOTHETICAL, prompt_lower):
        score -= 1.0
    if prompt_lower.rstrip().endswith("!"):
        score += 0.5
    return score


def audience_score(prompt_lower: str, context: dict) -> float:
    """
    Scores the risk based on who sees the output:
      External audience (client, customer, board...) → +1.5 per signal, max +3.0
      Internal audience (dev, engineer, team...)     → -0.5 (slight discount)
      context["destination"] == "client"             → +2.5 (definitive override)
    Range: -0.5 to +3.0
    """
    if context.get("destination") == "client":
        return 2.5

    ext  = sum(1 for kw in EXTERNAL_SIGNALS
               if re.search(r'\b' + re.escape(kw) + r'\b', prompt_lower))
    int_ = sum(1 for kw in INTERNAL_SIGNALS
               if re.search(r'\b' + re.escape(kw) + r'\b', prompt_lower))

    if ext > int_:
        return min(ext * 1.5, 3.0)
    if int_ > ext:
        return -0.5
    return 0.0


def _tool_name(t):
    if isinstance(t, dict):
        return (t.get("name") or "").lower()
    return (getattr(t, "name", "") or "").lower()


def tool_signal_score(tools: Optional[list]) -> float:
    """
    Scores based on provided tools (function calling):
      +0.5 per tool (up to +2.0 base)
      +0.75 extra per write/mutating tool (delete, update, send, post, etc.)
    Range: 0 to +3.5
    """
    if not tools:
        return 0.0
    base = min(len(tools) * 0.5, 2.0)
    write_count = sum(
        1 for t in tools
        if any(m in _tool_name(t) for m in WRITE_TOOL_MARKERS)
    )
    return base + (write_count * 0.75)


def extract_features(
    prompt: str,
    context: Optional[dict] = None,
    tools: Optional[list] = None,
) -> dict:
    """
    Extracts all scoring features from the prompt in < 1ms. No API calls.

    Returns a dict with all signals needed by decision_score().
    """
    if context is None:
        context = {}

    prompt_lower = prompt.lower()

    # Irreversibility multiplier is now integrated into _count_kw for stakes
    stakes_count    = _count_kw(STAKES_KEYWORDS, prompt_lower, check_reversibility=True)    # negation-aware, reversibility-aware
    reasoning_count = _count_kw(REASONING_KEYWORDS, prompt_lower)
    has_simple      = _has_kw(SIMPLE_KEYWORDS, prompt_lower)

    return {
        "length":               len(prompt),
        "stakes_count":         stakes_count,
        "reasoning_count":      reasoning_count,
        "irreversibility_mult": 1.0,
        "has_stakes_keywords":  stakes_count > 0,   # kept for bypass logic compat
        "has_reasoning_keywords": reasoning_count > 0,
        "has_numbers":          bool(re.search(r'\d', prompt)),
        "is_user_facing":       context.get("destination") == "client",
        "conversation_depth":   context.get("turn_count", 0),
        "has_simple_keywords":  has_simple,
        "domain_boost":         domain_boost(prompt_lower),
        "intent_score":         intent_score(prompt_lower),
        "audience_score":       audience_score(prompt_lower, context),
        "tool_signal":          tool_signal_score(tools),
        "prompt_lower":         prompt_lower,  # cached for reuse
    }


# ── Layer 2: Bypass Logic ─────────────────────────────────────────────────────

def needs_llm_classification(features: dict) -> bool:
    """
    Returns False if features strongly point to a specific tier (skip LLM).
    Returns True for ambiguous cases that need LLM confirmation.

    Clear-cut → LLM bypassed:
      - High stakes keyword present     → STRONG, skip LLM
      - User-facing context             → STRONG, skip LLM
      - Very simple (short, no numbers, simple keyword) → FAST, skip LLM

    Ambiguous → LLM called:
      Everything else
    """
    if features["has_stakes_keywords"] or features["is_user_facing"]:
        return False

    if (features["length"] < 50
            and not features["has_numbers"]
            and features["has_simple_keywords"]):
        return False

    return True


# ── Layer 2b: Hedged LLM Classifier ─────────────────────────────────────────

async def _call_one_classifier(
    provider: str,
    model_id: str,
    messages: list,
) -> Optional[float]:
    """
    Single classifier call to one provider.
    Returns float 0.0–1.0 on success, None on any failure or timeout.
    """
    try:
        result = await asyncio.wait_for(
            call_candidate(
                provider=provider,
                model_id=model_id,
                messages=messages,
                max_tokens=10,
                tier="fast",
                temperature=0.0,
            ),
            timeout=CLASSIFIER_TIMEOUT,
        )
        if result.success and result.content:
            m = re.search(r"0\.\d+|1\.0|[01]", result.content)
            if m:
                return max(0.0, min(1.0, float(m.group(0))))
    except (asyncio.TimeoutError, Exception) as e:
        logger.debug(f"Pulse classifier {provider}/{model_id} failed: {e}")
    return None


async def llm_classify_confidence(prompt: str) -> float:
    """
    Hedged request classifier — low cost, low latency, high reliability.

    Strategy:
      1. Send to PRIMARY (first healthy provider from CLASSIFIER_MODELS).
      2. Wait HEDGE_WINDOW_SECS (300ms).
      3. Primary responds fast → done (1 token call, ~200ms typical).
      4. Primary slow → send BACKUP simultaneously (hedge triggered).
      5. First valid float from either → WINNER, cancel loser immediately.
      6. Both fail within CLASSIFIER_TIMEOUT → return 0.5 (neutral, no blocking).

    Max active providers at once: 2 (never more).
    Token cost: ~1x (95% of cases), ~1.5x (5% of cases). Not 7x.
    """
    # Pick all healthy providers in priority order
    active_ids = {p["id"] for p in get_active_providers()}
    candidates = []
    for entry in CLASSIFIER_MODELS:
        if entry["provider"] not in active_ids:
            continue
        if await check_availability(entry["provider"], entry["model_id"]):
            candidates.append((entry["provider"], entry["model_id"]))

    if not candidates:
        logger.warning("Pulse classifier: no healthy provider available — defaulting to 0.5")
        return 0.5

    messages = [
        {
            "role": "system",
            "content": (
                "You are a classifier. Rate the complexity and stakes of the user's "
                "request on a scale from 0.0 to 1.0. Return ONLY the float, nothing else."
            ),
        },
        {"role": "user", "content": prompt},
    ]

    primary = candidates[0]
    backup  = candidates[1] if len(candidates) > 1 else None

    # Step 1: Start primary call
    primary_task = asyncio.create_task(
        _call_one_classifier(*primary, messages)
    )

    # Step 2: Wait hedge window — if primary responds fast, we're done
    try:
        result = await asyncio.wait_for(
            asyncio.shield(primary_task),
            timeout=HEDGE_WINDOW_SECS,
        )
        if result is not None:
            logger.debug(
                f"Pulse classifier: {primary[0]}/{primary[1]} responded in hedge window "
                f"→ {result:.2f} (1 token call)"
            )
            return result
    except asyncio.TimeoutError:
        pass  # primary slow → hedge triggered

    # Step 3: Primary slow — start backup simultaneously if available
    if backup:
        logger.debug(
            f"Pulse classifier: hedge triggered "
            f"(primary={primary[0]} slow) → adding backup={backup[0]}"
        )
        backup_task = asyncio.create_task(
            _call_one_classifier(*backup, messages)
        )
        tasks = {primary_task, backup_task}
    else:
        tasks = {primary_task}

    # Step 4: Race — first valid float wins
    remaining = CLASSIFIER_TIMEOUT - HEDGE_WINDOW_SECS  # ~1.2s left
    done, pending = await asyncio.wait(
        tasks,
        timeout=remaining,
        return_when=asyncio.FIRST_COMPLETED,
    )

    # Cancel loser immediately
    for t in pending:
        t.cancel()

    # Return first valid result
    for t in done:
        try:
            r = t.result()
            if r is not None:
                logger.debug(f"Pulse classifier: race result → {r:.2f}")
                return r
        except Exception:
            pass

    logger.warning("Pulse classifier: all providers failed — defaulting to 0.5")
    return 0.5


# ── Layer 3: Weighted Decision Score ─────────────────────────────────────────

def decision_score(features: dict, llm_confidence: Optional[float] = None, embedding_score: Optional[float] = None) -> float:
    """
    Computes the Pulse v3 raw score and applies Platt Scaling.
    
    Formula:
      stakes_count × 3.0 × irreversibility_mult
      + reasoning_count × 1.0
      + user_facing × 2.5
      + has_numbers × 1.5
      + has_simple × -1.0
      + depth × 0.5 (capped at 5 turns = max +2.5)
      + domain_boost (0 to +4.5)
      + intent_score (-1.5 to +4.0)
      + audience_score (-0.5 to +3.0)
      + tool_signal (0 to +3.5)
      + llm_confidence × 3.0 (ambiguous cases only, 0..+3.0)
      + embedding_score × 5.0 (ambiguous cases only, 0..+5.0)
    """
    raw_score = 0.0

    # Stakes — per-keyword, negation-aware, reversibility-multiplied
    raw_score += features.get("stakes_count", 0) * 3.0 * features.get("irreversibility_mult", 1.0)

    # Reasoning — per-keyword count
    raw_score += features.get("reasoning_count", 0) * 1.0

    # Boolean features
    raw_score += 2.5 if features.get("is_user_facing")    else 0.0
    raw_score += 1.5 if features.get("has_numbers")       else 0.0
    raw_score -= 1.0 if features.get("has_simple_keywords") else 0.0

    # Conversation depth (capped at 5 turns)
    raw_score += 0.5 * min(features.get("conversation_depth", 0), 5)

    # v3 new signals
    raw_score += features.get("domain_boost",    0.0)
    raw_score += features.get("intent_score",    0.0)
    raw_score += features.get("audience_score",  0.0)
    raw_score += features.get("tool_signal",     0.0)

    # LLM confidence contribution (ambiguous cases only)
    if llm_confidence is not None:
        raw_score += llm_confidence * 3.0
        
    # Embedding contribution
    if embedding_score is not None:
        raw_score += embedding_score * 5.0

    # Platt Scaling to calibrate into 0.0 to 10.0 smooth space
    # Formula: 10.0 / (1.0 + exp(-(A * raw_score + B)))
    A = 0.35
    B = -1.75
    try:
        calibrated_score = 10.0 / (1.0 + math.exp(-(A * raw_score + B)))
    except OverflowError:
        calibrated_score = 10.0 if raw_score > 0 else 0.0

    return calibrated_score



# ── Main Entry Points ─────────────────────────────────────────────────────────

async def classify_tier(
    prompt: str,
    context: Optional[dict] = None,
    tools: Optional[list] = None,
    caller_id: Optional[str] = None,
) -> tuple[str, float, bool]:
    """
    Pulse v3 auto-classifier core pipeline.

    L0: Redis cache  → return instantly on hit (0ms, 0 tokens)
    L1: Keyword score → return on definitive score (< 1ms, 0 tokens)
    L2: Hedged LLM   → primary + backup-if-slow (ambiguous only)
    L3: Cache result → store in Redis for 1 hour

    Returns (tier, score, used_llm).
    """
    # L0: Redis cache check
    cached = await get_pulse_cache(prompt)
    if cached:
        logger.debug(f"Pulse cache HIT → {cached}")
        return cached, 0.0, False

    # L1: Feature extraction + keyword scoring
    features = extract_features(prompt, context, tools)
    score    = decision_score(features)

    # L1 definitive early exits (no LLM needed)
    fast_t, strong_t = await get_caller_thresholds(caller_id or "default")
    
    # BUG-25 Fix: Force STRONG tier for high-stakes keywords or user-facing context
    if features.get("has_stakes_keywords") or features.get("is_user_facing"):
        tier = "strong"
        await set_pulse_cache(prompt, tier)
        logger.info(f"Pulse: score={score:.2f} (explicitly strong context) → {tier}")
        return tier, score, False

    # Note: Using Platt scaled threshold equivalents for early exits (approx > 9.0 and < 0.35)
    if score >= 9.0:
        tier = "strong"
        await set_pulse_cache(prompt, tier)
        logger.info(f"Pulse: score={score:.2f} (keyword definitive) → {tier}")
        return tier, score, False
    if score <= 0.35:
        tier = "fast"
        await set_pulse_cache(prompt, tier)
        logger.info(f"Pulse: score={score:.2f} (keyword definitive) → {tier}")
        return tier, score, False

    # L2: Hedged LLM + Embedding for ambiguous cases
    llm_confidence = None
    embedding_score = None
    if needs_llm_classification(features):
        # Run LLM Classifier and Embedding Classifier concurrently!
        llm_task = asyncio.create_task(llm_classify_confidence(prompt))
        emb_task = asyncio.create_task(compute_embedding_score(prompt))
        
        llm_confidence, embedding_score = await asyncio.gather(llm_task, emb_task)
        
        score = decision_score(features, llm_confidence, embedding_score)

    # Tier decision
    if score >= strong_t:
        tier = "strong"
    elif score >= fast_t:
        tier = "mid"
    else:
        tier = "fast"

    # L3: Cache result
    await set_pulse_cache(prompt, tier)

    logger.info(
        f"Pulse: score={score:.2f} llm_conf={llm_confidence} emb_score={embedding_score} "
        f"irr_mult={features['irreversibility_mult']:.1f} "
        f"domain={features['domain_boost']:.1f} "
        f"intent={features['intent_score']:.1f} → {tier}"
    )
    return tier, score, (llm_confidence is not None)


async def resolve_tier(
    prompt: str,
    explicit_tier: Optional[str] = None,
    context: Optional[dict] = None,
    tools: Optional[list] = None,
    caller_id: Optional[str] = None,
) -> tuple[str, Optional[float], Optional[bool]]:
    """
    Integration point for the /v1/complete endpoint.

    If explicit_tier is provided, manual routing wins (Pulse is skipped entirely).
    Callers must validate explicit_tier before calling — validation enforced in main.py (BUG-11).

    Returns (tier, decision_score, used_llm).
    """
    if explicit_tier is not None:
        logger.debug(f"Manual tier override: {explicit_tier}")
        return explicit_tier, None, None
    # Auto-resolve using Pulse v3
    tier, score, used_llm = await classify_tier(prompt, context, tools, caller_id)
    return tier, score, used_llm
