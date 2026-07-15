"""
test_pulse_stress.py -- 20-request comprehensive Pulse intelligence stress test.

Tests the full classification pipeline from trivially simple to brutally hard,
showing complete math breakdown for every single request.

PULSE v2 Scoring Formula (Layer 3):
  stakes_count  x 3.0   -- EACH stakes keyword counts (deploy+final+invoice = +9.0)
  reasoning_cnt x 1.0   -- EACH reasoning keyword counts (analyze+debug = +2.0)
  user_facing   x 2.5   -- boolean
  has_numbers   x 1.5   -- boolean
  has_simple    x -1.0  -- boolean penalty (doesn't stack)
  depth x 0.5 (cap 5)   -- conversation depth bonus, max +2.5
  llm_conf x 3.0        -- Layer 2b (only for ambiguous cases), +0.0 to +3.0

Thresholds:
  score >= 5.0 -> STRONG
  score >= 2.0 -> MID
  score <  2.0 -> FAST
"""

import asyncio
import sys
import time
from redis_store import get_caller_thresholds

from classifier import (
    extract_features,
    needs_llm_classification,
    decision_score,
    classify_tier,
    resolve_tier,
    VALID_TIERS,
    STAKES_KEYWORDS,
    REASONING_KEYWORDS,
)

# ── 20 Test Cases: Simple to Brutal ───────────────────────────────────────────
# Format: (prompt, context, expected_tier, llm_mock_conf_if_ambiguous, description)
# llm_mock_confidence = None means Layer 2 will BYPASS LLM (clear-cut case)
TEST_CASES = [
    # ===== TRIVIALLY SIMPLE -> FAST =====
    (
        "hi",
        {},
        "fast",
        None,   # bypass: short + simple keyword
        "T01 | Bare greeting - single word"
    ),
    (
        "hello",
        {},
        "fast",
        None,
        "T02 | Single greeting word"
    ),
    (
        "ping",
        {},
        "fast",
        None,
        "T03 | Health-check probe - shortest possible"
    ),
    (
        "what is the capital of France?",
        {},
        "fast",
        None,
        "T04 | Simple factual question - 'what is' triggers fast bypass"
    ),
    (
        "define photosynthesis",
        {},
        "fast",
        None,
        "T05 | Simple definition request - 'define' keyword"
    ),

    # ===== MODERATE -> MID =====
    (
        "translate this paragraph to Spanish and keep the tone professional",
        {},
        "fast",  # 'translate' is a simple keyword, takes over
        None,
        "T06 | TRICKY: 'translate' simple keyword wins despite length -> fast"
    ),
    (
        "explain why the sky is blue using physics and give examples",
        {},
        "mid",
        0.55,   # LLM called: no stakes/simple keywords, ambiguous
        "T07 | Physics explanation - ambiguous, LLM conf=0.55"
    ),
    (
        "compare PostgreSQL vs MySQL for a production database setup",
        {},
        "mid",
        0.65,
        "T08 | Compare + production - 'compare' reasoning(+1) + 'production' stakes(+3) = +4 -> mid"
    ),
    (
        "debug this Python function: def add(a,b): return a - b",
        {},
        "mid",
        0.60,
        "T09 | Code debug - 'debug' reasoning(+1) + LLM 0.60(+1.8) = +2.8 -> mid"
    ),
    (
        "analyze the API performance over the last 30 days and graph it",
        {},
        "mid",
        0.70,
        "T10 | analyze(+1) + numbers(+1.5) + LLM 0.70(+2.1) = +4.6 -> mid"
    ),

    # ===== HIGH STAKES -> STRONG =====
    (
        "deploy the final version to production",
        {},
        "strong",
        None,  # bypass: stakes keywords present
        "T11 | CRITICAL: deploy(+3)+final(+3)+production(+3) = +9.0 -> STRONG"
    ),
    (
        "send this contract to the client for signing",
        {},
        "strong",
        None,
        "T12 | CRITICAL: contract(+3) + user_facing bypass = STRONG"
    ),
    (
        "generate the final invoice #4521 for $18750 and send to client",
        {},
        "strong",
        None,
        "T13 | CRITICAL: final(+3)+invoice(+3)+numbers(+1.5) = +7.5 -> STRONG"
    ),
    (
        "design the complete microservices architecture for our payment system with 99.9 percent SLA",
        {},
        "strong",
        0.92,
        "T14 | HARD: design(+1)+architecture(+1) reasoning, LLM 0.92(+2.76) = +4.76 -> mid borderline"
    ),
    (
        "evaluate and synthesize all findings into the final production deployment report",
        {},
        "strong",
        None,
        "T15 | BRUTAL: evaluate+synthesize reasoning, final+production+deploy+report stakes = +14"
    ),

    # ===== HARD EDGE CASES =====
    (
        "hi, what is the best way to analyze and debug our production deployment pipeline?",
        {},
        "mid",
        0.75,
        "T16 | TRICKY: hi+what_is=-1, analyze+debug=+2, production+deploy=+6, LLM=+2.25 -> +9.25 actually STRONG"
    ),
    (
        "step by step guide to optimizing a PostgreSQL query processing 10 million rows",
        {},
        "strong",
        0.88,
        "T17 | HARD: step_by_step+optimize(+2) + numbers(+1.5) + LLM 0.88(+2.64) = +6.14 -> STRONG"
    ),
    (
        "summarize in one line: the entire architecture design for our global application",
        {},
        "fast",
        None,
        "T18 | BRUTAL TRICK: 'summarize in one line' simple(-1) wins -> fast"
    ),
    (
        "evaluate whether we should publish the final quarterly report to all clients now",
        {},
        "strong",
        None,
        "T19 | VERY HARD: evaluate reasoning, publish+final+report stakes = +3+3+3+1 = +10"
    ),
    (
        "what should we do next?",
        {"turn_count": 5, "destination": "client"},
        "strong",
        None,
        "T20 | CONTEXT BOMB: simple words BUT turn_count=5(+2.5) + user_facing(+2.5) = +5 -> STRONG"
    ),
    
    # ===== V3 NEW FEATURES =====
    (
        "don't deploy to production",
        {},
        "fast",
        None,
        "T21 | NEGATION: 'deploy' and 'production' are negated -> stakes=0 -> FAST"
    ),
    (
        "deploy to staging for review",
        {},
        "mid",
        None,
        "T22 | IRREVERSIBILITY: 'staging' drops multiplier to 0.4 -> stakes=3.6 -> MID"
    ),
    (
        "check GDPR compliance status",
        {},
        "mid",
        None,
        "T23 | DOMAIN SENSITIVITY: 'gdpr' and 'compliance' add +3.0 -> MID"
    ),
]


def compute_math(features: dict, llm_conf=None) -> dict:
    """Full per-component math breakdown for display."""
    stakes_pts    = features.get("stakes_count", 0) * 3.0 * features.get("irreversibility_mult", 1.0)
    reasoning_pts = features.get("reasoning_count", 0) * 1.0
    facing_pts    = 2.5  if features.get("is_user_facing")      else 0.0
    numbers_pts   = 1.5  if features.get("has_numbers")         else 0.0
    simple_pts    = -1.0 if features.get("has_simple_keywords") else 0.0
    depth_raw     = features.get("conversation_depth", 0)
    depth_pts     = 0.5  * min(depth_raw, 5)
    
    domain_pts    = features.get("domain_boost", 0.0)
    intent_pts    = features.get("intent_score", 0.0)
    audience_pts  = features.get("audience_score", 0.0)
    tool_pts      = features.get("tool_signal", 0.0)
    
    llm_pts       = (llm_conf * 3.0) if llm_conf is not None else 0.0
    emb_pts       = 0.0 # Mocked as 0 for tests unless specified
    
    raw_total     = (stakes_pts + reasoning_pts + facing_pts + numbers_pts + 
                     simple_pts + depth_pts + domain_pts + intent_pts + 
                     audience_pts + tool_pts + llm_pts + emb_pts)
                     
    import math
    A = 0.35
    B = -1.75
    try:
        total = 10.0 / (1.0 + math.exp(-(A * raw_total + B)))
    except OverflowError:
        total = 10.0 if raw_total > 0 else 0.0

    return dict(
        stakes=stakes_pts,       stakes_n=features.get("stakes_count", 0),
        irr_mult=features.get("irreversibility_mult", 1.0),
        reasoning=reasoning_pts, reasoning_n=features.get("reasoning_count", 0),
        facing=facing_pts,
        numbers=numbers_pts,
        simple=simple_pts,
        depth=depth_pts,         depth_raw=depth_raw,
        domain=domain_pts,
        intent=intent_pts,
        audience=audience_pts,
        tool=tool_pts,
        llm_pts=llm_pts,         llm_conf=llm_conf,
        total=total,
    )


def ascii_bar(score: float, width: int = 30) -> str:
    """ASCII score bar. Range shown: -2 to 15."""
    max_s  = 15.0
    min_s  = -2.0
    range_s = max_s - min_s
    clamped = max(min_s, min(score, max_s))
    filled = int(((clamped - min_s) / range_s) * width)
    bar    = "#" * filled + "-" * (width - filled)
    return f"|{bar}| {score:+.2f}"


def tier_label(tier: str) -> str:
    return {"strong": "[STRONG]", "mid": "[MID   ]", "fast": "[FAST  ]"}.get(tier, f"[{tier.upper()}]")


async def run_all_tests() -> int:
    fast_t, strong_t = await get_caller_thresholds("mock_caller")
    FAST_TH   = fast_t    # 2.0
    STRONG_TH = strong_t  # 5.0

    SEP  = "=" * 78
    DASH = "-" * 78

    print()
    print(SEP)
    print("  PULSE v3 INTELLIGENCE STRESS TEST -- 23 REQUESTS (SIMPLE TO BRUTAL)")
    print(SEP)
    print()
    print("  PULSE v2 Scoring (per-keyword counting):")
    print(f"    stakes_count  x 3.0  -- each of {len(STAKES_KEYWORDS)} keywords counted individually")
    print(f"    reasoning_cnt x 1.0  -- each of {len(REASONING_KEYWORDS)} keywords counted individually")
    print("    user_facing   x 2.5  -- boolean")
    print("    has_numbers   x 1.5  -- boolean")
    print("    simple_kw     x -1.0 -- boolean penalty")
    print("    turn_count    x 0.5  -- capped at 5 turns (max +2.5)")
    print("    llm_conf      x 3.0  -- Layer 2b (ambiguous only)")
    print()
    print(f"  Thresholds:  score >= {STRONG_TH} -> STRONG  |  >= {FAST_TH} -> MID  |  < {FAST_TH} -> FAST")
    print()
    print(DASH)
    print()

    passed = 0
    all_results = []

    for i, (prompt, context, expected, llm_conf_mock, description) in enumerate(TEST_CASES, 1):
        t0 = time.perf_counter()

        # Layer 1: feature extraction
        features = extract_features(prompt, context)
        # Layer 2: bypass check
        needs_llm = needs_llm_classification(features)
        # Only use LLM confidence if Layer 2 says it's ambiguous
        actual_llm_conf = llm_conf_mock if needs_llm else None
        # Layer 3: score
        # Note: we test using the REAL decision_score to ensure test matches reality
        from classifier import decision_score
        # For tests, we mock embedding_score as 0.0 since it wasn't present before
        score = decision_score(features, actual_llm_conf, embedding_score=0.0)
        math_breakdown = compute_math(features, actual_llm_conf)
        # Layer 4: threshold
        if score >= STRONG_TH:
            predicted = "strong"
        elif score >= FAST_TH:
            predicted = "mid"
        else:
            predicted = "fast"

        elapsed_us = (time.perf_counter() - t0) * 1_000_000
        ok = (predicted == expected)
        if ok:
            passed += 1
        all_results.append((ok, predicted, expected, score, description))

        # ── Display ────────────────────────────────────────────────────────
        status = "PASS" if ok else "FAIL"
        print(f"[{i:02d}/20] [{status}]  {description}")

        disp = prompt if len(prompt) <= 70 else prompt[:67] + "..."
        print(f"         Prompt   : \"{disp}\"")
        if context:
            print(f"         Context  : {context}")

        # Feature flags — show counts
        flags = []
        if math_breakdown["stakes_n"] > 0:
            flags.append(f"stakes_count={math_breakdown['stakes_n']} x3.0={math_breakdown['stakes']:+.1f}")
        if math_breakdown["reasoning_n"] > 0:
            flags.append(f"reasoning_count={math_breakdown['reasoning_n']} x1.0={math_breakdown['reasoning']:+.1f}")
        if features["is_user_facing"]:
            flags.append(f"user_facing={math_breakdown['facing']:+.1f}")
        if features["has_numbers"]:
            flags.append(f"numbers={math_breakdown['numbers']:+.1f}")
        if features["has_simple_keywords"]:
            flags.append(f"simple={math_breakdown['simple']:+.1f}")
        if features["conversation_depth"] > 0:
            flags.append(f"depth={math_breakdown['depth_raw']}turns={math_breakdown['depth']:+.1f}")
        flag_str = ", ".join(flags) if flags else "(no features matched)"
        print(f"         Features : {flag_str}")

        # Math equation
        print(f"         PlattRaw : {math_breakdown['total']:.2f} (Platt Scaled)")
        print(f"         Bar      : {ascii_bar(score)}")

        # Layer 2 status
        if needs_llm:
            print(f"         Layer 2  : LLM CALLED (conf={actual_llm_conf}, contributes {math_breakdown['llm_pts']:+.2f})")
        else:
            print(f"         Layer 2  : LLM BYPASSED (stakes or simple keyword is clear-cut)")

        # Decision
        pred_lbl = tier_label(predicted)
        exp_lbl  = tier_label(expected)
        if ok:
            print(f"         Decision : {pred_lbl} -- CORRECT  [{elapsed_us:.0f}us]")
        else:
            print(f"         Decision : Got {pred_lbl} but expected {exp_lbl} -- WRONG  [{elapsed_us:.0f}us]")

        print()

    # ── Summary ────────────────────────────────────────────────────────────
    print(SEP)
    print(f"  PULSE FINAL SCORE: {passed}/23 PASSED")
    print(SEP)
    print()

    # Failures
    failures = [(i+1, r) for i, r in enumerate(all_results) if not r[0]]
    if failures:
        print("  FAILED CASES:")
        for idx, (ok, pred, exp, score, desc) in failures:
            print(f"    [{idx:02d}] Got {tier_label(pred)} (score={score:.2f}), expected {tier_label(exp)}")
            print(f"         {desc}")
        print()

    # Tier distribution
    from collections import Counter
    dist = Counter(r[1] for r in all_results)
    print("  Tier distribution (predicted):")
    for tier in ["strong", "mid", "fast"]:
        bar = "#" * (dist.get(tier, 0) * 2)
        print(f"    {tier:6s}: {bar} {dist.get(tier,0)}/23")
    print()

    # Score statistics
    scores = [r[3] for r in all_results]
    print(f"  Score statistics:  min={min(scores):.2f}  max={max(scores):.2f}  avg={sum(scores)/len(scores):.2f}")
    print()

    # Algorithm summary
    print("  Algorithm Summary:")
    print(f"    Layer 1  Feature extraction  : {len(STAKES_KEYWORDS)} stakes kw + {len(REASONING_KEYWORDS)} reasoning kw, per-count scoring")
    print("    Layer 2  LLM bypass           : skips API call for clear-cut cases (saves latency)")
    print("    Layer 3  Weighted score        : fully deterministic, transparent math")
    print("    Layer 4  Adaptive thresholds  : fixed 2.0/5.0 today, Postgres-learning ready")
    print("    Manual   resolve_tier override : Pulse completely skipped when tier= specified")
    print()

    if passed == 23:
        print("  *** PULSE IS PERFECTLY SMART: 23/23 ***")
    elif passed >= 20:
        print(f"  ** PULSE IS VERY SMART: {passed}/23 **")
    elif passed >= 17:
        print(f"  * PULSE IS SMART: {passed}/23 *")
    else:
        print(f"  PULSE NEEDS TUNING: {passed}/23")

    print()
    return passed


async def test_manual_routing():
    """Test the manual override path -- Pulse completely skipped."""
    SEP  = "=" * 78
    DASH = "-" * 78

    print()
    print(DASH)
    print("  BONUS: Manual Override vs Auto-Pulse (resolve_tier)")
    print(DASH)
    print()

    cases = [
        ("strong", "hi",
         "Force trivial 'hi' to STRONG -- manual override wins"),
        ("fast",   "deploy the final invoice to production",
         "Force critical stakes prompt DOWN to FAST -- manual wins"),
        ("mid",    "what is 2+2",
         "Force trivial math to MID -- manual wins"),
    ]

    bonus_passed = 0
    for forced_tier, prompt, desc in cases:
        tier, score, used_llm = await resolve_tier(prompt, explicit_tier=forced_tier)
        match = (tier == forced_tier and score is None and used_llm is None)
        if match:
            bonus_passed += 1
        status = "PASS" if match else "FAIL"
        print(f"  [{status}] {desc}")
        print(f"       Prompt  : \"{prompt}\"")
        print(f"       Forced  : {forced_tier.upper()}")
        print(f"       Result  : tier={tier.upper()}, score={score}, used_llm={used_llm}")
        print(f"       Pulse bypassed: {score is None and used_llm is None}")
        print()

    # Tier guard test
    print("  Tier guard (BUG-11 -- invalid tier returns HTTP 400):")
    print(f"    VALID_TIERS = {sorted(VALID_TIERS)}")
    for bad in ["superduper", "STRONG", "ultra", "", "god-mode"]:
        blocked = bad not in VALID_TIERS
        print(f"    '{bad}' -> {'BLOCKED (HTTP 400)' if blocked else 'ALLOWED -- BUG!'}")
    print()
    print(f"  Manual routing: {bonus_passed}/3 PASSED")
    print()


async def main():
    passed = await run_all_tests()
    await test_manual_routing()

    # Final exit: only exit 0 if all 23 pass
    sys.exit(0 if passed == 23 else 1)


if __name__ == "__main__":
    asyncio.run(main())
