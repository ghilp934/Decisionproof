-- Phase 2: billing_orders schema changes for v1.0 payment front door
-- DP-V1-P1-SOW §5.3
-- Created: 2026-04-16
-- Purpose: Add CAPTURE_SUBMITTED / PAID_VERIFIED statuses + checkout_session_id FK

-- ============================================================================
-- 1. Expand billing_orders.status CHECK constraint
-- ============================================================================
-- Preserves 'PAID' for backward compat with existing pilot data.
-- New code must use 'PAID_VERIFIED' for all new orders.

ALTER TABLE billing_orders
    DROP CONSTRAINT IF EXISTS billing_orders_status_check;

ALTER TABLE billing_orders
    ADD CONSTRAINT billing_orders_status_check
    CHECK (status IN (
        'PENDING',              -- initial state when BillingOrder row created
        'CAPTURE_SUBMITTED',    -- NEW: sync capture accepted by PayPal (non-authoritative)
        'PAID_VERIFIED',        -- NEW: webhook PAYMENT.CAPTURE.COMPLETED confirmed
        'PAID',                 -- LEGACY: pilot data only — do not use for new orders
        'FAILED',
        'REFUNDED',
        'CANCELLED',
        'PARTIAL_REFUNDED'
    ));

-- ECONOMIC SIDE EFFECT NOTE:
--   'CAPTURE_SUBMITTED' is set by sync capture path — no entitlement change.
--   'PAID_VERIFIED'     is set ONLY by webhook handler — triggers _grant_entitlement().
--   Writing 'PAID_VERIFIED' outside the webhook handler is FORBIDDEN.

-- ============================================================================
-- 2. Add checkout_session_id FK
-- ============================================================================

ALTER TABLE billing_orders
    ADD COLUMN IF NOT EXISTS checkout_session_id UUID
    REFERENCES checkout_sessions(id);

CREATE INDEX IF NOT EXISTS idx_billing_orders_cs
    ON billing_orders(checkout_session_id)
    WHERE checkout_session_id IS NOT NULL;

-- ============================================================================
-- 3. Verification
-- ============================================================================

DO $$
BEGIN
    ASSERT (
        SELECT COUNT(*) FROM information_schema.columns
        WHERE table_name = 'billing_orders'
          AND column_name = 'checkout_session_id'
    ) = 1, 'checkout_session_id column not added to billing_orders';

    RAISE NOTICE 'Phase 2 migration 02 completed successfully';
END $$;
