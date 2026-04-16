-- Phase 2: Checkout Sessions Schema
-- DP-V1-P1-SOW §5.1 / §5.2
-- Created: 2026-04-16
-- Purpose: Payment front door — checkout_sessions + checkout_session_events

-- ============================================================================
-- 1. checkout_sessions
-- ============================================================================

CREATE TABLE IF NOT EXISTS checkout_sessions (
    -- Primary key: opaque UUID v4, safe to expose externally
    id                          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),

    -- Identity binding: both user_id AND tenant_id required (DEC-V1-05, DEC-V1-06)
    user_id                     UUID        NOT NULL,   -- Supabase auth.users.id
    tenant_id                   TEXT        NOT NULL REFERENCES tenants(tenant_id),

    -- Purchase intent: locked at creation, immutable thereafter
    plan_id                     TEXT        NOT NULL REFERENCES plans(plan_id),
    amount_usd_cents            BIGINT      NOT NULL,   -- e.g. 2900 for $29.00
    currency                    TEXT        NOT NULL DEFAULT 'USD'
                                            CHECK (currency IN ('USD')),

    -- State machine (SOW §7)
    status                      TEXT        NOT NULL DEFAULT 'DRAFT'
                                            CHECK (status IN (
                                                'DRAFT',
                                                'CHECKOUT_SESSION_CREATED',
                                                'PAYPAL_ORDER_CREATED',
                                                'APPROVED',
                                                'CAPTURE_SUBMITTED',
                                                'PAID_VERIFIED',
                                                'CANCELED',
                                                'EXPIRED',
                                                'FAILED'
                                            )),

    -- PayPal-Request-Id: generated at session creation, stored immutably (DEC-V1-14, DEC-V1-15)
    paypal_request_id_create    TEXT        NOT NULL UNIQUE,
    paypal_request_id_capture   TEXT        NOT NULL UNIQUE,

    -- PayPal order binding: null until create-order succeeds
    paypal_order_id             TEXT        UNIQUE,

    -- Anti-replay nonce: 32-byte hex, generated at session creation
    nonce                       TEXT        NOT NULL,

    -- Expiry: 30-minute TTL (CHECKOUT_SESSION_TTL_MINUTES=30, OI-04 LOCKED)
    expires_at                  TIMESTAMPTZ NOT NULL,

    -- Failure tracking
    failed_reason               TEXT,

    -- Audit
    created_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Standard indexes
CREATE INDEX IF NOT EXISTS idx_cs_tenant
    ON checkout_sessions(tenant_id);
CREATE INDEX IF NOT EXISTS idx_cs_user
    ON checkout_sessions(user_id);
CREATE INDEX IF NOT EXISTS idx_cs_status
    ON checkout_sessions(status);
CREATE INDEX IF NOT EXISTS idx_cs_expires
    ON checkout_sessions(expires_at)
    WHERE status NOT IN ('PAID_VERIFIED', 'CANCELED', 'EXPIRED', 'FAILED');
CREATE INDEX IF NOT EXISTS idx_cs_paypal_order
    ON checkout_sessions(paypal_order_id)
    WHERE paypal_order_id IS NOT NULL;

-- First-writer guard (OI-03 LOCKED): DB-level, one active session per tenant+plan
-- IntegrityError on this index → app fetches existing session (see repo_checkout.py)
CREATE UNIQUE INDEX IF NOT EXISTS uq_cs_tenant_plan_active
    ON checkout_sessions(tenant_id, plan_id)
    WHERE status NOT IN ('PAID_VERIFIED', 'CANCELED', 'EXPIRED', 'FAILED');

-- Updated_at trigger (reuse existing function from billing migration)
CREATE TRIGGER trigger_cs_updated_at
    BEFORE UPDATE ON checkout_sessions
    FOR EACH ROW EXECUTE FUNCTION update_billing_orders_updated_at();

-- RLS: tenants may only read their own sessions
ALTER TABLE checkout_sessions ENABLE ROW LEVEL SECURITY;

CREATE POLICY cs_tenant_select ON checkout_sessions
    FOR SELECT
    USING (tenant_id = current_setting('app.tenant_id', TRUE)::TEXT);

COMMENT ON TABLE checkout_sessions
    IS 'Phase 2: Payment front door sessions (DP-V1-P1-SOW). '
       'One active session per (tenant_id, plan_id) enforced by uq_cs_tenant_plan_active.';

-- ============================================================================
-- 2. checkout_session_events
-- ============================================================================

CREATE TABLE IF NOT EXISTS checkout_session_events (
    id              BIGSERIAL   PRIMARY KEY,
    session_id      UUID        NOT NULL REFERENCES checkout_sessions(id),
    event_type      TEXT        NOT NULL,
    -- CS_CREATED, ORDER_CREATED, CAPTURE_SUBMITTED,
    -- PAID_VERIFIED, EXPIRED, CANCELED, FAILED, REPLAY_BLOCKED
    actor           TEXT        NOT NULL DEFAULT 'SYSTEM',
    -- SYSTEM | USER | PAYPAL_WEBHOOK
    details         JSONB       NOT NULL DEFAULT '{}',
    -- Must NOT contain paypal_request_id_*, nonce, or raw secret values
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_cse_session
    ON checkout_session_events(session_id);
CREATE INDEX IF NOT EXISTS idx_cse_created
    ON checkout_session_events(created_at DESC);

COMMENT ON TABLE checkout_session_events
    IS 'Phase 2: Immutable audit trail for checkout session state transitions.';

-- ============================================================================
-- 3. Verification
-- ============================================================================

DO $$
BEGIN
    ASSERT (
        SELECT COUNT(*) FROM information_schema.tables
        WHERE table_name IN ('checkout_sessions', 'checkout_session_events')
    ) = 2, 'checkout_sessions tables not created';

    ASSERT (
        SELECT COUNT(*) FROM pg_indexes
        WHERE indexname = 'uq_cs_tenant_plan_active'
    ) = 1, 'First-writer guard index not created';

    RAISE NOTICE 'Phase 2 migration 01 completed successfully';
END $$;
