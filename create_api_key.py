"""
create_api_key.py — CLI helper to generate and register a new API key in Supabase.

Usage:
    python create_api_key.py <caller_id> [rate_limit_per_minute]
"""

import sys
import asyncio
from auth import generate_api_key, get_supabase_client

async def main():
    if len(sys.argv) < 2:
        print("Usage: python create_api_key.py <caller_id> [rate_limit_per_minute]")
        sys.exit(1)

    caller_id = sys.argv[1]
    rate_limit = 60
    if len(sys.argv) >= 3:
        try:
            rate_limit = int(sys.argv[2])
        except ValueError:
            print(f"Invalid rate limit '{sys.argv[2]}', defaulting to 60")

    raw_key, key_hash = generate_api_key()

    print(f"Registering API key for caller '{caller_id}' in Supabase...")
    try:
        supabase = await get_supabase_client()
        res = await supabase.table("api_keys").insert({
            "caller_id": caller_id,
            "key_hash": key_hash,
            "rate_limit_per_minute": rate_limit,
            "active": True
        }).execute()
        
        print("\nAPI Key Created Successfully!")
        print("-" * 50)
        print(f"Caller ID:   {caller_id}")
        print(f"Rate Limit:  {rate_limit} RPM")
        print(f"Raw API Key: {raw_key}")
        print("-" * 50)
        print("WARNING: This key is only shown ONCE. Store it securely; only its hash is saved.")
        
    except Exception as e:
        print(f"\nError creating API key: {e}")
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(main())
