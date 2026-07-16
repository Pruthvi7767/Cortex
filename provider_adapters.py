"""
provider_adapters.py — Thin adapter layer for multi-provider HTTP shapes.

Most providers expose an OpenAI-compatible chat completions endpoint.
Google Gemini is the one confirmed exception: while its generativelanguage.googleapis.com
path does have an OpenAI-compat shim (/v1beta/openai/), the native path differs.
We use the OpenAI-compat shim for Google too (base_url already set in config.py),
so request body stays uniform — but the auth mechanism differs: Google needs the
API key as a Bearer token in the Authorization header (same as others via the shim).

A separate ParseError is raised (not silently swallowed) when a response doesn't match
the expected schema — per AGENT.md Section 5, HTTP 200 alone is never enough.

BUG-06 FIX: get_endpoint_url() now raises ValueError for providers with an empty
base_url instead of silently constructing a malformed relative URL ("/chat/completions").
This surfaces Tier 2/3 providers that haven't been fully configured rather than letting
them consume race slots and fail with a generic network error.
"""

import os
import logging
from typing import Optional

logger = logging.getLogger("cortex.provider_adapters")


class ParseError(Exception):
    """Raised when a provider response cannot be parsed into the normalized shape."""
    pass


def get_endpoint_url(provider: str, model_id: str = "") -> str:
    """
    Returns the chat completions URL for the given provider by reading the
    provider registry from config.py. The URL is base_url + /chat/completions.

    Cloudflare's base_url contains a {CLOUDFLARE_ACCOUNT_ID} placeholder that
    is resolved here from the environment.

    BUG-06 FIX: Raises ValueError if base_url is empty. Tier 2/3 providers
    in the registry have empty base_url strings as placeholders for future
    implementation. Previously, get_endpoint_url() would silently produce
    "/chat/completions" (a relative path), causing httpx to raise an error
    that was only caught as a generic server_error — wasting a race slot and
    hiding the real problem (unconfigured base_url).
    """
    from config import get_provider_registry
    registry = {p["id"]: p for p in get_provider_registry()}

    if provider not in registry:
        raise ValueError(f"Unknown provider '{provider}' — not in provider registry")

    base_url = registry[provider]["base_url"].rstrip("/")

    if not base_url:
        raise ValueError(
            f"Provider '{provider}' has an empty base_url in the registry. "
            f"This provider is not yet fully configured for HTTP dispatch."
        )

    if provider == "cloudflare":
        account_id = os.getenv("CLOUDFLARE_ACCOUNT_ID", "")
        base_url = base_url.replace("{CLOUDFLARE_ACCOUNT_ID}", account_id)

    return f"{base_url}/chat/completions"


def get_auth_headers(provider: str) -> dict:
    """
    Returns the Authorization header for the given provider.
    All providers use Bearer tokens read from their configured env var.
    """
    from config import get_provider_registry
    registry = {p["id"]: p for p in get_provider_registry()}

    if provider not in registry:
        raise ValueError(f"Unknown provider '{provider}'")

    env_var = registry[provider]["env_var"]
    api_key = os.getenv(env_var, "")

    return {"Authorization": f"Bearer {api_key}"}


def build_request(
    provider: str,
    model_id: str,
    messages: list,
    max_tokens: int,
    tools: Optional[list] = None,
    temperature: Optional[float] = None,
) -> dict:
    """
    Returns the correctly-shaped JSON body for the given provider.
    All active providers use the standard OpenAI chat completions shape.
    """
    body: dict = {
        "model":  model_id,
        "messages": messages,
        "stream": False,
    }

    if max_tokens is not None:
        body["max_tokens"] = max_tokens

    if temperature is not None:
        body["temperature"] = temperature

    if tools:
        body["tools"] = tools
        body["tool_choice"] = "auto"

    return body


def parse_response(provider: str, raw_response: dict) -> dict:
    """
    Extracts a normalized response shape from any provider's raw JSON response.

    Returns:
        {
            "content":    str | None,
            "tool_calls": list | None,
            "usage": {
                "prompt_tokens":     int,
                "completion_tokens": int,
            }
        }

    Raises ParseError if the response doesn't match the OpenAI-compatible schema.
    Never silently returns garbage — schema drift is treated as a hard failure.
    """
    try:
        choices = raw_response.get("choices")
        if not choices or not isinstance(choices, list):
            raise ParseError(f"[{provider}] 'choices' missing or empty in response")

        message = choices[0].get("message", {})
        raw_content = message.get("content")
        
        # BUG-14 Fix: Normalise list-type content (e.g. Mistral multi-part) to a plain string
        if isinstance(raw_content, list):
            content = "".join(
                block.get("text", "") for block in raw_content if isinstance(block, dict)
            )
        else:
            content = raw_content

        tool_calls_raw = message.get("tool_calls")

        # Normalise tool_calls into a clean, consistent list shape
        tool_calls = None
        if tool_calls_raw:
            tool_calls = []
            for tc in tool_calls_raw:
                fn = tc.get("function", {})
                tool_calls.append({
                    "id":        tc.get("id", ""),
                    "name":      fn.get("name", ""),
                    "arguments": fn.get("arguments", "{}"),
                })

        usage_raw = raw_response.get("usage", {})
        usage = {
            "prompt_tokens":     usage_raw.get("prompt_tokens", 0),
            "completion_tokens": usage_raw.get("completion_tokens", 0),
        }

        return {
            "content":    content,
            "tool_calls": tool_calls,
            "usage":      usage,
        }

    except ParseError:
        raise
    except Exception as exc:
        raise ParseError(f"[{provider}] Unexpected response schema: {exc}") from exc
