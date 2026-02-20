-- P6.3: Webhook Dedup Gate Migration
-- Created: 2026-02-21
-- Purpose: Atomic idempotency gate for concurrent webhook delivery
--          Prevents multiple business-processing commits for the same event
--          even when 5 concurrent requests arrive with identical payload.
--
-- Design: INSERT ON CONFLICT DO NOTHING RETURNING id
--   → returned row = first/re-processing handler → continue
--   → no row       = duplicate/concurrent → 200 immediately (zero side effects)

-- ============================================================================
-- 1. webhook_dedup_events table
-- ============================================================================

CREATE TABLE IF NOT EXISTS webhook_dedup_events (
    id              BIGSERIAL PRIMARY KEY,

    -- Provider and dedup key (the atomic gate)
    provider        TEXT NOT NULL,                              -- paypal | toss
    dedup_key       TEXT NOT NULL,                              -- ev_<event_id> | tx_<tid> | pkey_<key>

    -- Timestamps
    first_seen_at   TIMESTAMPTZ NOT NULL DEFAULT now(),        -- First delivery time
    last_seen_at    TIMESTAMPTZ,                               -- Last duplicate delivery time

    -- Processing status
    -- processing : acquired, business logic running
    -- done       : successfully processed (do not re-process)
    -- failed     : processing error (PG retry is allowed)
    status          TEXT NOT NULL DEFAULT 'processing'
                    CHECK (status IN ('processing', 'done', 'failed')),

    -- Request payload hash only (never raw payload)
    request_hash    TEXT                                        -- SHA-256 hex of raw body
);

-- ============================================================================
-- 2. Unique constraint — the SSOT for concurrent idempotency
-- ============================================================================

-- Enforces: exactly one INSERT succeeds per (provider, dedup_key)
-- This is what makes the gate atomic under concurrent load.
ALTER TABLE webhook_dedup_events
    ADD CONSTRAINT uq_webhook_dedup_events
    UNIQUE (provider, dedup_key);

-- ============================================================================
-- 3. Indexes for query performance
-- ============================================================================

CREATE INDEX IF NOT EXISTS idx_webhook_dedup_provider_key
    ON webhook_dedup_events (provider, dedup_key);

CREATE INDEX IF NOT EXISTS idx_webhook_dedup_status
    ON webhook_dedup_events (status)
    WHERE status IN ('processing', 'failed');   -- Partial index: only active states

CREATE INDEX IF NOT EXISTS idx_webhook_dedup_first_seen
    ON webhook_dedup_events (first_seen_at DESC);

-- ============================================================================
-- 4. Comments
-- ============================================================================

COMMENT ON TABLE webhook_dedup_events IS
    'P6.3: Atomic idempotency gate for concurrent webhook delivery (DEC-P02-6)';

COMMENT ON COLUMN webhook_dedup_events.dedup_key IS
    'PayPal: ev_{event.id} | Toss: tx_{Tosspayments-Webhook-Transmission-Id} or pkey_{paymentKey}';

COMMENT ON COLUMN webhook_dedup_events.status IS
    'processing=in-flight, done=committed, failed=error(PG retry allowed)';

COMMENT ON COLUMN webhook_dedup_events.request_hash IS
    'SHA-256 of raw request body (audit only; never stores raw payload)';

-- ============================================================================
-- Verification
-- ============================================================================

-- Expected: 1 row per constraint/index
-- SELECT conname FROM pg_constraint WHERE conrelid = 'webhook_dedup_events'::regclass AND contype = 'u';
-- → uq_webhook_dedup_events
--
-- SELECT indexname FROM pg_indexes WHERE tablename = 'webhook_dedup_events';
-- → idx_webhook_dedup_provider_key, idx_webhook_dedup_status, idx_webhook_dedup_first_seen
