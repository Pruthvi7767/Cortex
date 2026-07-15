"""
create_api_key.py — Script to generate and insert a new API key into Postgres.

BUG-13 FIX: Now prompts for is_admin so admin keys can be created explicitly.
The is_admin column was added to the api_keys table as part of the BUG-13 fix
to gate /admin/* endpoints on caller privilege.
"""

import asyncio
import sys
from auth import generate_api_key
from db import get_pool, init_db, close_db

async def main():
    caller_id = input("Enter caller ID (e.g. 'markly-production'): ").strip()
    if not caller_id:
        print("Caller ID cannot be empty.")
        sys.exit(1)

    try:
        rpm = int(input("Enter rate limit per minute (default 60): ").strip() or "60")
    except ValueError:
        print("Invalid number.")
        sys.exit(1)

    # BUG-13 FIX: Prompt for admin flag so privileged keys can be created.
    is_admin_input = input("Grant admin privileges? (y/N): ").strip().lower()
    is_admin = is_admin_input in ("y", "yes")

    raw_key, key_hash = generate_api_key()

    print("\n--- NEW API KEY GENERATED ---")
    print(f"Key:      {raw_key}")
    print(f"Hash:     {key_hash}")
    print(f"Admin:    {is_admin}")
    print("-----------------------------\n")
    print("SAVE THIS KEY NOW. It cannot be retrieved again.")
    print("Connecting to Postgres to store hash...")

    await init_db()

    try:
        pool = get_pool()
        query = (
            "INSERT INTO api_keys (key_hash, caller_id, rate_limit_per_minute, is_admin) "
            "VALUES ($1, $2, $3, $4)"
        )
        await pool.execute(query, key_hash, caller_id, rpm, is_admin)
        print(f"Successfully saved to Postgres. (admin={is_admin})")
    except Exception as e:
        print(f"Failed to save to Postgres: {e}")
    finally:
        await close_db()

if __name__ == "__main__":
    asyncio.run(main())
