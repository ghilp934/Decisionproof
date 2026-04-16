# Decisionproof v1.0 — Phase 1 SOW: Payment Front Door

**Document ID**: DP-V1-P1-SOW  
**Version**: 0.2-draft  
**Status**: FINAL SPEC LOCK — approved, Phase 2 착수 가능  
**Date**: 2026-04-16  
**Scope**: Payment front door spec lock — checkout session, PayPal order/capture surface,
billing status endpoints, Toss runtime isolation contract  
**Author**: Engineering (co-drafted with Claude Sonnet 4.6)

> **v0.2 변경 요약 (2026-04-16)**:
> 1. Phase 1 / Phase 2 경계 재잠금 — Phase 1은 Spec Lock 산출물 전용
> 2. Appendix A 파일 목록 개수 정정 및 Phase 구분 재분류
> 3. OI-07 잠금 — return_url 기본 도메인 = `decisionproof.io.kr`
> 4. OI-04 / OI-05 기본값 잠금 (세션 TTL 30분, 엔티틀먼트 유효 30일)
> 5. OI-01 구체화 — beta plan ID 후보 명시

---

## Table of Contents

1. [Scope](#1-scope)
2. [Objectives](#2-objectives)
3. [Locked Decisions](#3-locked-decisions)
4. [Existing Repo Baseline Relevant to Phase 1](#4-existing-repo-baseline-relevant-to-phase-1)
5. [New Domain Model](#5-new-domain-model)
6. [Endpoint Contracts](#6-endpoint-contracts)
7. [State Machine and Transition Table](#7-state-machine-and-transition-table)
8. [Concurrency and Race-Condition Rules](#8-concurrency-and-race-condition-rules)
9. [Idempotency Rules](#9-idempotency-rules)
10. [Error Policy](#10-error-policy)
11. [Security Requirements](#11-security-requirements)
12. [Acceptance Criteria](#12-acceptance-criteria)
13. [Test Plan](#13-test-plan)
14. [Non-Goals](#14-non-goals)
15. [Open Issues and Assumptions](#15-open-issues-and-assumptions)
16. [Phase 2 Handoff Notes](#16-phase-2-handoff-notes)

---

## 1. Scope

Phase 1 is the **spec lock phase** for the payment front door.

The Decisionproof backend already has:
- Auth / signup / login (Supabase JWT)
- Auto personal-tenant creation on signup
- Token lifecycle endpoints
- Run / usage / demo endpoints
- PayPal and Toss webhook-backed entitlement backend

What is **not** present as a first-class public app surface is the payment initiation path:
a customer cannot currently start a payment, create a PayPal order, submit a capture, or see
their billing/onboarding status through a structured, auth-bound, idempotency-safe API.

### Phase 1 / Phase 2 Boundary (LOCKED)

**Phase 1 산출물** (이 문서의 범위):
- Spec Lock / SOW / contract / state machine / DB change spec
- Acceptance criteria / test matrix
- DB migration SQL (schema definition only, not applied to live environment)
- Model class additions / schema changes (scaffolding only, no live router wiring)
- Test skeleton files (test stubs — not required to pass yet)

**Phase 1에서 하지 않는 것** (Phase 2 착수 예정):
- Full endpoint implementation with live business logic
- Broad router wiring into `main.py`
- `repo_checkout.py` complete implementation
- PayPal client `PayPal-Request-Id` mandatory 변경 적용
- Webhook handler `PAID_VERIFIED` 전환 적용
- Integration test 실행 (환경 의존)

This document locks:
- The `checkout_sessions` domain object
- Five new API endpoint contracts
- The canonical payment state machine
- DB schema changes to existing tables
- Idempotency, concurrency, and error contracts
- Toss isolation scope
- Acceptance criteria and test plan

**Phase 1 does NOT begin broad implementation.** Code changes are limited to migration SQL,
model class additions, and test skeleton stubs.

---

## 2. Objectives

| # | Objective |
|---|-----------|
| O-1 | Define and lock the `checkout_sessions` domain object and its lifecycle |
| O-2 | Define and lock five new payment front door endpoints with complete contracts |
| O-3 | Lock the canonical payment state machine with no ambiguous transitions |
| O-4 | Ensure no path from synchronous capture to `Entitlement = ACTIVE` exists |
| O-5 | Ensure no guest (unauthenticated) checkout path exists by design |
| O-6 | Define Toss runtime isolation scope so Phase 4 has a clear checklist |
| O-7 | Produce a test matrix that can be executed before Phase 2 merge |

---

## 3. Locked Decisions

The following decisions are **frozen**. They must not be reopened during Phase 1 implementation
or Phase 2 scoping without an explicit written decision record.

| ID | Decision | Rationale |
|----|----------|-----------|
| DEC-V1-01 | External public label: "Decisionproof v1.0" | Branding |
| DEC-V1-02 | Operational reality: "paid private beta" — not public GA | Scale / risk |
| DEC-V1-03 | Payment source for v1.0: **PayPal only** | Toss excluded from launch-critical path |
| DEC-V1-04 | Payment model: **prepaid one-time**, not subscription-first | Simplicity |
| DEC-V1-05 | **Guest checkout is forbidden.** Checkout session creation requires authenticated user with resolved tenant | Orphan payment prevention |
| DEC-V1-06 | Every payment path must go through a server-created `checkout_session` | Anti-replay, binding |
| DEC-V1-07 | `Entitlement = ACTIVE` may **only** be entered from `PAID_VERIFIED` | Webhook-only activation |
| DEC-V1-08 | Synchronous capture success must **never** activate entitlement | DEC-V1-07 enforcement |
| DEC-V1-09 | `return_url` / `cancel_url` are **informational only** — not authority for entitlement | Redirect safety |
| DEC-V1-10 | K8s / EKS version upgrade is **frozen** until first paid usage is proven | Operational risk |
| DEC-V1-11 | Mini demo marketplace is demo/funnel only — not core paid access path | UX clarity |
| DEC-V1-12 | `decisionproof.io.kr` = paid beta landing / pricing / checkout / onboarding / docs | Surface split |
| DEC-V1-13 | `api.decisionproof.io.kr` = API / mini demo surface | Surface split |
| DEC-V1-14 | `PayPal-Request-Id` is **mandatory** on create-order and capture — not optional | Double-charge prevention |
| DEC-V1-15 | `paypal_request_id_create` and `paypal_request_id_capture` are generated at session creation and stored — **never regenerated** on retry | Idempotency safety |
| DEC-V1-16 | Toss must be removed from startup preflight, readyz health gate, K8s secrets, CI, ops alerts, and runbooks before v1.0 launch | Runtime isolation |

---

## 4. Existing Repo Baseline Relevant to Phase 1

### 4.1 Auth / Signup / Login (REUSE — no change)

**Files**: `apps/api/dpp_api/routers/auth.py`, `apps/api/dpp_api/auth/session_auth.py`

- `POST /v1/auth/signup`: creates Supabase user + auto-creates personal tenant
  (`tenant_id = "user_{user_id[:8]}"`) + `UserTenant` mapping (role=owner).
- `POST /v1/auth/login`: returns Supabase JWT `access_token`.
- `GET /v1/auth/confirmed`: email confirmation landing (HTML).
- Session auth: `SessionAuthContext(user_id, tenant_id, role)` resolved from Supabase JWT via
  `user_tenants` table lookup.

**Phase 1 dependency**: Checkout session creation requires `SessionAuthContext` (authenticated
user with resolved tenant). The existing session auth dependency chain is reused directly.

### 4.2 Tenant Resolution (REUSE — no change)

**Files**: `apps/api/dpp_api/db/models.py` (`UserTenant`), `apps/api/dpp_api/auth/session_auth.py`

- `user_tenants` table maps `user_id` (Supabase UUID) → `tenant_id` (app string).
- Only `status = 'active'` mappings are valid.
- Tenant resolution happens inside `get_session_auth_context()` dependency.

### 4.3 Token Lifecycle (REUSE — no change)

**Files**: `apps/api/dpp_api/routers/tokens.py`, `apps/api/dpp_api/auth/token_lifecycle.py`

- `POST /v1/tokens`: create opaque Bearer token (display-once)
- Token creation requires active entitlement (plan enforcer check)
- Token quota: max 5 per tenant

**Phase 1 dependency**: After `Entitlement = ACTIVE`, user navigates to token issuance via
existing path. No changes needed here.

### 4.4 Webhook Handlers / Entitlement Backend (PARTIAL CHANGE)

**File**: `apps/api/dpp_api/routers/webhooks.py`

- `POST /webhooks/paypal`: handles `PAYMENT.CAPTURE.COMPLETED`, `PAYMENT.CAPTURE.REFUNDED`,
  `CUSTOMER.DISPUTE.*`, `PAYMENT.CAPTURE.DENIED`.
- `_grant_entitlement(db, billing_order)`: sets `Entitlement.status = 'ACTIVE'`.
- `_revoke_entitlement(db, billing_order)`: sets `Entitlement.status = 'FREE'`.

**Current gap**: On `PAYMENT.CAPTURE.COMPLETED`, handler sets `billing_order.status = "PAID"`.
Phase 1 changes this to `"PAID_VERIFIED"` and adds `checkout_session.status = 'PAID_VERIFIED'`
update.

**Red team note**: The existing `_grant_entitlement` function is called only from webhook
handlers. This is correct. Phase 1 must not introduce any synchronous path that calls
`_grant_entitlement` directly.

### 4.5 Billing Models — Current State vs Required State

**File**: `apps/api/dpp_api/db/models.py`

| Model | Current status values | Required additions |
|-------|----------------------|--------------------|
| `BillingOrder` | `PENDING, PAID, FAILED, REFUNDED, CANCELLED, PARTIAL_REFUNDED` | `CAPTURE_SUBMITTED`, `PAID_VERIFIED` |
| `Entitlement` | `FREE, ACTIVE, SUSPENDED` | No change |
| `checkout_sessions` | **Does not exist** | Full new table |
| `checkout_session_events` | **Does not exist** | Full new table |

**Current `billing_orders` schema gap**: No `checkout_session_id` FK column.

### 4.6 Toss Runtime Dependencies (FLAGGED — Phase 4 isolation)

The following Toss dependencies exist in the current codebase and must be fully removed or
isolated before v1.0 launch (Phase 4 scope). They are documented here as a red-team surface:

| Location | Dependency | Risk if not isolated |
|----------|-----------|---------------------|
| `billing/active_preflight.py` | `_check_toss()` called in `run_billing_secrets_active_preflight()` | Startup failure / readyz degraded if Toss missing |
| `billing/active_preflight.py` | `result["toss"]` included in preflight result dict | `readyz` returns `billing_secrets: down` if Toss absent |
| `routers/webhooks.py:32` | `from dpp_api.billing.toss import get_toss_client` | Import-time load; toss.py instantiation at webhook call |
| `routers/health.py` | `get_billing_preflight_status()` includes Toss result | readyz reflects Toss status |
| `k8s/api-deployment.yaml:147,152` | `TOSS_SECRET_KEY`, `TOSS_WEBHOOK_SECRET` env var refs | Pod fails to start if secret absent |
| `k8s/base/api-deployment.yaml:147,152` | Same | Same |
| `k8s/overlays/pilot/create_pilot_secret.sh:46` | Toss placeholder in secret creation script | Ops confusion |
| `k8s/overlays/pilot/pilot_secret_values.env:32-33` | `TOSS_SECRET_KEY="placeholder"` | Potential silent preflight pass with placeholder |
| `k8s/secretproviderclass-dpp-secrets.yaml` | `toss-secret-key`, `toss-webhook-secret` in SecretProviderClass jmesPath | ASM secret must exist even if empty |
| `billing/webhook_dedup.py` | `get_toss_dedup_key()`, `try_acquire_dedup(db, "toss", ...)` | Not a startup risk, but operational confusion |

**Phase 1 action on Toss**: Document only. No code changes until Phase 4.
**Phase 4 mandatory deliverable**: All items in the above table must be resolved before
`DPP_BILLING_PREFLIGHT_REQUIRED=1` is safe for a PayPal-only runtime.

### 4.7 Plans Table (DEPENDENCY — data setup required)

**File**: `apps/api/dpp_api/db/models.py` (`Plan`), `apps/api/dpp_api/db/repo_plans.py`

- `plans` table exists in schema with `plan_id`, `name`, `status`, `features_json`, `limits_json`.
- Checkout session creation requires a valid `plan_id` with `status = 'ACTIVE'`.
- **Open issue OI-01**: Confirm which `plan_id` values exist in production/staging DB.
  If not seeded, a seed migration is required before Phase 2 can run E2E.

---

## 5. New Domain Model

### 5.1 `checkout_sessions` Table

```sql
CREATE TABLE IF NOT EXISTS checkout_sessions (
    -- Primary key: opaque UUID v4, safe to expose externally
    id                          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),

    -- Identity binding: both user_id AND tenant_id required (DEC-V1-05, DEC-V1-06)
    user_id                     UUID        NOT NULL,   -- Supabase auth.users.id
    tenant_id                   TEXT        NOT NULL REFERENCES tenants(tenant_id),

    -- Purchase intent: locked at session creation, immutable thereafter
    plan_id                     TEXT        NOT NULL REFERENCES plans(plan_id),
    amount_usd_cents            BIGINT      NOT NULL,   -- e.g. 2900 for $29.00
    currency                    TEXT        NOT NULL DEFAULT 'USD'
                                            CHECK (currency IN ('USD')),
    -- v1.0 only supports USD. KRW is reserved for post-v1.0 Toss integration.

    -- State machine (see Section 7)
    status                      TEXT        NOT NULL DEFAULT 'DRAFT'
                                            CHECK (status IN (
                                                'DRAFT',
                                                'CHECKOUT_SESSION_CREATED',
                                                'PAYPAL_ORDER_CREATED',
                                                'APPROVED',
                                                'CAPTURE_SUBMITTED',
                                                'PAID_VERIFIED',
                                                'CANCELED',
                                                'EXPIRED',
                                                'FAILED'
                                            )),

    -- PayPal-Request-Id: generated at session creation, stored immutably (DEC-V1-14, DEC-V1-15)
    paypal_request_id_create    TEXT        NOT NULL UNIQUE,  -- UUID for create-order
    paypal_request_id_capture   TEXT        NOT NULL UNIQUE,  -- UUID for capture

    -- PayPal order binding: null until create-order succeeds
    paypal_order_id             TEXT        UNIQUE,     -- nullable until PAYPAL_ORDER_CREATED

    -- Anti-replay nonce: 32-byte hex, generated at session creation
    nonce                       TEXT        NOT NULL,

    -- Expiry: sessions expire 30 minutes after creation (configurable via env)
    expires_at                  TIMESTAMPTZ NOT NULL,

    -- Failure tracking
    failed_reason               TEXT,                   -- populated only when status = 'FAILED'

    -- Audit
    created_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Indexes
CREATE INDEX idx_cs_tenant       ON checkout_sessions(tenant_id);
CREATE INDEX idx_cs_user         ON checkout_sessions(user_id);
CREATE INDEX idx_cs_status       ON checkout_sessions(status);
CREATE INDEX idx_cs_expires      ON checkout_sessions(expires_at)
    WHERE status NOT IN ('PAID_VERIFIED', 'CANCELED', 'EXPIRED', 'FAILED');
CREATE INDEX idx_cs_paypal_order ON checkout_sessions(paypal_order_id)
    WHERE paypal_order_id IS NOT NULL;

-- Updated_at trigger (reuse existing trigger function pattern)
CREATE TRIGGER trigger_cs_updated_at
    BEFORE UPDATE ON checkout_sessions
    FOR EACH ROW EXECUTE FUNCTION update_billing_orders_updated_at();

-- RLS: tenants may only read their own sessions
ALTER TABLE checkout_sessions ENABLE ROW LEVEL SECURITY;
CREATE POLICY cs_tenant_select ON checkout_sessions
    FOR SELECT
    USING (tenant_id = current_setting('app.tenant_id', TRUE)::TEXT);
-- Note: INSERT/UPDATE performed only by server-side role, not by tenant JWT directly.
```

**Immutability contract**: Once created, the following fields must never be updated:
`user_id`, `tenant_id`, `plan_id`, `amount_usd_cents`, `currency`,
`paypal_request_id_create`, `paypal_request_id_capture`, `nonce`, `expires_at`.

### 5.2 `checkout_session_events` Table (Audit Trail)

```sql
CREATE TABLE IF NOT EXISTS checkout_session_events (
    id              BIGSERIAL   PRIMARY KEY,
    session_id      UUID        NOT NULL REFERENCES checkout_sessions(id),
    event_type      TEXT        NOT NULL,
    -- Allowed values:
    --   CS_CREATED, ORDER_CREATED, CAPTURE_SUBMITTED,
    --   PAID_VERIFIED, EXPIRED, CANCELED, FAILED, REPLAY_BLOCKED
    actor           TEXT        NOT NULL DEFAULT 'SYSTEM',
    -- Allowed values: SYSTEM, USER, PAYPAL_WEBHOOK
    details         JSONB       NOT NULL DEFAULT '{}',
    -- Details must NOT contain PayPal-Request-Id, nonce, or raw secret values.
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_cse_session ON checkout_session_events(session_id);
CREATE INDEX idx_cse_created  ON checkout_session_events(created_at DESC);
```

### 5.3 `billing_orders` Schema Changes

The following alterations are required to existing `billing_orders`:

```sql
-- 1. Expand status CHECK constraint
-- Must preserve 'PAID' for backward compat with pilot data already in DB.
ALTER TABLE billing_orders
    DROP CONSTRAINT billing_orders_status_check;

ALTER TABLE billing_orders
    ADD CONSTRAINT billing_orders_status_check
    CHECK (status IN (
        'PENDING',             -- initial state when order row is created
        'CAPTURE_SUBMITTED',   -- NEW: sync capture accepted by PayPal
        'PAID_VERIFIED',       -- NEW: webhook PAYMENT.CAPTURE.COMPLETED confirmed
        'PAID',                -- LEGACY: preserve for pilot data, do not use for new orders
        'FAILED',
        'REFUNDED',
        'CANCELLED',
        'PARTIAL_REFUNDED'
    ));

-- 2. Add checkout_session_id FK
ALTER TABLE billing_orders
    ADD COLUMN checkout_session_id UUID REFERENCES checkout_sessions(id);

CREATE INDEX idx_billing_orders_cs ON billing_orders(checkout_session_id)
    WHERE checkout_session_id IS NOT NULL;
```

**ECONOMIC SIDE EFFECT MARKER — `billing_orders.status`**:
- `'CAPTURE_SUBMITTED'` is set by synchronous capture path. No entitlement change.
- `'PAID_VERIFIED'` is set **only** by webhook handler. Triggers `_grant_entitlement()`.
- Writing `'PAID_VERIFIED'` outside of the webhook handler is **forbidden**.

### 5.4 Canonical Amount Representation

- `checkout_sessions.amount_usd_cents` is stored as `BIGINT` (USD cents, e.g. 2900 = $29.00).
- `billing_orders.amount` remains `TEXT` decimal string (e.g. `"29.00"`) for PayPal API compat.
- Conversion: `amount_usd_cents / 100` as `Decimal` → format as `"29.00"` for PayPal requests.
- No float/double at any point (DEC-4211 compliance).

---

## 6. Endpoint Contracts

All five endpoints are prefixed `/v1/billing/` except `GET /v1/onboarding/status`.  
All endpoints require session JWT authentication (`Authorization: Bearer <jwt>`).  
All error responses conform to RFC 9457 `application/problem+json`.

---

### 6.1 `POST /v1/billing/checkout-sessions`

**Purpose**: Create a new checkout session. This is the mandatory first step before any payment
can begin.

**Auth**: Session JWT required. Resolves `user_id` and `tenant_id`. Guest requests → 401.

**Request**:
```json
{
  "plan_id": "plan_starter_v1"
}
```
`currency` is not a client parameter — it is derived from the plan definition server-side.

**Processing (ordered)**:
1. Validate JWT → resolve `user_id`, `tenant_id` via `UserTenant` lookup.
2. Look up `plans` by `plan_id` where `status = 'ACTIVE'`. Not found → 422.
3. Derive `amount_usd_cents` and `currency` from plan definition.
4. Check for existing non-terminal session for `(tenant_id, plan_id)`:
   - If exists and not expired → return existing session as 200 (idempotent).
   - If exists and expired → mark expired, proceed to create new.
5. Generate server-side: `paypal_request_id_create = str(uuid4())`,
   `paypal_request_id_capture = str(uuid4())`, `nonce = secrets.token_hex(32)`.
6. Set `expires_at = now() + SESSION_TTL_MINUTES` (env: `CHECKOUT_SESSION_TTL_MINUTES`, default: 30).
7. INSERT `checkout_sessions` with `status = 'CHECKOUT_SESSION_CREATED'`.
8. INSERT `checkout_session_events` (`CS_CREATED`, actor=`SYSTEM`).
9. Return 201.

**Response 201**:
```json
{
  "session_id": "019485ab-...",
  "status": "CHECKOUT_SESSION_CREATED",
  "plan_id": "plan_starter_v1",
  "amount": "29.00",
  "currency": "USD",
  "expires_at": "2026-04-16T12:30:00Z",
  "created_at": "2026-04-16T12:00:00Z"
}
```
**Response 200** (idempotent — existing session returned): Same schema.

**Fields never returned**: `paypal_request_id_create`, `paypal_request_id_capture`, `nonce`,
`user_id`.

**Failure modes**:

| Condition | Status | `reason_code` |
|-----------|--------|---------------|
| No/invalid JWT | 401 | `UNAUTHORIZED` |
| `plan_id` not found or inactive | 422 | `PLAN_NOT_FOUND` |
| `tenant_id` not resolved (no active UserTenant) | 403 | `TENANT_NOT_RESOLVED` |
| DB error | 500 | `INTERNAL_ERROR` |

---

### 6.2 `POST /v1/billing/paypal/orders`

**Purpose**: Create a PayPal order bound to an existing checkout session. Returns the PayPal
approval URL the client must redirect the buyer to.

**Auth**: Session JWT required. Tenant ownership enforced.

**Request**:
```json
{
  "session_id": "019485ab-..."
}
```

**Processing (ordered)**:
1. Validate JWT → `tenant_id`.
2. Fetch `checkout_sessions` by `session_id`. Not found → 404 (stealth — same response
   regardless of ownership mismatch).
3. Verify `session.tenant_id == auth.tenant_id`. Mismatch → 404 (stealth).
4. Check `expires_at`. Expired → UPDATE status to `EXPIRED`, INSERT event, return 410.
5. Check `status`:
   - `CHECKOUT_SESSION_CREATED` or `DRAFT`: proceed.
   - `PAYPAL_ORDER_CREATED` or beyond (non-terminal, non-expired): return existing
     `paypal_order_id` + approval URL as 200 (idempotent replay).
   - Terminal (`PAID_VERIFIED`, `CANCELED`, `EXPIRED`, `FAILED`) → 409.
6. Call `PayPalClient.create_order()` with:
   - `request_id = session.paypal_request_id_create` (stored value — never generate new).
   - `amount = format(session.amount_usd_cents / 100, ".2f")`.
   - `currency = session.currency`.
   - `internal_order_id = str(session.id)`.
   - `return_url = https://decisionproof.io.kr/checkout/return?session_id={session.id}` (server-controlled, locked domain).
   - `cancel_url = https://decisionproof.io.kr/checkout/cancel?session_id={session.id}` (server-controlled, locked domain).
   - Domain source: env var `CHECKOUT_SITE_BASE_URL`, default = `https://decisionproof.io.kr`. Must NOT use `api.decisionproof.io.kr`. Reason: paid beta checkout/onboarding surface is `decisionproof.io.kr` (DEC-V1-12).
7. In a single DB transaction:
   - INSERT `billing_orders` with `status = 'PENDING'`, `checkout_session_id = session.id`,
     `provider_order_id = paypal_order_id`, `provider = 'PAYPAL'`.
   - UPDATE `checkout_sessions.paypal_order_id = paypal_order_id`,
     `status = 'PAYPAL_ORDER_CREATED'`.
   - INSERT `checkout_session_events` (`ORDER_CREATED`, actor=`SYSTEM`,
     details=`{"paypal_order_id": "..."}`).
8. Extract `approve` link from PayPal response `links[]`.
9. Return 201.

**ECONOMIC SIDE EFFECT MARKER**: PayPal order creation creates a live PayPal order object.
Steps 6 and 7 must be atomic from the application perspective. If PayPal call succeeds but
DB transaction fails, the next retry will reuse `paypal_request_id_create` — PayPal will
return the same order idempotently. The application must handle PayPal returning an existing
order (status check on response) and recover by re-saving `paypal_order_id` to DB.

**Response 201**:
```json
{
  "session_id": "019485ab-...",
  "paypal_order_id": "5O190127TN364715T",
  "approval_url": "https://www.paypal.com/checkoutnow?token=5O190127TN364715T",
  "status": "PAYPAL_ORDER_CREATED",
  "expires_at": "2026-04-16T12:30:00Z"
}
```
**Response 200** (idempotent replay — existing order returned): Same schema.

**Failure modes**:

| Condition | Status | `reason_code` |
|-----------|--------|---------------|
| Session not found or wrong tenant | 404 | `SESSION_NOT_FOUND` |
| Session expired | 410 | `SESSION_EXPIRED` |
| Session in terminal state | 409 | `SESSION_TERMINAL_STATE` |
| PayPal API error (non-idempotent failure) | 502 | `PAYPAL_ORDER_CREATE_FAILED` |
| Missing PayPal credentials (preflight) | 503 | `BILLING_PROVIDER_UNAVAILABLE` |

---

### 6.3 `POST /v1/billing/paypal/capture`

**Purpose**: Submit a capture request for an approved PayPal order. This is the final step the
client calls after the buyer approves the payment on PayPal's hosted page. The result is
`CAPTURE_SUBMITTED` — **not** `PAID_VERIFIED`. Entitlement activation happens via webhook only.

**Auth**: Session JWT required. Tenant ownership enforced.

**Request**:
```json
{
  "session_id": "019485ab-..."
}
```

**Processing (ordered)**:
1. Validate JWT → `tenant_id`.
2. Fetch session. Not found or wrong tenant → 404 (stealth).
3. Check `expires_at`. Expired → 410.
4. Check `status`:
   - `PAYPAL_ORDER_CREATED` or `APPROVED`: proceed.
   - `CAPTURE_SUBMITTED`: return current state as 200 (idempotent — capture already submitted).
   - Terminal → 409.
   - `CHECKOUT_SESSION_CREATED` / `DRAFT` (order not yet created) → 422 `ORDER_NOT_CREATED`.
5. Verify `session.paypal_order_id` is not null. Null → 422 `ORDER_NOT_CREATED`.
6. Call `PayPalClient.capture_order()` with:
   - `paypal_order_id = session.paypal_order_id`.
   - `request_id = session.paypal_request_id_capture` (stored value — never generate new).
7. Handle PayPal response:
   - `status == 'COMPLETED'`:
     - `provider_capture_id` = first capture ID from response.
     - UPDATE `billing_orders`: `status = 'CAPTURE_SUBMITTED'`,
       `provider_capture_id = capture_id`.
     - UPDATE `checkout_sessions`: `status = 'CAPTURE_SUBMITTED'`.
     - INSERT `checkout_session_events` (`CAPTURE_SUBMITTED`, actor=`SYSTEM`).
   - `status == 'VOIDED'` or other non-COMPLETED:
     - UPDATE `checkout_sessions`: `status = 'FAILED'`, `failed_reason = paypal_status`.
     - UPDATE `billing_orders`: `status = 'FAILED'`.
     - INSERT `checkout_session_events` (`FAILED`).
     - Return 422 `CAPTURE_DECLINED`.
8. Return 202 (accepted — awaiting webhook confirmation).

**ECONOMIC SIDE EFFECT MARKER**: After a `COMPLETED` capture response, money has moved.
Do not activate entitlement here. Do not call `_grant_entitlement()` here.
The synchronous path terminates at `CAPTURE_SUBMITTED`.

**Response 202**:
```json
{
  "session_id": "019485ab-...",
  "status": "CAPTURE_SUBMITTED",
  "paypal_order_id": "5O190127TN364715T",
  "paypal_capture_id": "3C679366HH908993F",
  "message": "Payment capture submitted. Entitlement will be activated after webhook verification."
}
```
**Response 200** (idempotent replay — already submitted):
```json
{
  "session_id": "019485ab-...",
  "status": "CAPTURE_SUBMITTED",
  "paypal_order_id": "5O190127TN364715T",
  "paypal_capture_id": "3C679366HH908993F",
  "message": "Capture already submitted. Awaiting webhook verification."
}
```

**Failure modes**:

| Condition | Status | `reason_code` |
|-----------|--------|---------------|
| Session not found or wrong tenant | 404 | `SESSION_NOT_FOUND` |
| Session expired | 410 | `SESSION_EXPIRED` |
| Order not yet created | 422 | `ORDER_NOT_CREATED` |
| PayPal declined capture | 422 | `CAPTURE_DECLINED` |
| PayPal API error | 502 | `PAYPAL_CAPTURE_FAILED` |
| Terminal session | 409 | `SESSION_TERMINAL_STATE` |

---

### 6.4 `GET /v1/billing/me`

**Purpose**: Return the authenticated tenant's current billing status — entitlement status,
active plan, and most recent checkout session state. Used by the onboarding flow and client
polling.

**Auth**: Session JWT required.

**Request**: No body. Tenant derived from JWT.

**Processing**:
1. Resolve `tenant_id` from JWT.
2. Fetch most recent `checkout_sessions` for tenant (latest `created_at` DESC, limit 1).
3. Fetch `entitlements` for tenant (most recent active or most recent any).
4. Fetch `tenant_plan` if entitlement is active.
5. Return composite response.

**Response 200**:
```json
{
  "tenant_id": "user_a1b2c3d4",
  "entitlement": {
    "status": "FREE",
    "plan_id": null,
    "valid_from": null,
    "valid_until": null
  },
  "latest_checkout_session": {
    "session_id": "019485ab-...",
    "status": "CAPTURE_SUBMITTED",
    "plan_id": "plan_starter_v1",
    "amount": "29.00",
    "currency": "USD",
    "created_at": "2026-04-16T12:00:00Z",
    "updated_at": "2026-04-16T12:15:00Z"
  }
}
```
When no checkout session exists:
```json
{
  "tenant_id": "user_a1b2c3d4",
  "entitlement": { "status": "FREE", "plan_id": null, "valid_from": null, "valid_until": null },
  "latest_checkout_session": null
}
```

**Fields never returned**: `paypal_request_id_create`, `paypal_request_id_capture`, `nonce`.

**Failure modes**:

| Condition | Status | `reason_code` |
|-----------|--------|---------------|
| No/invalid JWT | 401 | `UNAUTHORIZED` |
| Tenant not resolved | 403 | `TENANT_NOT_RESOLVED` |

---

### 6.5 `GET /v1/onboarding/status`

**Purpose**: Return a structured onboarding checklist for the authenticated user. Used by the
front-end to display step-by-step progress.

**Auth**: Session JWT required.

**Response 200**:
```json
{
  "steps": {
    "signup_complete": true,
    "email_confirmed": true,
    "tenant_resolved": true,
    "payment_complete": false,
    "entitlement_active": false,
    "token_issued": false,
    "first_run_complete": false
  },
  "current_step": "payment_complete",
  "entitlement_status": "FREE",
  "checkout_session_status": "CAPTURE_SUBMITTED"
}
```

**Step resolution logic**:
- `signup_complete`: user_id exists in `user_tenants`.
- `email_confirmed`: Supabase `user.email_confirmed_at` is not null — checked via **Supabase
  Admin API + 60s in-memory cache** per tenant. JWT claim must NOT be used (stale risk).
  (OI-02 LOCKED)
- `tenant_resolved`: `user_tenants` row with `status = 'active'` exists.
- `payment_complete`: latest checkout session `status = 'PAID_VERIFIED'`.
- `entitlement_active`: `entitlements.status = 'ACTIVE'` for tenant.
- `token_issued`: at least one `api_tokens` or `api_keys` row with `status = 'ACTIVE'`.
- `first_run_complete`: at least one `runs` row with `status = 'COMPLETED'` for tenant.

**Failure modes**:

| Condition | Status | `reason_code` |
|-----------|--------|---------------|
| No/invalid JWT | 401 | `UNAUTHORIZED` |

---

### 6.6 Webhook Handler Modification: `POST /webhooks/paypal`

This is not a new endpoint but a **required modification** to the existing handler.

**Current behavior** on `PAYMENT.CAPTURE.COMPLETED`:
- `billing_order.status = "PAID"` → `_grant_entitlement()`

**Required behavior** on `PAYMENT.CAPTURE.COMPLETED`:
1. Existing verification flow unchanged (signature verify → re-query → dedup check).
2. After dedup acquire:
   a. Set `billing_order.status = 'PAID_VERIFIED'` (replaces `'PAID'`).
   b. Resolve `checkout_session_id` from `billing_order.checkout_session_id`.
   c. If `checkout_session_id` is not null: UPDATE `checkout_sessions.status = 'PAID_VERIFIED'`,
      INSERT `checkout_session_events` (`PAID_VERIFIED`, actor=`PAYPAL_WEBHOOK`).
   d. Call `_grant_entitlement(db, billing_order)` — **unchanged function**.
3. All other event types (`REFUNDED`, `DISPUTE`, `DENIED`) are unchanged.

**ECONOMIC SIDE EFFECT MARKER**: `_grant_entitlement()` is only called here, after webhook
verification. This is the sole path to `Entitlement = ACTIVE`.

---

## 7. State Machine and Transition Table

### 7.1 Canonical State Diagram

```
Visitor
  │
  ▼ POST /v1/auth/signup + login
[Authenticated User with Tenant]
  │
  ▼ POST /v1/billing/checkout-sessions
[DRAFT] ──────────────────────────────────────▶ [CHECKOUT_SESSION_CREATED]
                                                          │
                                  POST /v1/billing/paypal/orders
                                                          ▼
                                                [PAYPAL_ORDER_CREATED]
                                                          │
                                            (buyer approves at PayPal)
                                                          │
                                     ┌────────────────────┤
                                     │                    │
                              (return_url hit)    (capture called directly)
                                     ▼                    │
                                [APPROVED] ───────────────┘
                                     │
                        POST /v1/billing/paypal/capture
                                     ▼
                              [CAPTURE_SUBMITTED]  ◀── informational only
                                     │               capture ≠ entitlement
                        (PayPal PAYMENT.CAPTURE.COMPLETED webhook)
                                     ▼
                               [PAID_VERIFIED]  ──▶  Entitlement = ACTIVE
                                                      Token issuable
                                                      API usable

Any non-terminal state ──▶ [EXPIRED]   (expires_at crossed, sweep or on-access)
Any non-terminal state ──▶ [CANCELED]  (user explicit cancel or cancel_url)
Any non-terminal state ──▶ [FAILED]    (PayPal API error, capture declined)
```

**Terminal states**: `PAID_VERIFIED`, `CANCELED`, `EXPIRED`, `FAILED`  
Once terminal, no further transitions are permitted.

### 7.2 Transition Table

| From State | To State | Trigger | Authority | Side Effects |
|------------|----------|---------|-----------|--------------|
| (none) | `DRAFT` | `POST /v1/billing/checkout-sessions` | Server | none |
| `DRAFT` | `CHECKOUT_SESSION_CREATED` | Same request, successful INSERT | Server | none |
| `DRAFT` | `EXPIRED` | `expires_at` crossed | Sweep / on-access | none |
| `CHECKOUT_SESSION_CREATED` | `PAYPAL_ORDER_CREATED` | `POST /v1/billing/paypal/orders` | Server | **[EC]** PayPal order created; `billing_orders` INSERT |
| `CHECKOUT_SESSION_CREATED` | `EXPIRED` | `expires_at` crossed | Sweep / on-access | none |
| `CHECKOUT_SESSION_CREATED` | `CANCELED` | `cancel_url` hit | Server (redirect handler) | none |
| `PAYPAL_ORDER_CREATED` | `APPROVED` | `return_url` hit (optional) | Server (redirect handler) | none — informational only |
| `PAYPAL_ORDER_CREATED` | `CAPTURE_SUBMITTED` | `POST /v1/billing/paypal/capture` | Server | **[EC]** Capture submitted; `billing_orders.status = CAPTURE_SUBMITTED` |
| `PAYPAL_ORDER_CREATED` | `EXPIRED` | `expires_at` crossed | Sweep / on-access | none |
| `PAYPAL_ORDER_CREATED` | `CANCELED` | `cancel_url` | Server | none |
| `APPROVED` | `CAPTURE_SUBMITTED` | `POST /v1/billing/paypal/capture` | Server | **[EC]** Same as above |
| `APPROVED` | `EXPIRED` | `expires_at` crossed | Sweep / on-access | none |
| `CAPTURE_SUBMITTED` | `PAID_VERIFIED` | `PAYMENT.CAPTURE.COMPLETED` webhook | **Webhook handler only** | **[EC-AUTH]** `billing_orders.status = PAID_VERIFIED`; `_grant_entitlement()` called |
| `CAPTURE_SUBMITTED` | `FAILED` | `PAYMENT.CAPTURE.DENIED` webhook | Webhook handler | `billing_orders.status = FAILED` |

**[EC]** = Economic side effect present (money may move or be reserved).  
**[EC-AUTH]** = Economic side effect and entitlement activation. Most critical path.

### 7.3 Forbidden Transitions

The following transitions are **explicitly forbidden** and must be enforced programmatically:

| Forbidden Transition | Reason |
|---------------------|--------|
| Any state → `ACTIVE` entitlement (synchronous) | DEC-V1-07, DEC-V1-08 |
| `CAPTURE_SUBMITTED` → `PAID_VERIFIED` (outside webhook handler) | DEC-V1-07 |
| Any terminal state → any state | Terminal is final |
| `return_url` / `cancel_url` → entitlement change | DEC-V1-09 |
| `DRAFT` → `PAYPAL_ORDER_CREATED` (skipping `CHECKOUT_SESSION_CREATED`) | Invariant |

---

## 8. Concurrency and Race-Condition Rules

### 8.1 Double-Click / Duplicate Capture Request

**Scenario**: Client sends `POST /v1/billing/paypal/capture` twice before first completes.

**Rule**: The second request observes `status = 'CAPTURE_SUBMITTED'` and returns 200
(idempotent reply). `PayPalClient.capture_order()` is not called a second time because
the session status gate is checked before PayPal call.

**Defense-in-depth**: Even if the status gate is raced (two requests arrive before either
writes `CAPTURE_SUBMITTED`), `paypal_request_id_capture` is the same UUID in both calls.
PayPal will treat the second call as a replay and return the same capture result.

### 8.2 Webhook Duplicate

**Scenario**: PayPal delivers `PAYMENT.CAPTURE.COMPLETED` twice for the same capture.

**Rule**: Existing `WebhookDedupEvent` table with `UNIQUE (provider, event_id)` prevents
duplicate processing. Second webhook → dedup acquire fails → 200 no-op returned.
`_grant_entitlement()` is not called on the second event.

### 8.3 Capture vs Webhook Race

**Scenario**: Client calls capture; PayPal webhook arrives before capture HTTP response returns.

**Rule**: 
- Webhook processing sets `billing_order.status = 'PAID_VERIFIED'` and calls
  `_grant_entitlement()`.
- Sync capture path checks `billing_order.status` AFTER PayPal returns. If status is already
  `PAID_VERIFIED` (webhook won the race), sync path updates `checkout_sessions.status =
  'CAPTURE_SUBMITTED'` only if current value is not already `PAID_VERIFIED`.
- The sync path must never downgrade status from `PAID_VERIFIED` to `CAPTURE_SUBMITTED`.

**Implementation**: Use conditional UPDATE:
```sql
UPDATE checkout_sessions
   SET status = 'CAPTURE_SUBMITTED', updated_at = NOW()
 WHERE id = :session_id
   AND status NOT IN ('PAID_VERIFIED', 'CANCELED', 'EXPIRED', 'FAILED');
```

### 8.4 Session Expiry During Active Request

**Scenario**: Session expires at T=30min; user submits capture at T=29m59s; capture takes 5s.

**Rule**: Expiry is checked at request start (before PayPal call). If the session has not
expired at request start, the request proceeds to completion even if `expires_at` passes
during execution. This is acceptable — expiry is a UI-layer safeguard, not a money-leak vector.

### 8.5 Concurrent Checkout Session Creation

**Scenario**: Client sends two simultaneous `POST /v1/billing/checkout-sessions` requests.

**Rule**: Both will attempt to look up existing non-terminal session for the same tenant+plan.
Due to DB serialization, one will find nothing and INSERT; the other will find the first session
and return it. Both return the same session. No duplicate sessions for the same tenant+plan.

**Implementation**: Use `SELECT ... FOR UPDATE` or rely on `UNIQUE` constraint on
`(tenant_id, plan_id)` filtered on non-terminal statuses via partial index at application layer.
**Rule (OI-03 LOCKED)**: DB `UNIQUE (tenant_id, plan_id)` partial index (non-terminal
statuses only) is the first-writer guard. On `IntegrityError`, app fetches and returns the
existing session. Application-level pre-check alone is insufficient for this concurrency-sensitive path.

---

## 9. Idempotency Rules

### 9.1 Session-Level Idempotency

| Operation | Key | Behavior on duplicate |
|-----------|-----|----------------------|
| `POST /v1/billing/checkout-sessions` | `(tenant_id, plan_id)` + non-terminal | Return existing session, 200 |
| `POST /v1/billing/paypal/orders` | `session_id` in state `PAYPAL_ORDER_CREATED`+ | Return existing order data, 200 |
| `POST /v1/billing/paypal/capture` | `session_id` in state `CAPTURE_SUBMITTED`+ | Return existing capture data, 200 |

### 9.2 PayPal-Level Idempotency (External)

| PayPal call | Idempotency header | Key source |
|-------------|-------------------|------------|
| `POST /v2/checkout/orders` | `PayPal-Request-Id` | `checkout_sessions.paypal_request_id_create` |
| `POST /v2/checkout/orders/{id}/capture` | `PayPal-Request-Id` | `checkout_sessions.paypal_request_id_capture` |

**Rule**: Both keys are generated **once** at session creation and stored immutably.
On any retry, the stored key is used. A new UUID is **never** generated for a retry.

### 9.3 Webhook Dedup

| Layer | Key | Table |
|-------|-----|-------|
| PayPal webhook | `(provider='PAYPAL', event_id=X-PAYPAL-TRANSMISSION-ID)` | `webhook_dedup_events` |
| DB | `UNIQUE (provider, provider_order_id)` | `billing_orders` |
| DB | `UNIQUE (provider, event_id)` | `billing_events` |
| DB | `UNIQUE paypal_order_id` | `checkout_sessions` |
| DB | `UNIQUE paypal_request_id_create` | `checkout_sessions` |
| DB | `UNIQUE paypal_request_id_capture` | `checkout_sessions` |

### 9.4 Entitlement Idempotency

`_grant_entitlement()` uses upsert semantics: if entitlement row exists → update status to
`ACTIVE`; if not → create. Duplicate webhook cannot create duplicate entitlement rows
because `UNIQUE (tenant_id, plan_id)` constraint exists on `entitlements` table.

---

## 10. Error Policy

All error responses must conform to RFC 9457 `application/problem+json`.

Required fields:
```json
{
  "type": "https://api.decisionproof.ai/problems/{slug}",
  "title": "Human-readable title",
  "status": 4xx|5xx,
  "detail": "Specific detail for this instance",
  "instance": "/v1/billing/checkout-sessions",
  "trace_id": "uuid"
}
```

### 10.1 Payment-Path Error Codes

| `reason_code` | HTTP | Meaning |
|---------------|------|---------|
| `UNAUTHORIZED` | 401 | No or invalid JWT |
| `TENANT_NOT_RESOLVED` | 403 | No active user-tenant mapping |
| `SESSION_NOT_FOUND` | 404 | Session not found or wrong tenant (stealth) |
| `SESSION_EXPIRED` | 410 | Session has passed `expires_at` |
| `SESSION_TERMINAL_STATE` | 409 | Session is in a terminal state |
| `PLAN_NOT_FOUND` | 422 | `plan_id` not found or inactive |
| `ORDER_NOT_CREATED` | 422 | Capture attempted before order created |
| `CAPTURE_DECLINED` | 422 | PayPal declined the capture |
| `PAYPAL_ORDER_CREATE_FAILED` | 502 | PayPal API error on create |
| `PAYPAL_CAPTURE_FAILED` | 502 | PayPal API error on capture |
| `BILLING_PROVIDER_UNAVAILABLE` | 503 | Billing preflight not ready |
| `INTERNAL_ERROR` | 500 | Unexpected server error |

### 10.2 Fail-Closed Rule

Any ambiguous payment-side result must default to fail-closed:
- If PayPal returns an unexpected status on capture → `FAILED`, not `CAPTURE_SUBMITTED`.
- If webhook verification returns anything other than `SUCCESS` → reject, 401.
- If entitlement INSERT fails after `_grant_entitlement()` → log critical, do not silently
  succeed. Webhook returns 500 to trigger PayPal retry.

---

## 11. Security Requirements

| Requirement | Enforcement |
|-------------|-------------|
| No guest checkout | All `/v1/billing/*` and `/v1/onboarding/*` require session JWT via `get_session_auth_context()` dependency. No `optional=True`. |
| Tenant ownership isolation | `session.tenant_id == auth.tenant_id` check on every write endpoint. Mismatch → 404 (stealth). |
| `return_url` / `cancel_url` are server-controlled | Never accept `return_url` or `cancel_url` from client request body. Derive from `API_BASE_URL` env var only. |
| PayPal-Request-Id not logged | `paypal_request_id_create` and `paypal_request_id_capture` must not appear in any log line. |
| `nonce` not returned in API responses | Excluded from all response schemas. |
| Session immutability | `user_id`, `tenant_id`, `plan_id`, `amount_usd_cents`, `currency`, `paypal_request_id_*`, `nonce`, `expires_at` must not change after INSERT. Any attempted UPDATE of these fields by application code is a bug. |
| Webhook signature verification mandatory | `PAYMENT.CAPTURE.COMPLETED` must pass PayPal signature verification before any DB write. Verification failure → 401 (not 500). |
| No direct `Entitlement = ACTIVE` from sync path | Enforced by code review gate: grep for `_grant_entitlement` calls outside `webhooks.py`. |

---

## 12. Acceptance Criteria

Phase 1 is complete when **all** of the following pass:

| ID | Criterion | Verification |
|----|-----------|-------------|
| AC-01 | Unauthenticated `POST /v1/billing/checkout-sessions` returns 401 | Automated test |
| AC-02 | Authenticated request creates session; `paypal_request_id_create/capture` stored in DB | Automated test |
| AC-03 | Second identical request returns existing session (200, not 201) | Automated test |
| AC-04 | `POST /v1/billing/paypal/orders` with expired session returns 410 | Automated test |
| AC-05 | `POST /v1/billing/paypal/orders` with wrong tenant returns 404 (stealth) | Automated test |
| AC-06 | `POST /v1/billing/paypal/orders` calls PayPal with stored `paypal_request_id_create` | Mock + assertion test |
| AC-07 | `POST /v1/billing/paypal/orders` called twice → PayPal called once → same `paypal_order_id` returned | Mock + idempotency test |
| AC-08 | `POST /v1/billing/paypal/capture` calls PayPal with stored `paypal_request_id_capture` | Mock + assertion test |
| AC-09 | Successful capture sets `session.status = 'CAPTURE_SUBMITTED'` and `billing_order.status = 'CAPTURE_SUBMITTED'` | Automated test |
| AC-10 | Successful capture does NOT set `Entitlement.status = 'ACTIVE'` | Automated test — critical |
| AC-11 | `_grant_entitlement()` is not called from any synchronous endpoint | Code review gate: grep |
| AC-12 | Duplicate `POST /v1/billing/paypal/capture` returns 200 idempotent | Automated test |
| AC-13 | `PAYMENT.CAPTURE.COMPLETED` webhook → `session.status = 'PAID_VERIFIED'` | Automated test |
| AC-14 | `PAYMENT.CAPTURE.COMPLETED` webhook → `Entitlement.status = 'ACTIVE'` | Automated test |
| AC-15 | Duplicate `PAYMENT.CAPTURE.COMPLETED` webhook → no second entitlement activation | Automated test |
| AC-16 | `GET /v1/billing/me` returns correct entitlement and session state | Automated test |
| AC-17 | `GET /v1/onboarding/status` returns correct step completion | Automated test |
| AC-18 | All five endpoints return RFC 9457 `application/problem+json` on error | Automated test |
| AC-19 | `ruff check` passes with zero errors | CI gate |
| AC-20 | `mypy --strict` passes with zero errors | CI gate |
| AC-21 | `pytest` test suite passes (existing 133 tests + new Phase 1 tests) | CI gate |

---

## 13. Test Plan

### 13.1 New Test Files

```
apps/api/tests/
  unit/
    test_checkout_sessions.py     — Unit tests for checkout session CRUD and state machine
    test_billing_endpoints.py     — Unit tests for all 5 new endpoints (mocked PayPal)
    test_paypal_request_id.py     — Idempotency contract tests for PayPal-Request-Id
  integration/
    test_checkout_e2e.py          — E2E flow from session create to PAID_VERIFIED
    test_checkout_race.py         — Concurrency tests (double-click, duplicate webhook)
```

### 13.2 Test Matrix

| Test ID | File | Test Name | Type | Priority |
|---------|------|-----------|------|----------|
| T-01 | `test_billing_endpoints.py` | `test_create_session_no_auth_401` | Unit | P0 |
| T-02 | `test_billing_endpoints.py` | `test_create_session_invalid_plan_422` | Unit | P0 |
| T-03 | `test_billing_endpoints.py` | `test_create_session_success_stores_request_ids` | Unit | P0 |
| T-04 | `test_billing_endpoints.py` | `test_create_session_idempotent_returns_existing` | Unit | P0 |
| T-05 | `test_billing_endpoints.py` | `test_create_order_session_not_found_404` | Unit | P0 |
| T-06 | `test_billing_endpoints.py` | `test_create_order_wrong_tenant_stealth_404` | Unit | P0 |
| T-07 | `test_billing_endpoints.py` | `test_create_order_expired_session_410` | Unit | P0 |
| T-08 | `test_billing_endpoints.py` | `test_create_order_terminal_session_409` | Unit | P0 |
| T-09 | `test_billing_endpoints.py` | `test_create_order_uses_stored_request_id` | Unit | P0 |
| T-10 | `test_billing_endpoints.py` | `test_create_order_idempotent_paypal_called_once` | Unit | P0 |
| T-11 | `test_billing_endpoints.py` | `test_capture_before_order_created_422` | Unit | P0 |
| T-12 | `test_billing_endpoints.py` | `test_capture_success_sets_capture_submitted` | Unit | P0 |
| T-13 | `test_billing_endpoints.py` | `test_capture_success_does_NOT_activate_entitlement` | Unit | **CRITICAL** |
| T-14 | `test_billing_endpoints.py` | `test_capture_uses_stored_request_id_not_new` | Unit | P0 |
| T-15 | `test_billing_endpoints.py` | `test_capture_idempotent_second_call_200` | Unit | P0 |
| T-16 | `test_billing_endpoints.py` | `test_get_billing_me_no_auth_401` | Unit | P0 |
| T-17 | `test_billing_endpoints.py` | `test_get_billing_me_returns_correct_state` | Unit | P1 |
| T-18 | `test_billing_endpoints.py` | `test_onboarding_status_step_progression` | Unit | P1 |
| T-19 | `test_billing_endpoints.py` | `test_response_body_never_exposes_nonce_or_request_ids` | Unit | P0 |
| T-20 | `test_billing_endpoints.py` | `test_return_url_is_server_controlled` | Unit | P0 |
| T-21 | `test_checkout_sessions.py` | `test_session_immutable_fields_cannot_be_updated` | Unit | P0 |
| T-22 | `test_checkout_sessions.py` | `test_expired_session_transitions_correctly` | Unit | P1 |
| T-23 | `test_checkout_sessions.py` | `test_state_machine_rejects_forbidden_transitions` | Unit | P0 |
| T-24 | `test_paypal_request_id.py` | `test_request_id_same_across_retries` | Unit | P0 |
| T-25 | `test_paypal_request_id.py` | `test_new_uuid_never_generated_on_retry` | Unit | **CRITICAL** |
| T-26 | `test_checkout_race.py` | `test_double_click_capture_idempotent` | Integration | P0 |
| T-27 | `test_checkout_race.py` | `test_duplicate_webhook_no_double_grant` | Integration | **CRITICAL** |
| T-28 | `test_checkout_race.py` | `test_capture_webhook_race_no_status_downgrade` | Integration | P0 |
| T-29 | `test_checkout_e2e.py` | `test_full_flow_draft_to_paid_verified` | Integration | P0 |
| T-30 | `test_checkout_e2e.py` | `test_webhook_activates_entitlement_token_issuable` | Integration | **CRITICAL** |

**CRITICAL tests** (T-13, T-25, T-27, T-30) must pass before any Phase 2 work begins.

---

## 14. Non-Goals

The following are explicitly **out of scope for Phase 1**. Do not implement, propose, or
partially scaffold these items:

| # | Non-Goal | Deferral |
|---|----------|----------|
| NG-01 | Toss runtime isolation (code changes) | Phase 4 |
| NG-02 | Front-end checkout UI / payment page | Phase 5 |
| NG-03 | Subscription / recurring billing | Post-v1.0 |
| NG-04 | PayPal Subscription API | Post-v1.0 |
| NG-05 | KRW currency support | Post-Toss integration |
| NG-06 | Refund initiation endpoint | Post-v1.0 (refund via webhook only for v1.0) |
| NG-07 | Admin billing dashboard | Phase 5+ |
| NG-08 | New plan tier definitions | Separate data migration |
| NG-09 | K8s / EKS version upgrade | Frozen (DEC-V1-10) |
| NG-10 | SEO / marketing site changes | Phase 5 |
| NG-11 | Stripe re-integration | Never (removed) |
| NG-12 | Automated expired session sweep job | Phase 2+ (expiry handled on-access for now) |
| NG-13 | PayPal payout / seller-side flows | Out of scope for buyer payment path |
| NG-14 | Multi-tenant (non-personal) tenant management | Post-v1.0 |

---

## 15. Open Issues and Assumptions

| ID | Issue | Default Assumption (if no answer) | Owner |
|----|-------|-----------------------------------|-------|
| OI-01 | **[LOCKED — Phase 2 seed required]** v1.0 beta plan ID 후보: `beta_private_starter_v1`, `beta_private_growth_v1`. Generic public pricing 이름(`plan_starter_v1` 등)과 혼동 방지를 위해 `beta_private_` 접두어 사용. 실제 `amount_usd_cents` 및 `features_json`은 Phase 2 시작 전 operator가 확정하여 seed migration에 반영. | `beta_private_starter_v1` = $29.00/30일, `beta_private_growth_v1` = $79.00/30일 가정 | Operator |
| OI-02 | **[LOCKED]** `email_confirmed_at` 확인 방법 = **Supabase Admin API + short-lived cache** (TTL: 60초). JWT claim 의존 금지 — Supabase JWT에 `email_confirmed_at`이 포함되지 않을 수 있으며 claim 기반 확인은 stale 위험이 있음. `GET /onboarding/status` 호출 시 Admin API 조회 후 결과를 인메모리 캐시(tenant당 TTL 60초)로 보호. | Admin API + 60s cache | — |
| OI-03 | **[LOCKED]** 동시 세션 생성 충돌 처리 = **DB unique constraint + app retry**. checkout/order/create 경로는 concurrency-sensitive path이므로 앱 레벨 체크만으로는 불충분. `checkout_sessions`에 `UNIQUE (tenant_id, plan_id)` partial unique index(비-terminal 상태 한정)를 DB 차원에서 강제하고, `IntegrityError` 수신 시 앱이 기존 세션을 조회하여 반환. first-writer는 DB가 결정. | DB unique + app retry | — |
| OI-04 | **[LOCKED]** Checkout session TTL = **30분** (`CHECKOUT_SESSION_TTL_MINUTES=30`). 환경 변수로 override 가능하나 기본값은 30분으로 고정. | 30분 | — |
| OI-05 | **[LOCKED]** Entitlement validity (prepaid 1회 결제 기준) = **30일** (`ENTITLEMENT_VALIDITY_DAYS=30`). `valid_until = NOW() + INTERVAL '30 days'`로 계산. | 30일 | — |
| OI-06 | PayPal sandbox credentials Phase 2 테스트 전 준비 여부 | Phase 2 시작 전 필수 확인 | Operator |
| OI-07 | **[LOCKED]** `return_url` / `cancel_url` 기본 도메인 = **`decisionproof.io.kr`**. 이유: paid beta checkout/onboarding surface 일관성 (DEC-V1-12). `api.decisionproof.io.kr`는 절대 사용 금지. env var `CHECKOUT_SITE_BASE_URL`, default = `https://decisionproof.io.kr`. | `decisionproof.io.kr` | — |
| OI-08 | `APPROVED` state: `return_url` 핸들러로 명시적 설정 vs. capture 시 `PAYPAL_ORDER_CREATED`에서 직접 전환? | `APPROVED` optional; capture는 `PAYPAL_ORDER_CREATED`에서 직접 가능 | Engineering |
| OI-09 | `GET /v1/onboarding/status` rate limiting 필요 여부? | 기존 60 RPM 의존성 적용 | Engineering |
| OI-10 | Toss webhook handler: 완전 비활성(404) vs. 라우팅 유지(크레덴셜 없음)? | 핸들러 유지, Phase 4 전까지 non-launch-critical 문서화 | Engineering |

---

## 16. Phase 2 Handoff Notes

Phase 2 receives a codebase where:
- `checkout_sessions` and `checkout_session_events` tables are created and migrated.
- `billing_orders` has `checkout_session_id` FK and `CAPTURE_SUBMITTED`/`PAID_VERIFIED` statuses.
- All 5 endpoints are implemented, passing all 30 Phase 1 tests.
- PayPal client uses mandatory `PayPal-Request-Id` on both create-order and capture.
- Webhook handler transitions `billing_order` to `PAID_VERIFIED` and updates `checkout_session`.

**Phase 2 picks up**:
- PayPal return_url / cancel_url redirect handler (informational state update only).
- Expired session sweep job (background task or cron).
- `GET /v1/billing/checkout-sessions/{session_id}` public status polling endpoint.
- Race-condition hardening with `SELECT FOR UPDATE` on session state transitions.
- Load test of the 5-endpoint flow to validate concurrency behavior.
- Preparation for Phase 3 (auth-bound checkout frontend wiring).

---

## Appendix A: File Change Manifest

### Phase 1 필수 산출물 (신규 생성 4개 / 수정 2개)

```
[신규 생성 — Phase 1]
docs/V1_0_PHASE1_SOW.md                              (이 문서)
migrations/20260416_01_create_checkout_sessions.sql   (schema definition SQL)
migrations/20260416_02_alter_billing_orders_add_phase1.sql
tests/unit/test_checkout_sessions.py                 (test skeleton — stubs only)
tests/unit/test_billing_endpoints.py                 (test skeleton — stubs only)
tests/unit/test_paypal_request_id.py                 (test skeleton — stubs only)
tests/integration/test_checkout_e2e.py               (test skeleton — stubs only)
tests/integration/test_checkout_race.py              (test skeleton — stubs only)

[수정 — Phase 1 (scaffolding only)]
apps/api/dpp_api/db/models.py    (CheckoutSession + CheckoutSessionEvent 모델 class 추가;
                                   BillingOrder status enum + checkout_session_id 컬럼 추가;
                                   live router wiring 없음)
apps/api/dpp_api/schemas.py      (CheckoutSession 관련 Pydantic 스키마 class 추가만;
                                   기존 스키마 변경 없음)
```

> Phase 1 신규 생성: **9개** (SOW 1 + 마이그레이션 2 + 테스트 스켈레톤 6)  
> Phase 1 수정: **2개** (models.py, schemas.py)

---

### Phase 2 착수 예정 (Phase 1에서 건드리지 않음)

```
[신규 생성 — Phase 2]
apps/api/dpp_api/db/repo_checkout.py              (CheckoutSession CRUD + 상태 전환 로직)
apps/api/dpp_api/routers/billing.py               (5개 엔드포인트 full implementation)
apps/api/dpp_api/routers/onboarding.py            (GET /v1/onboarding/status)

[수정 — Phase 2]
apps/api/dpp_api/billing/paypal.py                (PayPal-Request-Id mandatory 적용)
apps/api/dpp_api/routers/webhooks.py              (PAID_VERIFIED 전환 + session 업데이트)
apps/api/dpp_api/main.py                          (billing, onboarding 라우터 등록)
```

---

### Phase 1 / Phase 2 모두 변경 없음

```
billing/active_preflight.py   (Toss isolation → Phase 4)
billing/toss.py               (Phase 4)
k8s/                          (Phase 4)
routers/auth.py               (no change)
routers/tokens.py             (no change)
```

---

## Appendix B: Spec Lock Approval Checklist

Phase 2 구현 착수 전 아래 항목이 모두 명시적으로 확인되어야 한다.

### Phase 1 종료 기준 (이 체크리스트 완료 = Phase 1 Done)

- [ ] **B-01**: Section 3 Locked Decisions 전체 승인
- [ ] **B-02**: OI-01 — beta plan ID (`beta_private_starter_v1`, `beta_private_growth_v1`) 및 실제 가격/기간 확정; seed migration 작성 준비 완료
- [ ] **B-03**: OI-04 — Session TTL 30분 **[LOCKED]** 재확인
- [ ] **B-04**: OI-05 — Entitlement validity 30일 **[LOCKED]** 재확인
- [ ] **B-05**: OI-07 — return_url 도메인 `decisionproof.io.kr` **[LOCKED]** 재확인
- [ ] **B-06**: AC-10, AC-11 (sync path에서 entitlement ACTIVE 불가) 명시적 승인
- [ ] **B-07**: T-13, T-25, T-27, T-30 (CRITICAL 테스트 4개) Phase 2 merge blocker로 지정 승인
- [ ] **B-08**: Appendix A Phase 1 / Phase 2 파일 경계 승인
- [ ] **B-09**: Phase 4 Toss isolation scope (Section 4.6 표) v1.0 launch gate로 승인
- [ ] **B-10**: Migration SQL (`20260416_01`, `20260416_02`) 검토 완료
- [ ] **B-11**: Test skeleton 파일 6개 (stubs) 생성 확인

**B-11까지 완료 = Phase 1 Done / Phase 2 착수 가능.**

---

### Phase 2 착수 게이트 (Phase 1 종료와 별도 — Phase 2 첫 PR 머지 전 필수)

- [ ] **PG-01**: OI-06 — PayPal sandbox credentials 준비 완료 확인
- [ ] **PG-02**: OI-01 — `beta_private_starter_v1` / `beta_private_growth_v1` 실제 가격·기간 확정 및 seed migration 작성 완료

---

*This document must not be edited after approval without a new version bump and explicit
re-approval. All implementation must conform to the contracts defined here.*

*Last updated: 2026-04-16 | DP-V1-P1-SOW v0.2 — FINAL SPEC LOCK*
