-- Phase 3: SES Feedback Events Table
-- Stores bounce/complaint/delivery notifications from SES via SNS/SQS
-- Created: 2026-02-17

-- Create table
CREATE TABLE IF NOT EXISTS ses_feedback_events (
    -- Primary key
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    -- Timestamps
    received_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    -- SES notification metadata
    notification_type TEXT NOT NULL CHECK (notification_type IN ('Bounce', 'Complaint', 'Delivery')),
    message_id TEXT NOT NULL,  -- SES messageId
    source TEXT NOT NULL,      -- Sender email
    destinations JSONB NOT NULL DEFAULT '[]'::jsonb,  -- Array of recipient emails
    primary_recipient TEXT,    -- First recipient (for easy filtering)

    -- Type-specific data
    type_data JSONB NOT NULL DEFAULT '{}'::jsonb,
    -- For Bounce: bounce_type, bounce_sub_type, bounced_recipients
    -- For Complaint: complaint_feedback_type, complained_recipients
    -- For Delivery: processing_time_millis, smtp_response

    -- Full payload for debugging
    payload JSONB NOT NULL,

    -- Metadata
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Indexes for common queries
CREATE INDEX IF NOT EXISTS idx_ses_feedback_notification_type
    ON ses_feedback_events(notification_type);

CREATE INDEX IF NOT EXISTS idx_ses_feedback_received_at
    ON ses_feedback_events(received_at DESC);

CREATE INDEX IF NOT EXISTS idx_ses_feedback_source
    ON ses_feedback_events(source);

CREATE INDEX IF NOT EXISTS idx_ses_feedback_primary_recipient
    ON ses_feedback_events(primary_recipient)
    WHERE primary_recipient IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_ses_feedback_message_id
    ON ses_feedback_events(message_id);

-- GIN index for JSONB queries
CREATE INDEX IF NOT EXISTS idx_ses_feedback_payload_gin
    ON ses_feedback_events USING gin(payload);

-- Comments
COMMENT ON TABLE ses_feedback_events IS 'SES bounce/complaint/delivery notifications (Phase 3)';
COMMENT ON COLUMN ses_feedback_events.notification_type IS 'Bounce, Complaint, or Delivery';
COMMENT ON COLUMN ses_feedback_events.message_id IS 'SES messageId from mail.messageId';
COMMENT ON COLUMN ses_feedback_events.type_data IS 'Type-specific fields (bounce_type, complaint_feedback_type, etc.)';
COMMENT ON COLUMN ses_feedback_events.payload IS 'Full SES notification JSON for debugging';

-- Row-Level Security (RLS)
-- Idempotent: safe to re-run (DO block skips if table not found).
-- NOTE: 20260220_fix_ses_feedback_events_rls_and_guard.sql is the canonical
--       apply-to-existing-DB migration. This block keeps the source of truth
--       in sync so a fresh DB deployment via this file also gets RLS.
DO $$
BEGIN
  IF to_regclass('public.ses_feedback_events') IS NOT NULL THEN
    EXECUTE 'ALTER TABLE public.ses_feedback_events ENABLE ROW LEVEL SECURITY';
    EXECUTE 'REVOKE ALL ON TABLE public.ses_feedback_events FROM anon';
    EXECUTE 'REVOKE ALL ON TABLE public.ses_feedback_events FROM authenticated';
    EXECUTE 'GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE public.ses_feedback_events TO service_role';
    EXECUTE 'GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE public.ses_feedback_events TO postgres';
  END IF;
END $$;
