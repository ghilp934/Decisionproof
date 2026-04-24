# SPEC LOCK: Public Contract v0.4.2.11 (MT0A-2 Aligned)

**Status**: LOCKED (Single Source of Truth) — Updated per MT0A-2 Pricing Skeleton Lock
**Effective Date**: 2026-04-24 (MT0A-2 patch); supersedes MT0A-1 Surface Sync Patch (v0.4.2.10)
**Project**: Decisionproof
**Purpose**: This document defines the **public API contract** aligned with MT0A-1 + MT0A-2 DEC decisions. All documentation (public/docs, pilot docs, llms.txt) and machine-readable specs (function-calling-specs.json, OpenAPI) MUST conform to this specification. Earlier spec values (`sk_{key_id}_{secret}`, `api.decisionproof.ai`, 45-day retention) are **superseded** by the MT0A-1/MT0A-2 DEC register.

**MT0A-1 supersede summary**:
- Auth token format: `sk_{key_id}_{secret}` → `dp_live_{secret}` (DEC-MT0A-01)
- API host: `api.decisionproof.ai` → `api.decisionproof.io.kr` (DEC-MT0A-03)
- Retention: 45-day run / 30-day S3 → Hot 30 days default (Sandbox), Cold/Deep by plan/contract (DEC-MT0A-05)
- Pricing unit: Decision Credits ban retained; Sandbox is time-boxed, limit-enforced, fail-closed (DEC-MT0A-04)
- `max_cost_usd` is always a **per-run spend cap** under `reservation.max_cost_usd` (DEC-MT0A-02)

**MT0A-2 supersede summary**:
- Public commercial skeleton locked: Sandbox (active) / Design Partner (limited paid pilot, application required) / Growth (Post-GA waitlist) / Enterprise (contract-only / Contact Sales) (DEC-MT0A-02-TIER-SKELETON).
- **Universal hard-stop commercial rule (every tier)**: no automatic postpaid overage; capacity exhaustion is hard-stop / fail-closed; capacity increases require explicit plan upgrade, prepaid top-up, reserve increase, signed order-form amendment, manual invoice settlement, or contract-defined Enterprise limit change (§7.6).
- **No public unlimited tier**. A runtime entitlement field rendering `0` for an Enterprise contract row means contract-defined / unset; it is not customer-facing unlimited (§7.6, §7.7).
- **Founder capacity doctrine**: max 3 signed Design Partners pre-v1.0; max 2 concurrent live onboardings (§7.7).
- Sandbox MT0A-1 commitments preserved unchanged (price US$29 / 30-day, six runtime-aligned hard limits, no overage billing, Hot 30-day retention, best-effort email support).
- Design Partner / Growth / Enterprise public numeric values: not published in MT0A-2 (CONTRACT_DEFINED / POST_GA).
- Cold Archive / Deep Archive commercial packaging: deferred (see `_DP_v1_0/mt0a_closeout/mt0a2_archive_packaging_gate.md`).

---

## 1. Authentication (LOCK — MT0A-1 aligned)

### 1.1 Header Format
- **Header Name**: `Authorization`
- **Scheme**: `Bearer`
- **Token Format**: `dp_live_{secret}`
  - Example placeholder: `dp_live_your_key_here`

### 1.2 Request Example
```http
POST /v1/runs HTTP/1.1
Host: api.decisionproof.io.kr
Authorization: Bearer dp_live_your_key_here
Idempotency-Key: unique-request-id-12345
Content-Type: application/json
```

### 1.3 FORBIDDEN (Documentation Ban)
- ❌ `X-API-Key` header (removed, not supported)
- ❌ Key format `dw_live_*` or `dw_test_*` (legacy, replaced)
- ❌ Key format `sk_live_*` or `sk_test_*` (environment prefix removed)
- ❌ Key format `dpp_live_*` (legacy customer-facing format; superseded by `dp_live_*` per DEC-MT0A-01)
- ❌ Key format `sk_{key_id}_{secret}` (legacy; superseded by `dp_live_*` per DEC-MT0A-01)

### 1.4 Error Responses
- **401 Unauthorized**: Missing or invalid `Authorization` header
- **403 Forbidden**: Valid token but insufficient permissions (tenant mismatch)

---

## 2. Runs API (LOCK)

### 2.1 POST /v1/runs - Create Run (Async)

#### Request
- **Method**: `POST /v1/runs`
- **Status**: `202 Accepted` (asynchronous operation)
- **Headers**:
  - `Authorization: Bearer dp_live_{secret}` (REQUIRED)
  - `Idempotency-Key: <unique-id>` (REQUIRED for duplicate prevention)
  - `Content-Type: application/json`

#### Request Body (RunCreateRequest)
```json
{
  "pack_type": "decision",
  "inputs": {
    "question": "Should we proceed with Plan A?",
    "context": {...}
  },
  "reservation": {
    "max_cost_usd": "0.0500",
    "timebox_sec": 90,
    "min_reliability_score": 0.8
  },
  "meta": {
    "trace_id": "optional-trace-id"
  }
}
```

**Schema Requirements**:
- `pack_type` (string, required): Pack type identifier
- `inputs` (object, required): Pack-specific inputs
- `reservation` (object, required):
  - `max_cost_usd` (string, required): **per-run spend cap** — the maximum USD amount reserved for a single run. It is not an account-level, workspace-level, monthly, or billing-cycle budget. 4 decimal places max (e.g., "0.0050").
  - `timebox_sec` (integer, optional): 1-90, default 90
  - `min_reliability_score` (float, optional): 0.0-1.0, default 0.8
- `meta` (object, optional):
  - `trace_id` (string, optional): Distributed tracing ID

#### Response (202 Accepted - RunReceipt)
```json
{
  "run_id": "run_abc123def456...",
  "status": "queued",
  "poll": {
    "href": "/v1/runs/run_abc123def456...",
    "recommended_interval_ms": 1500,
    "max_wait_sec": 90
  },
  "reservation": {
    "reserved_usd": "0.0500"
  },
  "meta": {
    "trace_id": "optional-trace-id",
    "profile_version": "v0.4.2.2"
  }
}
```

### 2.2 GET /v1/runs/{run_id} - Poll Run Status

#### Request
```http
GET /v1/runs/run_abc123def456... HTTP/1.1
Authorization: Bearer dp_live_your_key_here
```

#### Response (200 OK - RunStatusResponse)
```json
{
  "run_id": "run_abc123def456...",
  "status": "completed",
  "money_state": "settled",
  "cost": {
    "reserved_usd": "0.0500",
    "used_usd": "0.0120",
    "minimum_fee_usd": "0.0010",
    "budget_remaining_usd": "99.9880"
  },
  "result": {
    "presigned_url": "https://s3.amazonaws.com/...",
    "sha256": "abc123...",
    "expires_at": "2026-02-17T12:00:00Z"
  },
  "error": null,
  "meta": {
    "trace_id": "optional-trace-id",
    "profile_version": "v0.4.2.2"
  }
}
```

**Status Values** (Enum):
- `queued`: Submitted, awaiting worker
- `processing`: Worker executing
- `completed`: Success, result available
- `failed`: Error occurred
- `expired`: Exceeded retention period (410 Gone)

**Money State Values** (Enum):
- `reserved`: Budget locked, not yet settled
- `settled`: Final cost charged
- `refunded`: Partial refund issued (failure/cancellation)

### 2.3 GET /v1/tenants/{tenant_id}/usage - Usage Summary

#### Request
```http
GET /v1/tenants/tenant_abc123/usage HTTP/1.1
Authorization: Bearer dp_live_your_key_here
```

#### Response (200 OK)
```json
{
  "tenant_id": "tenant_abc123",
  "period": "2026-02",
  "total_spent_usd": "12.3456",
  "budget_limit_usd": "100.0000",
  "budget_remaining_usd": "87.6544",
  "runs": {
    "total": 150,
    "completed": 145,
    "failed": 5
  }
}
```

### 2.4 FORBIDDEN (Documentation Ban)
- ❌ `workspace_id` in request body (removed)
- ❌ `plan_id` in request body (removed, internal only)
- ❌ `run_id` in request body (server-generated, not client-provided)
- ❌ Synchronous 200 OK response (must be 202 + polling)

---

## 3. Idempotency (LOCK)

### 3.1 Idempotency-Key Header
- **Header**: `Idempotency-Key: <unique-string>`
- **Scope**: Per-tenant, per-key
- **TTL**: 7 days (duplicate detection window)
- **Behavior**:
  - Same `Idempotency-Key` within 7 days → 202 with existing `run_id` (no charge)
  - Same key after 7 days → treated as new request (billable)

### 3.2 Example
```bash
# First request
curl -X POST https://api.decisionproof.io.kr/v1/runs \
  -H "Authorization: Bearer dp_live_your_key_here" \
  -H "Idempotency-Key: request-20260217-001" \
  -d '{"pack_type": "decision", ...}'

# Response: 202 Accepted, run_id: run_abc123

# Duplicate request (within 7 days)
curl -X POST https://api.decisionproof.io.kr/v1/runs \
  -H "Authorization: Bearer dp_live_your_key_here" \
  -H "Idempotency-Key: request-20260217-001" \
  -d '{"pack_type": "decision", ...}'

# Response: 202 Accepted, run_id: run_abc123 (same), deduplication_status: "duplicate"
```

### 3.3 FORBIDDEN (Documentation Ban)
- ❌ `(workspace_id, run_id)` as idempotency scope (replaced by `Idempotency-Key`)

---

## 4. Error Responses (RFC 9457 Problem Details) (LOCK)

### 4.1 Standard Format
- **Media Type**: `application/problem+json`
- **Required Fields**:
  - `type` (string): URI reference for error type
  - `title` (string): Human-readable summary
  - `status` (integer): HTTP status code
  - `detail` (string): Detailed explanation
  - `instance` (string): URI reference for this occurrence

### 4.2 Extension Fields
- `reason_code` (string): Machine-readable error code (enum)
- `trace_id` (string): Distributed tracing ID

### 4.3 Example
```json
{
  "type": "https://docs.decisionproof.io.kr/errors/budget-exceeded",
  "title": "Budget Exceeded",
  "status": 402,
  "detail": "Requested max_cost_usd (0.0500) exceeds remaining budget (0.0200)",
  "instance": "/v1/runs",
  "reason_code": "BUDGET_EXCEEDED",
  "trace_id": "abc123-def456-789"
}
```

### 4.4 Common Error Codes
| Status | Reason Code | Description |
|--------|-------------|-------------|
| 401 | `AUTH_MISSING` | Missing `Authorization` header |
| 401 | `AUTH_INVALID` | Invalid token format or expired |
| 402 | `BUDGET_EXCEEDED` | Insufficient budget |
| 403 | `TENANT_MISMATCH` | Token does not own requested resource |
| 404 | `RUN_NOT_FOUND` | Run ID not found (or tenant mismatch) |
| 410 | `RUN_EXPIRED` | Run exceeded retention period |
| 422 | `INVALID_MONEY_SCALE` | `max_cost_usd` has >4 decimal places |
| 422 | `INVALID_PACK_TYPE` | Unknown `pack_type` |
| 429 | `RATE_LIMIT_EXCEEDED` | Too many requests |

---

## 5. Rate Limiting (LOCK)

### 5.1 Rate Limit Headers
**Response Headers** (all requests):
- `RateLimit-Limit: 100` - Total requests allowed in window
- `RateLimit-Remaining: 87` - Requests remaining
- `RateLimit-Reset: 1708156800` - Unix timestamp of next window reset

### 5.2 429 Too Many Requests
**Headers**:
- `Retry-After: 60` - Seconds until retry allowed
- `RateLimit-Limit: 100`
- `RateLimit-Remaining: 0`
- `RateLimit-Reset: 1708156800`

**Body** (RFC 9457):
```json
{
  "type": "https://docs.decisionproof.io.kr/errors/rate-limit-exceeded",
  "title": "Rate Limit Exceeded",
  "status": 429,
  "detail": "Request rate limit exceeded (100 req/min). Retry after 60 seconds.",
  "instance": "/v1/runs",
  "reason_code": "RATE_LIMIT_EXCEEDED",
  "trace_id": "abc123-def456-789"
}
```

### 5.3 Client Handling
```python
response = requests.post(url, headers=headers, json=body)

if response.status_code == 429:
    retry_after = int(response.headers.get("Retry-After", 60))
    time.sleep(retry_after)
    # Retry request
```

---

## 6. Tracing & Observability (LOCK)

### 6.1 Trace Context (W3C Trace Context)
**Request Header** (optional):
```http
traceparent: 00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01
```

**Response Header** (always present):
```http
X-Request-ID: req_abc123def456789
```

### 6.2 Trace Propagation
- Client sends `traceparent` → API passes to Worker → Worker passes to Reaper
- `trace_id` in `meta` field (request/response) links all operations
- `X-Request-ID` identifies specific API request (independent of `trace_id`)

### 6.3 Logging & Privacy
**Forbidden** (must NOT be logged):
- API keys / tokens (partial masking only: `sk_abc***`)
- Input data (PII risk)
- Result data (PII risk)

**Allowed**:
- `run_id`, `tenant_id`, `trace_id`, `request_id`
- `pack_type`, `status`, `money_state`
- Cost/budget values (USD amounts)
- Error codes and non-PII error details

---

## 7. Metering & Billing (LOCK)

### 7.1 Unit of Charge
- **Currency**: USD (United States Dollar)
- **Precision**: 4 decimal places (e.g., `"0.0050"` = half a cent)
- **Internal Storage**: USD micros (BIGINT, 1,000,000 micros = $1.00)

### 7.2 Charge Conditions
**Billable** (charges applied):
- `status: completed` - Successful execution
- `status: failed` with business logic errors (pack execution failure)

**Non-Billable** (no charge):
- `status: failed` with auth/validation errors (401, 403, 422)
- Duplicate requests (same `Idempotency-Key` within 7 days)

### 7.3 Cost Breakdown
```json
{
  "cost": {
    "reserved_usd": "0.0500",      // Max budget locked at submission
    "used_usd": "0.0120",           // Actual cost (execution + minimum fee)
    "minimum_fee_usd": "0.0010",    // Base fee per run
    "budget_remaining_usd": "99.9880"  // Tenant budget after settlement
  }
}
```

### 7.4 FORBIDDEN (Documentation Ban)
- ❌ "Decision Credits" (DC) terminology in customer-facing docs
- ❌ Credit/point-based pricing (use USD only)
- ❌ "Monthly DC quota" (use USD budget)
- ❌ "Unlimited API usage", "unlimited runs", "unlimited requests", "unmetered Sandbox" (Sandbox is time-boxed, limit-enforced, fail-closed per DEC-MT0A-04)
- ❌ Invented replacement quotas such as "1,000 runs included" or "$X monthly budget" unless a pricing DEC (MT0A-2) confirms the number from runtime entitlement SSOT and legal/commercial review.

### 7.5 Sandbox Metering Model (MT0A-1, runtime-aligned numerics)

Sandbox is a time-boxed, limit-enforced paid beta access path. The following **hard numerics** are enforced by the runtime (`dpp/apps/api/dpp_api/pricing/fixtures/pricing_ssot.json`, Sandbox paid private beta entitlement) **before** any AI inference cost is incurred, and are published as a customer-facing contract:

| Limit | Value | Runtime policy | Breach behaviour |
|---|---|---|---|
| Access window | 30 days per payment; manual renewal only | time-boxed entitlement | entitlement expiry → 402 `entitlement_inactive` |
| Per-run spend cap | US$5.00 (`reservation.max_cost_usd`) | `max_cost_usd_micros` pack limit | 422 `max_cost_too_high` (non-billable) |
| Workspace rate limit | 60 requests per minute (sliding window) | `rate_limit_rpm: 60` + Redis sliding window | 429 `quota-exceeded` + `Retry-After` |
| Monthly metered-operation cap | up to 2,000 operations per 30-day cycle | `monthly_quota_dc: 2000`, `hard_overage_dc_cap` absorbed into overall cap; `overage_behavior: block_on_breach` | 429 `quota-exceeded` (non-billable) |
| Per-run execution timeout | 30 seconds | `max_execution_seconds: 30` | run terminated; reaper reconciles |
| Per-run input token limit | 16,000 tokens | `max_input_tokens: 16000` | 422 |
| Per-run output token limit | 4,000 tokens | `max_output_tokens: 4000` | 422 / run terminated mid-execution |
| API keys per workspace | up to 3 concurrent `dp_live_{secret}` keys | token manager | 403 / refuse issuance |
| Overage billing | **none** — any breach yields HTTP 429, never a charge | `overage_behavior: block_on_breach` + `hard_spending_limit` | N/A |

**Fail-closed guarantee**: the US$29 / 30-day fee is the only amount Decisionproof charges for Sandbox access. Any request that would exceed any of the limits above is **rejected pre-cost** and does not consume LLM inference credits against the Sandbox user.

**Tier scope**: the Sandbox plan is not the B2B Design Partner offer. Design Partner engagements are contracted separately with their own limits defined in the signed pilot agreement. Growth / Enterprise tier shape is locked in §7.7 (MT0A-2); customer-facing numeric values for those tiers are not published in MT0A-2.

### 7.6 Universal hard-stop commercial rule (MT0A-2)

This rule applies to **every** Decisionproof tier — Sandbox, Design Partner, Growth, Enterprise — and is binding for all customer-facing copy and runtime behaviour.

1. **No automatic postpaid overage in any tier.** "Overage" means allowance exhaustion, not later billing. When a prepaid, invoiced, contract-defined, or reserved allowance is exhausted, the default behaviour is **hard-stop / fail-closed**.
2. **Allowed additional-capacity mechanisms**: explicit plan upgrade, prepaid top-up, reserve increase, signed order-form amendment, manual invoice settlement before capacity increase, contract-defined Enterprise limit change.
3. **Forbidden mechanisms** (must not appear in any active customer-facing copy or runtime promise):
   - automatic postpaid pay-as-you-go overage,
   - silent continuation followed by later true-up,
   - unlimited use with later reconciliation,
   - "included usage + automatic overage invoice",
   - "usage above included quota is billed automatically".
4. **Runtime semantics**:
   - HTTP 429 is the default response for rate / quota exhaustion (with `RateLimit-Remaining` / `Retry-After` headers per §5).
   - HTTP 402 may be used **only** for a documented Decisionproof entitlement-inactive / unfunded gate (per §7 of pricing_ssot.json `payment_required_http_status_optional`).
   - No request may continue into provider / LLM work after a known hard cap is exhausted.
5. **Runtime `0` semantics**: a runtime entitlement field rendering `0` for a contract row (e.g. ENTERPRISE) means **contract-defined / unset / not applicable**. It must never be rendered as customer-facing unlimited. Currently the runtime `is_zero_unlimited()` helper (`pricing/models.py:156`) treats `0` as "custom_or_unlimited" for `included_dc_per_month`, `monthly_quota_dc`, `rate_limit_rpm`, `hard_overage_dc_cap`; this internal semantic must not be exposed to customer-facing surfaces, and the runtime semantic itself is to be tightened in Phase 1 (see `_DP_v1_0/mt0a_closeout/mt0a2_runtime_alignment_plan.md`).
6. **ENTERPRISE `overage_behavior` drift**: `pricing_ssot.json` ENTERPRISE row currently sets `overage_behavior: "notify_only"`. Founder-locked doctrine is `block_on_breach` for every tier. Customer-facing copy must reflect `block_on_breach` semantics regardless of the SSOT row; runtime alignment is tracked as `OPEN-RUNTIME-ENTERPRISE-OVERAGE-BEHAVIOR` in Phase 1.

### 7.7 Tier Skeleton (MT0A-2)

The four-tier public skeleton, locked by founder decision and Red Team-audited:

| Tier | Public activation state | Public CTA (allowed) | Forbidden CTA |
|---|---|---|---|
| Sandbox | Active paid private beta | Join Sandbox beta / Start Sandbox / Get Sandbox access | Unlimited / Scale instantly / Enterprise-ready / Auto-upgrade |
| Design Partner | Limited availability — **max 3 signed pre-v1.0; max 2 concurrent live onboardings** | Apply for Design Partner / Request review / Discuss pilot fit | Start Now / Buy Now / Activate instantly / Checkout |
| Growth | Post-GA / Waitlist (no self-serve in MT0A-2) | Join waitlist / Get notified / Request Growth access | Start Now / Upgrade now / Checkout / Self-serve |
| Enterprise | Contract-only / Contact Sales (no self-serve) | Contact Sales / Request legal & security review / Discuss Enterprise needs | Start Now / Buy Now / Unlimited / Self-serve |

**Public numeric publication rule**:
- Sandbox numerics are MT0A-1-locked and re-stated in §7.5.
- Design Partner / Growth / Enterprise numerics are **not** published as customer-facing commitments in MT0A-2. Internal runtime `pricing_ssot.json` STARTER / GROWTH / ENTERPRISE rows are reference values only; their public release is gated by signed Order Form (Design Partner), DEC + GA readiness (Growth), and signed Master Service Agreement (Enterprise).
- "CONTRACT_DEFINED" wording must be used in customer-facing copy where a tier has not yet published numerics. The literal token "Unlimited" (case-insensitive) is forbidden across every tier.

**Capacity doctrine (founder)**:
- Maximum signed Design Partners before v1.0: **3 total**.
- Maximum concurrent live Design Partner onboardings: **2**.
- Public copy must never imply broad or immediate Design Partner availability.

**Billing path by tier**:
- Sandbox: PayPal during paid private beta; manual renewal only; no auto-renewal.
- Design Partner: manual invoice + IBK bank remittance + applicable tax invoice (Billing Path A per DEC-P02-BILLING MT0A-1 supplement); payment in advance unless Order Form states otherwise.
- Growth / Enterprise: not in MT0A-2 scope.

**Support entitlement by tier**:
- Sandbox: best-effort email; target first response within 1 business day; no 24/7; no phone; no uptime SLA.
- Design Partner: Sev1 4h / Sev2 8h / Sev3 1 BD / Sev4 2 BD under signed pilot agreement; availability target binds only if expressly included in signed agreement.
- Growth / Enterprise: not publicly committed in MT0A-2; muted wording only.

**Forbidden public commitments across all tiers** (P0):
- Decision Credits / DC customer-facing wording (DEC-MT0A-04 — retained).
- "No TTL" / "unlimited retention" / "unlimited runs" / "unlimited Sandbox" / "unmetered Sandbox" / any "Unlimited" tier (DEC-MT0A-04 / MT0A-2).
- "automatic overage" / "automatically billed" / "pay as you go" / "true-up" / "later true-up" (MT0A-2 §7.6).
- 24/7 support as default; phone support as default; named TAM unless contract-specified; SSO/audit "available" without runtime-ready evidence.

---

## 8. Retention & Data Lifecycle (LOCK — MT0A-1 aligned)

### 8.1 Customer-facing Retention Tiers (MT0A-1)

| Tier | Public Meaning | MT0A-1 Surface Language |
|---|---|---|
| Hot | Online operational/audit data available for immediate export | Hot online access for 30 days |
| Cold | Archived audit records retrievable after the hot window | Cold archive up to 1 year, available only where included in the plan or contract |
| Deep Archive | Long-term retention for regulated/high-retention customers | Paid add-on / contract-specific option |

### 8.2 Sandbox Retention (MT0A-1)

- Sandbox includes Hot online access for 30 days by default.
- Sandbox data does **not** automatically move to Cold Archive.
- After the Hot window, Sandbox records and result artifacts are **not guaranteed** to remain online or retrievable and may be queued for deletion/purge under the applicable data handling policy, unless a separate plan, contract, legal hold, or security/audit obligation requires otherwise.
- Cold Archive and Deep Archive are available only where included in the customer's plan or contract.

### 8.3 Run API Retention Behavior
- **After the applicable retention/purge window**:
  - `GET /v1/runs/{run_id}` returns `410 Gone`
  - Result artifact is subject to the storage-tier lifecycle corresponding to the customer's active plan or contract

### 8.4 Superseded (legacy)
The previous "45-day run retention / 30-day S3 lifecycle" baseline is superseded by the tiering above per DEC-MT0A-05. Internal lifecycle defaults may continue to use shorter windows for Sandbox; the customer-facing promise is Hot 30 days only, with no implicit Cold Archive for Sandbox.

### 8.2 410 Gone Response
```json
{
  "type": "https://docs.decisionproof.io.kr/errors/run-expired",
  "title": "Run Expired",
  "status": 410,
  "detail": "Run run_abc123def456 exceeded the applicable retention window",
  "instance": "/v1/runs/run_abc123def456",
  "reason_code": "RUN_EXPIRED",
  "trace_id": "abc123-def456-789"
}
```

---

## 9. OpenAPI / Machine-Readable Specs (LOCK)

### 9.1 Security Scheme (openapi.json)
```json
{
  "components": {
    "securitySchemes": {
      "BearerAuth": {
        "type": "http",
        "scheme": "bearer",
        "bearerFormat": "dp_live_{secret}",
        "description": "Bearer token authentication. Format: dp_live_{secret} (example placeholder: dp_live_your_key_here)"
      }
    }
  }
}
```

**FORBIDDEN**:
- ❌ `sk_{environment}_{key_id}_{secret}` (no environment prefix)
- ❌ `X-API-Key` security scheme
- ❌ `sk_{key_id}_{secret}` (superseded by `dp_live_*` per DEC-MT0A-01)
- ❌ `dpp_live_*` (superseded by `dp_live_*` per DEC-MT0A-01)

### 9.2 Function Calling Specs (/docs/function-calling-specs.json)
- **Schema Source**: `RunCreateRequest` Pydantic model (apps/api/dpp_api/schemas.py)
- **Generation**: Auto-generated from model (no hardcoded schema)
- **Required Fields**: `pack_type`, `inputs`, `reservation`
- **Forbidden Fields**: `workspace_id`, `plan_id` (must NOT appear in schema)

---

## 10. Compliance & Change Control (LOCK)

### 10.1 Contract Change Process
1. Update this `SPEC_LOCK_PUBLIC_CONTRACT.md`
2. Update all documentation to match (public/docs, pilot, llms.txt)
3. Update machine-readable specs (function-calling-specs.json, OpenAPI)
4. Run regression tests (`test_docs_spec_lock.py`)
5. Increment version number (e.g., v0.4.2.2 → v0.4.3.0)

### 10.2 Forbidden Drift Detection
- **CI/CD Gate**: Automated test scans for forbidden tokens
- **Tokens (auth / domain / legacy)**: `X-API-Key`, `dw_live_`, `dw_test_`, `sk_live_`, `sk_test_`, `sk_{key_id}_{secret}`, `dpp_live_`, `workspace_id`, `plan_id`, `api.decisionproof.ai`
- **Tokens (pricing / metering — DEC-MT0A-04 / MT0A-2)**: `Decision Credits`, `decision credits`, `monthly DC`, `credit quota`, `included credits`, `No TTL`, `unlimited retention`, `unlimited runs`, `unlimited Sandbox`, `unmetered Sandbox`
- **Tokens (overage / unlimited — MT0A-2 §7.6)**: `automatic overage`, `automatically billed`, `pay as you go`, `pay-as-you-go`, `true-up`, `later true-up`, `Unlimited` (case-insensitive in active customer copy), `Enterprise unlimited`
- **Tokens (false-surface — MT0A-2 §7.7)**: `Start Now` / `Buy Now` / `Activate instantly` / `Checkout` / `Self-serve` on Design Partner, Growth, Enterprise contexts; `Growth available now`, `Design Partner instant activation`
- **Scope**: `public/`, `docs/pilot/`, `public/llms.txt`, `public/llms-full.txt`, `apps/api/dpp_api/main.py`, `postman/`

### 10.3 Version History
| Version | Date | Changes |
|---------|------|---------|
| v0.4.2.2 | 2026-02-17 | Initial SPEC LOCK: Bearer auth, async runs, RFC 9457, rate limits, no DC terminology |
| v0.4.2.10 (MT0A-1) | 2026-04-24 | MT0A-1 Surface Sync Patch: auth token → `dp_live_{secret}`; API host → `api.decisionproof.io.kr`; retention → Hot 30 default + Cold/Deep by plan/contract; Sandbox limit-enforced (no unlimited, no invented quota); `reservation.max_cost_usd` per-run qualifier locked. |
| v0.4.2.11 (MT0A-2) | 2026-04-24 | MT0A-2 Pricing Skeleton Lock: four-tier skeleton (Sandbox active / Design Partner limited application / Growth Post-GA waitlist / Enterprise contract-only); universal hard-stop commercial rule (§7.6); founder capacity doctrine max 3 DP / max 2 concurrent (§7.7); runtime `0` ≠ unlimited; ENTERPRISE `overage_behavior` runtime drift logged for Phase 1; Cold/Deep Archive packaging deferred. No customer-facing commercial values published for Design Partner / Growth / Enterprise. |

---

**END OF SPEC LOCK**

This document is the **single source of truth** for public API contracts. All deviations must be approved and documented here first.
