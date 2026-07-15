"""
pulse_learner.py — Adaptive Layer 4 Threshold Updater

This script runs periodically (e.g., via cron) to analyze the recent history
of requests for each caller in the `requests_log` table.

Based on the success rate of 'fast' and 'mid' tier requests, it dynamically
adjusts the thresholds for that caller, updating the `pulse_profiles` table
in Postgres and syncing to the Redis cache.

Logic:
- Require >= 100 total requests in the last 7 days to trigger learning.
- If fast/mid success rate > 99% → Trust (lower thresholds: 1.5, 4.0).
- If fast/mid success rate < 95% → Penalty (raise thresholds: 3.0, 6.0).
- Otherwise, use defaults (2.0, 5.0).
"""

import asyncio
import logging
from db import init_db, close_db, get_pool
from redis_store import set_caller_thresholds

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("cortex.pulse_learner")

async def update_thresholds():
    logger.info("Starting pulse learner job...")
    await init_db()
    pool = get_pool()

    query = """
        SELECT 
            caller_id,
            COUNT(*) as total_reqs,
            COUNT(*) FILTER (WHERE tier_requested IN ('fast', 'mid')) as fast_mid_reqs,
            COUNT(*) FILTER (WHERE tier_requested IN ('fast', 'mid') AND success = true) as fast_mid_successes
        FROM requests_log
        WHERE created_at >= now() - INTERVAL '7 days'
        GROUP BY caller_id
        HAVING COUNT(*) >= 100
    """

    try:
        async with pool.acquire() as conn:
            records = await conn.fetch(query)
            
            for row in records:
                caller_id = row["caller_id"]
                total_reqs = row["total_reqs"]
                fast_mid_reqs = row["fast_mid_reqs"]
                fast_mid_successes = row["fast_mid_successes"]

                # Default thresholds
                fast_t = 2.0
                strong_t = 5.0

                if fast_mid_reqs > 0:
                    success_rate = fast_mid_successes / fast_mid_reqs
                    if success_rate > 0.99:
                        fast_t = 1.5
                        strong_t = 4.0
                        logger.info(f"[{caller_id}] High trust ({success_rate:.1%}). Dropping thresholds to {fast_t}/{strong_t}")
                    elif success_rate < 0.95:
                        fast_t = 3.0
                        strong_t = 6.0
                        logger.info(f"[{caller_id}] High error ({success_rate:.1%}). Raising thresholds to {fast_t}/{strong_t}")
                    else:
                        logger.info(f"[{caller_id}] Normal ({success_rate:.1%}). Using defaults {fast_t}/{strong_t}")
                else:
                    logger.info(f"[{caller_id}] No fast/mid history. Using defaults {fast_t}/{strong_t}")

                # Update Postgres
                upsert_query = """
                    INSERT INTO pulse_profiles (caller_id, fast_threshold, strong_threshold, last_updated)
                    VALUES ($1, $2, $3, now())
                    ON CONFLICT (caller_id) DO UPDATE SET
                        fast_threshold = EXCLUDED.fast_threshold,
                        strong_threshold = EXCLUDED.strong_threshold,
                        last_updated = now()
                """
                await conn.execute(upsert_query, caller_id, fast_t, strong_t)

                # Update Redis Cache
                await set_caller_thresholds(caller_id, fast_t, strong_t)
                
        logger.info(f"Successfully processed {len(records)} caller profiles.")
    except Exception as e:
        logger.error(f"Error during pulse learning: {e}")
    finally:
        await close_db()

if __name__ == "__main__":
    asyncio.run(update_thresholds())
