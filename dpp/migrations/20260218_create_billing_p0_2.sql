-- P0-2: Billing Schema Migration (PayPal + TossPayments)
-- DEC-P02-1 ~ DEC-P02-6 Implementation
-- Created: 2026-02-18
-- Purpose: Paid Pilot payment provider migration from Stripe to PayPal/Toss

-- ============================================================================
-- 1. Billing Orders Table
-- ============================================================================

CREATE TABLE IF NOT EXISTS billing_orders (
    id BIGSERIAL PRIMARY KEY,
    tenant_id TEXT NOT NULL REFERENCES tenants(tenant_id),

    -- Provider identification (DEC-P02-1)
    provider TEXT NOT NULL CHECK (provider IN ('PAYPAL', 'TOSS')),
    provider_order_id TEXT NOT NULL,
    provider_capture_id TEXT,  -- PayPal capture ID (nullable)
    provider_payment_key TEXT, -- Toss paymentKey (nullable)

    -- Order details
    plan_id TEXT NOT NULL REFERENCES plans(plan_id),
    currency TEXT NOT NULL DEFAULT 'USD' CHECK (currency IN ('USD', 'KRW')),
    amount TEXT NOT NULL, -- Decimal string for precision

    -- Status tracking
    status TEXT NOT NULL DEFAULT 'PENDING' CHECK (status IN (
        'PENDING', 'PAID', 'FAILED', 'REFUNDED', 'CANCELLED', 'PARTIAL_REFUNDED'
    )),

    -- Order metadata (renamed to avoid SQLAlchemy reserved keyword)
    order_metadata JSONB,

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    -- DEC-P02-6: Idempotency constraint
    CONSTRAINT uq_billing_orders_provider_order UNIQUE (provider, provider_order_id)
);

-- Indexes
CREATE INDEX idx_billing_orders_tenant ON billing_orders(tenant_id);
CREATE INDEX idx_billing_orders_status ON billing_orders(status);
CREATE INDEX idx_billing_orders_created ON billing_orders(created_at DESC);

-- Updated_at trigger
CREATE OR REPLACE FUNCTION update_billing_orders_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trigger_billing_orders_updated_at
    BEFORE UPDATE ON billing_orders
    FOR EACH ROW
    EXECUTE FUNCTION update_billing_orders_updated_at();

-- Row Level Security (RLS)
ALTER TABLE billing_orders ENABLE ROW LEVEL SECURITY;

-- Policy: tenants can only see their own orders
CREATE POLICY tenant_billing_orders_select ON billing_orders
    FOR SELECT
    USING (tenant_id = current_setting('app.tenant_id', TRUE)::TEXT);

COMMENT ON TABLE billing_orders IS 'P0-2: Payment orders from PayPal or TossPayments (DEC-P02-1)';


-- ============================================================================
-- 2. Billing Events Table
-- ============================================================================

CREATE TABLE IF NOT EXISTS billing_events (
    id BIGSERIAL PRIMARY KEY,

    -- Provider identification (DEC-P02-5)
    provider TEXT NOT NULL CHECK (provider IN ('PAYPAL', 'TOSS')),
    event_id TEXT NOT NULL,
    event_type TEXT NOT NULL,

    -- Related order (nullable)
    order_id BIGINT REFERENCES billing_orders(id),

    -- Event payload
    raw_payload JSONB NOT NULL,

    -- Processing status
    received_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    processed_at TIMESTAMPTZ,

    -- Verification (DEC-P02-5)
    verification_status TEXT NOT NULL CHECK (verification_status IN (
        'SUCCESS', 'FAILED', 'PENDING', 'FRAUD'
    )),
    verification_meta JSONB,

    -- DEC-P02-6: Idempotency constraint
    CONSTRAINT uq_billing_events_provider_event UNIQUE (provider, event_id)
);

-- Indexes
CREATE INDEX idx_billing_events_order ON billing_events(order_id);
CREATE INDEX idx_billing_events_received ON billing_events(received_at DESC);
CREATE INDEX idx_billing_events_provider_type ON billing_events(provider, event_type);

-- Row Level Security (RLS) - Admin only
ALTER TABLE billing_events ENABLE ROW LEVEL SECURITY;

CREATE POLICY admin_billing_events_all ON billing_events
    FOR ALL
    USING (current_setting('app.role', TRUE) = 'admin');

COMMENT ON TABLE billing_events IS 'P0-2: Webhook events from PayPal or TossPayments (DEC-P02-5, DEC-P02-6)';


-- ============================================================================
-- 3. Entitlements Table
-- ============================================================================

CREATE TABLE IF NOT EXISTS entitlements (
    id BIGSERIAL PRIMARY KEY,
    tenant_id TEXT NOT NULL REFERENCES tenants(tenant_id),
    plan_id TEXT NOT NULL REFERENCES plans(plan_id),

    -- Status tracking (DEC-P02-2, DEC-P02-3, DEC-P02-4)
    status TEXT NOT NULL DEFAULT 'FREE' CHECK (status IN (
        'FREE', 'ACTIVE', 'SUSPENDED'
    )),

    -- Validity period
    valid_from TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    valid_until TIMESTAMPTZ,

    -- Audit trail
    order_id BIGINT REFERENCES billing_orders(id),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT uq_entitlements_tenant_plan UNIQUE (tenant_id, plan_id)
);

-- Indexes
CREATE INDEX idx_entitlements_tenant ON entitlements(tenant_id);
CREATE INDEX idx_entitlements_status ON entitlements(status);
CREATE INDEX idx_entitlements_valid ON entitlements(tenant_id, valid_from, valid_until);

-- Updated_at trigger
CREATE OR REPLACE FUNCTION update_entitlements_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trigger_entitlements_updated_at
    BEFORE UPDATE ON entitlements
    FOR EACH ROW
    EXECUTE FUNCTION update_entitlements_updated_at();

-- Row Level Security (RLS)
ALTER TABLE entitlements ENABLE ROW LEVEL SECURITY;

CREATE POLICY tenant_entitlements_select ON entitlements
    FOR SELECT
    USING (tenant_id = current_setting('app.tenant_id', TRUE)::TEXT);

COMMENT ON TABLE entitlements IS 'P0-2: Tenant plan entitlements and status (DEC-P02-2, DEC-P02-3, DEC-P02-4)';


-- ============================================================================
-- 4. Billing Audit Logs Table
-- ============================================================================

CREATE TABLE IF NOT EXISTS billing_audit_logs (
    id BIGSERIAL PRIMARY KEY,

    -- Event identification
    event_type TEXT NOT NULL,
    -- PAYMENT_COMPLETED, PAYMENT_REFUNDED, ENTITLEMENT_ACTIVATED, etc.

    -- Related entities
    tenant_id TEXT,
    related_entity_type TEXT, -- ORDER, ENTITLEMENT, API_KEY
    related_entity_id TEXT,

    -- Actor and details
    actor TEXT, -- SYSTEM, ADMIN, WEBHOOK
    details JSONB NOT NULL,

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Indexes
CREATE INDEX idx_billing_audit_tenant ON billing_audit_logs(tenant_id);
CREATE INDEX idx_billing_audit_created ON billing_audit_logs(created_at DESC);
CREATE INDEX idx_billing_audit_event_type ON billing_audit_logs(event_type);

-- Row Level Security (RLS) - Admin only
ALTER TABLE billing_audit_logs ENABLE ROW LEVEL SECURITY;

CREATE POLICY admin_billing_audit_logs_select ON billing_audit_logs
    FOR SELECT
    USING (
        current_setting('app.role', TRUE) = 'admin'
        OR tenant_id = current_setting('app.tenant_id', TRUE)::TEXT
    );

COMMENT ON TABLE billing_audit_logs IS 'P0-2: Audit trail for payment and entitlement changes (DEC-P02-4)';


-- ============================================================================
-- 5. Seed Data (Optional)
-- ============================================================================

-- Insert default FREE entitlements for existing tenants
-- (Run this after migration if tenants exist)
-- INSERT INTO entitlements (tenant_id, plan_id, status, valid_from)
-- SELECT
--     t.tenant_id,
--     'plan_free' AS plan_id,
--     'FREE' AS status,
--     NOW() AS valid_from
-- FROM tenants t
-- WHERE NOT EXISTS (
--     SELECT 1 FROM entitlements e WHERE e.tenant_id = t.tenant_id
-- );


-- ============================================================================
-- 6. Migration Verification
-- ============================================================================

-- Verify tables created
DO $$
BEGIN
    ASSERT (SELECT COUNT(*) FROM information_schema.tables
            WHERE table_name IN ('billing_orders', 'billing_events', 'entitlements', 'billing_audit_logs')) = 4,
           'Not all billing tables were created';

    RAISE NOTICE 'P0-2 Billing Schema Migration completed successfully';
END $$;
