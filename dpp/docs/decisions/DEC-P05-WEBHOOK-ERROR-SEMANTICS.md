# DEC-P05-WEBHOOK-ERROR-SEMANTICS

**Decision**: P5.7 Webhook Error Semantics — Strict Client-vs-Server Failure Taxonomy
**Status**: Accepted
**Date**: 2026-02-21
**Phase**: Pilot Cutover Security Hardening

---

## Context

DPP receives payment webhooks from PayPal and TossPayments. Both providers implement
**retry logic**: when they receive a 5xx response they retry the webhook (typically
with exponential backoff). When they receive a 4xx response they do **not** retry
(treating it as a permanent client-side rejection).

Before P5.7, the webhook handlers contained two critical misclassification bugs:

1. **Signature mismatch returned 500** (not 401): The outer `except Exception` block
   in the PayPal handler was catching the `HTTPException(401)` raised inside the same
   block for signature mismatch, then re-raising it as `HTTPException(500)`. This meant
   a bad actor sending a webhook with a wrong signature caused a 500 response, which
   PayPal retried — compounding the problem.

2. **Network error during Toss re-query returned 401** (not 500): A transient upstream
   timeout was classified as a client authentication failure, which would cause the
   legitimate webhook to be permanently dropped by TossPayments after the first retry.

These bugs combined create a **retry storm** risk: providers retry what should be
permanent rejections, and permanently drop what should be transient failures.

---

## Decision

Adopt a strict HTTP status code taxonomy for all webhook endpoints:

### Taxonomy

| Class | Condition | HTTP Status | Error Code |
|-------|-----------|-------------|------------|
| A | Invalid JSON / malformed payload | 400 | `WEBHOOK_INVALID_JSON` |
| A | Missing required body fields | 400 | `WEBHOOK_INVALID_PAYLOAD` |
| B | Signature invalid / `verification_status != SUCCESS` | **401** | `WEBHOOK_SIGNATURE_INVALID` |
| C | Required provider headers missing | 400 | `WEBHOOK_MISSING_HEADERS` |
| C | Required signature header missing (when secret configured) | 400 | `WEBHOOK_MISSING_SIGNATURE_HEADER` |
| C | Unknown paymentKey (Toss 404) | 400 | `WEBHOOK_INVALID_PAYMENT_KEY` |
| D | Our misconfig: missing secret / webhook_id | **500** | `WEBHOOK_PROVIDER_MISCONFIG` |
| D | Our misconfig: Toss credentials rejected (401 from upstream) | **500** | `WEBHOOK_PROVIDER_MISCONFIG` |
| E | Upstream network / timeout / SDK error during verification | **500** | `WEBHOOK_VERIFY_UPSTREAM_FAILED` |
| F | Internal DB / processing error after successful verification | **500** | `WEBHOOK_INTERNAL_ERROR` |

**500 is ONLY for D, E, F. Signature mismatch is NEVER 500.**

### Why signature mismatch is 401 (not 500)

401 = "I received your request but cannot authenticate it." This accurately describes
signature failure: the payload arrived but its authenticity cannot be confirmed.

Returning 500 for signature mismatch tells the provider to retry — generating
repeated log noise, occupying server resources, and masking legitimate issues.
Returning 401 signals a permanent rejection of that specific webhook call.

### Why 500 is reserved for misconfig / upstream / internal errors

500 = "I had an internal problem; please retry later." This is correct for:
- **D (misconfig)**: Our server is not properly set up; an operator must fix it.
  Retry will keep failing until config is corrected.
- **E (upstream)**: The PayPal or Toss API was unreachable. A transient network
  problem; retry after a delay is appropriate. We add `Retry-After: 60`.
- **F (internal)**: Our DB had a transient issue. The webhook is valid; retry
  is appropriate.

Using 500 for these ensures the provider retries and the event is not permanently lost.

---

## Retry Risk Note

**Retry storms** occur when:
1. Client errors are classified as server errors → provider retries permanently invalid webhooks
2. High-volume compromised or misconfigured provider sends repeated bad signatures
3. Each retry triggers logging/alerting, creating alert fatigue

**Operational guidance**:
- Monitor `WEBHOOK_SIGNATURE_INVALID` (401) rate per provider. A sudden spike indicates
  a provider misconfiguration, MITM attempt, or certificate rotation issue.
- Monitor `WEBHOOK_VERIFY_UPSTREAM_FAILED` (500) rate. A sustained spike indicates
  PayPal/Toss API availability problems; alert the on-call team.
- Monitor `WEBHOOK_PROVIDER_MISCONFIG` (500). Any occurrence in production is a P1 alert —
  our application is starting without required environment variables.
- The `Retry-After: 60` header on all 5xx responses gives providers a minimum back-off
  hint, reducing burst retry load.

**Idempotency**: Retry safety is guaranteed by the existing `BillingEvent.event_id`
deduplication (DEC-P02-6). Retried webhooks for already-processed events return
`{"status": "already_processed"}` with HTTP 200.

---

## Implementation Details

### `_webhook_problem()` helper

A single internal helper (`routers/webhooks.py`) centralizes all webhook error
responses:
- Logs **exactly once** per failure (warning for 4xx, error for 5xx)
- Structured log extras: `provider`, `payload_hash`, `error_code`, `request_id`
- Returns RFC 9457 Problem Details with extensions: `provider`, `payload_hash`,
  `error_code`, `instance`
- Adds `Retry-After: 60` header on all 5xx responses
- Never includes raw header values, payload content, or exception messages
  (all sanitized via `sanitize_str`)

### Header validation

PayPal verification headers (`X-PAYPAL-TRANSMISSION-*`) changed from FastAPI's
`Header(...)` (required, returns 422 on missing) to `Optional[str] = Header(None)` with
explicit validation returning 400. This ensures missing headers return 400 (class C),
not FastAPI's default 422.

### Toss HMAC signature (optional)

TossPayments currently verifies via API re-query (DEC-P02-5). An optional HMAC layer
is available when `TOSS_WEBHOOK_SECRET` is configured:
- If env var is set: `X-TossPayments-Signature` is required; mismatch → 401
- If env var is unset: falls back to re-query only (backward compatible)

### Exception isolation

The critical fix for the PayPal bug: the signature mismatch check is **outside** the
`try` block that catches SDK errors. Specific exceptions (`ValueError`, `httpx.RequestError`,
`httpx.HTTPStatusError`) are caught individually with appropriate status codes. No
`except Exception: pass` exists in any webhook runtime path.

---

## Alternatives Considered

### Alt-1: Return 400 for all verification failures (rejected)

**Rejected**: 400 implies a permanently malformed request. An upstream timeout during
verification is not the client's fault. Using 400 here would cause providers to
permanently drop valid webhooks that failed due to our transient network issues.

### Alt-2: Return 403 for signature mismatch (rejected)

**Rejected**: 403 implies authentication succeeded but authorization failed. For webhook
signature verification, authentication is the exact issue (the signature is invalid).
401 is semantically correct. Additionally, some providers treat 403 as "do not retry"
while others may retry — 401 is unambiguous in the webhook context.

### Alt-3: Swallow misconfig errors silently and log only (rejected)

**Rejected**: Silent swallow of `WEBHOOK_PROVIDER_MISCONFIG` would cause all webhooks
to succeed without being processed. Billing events would be permanently lost. Fail-fast
with 500 ensures the problem surfaces immediately.

---

## Explicit Prohibitions

- **Never swallow provider misconfig**: If `PAYPAL_WEBHOOK_ID`, `PAYPAL_CLIENT_SECRET`,
  or `TOSS_SECRET_KEY` are missing, the handler MUST return 500 — not silently pass.
- **Never log raw payload content**: Log only `payload_hash`, `payload_size`, `provider`,
  `error_code`. Never log body content, headers, or exception messages unsanitized.
- **Never use `except Exception: pass`** in webhook runtime paths.

---

## Acceptance Criteria

- [x] RC-10.P5.7 gate: 5/5 PASSED
- [x] No `except Exception: pass` in webhook handlers
- [x] Signature mismatch → 401 in both PayPal and Toss paths
- [x] Network/timeout during verification → 500 with Retry-After: 60
- [x] All 5xx responses include `Retry-After: 60`
- [x] No raw payload / token / header values in any log line
- [x] `payload_hash` present in all webhook log events (WEBHOOK_RECEIVED, WEBHOOK_*_INVALID, etc.)
- [x] RFC 9457 Problem Details in all error responses with `provider`, `payload_hash`, `error_code` extensions

---

## References

- `dpp/apps/api/dpp_api/routers/webhooks.py` — P5.7 implementation
- `dpp/apps/api/tests/test_rc10_webhook_error_semantics.py` — RC-10.P5.7 test gate
- `dpp/docs/decisions/DEC-P02-5.md` — Webhook verification policy
- `dpp/docs/decisions/DEC-P02-6.md` — Idempotency policy
- `dpp/docs/decisions/DEC-P05-LOG-MASKING-WORM.md` — Log sanitization (P5.2)
- `dpp/tools/README_RC_GATES.md` — RC-10.P5.7 documentation
