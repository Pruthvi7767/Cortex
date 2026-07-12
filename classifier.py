"""
classifier.py — Pulse Auto-Classifier for Tier Selection.

Implements the 3-layer (plus Layer 4 placeholder) tier classification logic
for deciding between "strong", "mid", and "fast" tiers when a caller doesn't
explicitly specify one.

Manual routing overrides Pulse completely: if a tier is requested, Pulse is skipped.
"""

import re
import logging
from typing import Optional

from race import call_candidate

logger = logging.getLogger("cortex.pulse")

# ── Layer 1: Cheap feature extraction ──────────────────────────────────────

STAKES_KEYWORDS = {
    "final", "send to client", "publish", "report", "invoice", "quote",
    "contract", "production", "deploy"
}

REASONING_KEYWORDS = {
    "analyze", "design", "architecture", "compare", "explain why",
    "solve", "step by step", "evaluate", "synthesize", "debug"
}

SIMPLE_KEYWORDS = {
    "what is", "define", "translate", "summarize in one line",
    "say hello", "hello", "hi", "ping"
}


def extract_features(prompt: str, context: Optional[dict] = None) -> dict:
    """
    Extracts cheap text features from the prompt in < 1ms.
    No API calls.
    """
    if context is None:
        context = {}

    prompt_lower = prompt.lower()
    
    def has_kw(kws):
        # Use word boundaries so 'hi' doesn't match inside 'this'
        pattern = r'\b(?:' + '|'.join(re.escape(k) for k in kws) + r')\b'
        return bool(re.search(pattern, prompt_lower))

    has_stakes = has_kw(STAKES_KEYWORDS)
    has_reasoning = has_kw(REASONING_KEYWORDS)
    has_simple = has_kw(SIMPLE_KEYWORDS)
    
    # Regex check for any digit
    has_numbers = bool(re.search(r'\d', prompt))
    
    is_user_facing = (context.get("destination") == "client")
    conversation_depth = context.get("turn_count", 0)

    return {
        "length": len(prompt),
        "has_stakes_keywords": has_stakes,
        "has_numbers": has_numbers,
        "is_user_facing": is_user_facing,
        "conversation_depth": conversation_depth,
        "has_reasoning_keywords": has_reasoning,
        "has_simple_keywords": has_simple,
    }


# ── Layer 2: Bypass logic ──────────────────────────────────────────────────

def needs_llm_classification(features: dict) -> bool:
    """
    Determines if we can confidently skip the LLM classification step.
    Returns False if features strongly point to a specific tier already.
    Returns True for ambiguous cases.
    """
    # High stakes always go to strong, no need for LLM confirmation
    if features["has_stakes_keywords"] or features["is_user_facing"]:
        return False
        
    # Very simple, short, number-less prompts obviously go to fast
    if (features["length"] < 50 
        and not features["has_numbers"] 
        and features["has_simple_keywords"]):
        return False
        
    # Ambiguous cases need the LLM
    return True


# ── Layer 2b: Lightweight LLM classification ───────────────────────────────

async def llm_classify_confidence(prompt: str) -> float:
    """
    Calls a fast-tier model to estimate how "high-stakes/complex" the prompt is.
    Returns a float between 0.0 and 1.0. 
    Defaults to 0.5 on parsing failure or network error.
    """
    messages = [
        {
            "role": "system",
            "content": "You are a classifier. Rate the complexity and stakes of the user's request on a scale from 0.0 to 1.0. Return ONLY the float number, nothing else."
        },
        {
            "role": "user", 
            "content": prompt
        }
    ]
    
    # Using a fast-tier model from our registry
    provider = "nvidia"
    model_id = "meta/llama-3.1-8b-instruct" 
    
    logger.debug(f"Calling LLM classifier for prompt: {prompt[:50]}...")
    
    try:
        # Reuse Phase 4's call_candidate
        result = await call_candidate(
            provider=provider,
            model_id=model_id,
            messages=messages,
            max_tokens=10,
            tier="fast"
        )
        
        if result.success and result.content:
            # Try to extract a float from the response
            match = re.search(r"0\.\d+|1\.0|0|1", result.content)
            if match:
                confidence = float(match.group(0))
                return max(0.0, min(1.0, confidence)) # clamp 0-1
                
    except Exception as e:
        logger.warning(f"LLM classification failed: {e}")
        
    # Default to neutral on any failure
    return 0.5


# ── Layer 3: Weighted Decision Score ───────────────────────────────────────

def decision_score(features: dict, llm_confidence: Optional[float] = None) -> float:
    """
    Calculates the final tier score based on extracted features and optional LLM confidence.
    """
    score = 0.0
    score += 3.0 if features["has_stakes_keywords"] else 0
    score += 2.5 if features["is_user_facing"] else 0
    score += 1.5 if features["has_numbers"] else 0
    score += 1.0 if features["has_reasoning_keywords"] else 0
    score -= 1.0 if features["has_simple_keywords"] else 0
    
    # Cap conversation depth contribution
    score += 0.5 * min(features["conversation_depth"], 5)
    
    if llm_confidence is not None:
        score += llm_confidence * 3.0
        
    return score


# ── Layer 4: Adaptive Thresholds (Placeholder) ─────────────────────────────

def get_adaptive_threshold(task_role: Optional[str] = None) -> dict:
    """
    PLACEHOLDER: This will eventually read historical outcome data from Supabase 
    (Phase 6+) and adjust thresholds per task_role/feature combo based on real 
    success/failure patterns.
    
    NOT implemented in Phase 5 — returns fixed default thresholds for now.
    """
    return {
        "fast_threshold": 2.0,
        "strong_threshold": 5.0
    }


# ── Main Entry Points ──────────────────────────────────────────────────────

async def classify_tier(prompt: str, context: Optional[dict] = None) -> str:
    """
    Pulse Auto-classifier core pipeline.
    Returns "strong", "mid", or "fast".
    """
    features = extract_features(prompt, context)
    
    llm_confidence = None
    if needs_llm_classification(features):
        llm_confidence = await llm_classify_confidence(prompt)
        
    score = decision_score(features, llm_confidence)
    thresholds = get_adaptive_threshold()
    
    if score >= thresholds["strong_threshold"]:
        tier = "strong"
    elif score >= thresholds["fast_threshold"]:
        tier = "mid"
    else:
        tier = "fast"
        
    logger.info(
        f"Pulse classification: score={score:.2f} (llm_conf={llm_confidence}) -> {tier}"
    )
    return tier


async def resolve_tier(
    prompt: str, 
    explicit_tier: Optional[str] = None, 
    context: Optional[dict] = None
) -> str:
    """
    Integration point for Phase 7 endpoint.
    If explicit_tier is provided, manual routing wins (skips Pulse).
    Otherwise, invokes Pulse auto-classification.
    """
    if explicit_tier is not None:
        logger.debug(f"Manual tier override: {explicit_tier}")
        return explicit_tier
        
    return await classify_tier(prompt, context)
