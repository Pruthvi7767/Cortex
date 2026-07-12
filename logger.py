"""
logger.py — Async request logging to Supabase and troubleshooting query helper.
"""

import logging
from typing import Optional
from auth import get_supabase_client

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
    error_type: Optional[str] = None
):
    """
    Inserts a row into Supabase's requests_log table.
    
    CRITICAL: Swallows all database exceptions to prevent logging failures from
    impacting client API responses.
    
    This function should be called via asyncio.create_task() to be non-blocking.
    """
    try:
        supabase = await get_supabase_client()
        await supabase.table("requests_log").insert({
            "request_id": request_id,
            "caller_id": caller_id,
            "tier_requested": tier_requested,
            "tier_source": tier_source,
            "provider_used": provider_used,
            "model_used": model_used,
            "latency_ms": latency_ms,
            "success": success,
            "error_type": error_type
        }).execute()
    except Exception as e:
        # Never let logging crash the request pipeline
        logger.warning(f"Failed to log request {request_id} to Supabase: {e}")


async def get_recent_failures(limit: int = 50) -> list:
    """
    Helper to query recent failed requests for diagnostics.
    """
    try:
        supabase = await get_supabase_client()
        res = await supabase.table("requests_log")\
            .select("*")\
            .eq("success", False)\
            .order("created_at", desc=True)\
            .limit(limit)\
            .execute()
        return res.data or []
    except Exception as e:
        logger.error(f"Error querying recent failures from Supabase: {e}")
        return []
