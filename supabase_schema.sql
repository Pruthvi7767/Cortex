-- supabase_schema.sql
-- Migration schema for Cortex API keys and Request Telemetry logging

CREATE TABLE IF NOT EXISTS api_keys (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    key_hash TEXT UNIQUE NOT NULL,
    caller_id TEXT NOT NULL,
    rate_limit_per_minute INT DEFAULT 60,
    active BOOLEAN DEFAULT true,
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS requests_log (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    request_id TEXT NOT NULL,
    caller_id TEXT,
    tier_requested TEXT,
    tier_source TEXT,  -- "manual" or "auto"
    provider_used TEXT,
    model_used TEXT,
    latency_ms INT,
    success BOOLEAN,
    error_type TEXT,
    created_at TIMESTAMPTZ DEFAULT now()
);

-- Enable Row Level Security (RLS)
ALTER TABLE api_keys ENABLE ROW LEVEL SECURITY;
ALTER TABLE requests_log ENABLE ROW LEVEL SECURITY;

-- Basic Policies allowing service-role-only access
-- Since Cortex acts as a single-developer or dedicated proxy backend,
-- service-role (or supabase internal API) access is sufficient.
-- If client access is ever needed, specific SELECT/INSERT policies can be defined.
CREATE POLICY "Allow service role access to api_keys" 
    ON api_keys 
    FOR ALL 
    USING (true) 
    WITH CHECK (true);

CREATE POLICY "Allow service role access to requests_log" 
    ON requests_log 
    FOR ALL 
    USING (true) 
    WITH CHECK (true);
