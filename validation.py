"""
validation.py — single validation gate for all LLM responses.

Every response from every candidate passes through validate_response() before
being declared a winner. An HTTP 200 is never sufficient on its own — per
AGENT.md Section 5.

Failure reason strings are fixed categories used for Phase 6 logging:
  "empty"               — content is None or whitespace-only
  "refused"             — response is almost entirely a refusal pattern
  "invalid_tool_schema" — tool call arguments don't match expected schema
  "hallucinated_tool"   — tool name not in caller's whitelist
"""

import json
import logging
from typing import Optional

logger = logging.getLogger("cortex.validation")

# Refusal patterns — checked only when the ENTIRE response is dominated by one.
# We do NOT false-positive on legitimate answers that mention these in passing
# (e.g., explaining content policy). The threshold check below handles this.
_REFUSAL_PHRASES = [
    "i cannot help with",
    "i'm not able to",
    "i am not able to",
    "against my guidelines",
    "i'm unable to",
    "i am unable to",
    "as an ai, i cannot",
    "i must decline",
    "i can't assist with",
    "i cannot assist with",
    "this request goes against",
    "i won't be able to help",
    "i will not help",
    "i cannot provide",
    "not something i can help",
]

# A response is considered a refusal ONLY if:
#   1. It matches a refusal phrase, AND
#   2. The matching phrase covers more than this fraction of the total content.
# This avoids false-positives on long answers that happen to use these words.
_REFUSAL_DOMINANCE_THRESHOLD = 0.6  # 60% of content is refusal-like


def validate_response(
    parsed_response: Optional[dict],
    expected_tool_schema: Optional[dict] = None,
    tool_whitelist: Optional[list] = None,
) -> tuple:
    """
    Validates a parsed LLM response against all quality gates.

    Args:
        parsed_response:     Output of provider_adapters.parse_response().
        expected_tool_schema: Optional dict of {param_name: expected_type_str}.
                              If provided, tool_call arguments are checked against it.
        tool_whitelist:       Optional list of allowed tool names.
                              If provided, tool_call name must be in this list.

    Returns:
        (is_valid: bool, failure_reason: str | None)
        failure_reason is None when is_valid is True.
    """
    if parsed_response is None:
        return False, "empty"

    content = parsed_response.get("content") or ""
    tool_calls = parsed_response.get("tool_calls")

    # Check 1 — empty response (no content AND no tool calls)
    if not content.strip() and not tool_calls:
        return False, "empty"

    # Check 2 — refusal pattern (only if there IS content)
    if content.strip():
        content_lower = content.lower().strip()
        for phrase in _REFUSAL_PHRASES:
            if phrase in content_lower:
                # Calculate what fraction of the content the matched phrase covers
                dominance = len(phrase) / max(len(content_lower), 1)
                if dominance >= _REFUSAL_DOMINANCE_THRESHOLD:
                    logger.debug(f"Refusal detected (dominance={dominance:.2f}): '{phrase}'")
                    return False, "refused"

    # Check 3 & 4 — tool call validation (only when caller provided constraints)
    if tool_calls and (expected_tool_schema or tool_whitelist):
        for tc in tool_calls:
            tool_name = tc.get("name", "")

            # Check 4 — tool name must be in whitelist (catches hallucinated tool names)
            if tool_whitelist and tool_name not in tool_whitelist:
                logger.warning(f"Hallucinated tool name: '{tool_name}' not in whitelist {tool_whitelist}")
                return False, "hallucinated_tool"

            # Check 3 — tool arguments must match expected schema
            if expected_tool_schema:
                try:
                    args_str = tc.get("arguments", "{}")
                    args = json.loads(args_str) if isinstance(args_str, str) else args_str
                except json.JSONDecodeError:
                    return False, "invalid_tool_schema"

                for param_name, expected_type in expected_tool_schema.items():
                    if param_name not in args:
                        logger.debug(f"Tool schema mismatch: missing param '{param_name}'")
                        return False, "invalid_tool_schema"
                    # Basic type check using string type names
                    actual_type = type(args[param_name]).__name__
                    if actual_type != expected_type:
                        logger.debug(
                            f"Tool schema mismatch: '{param_name}' expected {expected_type}, got {actual_type}"
                        )
                        return False, "invalid_tool_schema"

    return True, None
