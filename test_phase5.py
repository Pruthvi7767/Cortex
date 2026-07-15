"""
test_phase5.py — Phase 5 validation for Pulse Auto-Classifier.

Tests the tier selection logic:
1. Feature extraction
2. Bypass logic (needs_llm_classification)
3. Decision scoring
4. End-to-end classify_tier (with mocked LLM)
5. resolve_tier manual override
6. resolve_tier auto fallback
"""

import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from classifier import (
    extract_features,
    needs_llm_classification,
    decision_score,
    classify_tier,
    resolve_tier
)

_results = []

def report(n: int, ok: bool, msg: str):
    status = "OK" if ok else "FAIL"
    line = f"{n}. [{status}] {msg}"
    _results.append((ok, line))
    print(line)


async def test1_extract_features():
    """Extract features identifies stakes, simple keywords, and numbers."""
    # Stakes + numbers
    feat1 = extract_features("Here is the final invoice for $1000", context={"destination": "client"})
    ok1 = (feat1["has_stakes_keywords"] is True and 
           feat1["has_numbers"] is True and 
           feat1["is_user_facing"] is True and
           feat1["has_simple_keywords"] is False)
           
    # Simple, no numbers
    feat2 = extract_features("what is love?")
    ok2 = (feat2["has_simple_keywords"] is True and 
           feat2["has_stakes_keywords"] is False and 
           feat2["has_numbers"] is False)
           
    # Reasoning
    feat3 = extract_features("analyze this step by step")
    ok3 = (feat3["has_reasoning_keywords"] is True and 
           feat3["has_stakes_keywords"] is False)

    ok = ok1 and ok2 and ok3
    report(1, ok, f"extract_features correctly identifies features: {ok1}, {ok2}, {ok3}")


async def test2_needs_llm_classification():
    """Bypass logic correctly skips obvious cases and flags ambiguous ones."""
    # Obvious simple prompt: short, no numbers, simple keyword -> bypass (False)
    # wait, length < 50 AND no numbers AND simple keywords.
    feat_simple = extract_features("what is 2+2") 
    # "what is 2+2" has a number. So it might need LLM if my logic is exact. Let's make it word based to be safe.
    feat_simple_text = extract_features("what is the capital of france")
    needs_simple = needs_llm_classification(feat_simple_text)
    
    # Obvious high stakes: "final invoice"
    feat_stakes = extract_features("write the final invoice for client X")
    needs_stakes = needs_llm_classification(feat_stakes)
    
    # Ambiguous mid-length prompt
    feat_ambig = extract_features("Consider the implications of quantum computing on modern cryptography protocols. Discuss potential solutions.")
    needs_ambig = needs_llm_classification(feat_ambig)

    ok = (needs_simple is False and needs_stakes is False and needs_ambig is True)
    report(2, ok, f"needs_llm_classification: simple={needs_simple}(F), stakes={needs_stakes}(F), ambig={needs_ambig}(T)")


async def test3_decision_score():
    """decision_score produces higher scores for stakes vs simple."""
    feat_stakes = extract_features("final report", context={"destination": "client"})
    score_stakes = decision_score(feat_stakes, llm_confidence=None) # 3.0(stakes) + 2.5(client) = 5.5
    
    feat_simple = extract_features("what is this")
    score_simple = decision_score(feat_simple, llm_confidence=None) # -1.0(simple)
    
    feat_ambig = extract_features("analyze this data")
    score_ambig = decision_score(feat_ambig, llm_confidence=0.8) # 1.0(reasoning) + 0.8*3.0 = 3.4
    
    ok = (score_stakes > score_ambig > score_simple)
    report(3, ok, f"decision_score ranking: stakes({score_stakes}) > ambig({score_ambig}) > simple({score_simple})")


@patch("classifier.llm_classify_confidence", new_callable=AsyncMock)
async def test4_classify_tier(mock_llm):
    """classify_tier end-to-end with mocked LLM. classify_tier returns (tier, score, used_llm)."""
    mock_llm.return_value = 0.9  # high confidence when called

    # Simple -> fast (bypass LLM, score=-1.0)
    tier_simple, score_simple, used_llm_simple = await classify_tier("what is love")

    # Stakes -> strong (bypass LLM, score=5.5)
    tier_stakes, score_stakes, used_llm_stakes = await classify_tier(
        "deploy the production contract", context={"destination": "client"}
    )

    # Ambiguous -> mid (calls LLM: reasoning=1.0 + llm_conf=0.9*3.0=2.7 → score=3.7)
    tier_ambig, score_ambig, used_llm_ambig = await classify_tier("analyze this data thoroughly")

    ok = (tier_simple == "fast" and tier_stakes == "strong" and tier_ambig == "mid"
          and used_llm_simple == False and used_llm_stakes == False and used_llm_ambig == True)
    report(4, ok, f"classify_tier e2e: simple={tier_simple}(score={score_simple:.1f}), "
                  f"stakes={tier_stakes}(score={score_stakes:.1f}), "
                  f"ambig={tier_ambig}(score={score_ambig:.1f}, used_llm={used_llm_ambig})")


@patch("classifier.classify_tier", new_callable=AsyncMock)
async def test5_resolve_tier_override(mock_classify):
    """resolve_tier with explicit_tier returns immediately without calling classify_tier."""
    tier, score, used_llm = await resolve_tier("test prompt", explicit_tier="strong")
    ok = (tier == "strong" and mock_classify.call_count == 0 and score is None and used_llm is None)
    report(5, ok, f"resolve_tier manual override: tier={tier}, score={score}, classify_tier_calls={mock_classify.call_count}")


@patch("classifier.classify_tier", new_callable=AsyncMock)
async def test6_resolve_tier_auto(mock_classify):
    """resolve_tier without explicit_tier calls classify_tier."""
    # classify_tier returns (tier, score, used_llm)
    mock_classify.return_value = ("mid", 3.5, True)
    tier, score, used_llm = await resolve_tier("test prompt", explicit_tier=None)
    ok = (tier == "mid" and mock_classify.call_count == 1)
    report(6, ok, f"resolve_tier auto fallback: tier={tier}, classify_tier_calls={mock_classify.call_count}")


async def run_all():
    print("=== Phase 5 Test Script ===\n")
    
    await test1_extract_features()
    await test2_needs_llm_classification()
    await test3_decision_score()
    await test4_classify_tier()
    await test5_resolve_tier_override()
    await test6_resolve_tier_auto()
    
    print("\n=== Summary ===")
    passed = sum(1 for ok, _ in _results if ok)
    failed = sum(1 for ok, _ in _results if not ok)
    print(f"{passed}/{len(_results)} tests passed")
    if failed:
        import sys
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(run_all())
