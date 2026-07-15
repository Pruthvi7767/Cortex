-- postgres_schema.sql
-- Migration schema for Cortex API keys and Request Telemetry logging
--
-- BUG-10 FIX: Added indexes on requests_log(created_at DESC) and
--             requests_log(caller_id) for efficient query performance at scale.
--             The get_recent_failures() query (ORDER BY created_at DESC LIMIT N)
--             was doing a full table scan without the created_at index.
--
-- BUG-12 (indirectly relevant): Ensure DATABASE_URL in .env points to port 5432.
--
-- BUG-13 FIX: Added is_admin column to api_keys table.
--             The /admin/reload-config endpoint now requires is_admin = true.
--             Default is false so existing keys are unaffected.

CREATE TABLE IF NOT EXISTS api_keys (
    id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    key_hash              TEXT UNIQUE NOT NULL,
    caller_id             TEXT NOT NULL,
    rate_limit_per_minute INT DEFAULT 60,
    active                BOOLEAN DEFAULT true,
    -- BUG-13 FIX: role/privilege field for admin-only endpoints
    is_admin              BOOLEAN DEFAULT false,
    created_at            TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS requests_log (
    id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    request_id           TEXT NOT NULL,
    caller_id            TEXT,
    tier_requested       TEXT,
    tier_source          TEXT,  -- "manual" or "auto"
    provider_used        TEXT,
    model_used           TEXT,
    latency_ms           INT,
    success              BOOLEAN,
    error_type           TEXT,
    created_at           TIMESTAMPTZ DEFAULT now(),
    prompt_tokens        INT,
    completion_tokens    INT,
    total_tokens         INT,
    decision_score       FLOAT,
    nvidia_attempted     BOOLEAN,
    nvidia_succeeded     BOOLEAN,
    validation_rejections TEXT
);

-- BUG-10 FIX: Index for get_recent_failures() which orders by created_at DESC
CREATE INDEX IF NOT EXISTS idx_requests_log_created_at
    ON requests_log (created_at DESC);

-- BUG-10 FIX: Index for per-caller analytics queries from future frontend
CREATE INDEX IF NOT EXISTS idx_requests_log_caller_id
    ON requests_log (caller_id);

-- Index for common filter: failed requests only
CREATE INDEX IF NOT EXISTS idx_requests_log_success
    ON requests_log (success)
    WHERE success = false;

-- Pulse v3: Adaptive Layer 4 Thresholds
CREATE TABLE IF NOT EXISTS pulse_profiles (
    caller_id TEXT PRIMARY KEY,
    fast_threshold FLOAT DEFAULT 2.0,
    strong_threshold FLOAT DEFAULT 5.0,
    last_updated TIMESTAMPTZ DEFAULT now()
);
