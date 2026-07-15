"""
pulse_embedding.py — Pulse v3 Layer 3 (Embedding Classifier)

Computes prompt embeddings via the Google Gemini API (or chosen provider)
and scores the prompt using Cosine Similarity against known Fast/Strong anchors.

Anchor embeddings are cached in Redis to avoid redundant API calls on startup.
"""

import os
import math
import httpx
import logging
import json
import numpy as np

from config import EMBEDDING_PROVIDER, EMBEDDING_MODEL
from redis_store import client, _safe_execute, RedisConnectionError

logger = logging.getLogger("cortex.pulse_embedding")

STRONG_ANCHORS = [
    "deploy to production",
    "delete database",
    "drop table users",
    "send the final invoice to the client",
    "generate the final contract",
    "evaluate the microservice architecture",
]

FAST_ANCHORS = [
    "hello",
    "what is 2+2",
    "hi there",
    "summarize this short text",
    "give me a joke",
    "what is the capital of france",
]

# Cache TTL for anchor embeddings (7 days)
ANCHOR_CACHE_TTL = 7 * 86400

def get_api_key(provider: str) -> str:
    """Helper to fetch API key from environment."""
    env_vars = {
        "google": "GOOGLE_API_KEY",
        "nvidia": "NVIDIA_API_KEY",
        "cloudflare": "CLOUDFLARE_API_KEY",
    }
    key_name = env_vars.get(provider)
    if not key_name:
        raise ValueError(f"No known environment variable for embedding provider {provider}")
    key = os.getenv(key_name)
    if not key:
        raise ValueError(f"API key missing for embedding provider {provider} ({key_name})")
    return key

def get_base_url(provider: str) -> str:
    from config import get_provider_registry
    registry = get_provider_registry()
    for p in registry:
        if p["id"] == provider:
            return p["base_url"]
    raise ValueError(f"Provider {provider} not found in registry")

async def get_embedding(text: str) -> list[float] | None:
    """
    Fetches the embedding vector for a given string.
    Returns None if the API fails or is not configured.
    """
    try:
        api_key = get_api_key(EMBEDDING_PROVIDER)
        base_url = get_base_url(EMBEDDING_PROVIDER)
    except Exception as e:
        logger.error(f"Embedding config error: {e}")
        return None

    # Google specific OpenAI compatibility URL handling
    if EMBEDDING_PROVIDER == "google" and "v1beta/openai" in base_url:
        url = base_url.rstrip("/") + "/embeddings"
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    else:
        # Generic OpenAI compatible
        url = base_url.rstrip("/") + "/embeddings"
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    
    payload = {
        "model": EMBEDDING_MODEL,
        "input": text
    }

    try:
        async with httpx.AsyncClient(timeout=3.0) as http_client:
            resp = await http_client.post(url, json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()
            return data["data"][0]["embedding"]
    except Exception as e:
        logger.error(f"Embedding API call failed: {e}")
        return None

def cosine_similarity(vec1: list[float], vec2: list[float]) -> float:
    """Compute cosine similarity between two vectors."""
    dot_product = sum(a * b for a, b in zip(vec1, vec2))
    mag1 = math.sqrt(sum(a * a for a in vec1))
    mag2 = math.sqrt(sum(b * b for b in vec2))
    if mag1 == 0 or mag2 == 0:
        return 0.0
    return dot_product / (mag1 * mag2)

async def _get_anchor_embeddings(anchors: list[str], prefix: str) -> list[list[float]]:
    """Fetch embeddings for anchors, checking Redis cache first."""
    embeddings = []
    for anchor in anchors:
        key = f"pulse:embed:{prefix}:{anchor}"
        async def _check():
            return await client.get(key)
            
        try:
            cached_str = await _safe_execute(_check())
        except RedisConnectionError:
            cached_str = None
            
        if cached_str:
            embeddings.append(json.loads(cached_str))
        else:
            emb = await get_embedding(anchor)
            if emb:
                embeddings.append(emb)
                async def _set():
                    await client.set(key, json.dumps(emb), ex=ANCHOR_CACHE_TTL)
                try:
                    await _safe_execute(_set())
                except RedisConnectionError:
                    pass
    return embeddings

async def compute_embedding_score(prompt: str) -> float | None:
    """
    Computes a continuous score (0.0 to 1.0) based on embedding similarity.
    Returns 1.0 if the prompt is entirely similar to strong anchors.
    Returns 0.0 if the prompt is entirely similar to fast anchors.
    Returns None if embedding fails.
    """
    prompt_emb = await get_embedding(prompt)
    if not prompt_emb:
        return None

    strong_embs = await _get_anchor_embeddings(STRONG_ANCHORS, "strong")
    fast_embs = await _get_anchor_embeddings(FAST_ANCHORS, "fast")

    if not strong_embs or not fast_embs:
        return None

    # Compute max similarity to strong and fast anchor sets
    max_strong_sim = max(cosine_similarity(prompt_emb, a) for a in strong_embs)
    max_fast_sim = max(cosine_similarity(prompt_emb, a) for a in fast_embs)

    # Calculate relative affinity: 0.0 means identical to fast, 1.0 means identical to strong
    # If they are equal, it returns 0.5.
    total = max_strong_sim + max_fast_sim
    if total == 0:
        return 0.5
        
    score = max_strong_sim / total
    return score
