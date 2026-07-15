"""
db.py — PostgreSQL connection pool manager using asyncpg.
"""

import logging
import asyncpg
from typing import Optional
from config import DATABASE_URL

logger = logging.getLogger("cortex.db")

_pool: Optional[asyncpg.Pool] = None

async def init_db():
    """
    Initializes the shared asyncpg connection pool.
    Called on FastAPI startup.
    """
    global _pool
    if _pool is None:
        try:
            logger.info("Initializing Postgres connection pool...")
            _pool = await asyncpg.create_pool(
                dsn=DATABASE_URL,
                min_size=2,
                max_size=20,
                command_timeout=10.0,
                server_settings={
                    'application_name': 'cortex_proxy',
                    'timezone': 'UTC'
                }
            )
            logger.info("Postgres connection pool initialized.")
        except Exception as e:
            logger.error(f"Failed to initialize Postgres pool: {e}")
            raise e

async def close_db():
    """
    Closes the shared asyncpg connection pool.
    Called on FastAPI shutdown.
    """
    global _pool
    if _pool is not None:
        try:
            logger.info("Closing Postgres connection pool...")
            await _pool.close()
            _pool = None
            logger.info("Postgres connection pool closed.")
        except Exception as e:
            logger.error(f"Error while closing Postgres pool: {e}")

def get_pool() -> asyncpg.Pool:
    """
    Returns the initialized asyncpg connection pool.
    Raises RuntimeError if the pool is not initialized.
    """
    global _pool
    if _pool is None:
        raise RuntimeError("Database pool has not been initialized. Call init_db() first.")
    return _pool
