-- Phase 2 seed: beta_private_starter_v1 plan
-- All NOT NULL columns must be explicit: DB has no server-side DEFAULTs for
-- default_profile_version, limits_json, updated_at (SQLAlchemy Python-side defaults
-- do not apply on raw SQL execution).
INSERT INTO plans (
  plan_id,
  name,
  status,
  default_profile_version,
  features_json,
  limits_json,
  created_at,
  updated_at
)
VALUES (
  'beta_private_starter_v1',
  'Decisionproof Private Beta',
  'ACTIVE',
  'v0.4.2.2',
  '{"price_usd_cents": 2900, "label": "Private Beta", "validity_days": 30, "allowed_pack_types": ["decision", "url", "ocr"]}',
  '{"rate_limit_post_per_min": 10, "rate_limit_poll_per_min": 120, "pack_type_limits": {"decision": {"max_cost_usd_micros": 5000000}, "url": {"max_cost_usd_micros": 5000000}, "ocr": {"max_cost_usd_micros": 5000000}}}',
  now(),
  now()
)
ON CONFLICT (plan_id) DO UPDATE SET
  features_json           = EXCLUDED.features_json,
  limits_json             = EXCLUDED.limits_json,
  status                  = 'ACTIVE',
  default_profile_version = EXCLUDED.default_profile_version,
  updated_at              = now();