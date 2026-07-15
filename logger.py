"""
logger.py — Async request logging to Postgres and troubleshooting query helper.
"""

import logging
from typing import Optional
from db import get_pool

logger = logging.getLogger("cortex.logger")

async def log_request(
    request_id: str,
    caller_id: Optional[str],
    tier_requested: Optional[str],
    tier_source: Optional[str],
    provider_used: Optional[str],
    model_used: Optional[str],
    latency_ms: Optional[int],
    success: bool,
    error_type: Optional[str] = None,
    prompt_tokens: Optional[int] = None,
    completion_tokens: Optional[int] = None,
    total_tokens: Optional[int] = None,
    decision_score: Optional[float] = None,
    nvidia_attempted: Optional[bool] = None,
    nvidia_succeeded: Optional[bool] = None,
    validation_rejections: Optional[str] = None
):
    """
    Inserts a row into the Postgres requests_log table.
    
    CRITICAL: Swallows all database exceptions to prevent logging failures from
    impacting client API responses.
    
    This function should be called via asyncio.create_task() to be non-blocking.
    """
    try:
        pool = get_pool()
        query = """
            INSERT INTO requests_log (
                request_id, caller_id, tier_requested, tier_source, provider_used,
                model_used, latency_ms, success, error_type, prompt_tokens,
                completion_tokens, total_tokens, decision_score, nvidia_attempted,
                nvidia_succeeded, validation_rejections
            ) VALUES (
                $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16
            )
        """
        await pool.execute(
            query,
            request_id, caller_id, tier_requested, tier_source, provider_used,
            model_used, latency_ms, success, error_type, prompt_tokens,
            completion_tokens, total_tokens, decision_score, nvidia_attempted,
            nvidia_succeeded, validation_rejections
        )
    except Exception as e:
        # Never let logging crash the request pipeline
        logger.warning(f"Failed to log request {request_id} to Postgres: {e}")


async def get_recent_failures(limit: int = 50) -> list:
    """
    Helper to query recent failed requests for diagnostics.
    """
    try:
        pool = get_pool()
        query = "SELECT * FROM requests_log WHERE success = false ORDER BY created_at DESC LIMIT $1"
        rows = await pool.fetch(query, limit)
        return [dict(row) for row in rows]
    except Exception as e:
        logger.error(f"Error querying recent failures from Postgres: {e}")
        return []
