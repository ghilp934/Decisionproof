# Decisionproof Pilot Runbook (RC-9)

This runbook is intentionally vendor-neutral and contains no secrets. Replace placeholders during deployment.

## Triage Basics
- Confirm scope: API only vs Worker/Reaper impact
- Collect correlation keys from logs:
  - request_id (RC-6)
  - trace_id/span_id (RC-7)
- Identify release boundary: "what changed" since last known-good deploy

## Money Leak Detection
When `log_entries{severity="CRITICAL", reconcile_type="no_receipt_audit"} > 0`:
1) Freeze: stop onboarding new pilot tenants if possible
2) Find affected run_id(s) and correlate request_id/trace_id
3) Verify S3 object metadata vs DB reservation/settlement records
4) If unresolved within 5 minutes → execute Rollback Procedure and escalate

## API Service Down
When `up{job="dpp-api"} == 0` for > 2 minutes:
1) Check /health and /readyz (if reachable)
2) Check DB connectivity errors and recent deploy status
3) If tied to deploy and not quickly reversible → Rollback Procedure

## Database Connection Failure
When `dpp_readyz_database_status != 1` for > 1 minute:
1) Check DB health, connection limits, pool exhaustion
2) If correlated with new deployment/migration → consider rollback

## Elevated Error Rate
When 5xx ratio > 5% over 5 minutes:
1) Identify top failing endpoints and error types (RFC 9457 problem types)
2) Correlate traces for representative failures
3) Apply Kill Switch / stop rules if overload-like, otherwise rollback if regression

## High Queue Depth
When `sqs_queue_depth{queue="dpp-runs"} > 1000`:
1) Check worker throughput and error logs
2) Scale workers if safe
3) Confirm no billing invariant violations emerge

## Worker Heartbeat Missing
When heartbeat gap > 120s:
1) Inspect worker logs for stuck runs
2) Restart worker instance if necessary
3) Verify queue drains and runs complete

## Rollback Procedure
```bash
kubectl rollout undo deployment/dpp-api
kubectl rollout undo deployment/dpp-worker
kubectl rollout undo deployment/dpp-reaper

kubectl rollout status deployment/dpp-api
```

## Kill Switch
Manual stop rules (pilot pause):
- Incident count spikes
- Money leak suspected
- Missing critical logs/traces

Prefer "stop the pilot" over "ship a hotfix" if invariants are at risk.

## Escalation
- On-call: <ONCALL_PLACEHOLDER>
- Incident commander: <IC_PLACEHOLDER>
- Postmortem doc: <POSTMORTEM_TEMPLATE_LINK_PLACEHOLDER>
