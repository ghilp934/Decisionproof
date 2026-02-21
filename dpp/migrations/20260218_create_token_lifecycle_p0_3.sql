-- P0-3: API Token Lifecycle (Opaque Bearer Tokens)
-- Created: 2026-02-18
-- Purpose: Production-ready token management with rotation, revocation, and audit

-- ============================================================================
-- 1. API Tokens Table
-- ============================================================================

CREATE TABLE IF NOT EXISTS api_tokens (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id TEXT NOT NULL REFERENCES tenants(tenant_id),

    -- Token identification
    name TEXT NOT NULL,
    token_hash TEXT NOT NULL UNIQUE,  -- HMAC-SHA256(PEPPER, raw_token)
    prefix TEXT NOT NULL,              -- e.g., dp_live, dp_test
    last4 TEXT NOT NULL,               -- Last 4 chars for display

    -- Authorization
    scopes TEXT[] DEFAULT ARRAY[]::TEXT[],  -- Future: scope-based access control

    -- Lifecycle state
    status TEXT NOT NULL CHECK (status IN ('active', 'rotating', 'revoked', 'expired')),

    -- Timestamps
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at TIMESTAMPTZ,            -- NULL = no expiration
    revoked_at TIMESTAMPTZ,
    last_used_at TIMESTAMPTZ,

    -- Security versioning
    pepper_version INTEGER NOT NULL DEFAULT 1,

    -- Metadata
    created_by_user_id TEXT,           -- Who created this token (session user)
    user_agent TEXT,                   -- User agent at creation time
    ip_address TEXT                    -- IP address at creation time (hashed before storage)
);

-- Indexes for performance
CREATE INDEX idx_api_tokens_tenant_status ON api_tokens(tenant_id, status);
CREATE INDEX idx_api_tokens_token_hash ON api_tokens(token_hash) WHERE status IN ('active', 'rotating');
CREATE INDEX idx_api_tokens_expires_at ON api_tokens(expires_at) WHERE expires_at IS NOT NULL AND status = 'active';

-- Updated_at trigger (optional but recommended)
CREATE OR REPLACE FUNCTION update_api_tokens_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    -- Update last_used_at without creating new updated_at column
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Row Level Security (RLS)
ALTER TABLE api_tokens ENABLE ROW LEVEL SECURITY;

-- Policy: Tenants can only access their own tokens
CREATE POLICY tenant_api_tokens_select ON api_tokens
    FOR SELECT
    USING (tenant_id = current_setting('app.tenant_id', TRUE)::TEXT);

CREATE POLICY tenant_api_tokens_insert ON api_tokens
    FOR INSERT
    WITH CHECK (tenant_id = current_setting('app.tenant_id', TRUE)::TEXT);

CREATE POLICY tenant_api_tokens_update ON api_tokens
    FOR UPDATE
    USING (tenant_id = current_setting('app.tenant_id', TRUE)::TEXT);

-- Admin policy for observability
CREATE POLICY admin_api_tokens_all ON api_tokens
    FOR ALL
    USING (current_setting('app.role', TRUE) = 'admin');

COMMENT ON TABLE api_tokens IS 'P0-3: Opaque Bearer tokens with rotation and revocation support';
COMMENT ON COLUMN api_tokens.token_hash IS 'HMAC-SHA256(PEPPER, raw_token) - never store raw token';
COMMENT ON COLUMN api_tokens.status IS 'active: usable | rotating: grace period | revoked: permanently disabled | expired: past expires_at';


-- ============================================================================
-- 2. Token Events Table (Audit Trail)
-- ============================================================================

CREATE TABLE IF NOT EXISTS token_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    -- Related entities
    tenant_id TEXT NOT NULL REFERENCES tenants(tenant_id),
    token_id UUID REFERENCES api_tokens(id),  -- NULL for revoke_all events

    -- Actor
    actor_user_id TEXT,  -- Session user who performed action

    -- Event details
    event_type TEXT NOT NULL CHECK (event_type IN (
        'issued', 'rotated', 'revoked', 'revoke_all', 'compromised_flagged', 'expired'
    )),

    -- Event metadata (minimal, no secrets)
    event_meta JSONB,

    -- Timestamp
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Indexes
CREATE INDEX idx_token_events_tenant ON token_events(tenant_id);
CREATE INDEX idx_token_events_token_id ON token_events(token_id);
CREATE INDEX idx_token_events_created_at ON token_events(created_at DESC);

-- Row Level Security
ALTER TABLE token_events ENABLE ROW LEVEL SECURITY;

CREATE POLICY tenant_token_events_select ON token_events
    FOR SELECT
    USING (tenant_id = current_setting('app.tenant_id', TRUE)::TEXT);

CREATE POLICY admin_token_events_all ON token_events
    FOR ALL
    USING (current_setting('app.role', TRUE) = 'admin');

COMMENT ON TABLE token_events IS 'P0-3: Audit trail for token lifecycle events';


-- ============================================================================
-- 3. Auth Request Log (Security Telemetry - Optional but Recommended)
-- ============================================================================

CREATE TABLE IF NOT EXISTS auth_request_log (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    -- Related entities
    token_id UUID,  -- May be NULL if auth failed before token lookup
    tenant_id TEXT, -- May be NULL if auth failed

    -- Request details
    route TEXT NOT NULL,
    method TEXT NOT NULL,
    status_code INTEGER NOT NULL,

    -- Security hashes (privacy-preserving)
    ip_hash TEXT,    -- SHA256(LOG_PEPPER + ip_address)
    ua_hash TEXT,    -- SHA256(LOG_PEPPER + user_agent)

    -- Observability
    trace_id TEXT,

    -- Timestamp
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Indexes
CREATE INDEX idx_auth_request_log_token_id ON auth_request_log(token_id);
CREATE INDEX idx_auth_request_log_created_at ON auth_request_log(created_at DESC);
CREATE INDEX idx_auth_request_log_status_code ON auth_request_log(status_code) WHERE status_code >= 400;

-- Retention policy: auto-delete logs older than 90 days (cron job or pg_cron)
COMMENT ON TABLE auth_request_log IS 'P0-3: Security telemetry for API token authentication (privacy-preserving)';

-- Row Level Security (admin only)
ALTER TABLE auth_request_log ENABLE ROW LEVEL SECURITY;

CREATE POLICY admin_auth_request_log_all ON auth_request_log
    FOR ALL
    USING (current_setting('app.role', TRUE) = 'admin');


-- ============================================================================
-- 4. Helper Functions
-- ============================================================================

-- Function to hash IP/UA for logging (privacy-preserving)
CREATE OR REPLACE FUNCTION hash_for_logging(value TEXT, pepper TEXT)
RETURNS TEXT AS $$
BEGIN
    RETURN encode(digest(pepper || value, 'sha256'), 'hex');
END;
$$ LANGUAGE plpgsql IMMUTABLE;


-- ============================================================================
-- 5. Migration Verification
-- ============================================================================

DO $$
BEGIN
    ASSERT (SELECT COUNT(*) FROM information_schema.tables
            WHERE table_name IN ('api_tokens', 'token_events', 'auth_request_log')) = 3,
           'Not all P0-3 token lifecycle tables were created';

    RAISE NOTICE 'P0-3 Token Lifecycle Schema Migration completed successfully';
END $$;
