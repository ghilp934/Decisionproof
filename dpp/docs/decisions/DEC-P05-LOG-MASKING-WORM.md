# DEC-P05-LOG-MASKING-WORM

**Decision**: P5.2 Log Masking + P5.3 Kill-Switch WORM Audit
**Status**: Accepted
**Date**: 2026-02-20
**Phase**: Pilot Cutover Security Hardening

---

## Context

DPP v0.4.2.2 processes payments via PayPal and TossPayments webhooks. Prior to this
decision, webhook bodies and exception tracebacks were logged verbatim, risking PII
(email addresses, payer names) and payment secrets (Bearer tokens, API keys) appearing
in CloudWatch / Datadog log streams accessible to non-privileged operators.

Additionally, kill-switch state changes had no tamper-evident audit trail; a compromised
or rogue operator could silently disable safety controls.

---

## Decisions

### P5.2: Centralized Log Sanitizer (`sanitize.py`)

**Decision**: All log output passes through a three-tier sanitizer applied at the
`JSONFormatter` level, ensuring no PII or secrets reach the log transport.

**Three-tier size gate** (ReDoS mitigation):

| Input size | Action |
|---|---|
| `> 2048` chars | Return `[TRUNCATED len=N sha256=...]`; skip all regex |
| `> 512` chars | Prefix check only (`Bearer `, `Basic `); skip regex |
| `≤ 512` chars | Full regex replacement (5 pre-compiled patterns) |

**Rationale for size gates**:
- Catastrophic backtracking attacks target inputs that are large enough to trigger
  polynomial-time regex evaluation. By refusing to run regex on inputs over 512 chars,
  we eliminate the attack surface entirely.
- Pre-compiling patterns at module import eliminates per-call compilation overhead.

**Rationale for `capture_locals=False`**:
- Python's `traceback.TracebackException` can optionally capture local variable frames.
  Local variables may contain request bodies, tokens, or database records. Setting
  `capture_locals=False` ensures tracebacks contain only the call stack, not values.

**Sensitive key list** (dict-key redaction):
```
authorization, token, access_token, refresh_token, api_key, secret, signature,
email, phone, card, pan, cvv, cvc, payer, billing, customerkey, paymentkey, billingkey
```

These cover both camelCase (TossPayments) and snake_case (PayPal) API field names.

---

### P5.3: Kill-Switch WORM Audit

**Decision**: Kill-switch changes write an audit record to S3 Object Lock (GOVERNANCE
mode, 7-year retention) **before** state is mutated. Fail-closed semantics (`STRICT=1`)
block the change if the audit write fails.

**Fail-closed principle**:
> A failed audit write is treated as a security event, not a degraded-but-acceptable
> condition. In `STRICT=1` mode, the system refuses to proceed rather than risk an
> unaudited state change.

**GOVERNANCE vs COMPLIANCE**:
- COMPLIANCE Object Lock prevents deletion even by the bucket owner; it is irreversible.
- GOVERNANCE allows lock bypass with explicit IAM permission
  (`s3:BypassGovernanceRetention`), which enables emergency record correction during
  the development phase without requiring AWS Support intervention.
- **Plan**: Upgrade to COMPLIANCE once the audit pipeline is validated in production
  (target: post-Pilot Phase 6).

**Token / IP fingerprinting**:
- Raw admin tokens and IP addresses are **never** stored in audit records.
- A 12-character sha256 hex fingerprint is stored instead, sufficient for operator
  correlation (matching a known token to an audit record) without exposing credentials.

**Record format** (`schema_version: "1.0"`):
- Immutable once written; schema changes require a new schema version.
- `result` field captures whether the state change succeeded (`"ok"`) or was blocked
  (`"failed"`); the `error` field provides the reason on failure.

---

## Alternatives Considered

### Alt-1: Middleware-level sanitizer (rejected)

Apply sanitization in a Starlette middleware before log handlers are called.

**Rejected because**: Middleware cannot intercept `exc_info` tracebacks generated inside
route handlers. The formatter-level approach covers all log paths uniformly.

### Alt-2: Regex on full log line (rejected)

Run a single regex on the final JSON string after formatting.

**Rejected because**: Parsing structured JSON with regex is fragile (false positives on
base64 payloads). The key-based dict approach is more precise and faster.

### Alt-3: COMPLIANCE Object Lock from day 1 (deferred)

Use COMPLIANCE retention to make records permanently immutable.

**Deferred**: COMPLIANCE records cannot be deleted or shortened even by AWS Support.
During pilot, we need flexibility to correct erroneous records. GOVERNANCE provides
tamper-evidence with a controlled escape hatch.

---

## Consequences

**Positive**:
- PII and secrets can no longer leak via log streams to non-privileged log viewers.
- Kill-switch changes have a tamper-evident, independently verifiable audit trail.
- Fail-closed semantics prevent silent audit failures in production.

**Negative / Trade-offs**:
- Sanitizer adds a small CPU cost per log record (~microseconds for ≤512 char strings).
- `[REDACTED]` markers reduce debuggability for support engineers; compensated by
  `payload_hash` and `error_type` fields that preserve enough signal for root cause analysis.
- GOVERNANCE Object Lock requires `s3:BypassGovernanceRetention` for record deletion,
  which must be tightly controlled via IAM.

---

## References

- `dpp/apps/api/dpp_api/utils/sanitize.py` — sanitizer implementation
- `dpp/apps/api/dpp_api/audit/sinks.py` — S3 / file / failing sinks
- `dpp/apps/api/dpp_api/audit/kill_switch_audit.py` — record builder
- `dpp/ops/runbooks/kill_switch_audit_worm.md` — operational runbook
- `dpp/apps/api/tests/test_rc10_logging_masking_and_worm_audit.py` — RC-10 test gate
