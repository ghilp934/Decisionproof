# Decisionproof v0.4 — Pilot Readiness Document

> **Status**: Paid private beta — invited participants only.

## Scope

This pilot covers invited beta participants accessing the Decisionproof API
(`https://api.decisionproof.io.kr`) under the `beta_private_starter_v1` plan.

In scope:
- `POST /v1/runs` and `GET /v1/runs/{run_id}` endpoints
- Dashboard API key management (create / revoke, up to 3 keys per workspace)
- PayPal-based payment and 30-day access-cycle renewal
- Audit log export from the dashboard

Out of scope for this pilot:
- Third-party compliance certifications (not claimed during paid private beta)
- Uptime SLA commitments (not published during paid private beta)

## Entry Criteria

- [ ] RC Gates (all P0/P1/P2 gates) passing on `master`
- [ ] PayPal sandbox end-to-end checkout smoke test passed
- [ ] API key creation and revocation verified in staging
- [ ] WORM S3 bucket confirmed write-once policy active
- [ ] Invite email dispatched to pilot cohort

## Exit Criteria

- [ ] At least one pilot participant has successfully completed a run end-to-end
- [ ] No P0 defects open (data loss, payment failure, auth bypass)
- [ ] Audit log export verified by at least one participant
- [ ] No mandatory consumer-rights violations identified in Terms of Use

## Monitoring & Alerts

Primary signals during the pilot period:
- API error rate (5xx) on `/v1/runs` — threshold: >1% over 5-minute window
- Run reaper lag — threshold: >5 minutes behind expected reconciliation interval
- Payment capture failures from PayPal webhook — threshold: any failure triggers investigation
- WORM bucket write failures — threshold: any failure triggers immediate escalation

Contact for incidents: ghilplip934@gmail.com (response within 1 business day during beta).

## Kill Switch

If a critical defect is discovered during the pilot:

1. **Soft stop**: Operator sets workspace-level rate limit to 0 on affected tenants
2. **Hard stop**: API key revocation for all active pilot keys from the dashboard
3. **Payment freeze**: New PayPal order creation disabled at the checkout flow level
4. **Communication**: Email notification sent to all active pilot participants within 2 hours

Recovery is manual and requires a patch release passing all RC gates before pilot access is restored.
