-- Fix Issue 2: Populate limits_json and complete features_json for beta_private_starter_v1.
--
-- Previously limits_json was NULL and features_json lacked allowed_pack_types.
-- Both are required by plan_enforcer.py:
--   - features_json.allowed_pack_types → check_allowed_pack_type()
--   - limits_json.pack_type_limits.*.max_cost_usd_micros → check_pack_type_max_cost()
--   - limits_json.rate_limit_post_per_min → check_rate_limit_post()
--
-- Pack type cap $5.00 (5_000_000 micros) per run is conservative for a $29/month beta plan.
-- Rate limits: 10 POST/min, 120 poll/min — adequate for single-user private beta sessions.

UPDATE plans
SET
  features_json = '{"price_usd_cents": 2900, "label": "Private Beta", "validity_days": 30, "allowed_pack_types": ["decision", "url", "ocr"]}',
  limits_json   = '{"rate_limit_post_per_min": 10, "rate_limit_poll_per_min": 120, "pack_type_limits": {"decision": {"max_cost_usd_micros": 5000000}, "url": {"max_cost_usd_micros": 5000000}, "ocr": {"max_cost_usd_micros": 5000000}}}',
  updated_at    = now()
WHERE plan_id = 'beta_private_starter_v1';
