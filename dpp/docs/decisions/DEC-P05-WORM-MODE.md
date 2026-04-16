# DEC-P05-WORM-MODE

**Decision**: P5.8 WORM Mode Hardening — Explicit GOVERNANCE/COMPLIANCE Selection
**Status**: Accepted
**Date**: 2026-02-21
**Phase**: Pilot Cutover Security Hardening

---

## Context

DPP stores kill-switch audit records in S3 with Object Lock (WORM) to ensure immutability.
Before P5.8, the WORM mode was hardcoded to `GOVERNANCE` in `S3WormAuditSink` with no
mechanism for operators to select `COMPLIANCE` or to enforce explicit mode declaration
in production-required mode.

Two gaps existed:

1. **Silent default**: In required mode (`KILL_SWITCH_AUDIT_REQUIRED=1`), the code silently
   applied `GOVERNANCE` even if the operator intended `COMPLIANCE`. This is a misconfiguration
   risk — the actual retention strength may not match operational intent.

2. **No COMPLIANCE support**: Production environments requiring maximum audit integrity
   (e.g., financial compliance, SOC2 Type II) need COMPLIANCE mode where no principal,
   including root, can delete or shorten retention.

---

## Decision

Introduce `KILL_SWITCH_AUDIT_WORM_MODE` env var with two valid values:
- `GOVERNANCE` — pilot/pre-prod default; break-glass override possible
- `COMPLIANCE` — production locked; no override possible

### Required Mode Enforcement

When `KILL_SWITCH_AUDIT_REQUIRED=1`:
- `KILL_SWITCH_AUDIT_WORM_MODE` MUST be explicitly set
- Absent → `AuditSinkConfigError(WORM_MODE_REQUIRED_BUT_NOT_SET)` at boot and on each request
- Invalid (e.g., `governance`, `GOVT`) → `AuditSinkConfigError(INVALID_WORM_MODE)`
- Operators must consciously choose — no silent defaults in production-required mode

When `KILL_SWITCH_AUDIT_REQUIRED=0` (dev/CI):
- `KILL_SWITCH_AUDIT_WORM_MODE` falls back to `GOVERNANCE` if not set
- This is acceptable for non-required mode where audit records are advisory

### PutObject Pairing Invariant

`S3WormAuditSink.put_record()` ALWAYS sends `ObjectLockMode` and `ObjectLockRetainUntilDate`
as a paired set. AWS S3 Object Lock requires both parameters; providing only one is an API error.

### No Bypass Behavior

`put_record()` NEVER includes `BypassGovernanceRetention` or any bypass-related parameters.
The service role must NOT hold `s3:BypassGovernanceRetention`. Break-glass bypass capability
is restricted to a dedicated break-glass IAM role (see runbook).

---

## GOVERNANCE vs COMPLIANCE

### GOVERNANCE

- Objects can be deleted or retention-shortened with:
  1. `s3:BypassGovernanceRetention` IAM permission on the actor's role, AND
  2. `x-amz-bypass-governance-retention: true` header on the API request
- Bypass attempts are captured by CloudTrail data events → EventBridge → SNS alert
- Our service role MUST NOT hold `s3:BypassGovernanceRetention`

**Use when**: Break-glass deletion may be operationally required (e.g., GDPR erasure with
regulatory approval, operational mistake recovery during pilot).

### COMPLIANCE

- Objects CANNOT be deleted or retention-shortened by any principal (including root)
- AWS enforces this at the storage layer; IAM permissions cannot override it
- DELETE attempts during the retention period return `ObjectLockConfigurationError`
- CloudTrail data events still capture failed DELETE attempts — useful for detecting probes

**Use when**: Audit integrity is a hard compliance requirement and no emergency deletion
scenario is acceptable (SOC2, PCI-DSS, financial audit trails).

---

## Configuration Reference

| Env Var | Required? | Valid Values | Default | Notes |
|---------|-----------|--------------|---------|-------|
| `KILL_SWITCH_AUDIT_WORM_MODE` | When `REQUIRED=1`: mandatory | `GOVERNANCE`, `COMPLIANCE` | `GOVERNANCE` (non-required only) | Case-sensitive, uppercase |
| `KILL_SWITCH_AUDIT_REQUIRED` | No | `0`, `1` | `0` | `1` = fail-closed, explicit WORM_MODE required |
| `KILL_SWITCH_AUDIT_BUCKET` | When `REQUIRED=1`: mandatory | S3 bucket name | _(unset)_ | Must have Object Lock enabled at creation |
| `KILL_SWITCH_AUDIT_REGION` | No | AWS region | `AWS_DEFAULT_REGION` | Defaults to `us-east-1` if both unset |

---

## Error Codes

| Code | Condition | Operator Action |
|------|-----------|-----------------|
| `AUDIT_SINK_REQUIRED_BUT_NOT_CONFIGURED` | `REQUIRED=1` + no `KILL_SWITCH_AUDIT_BUCKET` | Set `KILL_SWITCH_AUDIT_BUCKET` to an S3 Object Lock bucket |
| `WORM_MODE_REQUIRED_BUT_NOT_SET` | `REQUIRED=1` + no `KILL_SWITCH_AUDIT_WORM_MODE` | Set `KILL_SWITCH_AUDIT_WORM_MODE=GOVERNANCE` or `COMPLIANCE` |
| `INVALID_WORM_MODE` | `REQUIRED=1` + `WORM_MODE` not in `{GOVERNANCE, COMPLIANCE}` | Check for typos; values are case-sensitive and must be uppercase |

---

## CloudTrail Data Events Requirement

CloudTrail **data events** (not management events) are required for object-level visibility.
Management events capture bucket-level operations but NOT individual object operations
(PutObject, DeleteObject, PutObjectRetention).

Without data events:
- PutObject calls from our service are not individually auditable
- DELETE attempts on locked objects are invisible in CloudTrail
- Governance bypass events are not captured

Data events must be enabled for the audit bucket. See `kill_switch_audit_break_glass_alerts.md`
for the exact `put-event-selectors` command.

---

## Alternatives Considered

### Alt-1: Always use COMPLIANCE (rejected for pilot phase)

**Rejected**: COMPLIANCE mode prevents any deletion, including emergency corrections during
the pilot phase. If a kill-switch audit record is written incorrectly (e.g., wrong tenant_id),
there is no recovery path. GOVERNANCE provides break-glass flexibility during the pilot while
still detecting unauthorized access via CloudTrail + EventBridge.

Production environments may opt into COMPLIANCE via `KILL_SWITCH_AUDIT_WORM_MODE=COMPLIANCE`
once the pilot phase is complete and operational procedures are stable.

### Alt-2: Auto-detect mode based on environment (rejected)

**Rejected**: Inferring production from environment variables (e.g., `ENVIRONMENT=prod`) creates
implicit coupling and can fail silently. Operators must explicitly declare the WORM mode so that
configuration intent is always visible in the deployment manifest.

### Alt-3: Default to GOVERNANCE in required mode (rejected)

**Rejected**: A silent default in required mode means operators may not realize they are running
GOVERNANCE when they intended COMPLIANCE (or vice versa). The error message
`WORM_MODE_REQUIRED_BUT_NOT_SET` forces a conscious decision at deployment time.

---

## Acceptance Criteria

- [x] RC-10.P5.8 gate: 6/6 PASSED
- [x] `S3WormAuditSink(bucket, mode=...)` accepts GOVERNANCE and COMPLIANCE
- [x] `put_record()` always sends `ObjectLockMode` + `ObjectLockRetainUntilDate` as a pair
- [x] `put_record()` never includes `BypassGovernanceRetention` or any bypass parameters
- [x] `validate_audit_required_config()` raises `WORM_MODE_REQUIRED_BUT_NOT_SET` when mode absent
- [x] `validate_audit_required_config()` raises `INVALID_WORM_MODE` for invalid values
- [x] `get_default_audit_sink()` passes `KILL_SWITCH_AUDIT_WORM_MODE` to `S3WormAuditSink`
- [x] Runbook updated with GOVERNANCE vs COMPLIANCE guidance + CloudTrail data events requirement

---

## References

- `dpp/apps/api/dpp_api/audit/sinks.py` — P5.8 implementation
- `dpp/apps/api/tests/test_rc10_worm_mode_hardening.py` — RC-10.P5.8 test gate
- `dpp/ops/runbooks/kill_switch_audit_break_glass_alerts.md` — Break-glass procedures (updated P5.8)
- `dpp/docs/decisions/DEC-P05-LOG-MASKING-WORM.md` — P5.3 WORM initial design
- `dpp/tools/README_RC_GATES.md` — RC-10.P5.8 documentation
