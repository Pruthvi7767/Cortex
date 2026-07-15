# Phase 8.5 — Supabase to Postgres Migration Log

## Overview
This phase migrated the system's database layer from the `supabase-py` SDK over HTTP to raw Postgres using the `asyncpg` driver over TCP. This removes an external network dependency for our database, switching to a standard connection-pooled async approach while preserving the fire-and-forget logging semantics necessary to keep API response times low.

## Changes Made
- **Database Connection Pooling:** 
  - Added `DATABASE_URL` to `.env.example` and `config.py`.
  - Created `db.py` to manage a global `asyncpg` connection pool.
  - Initialized and cleanly closed the pool using the FastAPI `lifespan` context manager in `main.py`.
- **Auth Rewrite:**
  - `auth.py` was completely rewritten to execute parameterized SQL queries via `asyncpg`, dropping `supabase` dependencies.
  - `create_api_key.py` was updated to insert API key records into raw Postgres.
- **Logger Rewrite & Telemetry Expansion:**
  - `logger.py` was rewritten to use `asyncpg` inserts for the `requests_log` table.
  - Added the 7 new columns to the `requests_log` schema (`postgres_schema.sql`): `prompt_tokens`, `completion_tokens`, `total_tokens`, `decision_score`, `nvidia_attempted`, `nvidia_succeeded`, `validation_rejections`.
  - Updated `RaceResult` and `execute_race` in `race.py` to track token usage (parsed from provider responses) and whether NVIDIA was attempted/succeeded, passing these down.
  - Updated `main.py` to pass these new fields to the background `log_request` task.
- **Docker & Dependencies:**
  - Added the `postgres:16-alpine` service to `docker-compose.yml`.
  - Replaced `supabase` with `asyncpg` in `requirements.txt`.
- **Testing Integration:**
  - `test_phase6.py` was heavily rewritten to properly mock `asyncpg` connection pools instead of Supabase client builder methods, successfully verifying all Auth and Logging functionality.
  - `test_phase7_chaos.py` remains functional.

## Outcome
The application now uses raw Postgres seamlessly with a proper asyncpg connection pool. The `supabase_schema.sql` has been purged in favor of `postgres_schema.sql`, simplifying the technology stack as per the latest requirements.
