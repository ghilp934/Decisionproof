# Decisionproof — v0.4.2.11 (MT0A-2 Aligned)
## Execution governance layer for AI runs

[![Status](https://img.shields.io/badge/Status-Paid%20Private%20Beta-blue)](#status)
[![Tests](https://img.shields.io/badge/Tests-passing-success)](#)
[![Money](https://img.shields.io/badge/Money-Zero%20leak%20design-critical)](#money-flow-zero-leak-design)
[![License](https://img.shields.io/badge/License-Apache--2.0-blue)](#license)

**Spend caps, receipts, and automatic reconciliation for AI runs.**

Decisionproof is an execution governance / settlement integrity control layer for AI runs. It is **not an agent framework, prompt orchestration layer, model router, or model-quality evaluator.** You submit a run with a per-run spend cap under `reservation.max_cost_usd`. The platform reserves budget, executes the work, writes a result artifact, and only then settles the cost. If a worker crashes, a lease expires, or a retry collides, a reconciliation loop rolls the run forward or back — deterministically, without silent money drift.

Built for teams that treat AI execution as an accountable economic transaction: runs need to clear cleanly, cost needs to settle predictably, and the audit trail has to hold up under review. Initial v1.0 integration is LiteLLM-first; other frameworks should route through the supported proxy path unless separately agreed.

---

## Status

**Paid Private Beta — Not Public GA.**

This release is the Sandbox paid private beta, intended for individual or small-scale evaluation. It is **time-boxed and limit-enforced** — there is no unlimited usage, no overage billing, and no auto-renewal. Requests that exceed the active Sandbox limits are rejected fail-closed rather than accepted for additional charges. APIs, limits, and operational defaults may change without notice. No uptime SLA during the Sandbox paid private beta. The $29 / 30-day Sandbox plan is not the B2B Design Partner offer; Design Partner engagements are contracted separately.

- Access requires account registration and payment at [decisionproof.io.kr](https://decisionproof.io.kr)
- Sandbox payments are processed through PayPal during the paid private beta
- $29 USD per 30-day access cycle, manual renewal only (no auto-renewal)
- Workspace rate limits, per-run USD spend caps, and API-key limits apply (see [API docs](https://decisionproof.io.kr/docs/quickstart.html))

Repository: https://github.com/ghilp934/Decisionproof

---

## Why Decisionproof is different

Agent frameworks, durable execution engines, and orchestration toolkits cover **how** an AI run is constructed and resumed. Decisionproof sits one layer up: it governs **what a run is allowed to spend, how completion is proven, and how cost settles** when things go wrong.

- **Spend caps, reserved before work starts.** A run that would exceed its `reservation.max_cost_usd` per-run spend cap is rejected, not truncated mid-execution.
- **Receipt-backed settlement.** Cost is committed only when the result artifact is written and its metadata is captured. No receipt means no settlement.
- **Reaper reconciliation.** A continuous loop scans for lease expiry, stuck finalize stages, and missing receipts — and rolls forward or back deterministically.
- **Audit trail as first-class output.** Run metadata, state transitions, receipt records, and result references are logged. Exports are governed by the applicable retention tier: Sandbox includes Hot online access for 30 days by default; Cold Archive and Deep Archive are available only where included in the customer's plan or contract.

This is not a replacement for your agent framework. It is the financial and operational control layer you run around it.

---

## How to Get Access

1. Visit [decisionproof.io.kr](https://decisionproof.io.kr)
2. Register and confirm your email
3. Complete checkout via PayPal
4. Issue an API token from your dashboard
5. Submit your first run

Questions or feedback? Use the [contact form](https://decisionproof.io.kr/contact.html).

---

## Supported Pack Types

| Pack Type | Description |
|---|---|
| `decision` | Structured multi-factor decision scoring |
| `url` | URL screening and content analysis |
| `ocr` | Document and image OCR extraction |

---

## Sandbox Beta Constraints (hard-coded numerics)

Every Sandbox request is evaluated against the limits below **before** any AI inference cost is incurred. Requests that would breach any limit are rejected with HTTP 429 `quota-exceeded`. There is no overage billing; US$29 / 30-day access is the only amount Decisionproof charges.

- **Plan label**: Sandbox (paid private beta) — time-boxed, limit-enforced, **not unlimited**
- **Payment**: PayPal during Sandbox beta; B2B Design Partner engagements are contracted separately and billed by invoice and bank remittance
- **Access window**: 30 days per payment (US$29), manual renewal only, no auto-renewal
- **Workspace rate limit**: **60 requests per minute** (sliding window); exceeding returns HTTP 429 with `RateLimit-Remaining` / `Retry-After` headers
- **Monthly metered-operation cap**: **up to 2,000 metered operations per 30-day access cycle**, hard-capped, fail-closed
- **Per-run spend cap (`reservation.max_cost_usd`)**: **US$5.00** — maximum USD reserved for a single run; not an account/monthly budget
- **Per-run execution timeout**: **30 seconds**
- **Per-run input / output token limits**: **16,000 / 4,000 tokens**
- **API keys**: up to **3 concurrent** `dp_live_{secret}` keys per workspace
- **Overage**: **no overage billing** — any breach yields HTTP 429, never a charge
- **Retention**: Hot online access for 30 days by default; Cold Archive and Deep Archive are not included unless separately contracted
- **No uptime SLA** during Sandbox paid private beta
- **No free trial**

These numerics align with the internal runtime entitlement configuration (`dpp/apps/api/dpp_api/pricing/fixtures/pricing_ssot.json`) for the Sandbox paid private beta. B2B Design Partner contracts define their own limits in the signed pilot agreement.

---

## Commercial tier skeleton (MT0A-2 lock)

Decisionproof publishes a controlled four-tier commercial skeleton. Only Sandbox is currently sold self-serve. The skeleton is locked in `_DP_v1_0/mt0a_closeout/dec/DEC-MT0A-02-TIER-SKELETON.md` and reflected in `dpp/docs/SPEC_LOCK_PUBLIC_CONTRACT.md` §7.7.

| Tier | Status | Public CTA | Public price |
|---|---|---|---|
| Sandbox | Active paid private beta | Join Sandbox beta | US$29 / 30-day access cycle |
| Design Partner | Limited paid pilot — max 3 signed pre-v1.0; max 2 concurrent live onboardings | Apply for Design Partner | Contracted separately; no public amount |
| Growth | Post-GA / Waitlist | Join waitlist | Not public in MT0A-2 |
| Enterprise | Contract-only / Contact Sales | Contact Sales | Not public; no unlimited claim |

**Universal hard-stop commercial rule (every tier)** — Decisionproof does **not** run automatic postpaid overage in any tier. When the prepaid, invoiced, contract-defined, or reserved allowance is exhausted, the system hard-stops with HTTP 429 (rate / quota) or HTTP 402 (entitlement-inactive). Increasing capacity requires explicit plan upgrade, prepaid top-up, reserve increase, signed order-form amendment, manual invoice settlement, or contract-defined Enterprise limit change. **No tier is unlimited**; runtime configuration `0` for an Enterprise contract row means contract-defined / unset, not unlimited public usage.

**Support entitlement by tier** — Sandbox: best-effort email, target first response within 1 business day, no 24/7, no phone, no uptime SLA. Design Partner: Sev1 4h / Sev2 8h / Sev3 1 BD / Sev4 2 BD under signed pilot agreement; availability target binds only if expressly included. Growth: Post-GA, no public Sev commitment in MT0A-2. Enterprise: contract-defined; no public TAM / Slack / 24/7 promise unless signed.

---

## What is Decisionproof?

Decisionproof runs *decision packs* (units of work) as **runs** through a distributed pipeline:

1. **API** validates the request, enforces plan and spend-cap limits, and enqueues work.
2. **Worker** executes the pack from a queue and produces a result artifact. Depending on pack type, a worker performs structured scoring, URL screening, OCR/document extraction, or research-style processing. The result is written to durable storage so downstream systems can review outcomes under a controlled audit trail.
3. **Reaper** continuously reconciles edge cases (stuck runs, lease expiry, partial commits) using deterministic roll-forward / roll-back rules.

The core design goal is to remain **failure-safe** under retries, timeouts, and worker crashes by combining:
- **Idempotency** at the API boundary (per-tenant idempotency key).
- **Leases + heartbeats** for long-running work.
- **Receipt-based settlement** where result-store metadata acts as the authoritative proof for reconciliation.

---

## How it works (technical overview)

### Run lifecycle (high level)

A run moves through a state machine similar to:

- `RESERVED` → `QUEUED` → `PROCESSING` → `COMPLETED | FAILED`

Key mechanisms:

- **Idempotency:** a DB-level unique constraint on `(tenant_id, idempotency_key)` prevents duplicate run creation when clients retry.  
- **Leases:** Workers claim a run with a lease token and extend `lease_expires_at` periodically to prevent false zombie detection during long executions.
- **Optimistic locking:** critical updates use a version-based check to prevent concurrent state corruption.

### Money flow (zero leak design)

Decisionproof treats cost settlement as a “receipt-backed” flow:

1. **Reserve budget** (atomic) → run becomes `RESERVED`
2. **Write DB + enqueue** (transaction-aware) → `QUEUED`
3. Worker **claims lease** (CAS / token) → `PROCESSING`
4. Worker executes pack → computes `actual_cost`
5. Worker finalizes via a staged flow:

   - **PHASE 1 — CLAIM** (finalize stage = `CLAIMED`)
   - **PHASE 2 — RESULT UPLOAD** (result store metadata includes `actual_cost_usd_micros`)
   - **PHASE 3 — COMMIT** (settle budget with actual cost, persist completion, delete queue message)

6. **Reaper reconciliation** continuously checks for partial/failed transitions:

   - **Lease expired:** rollback/refund + mark as failed (policy-dependent)
   - **CLAIMED stuck:** *roll-forward* if result exists, else *roll-back*
   - **No receipt:** raise `AUDIT_REQUIRED` (critical alert)

This design makes it possible to recover deterministically after crashes without “silent money drift”.

---

## API surface (pilot)

### Base endpoints (local)

- `GET /health` — liveness probe (always 200 OK)
- `GET /readyz` — readiness probe (checks DB/Redis/S3/SQS; returns 503 if any dependency is down)
- `GET /docs` — Swagger UI (OpenAPI)
- `GET /redoc` — ReDoc

### Run API (example)

Submit a run:

```bash
curl -X POST http://localhost:8000/v1/runs \
  -H "Authorization: Bearer dp_live_your_key_here" \
  -H "Content-Type: application/json" \
  -H "Idempotency-Key: my-unique-key-123" \
  -d '{
    "pack_type": "decision",
    "inputs": {
      "question": "Should I launch this product?",
      "mode": "detailed"
    },
    "reservation": {
      "max_cost_usd": "5.00"
    }
  }'
```

`reservation.max_cost_usd` is the **per-run spend cap** — the maximum USD amount reserved for a single run. It is not your monthly, account-level, or workspace-level budget.

Get run status:

```bash
curl http://localhost:8000/v1/runs/<RUN_ID> \
  -H "Authorization: Bearer dp_live_your_key_here"
```

---

## Reliability primitives (why it stays predictable)

### 1) Idempotency & retries
- Clients can safely retry with the same `Idempotency-Key`.
- The API returns the existing run instead of creating duplicates.

### 2) Heartbeat & visibility extension
- Workers extend:
  - DB lease (`lease_expires_at`)
  - SQS visibility timeout
- This prevents premature “zombie” recovery while a legitimate long job is still running.

### 3) Reconciliation loop (Reaper)
- The Reaper periodically scans for:
  - expired leases
  - stuck finalize stages
  - missing receipts / inconsistent states
- It uses deterministic rules to either roll-forward or roll-back.

---

## Security model (pilot)

- **API keys:** stored hashed (SHA-256), validated with format + checksum; revocable from the dashboard.
- **Errors:** RFC 9457 `application/problem+json` for consistent, machine-parseable error handling.
- **CORS:** production uses an explicit allowlist via `CORS_ALLOWED_ORIGINS` (dev fallback uses localhost variants; never `"*"` with credentials).
- **Supabase / Postgres SSOT:** the production guide assumes Supabase Postgres as the primary DB. RLS is enabled for public tables with a default-deny stance.
- **Tenant isolation:** API keys only authorize access to their own tenant's runs and audit records.
- **Payments:** Sandbox payments are processed through PayPal during the paid private beta. No card numbers or payment credentials are stored by Decisionproof. B2B Design Partner engagements are contracted separately and billed through manual invoice, bank remittance, and applicable tax-invoice workflows.

These are architectural controls, not certifications. Decisionproof does not claim third-party compliance certifications or regulatory coverage during the paid private beta.

> For security reporting, do not open a public issue. Use GitHub Security Advisories / private reporting when enabled.

---

## Quickstart (local dev)

### Prerequisites
- Python 3.12+
- PostgreSQL 15+
- Redis 7+
- Docker (for local infra)

### Run locally

```bash
# 1) Clone
git clone https://github.com/ghilp934/Decisionproof.git
cd Decisionproof/dpp

# 2) Install (editable)
pip install -e ".[dev]"

# 3) Start local infra
cd infra
docker compose up -d  # or: docker-compose up -d

# 4) Run migrations
export DATABASE_URL="postgresql://dpp_user:dpp_pass@localhost:5432/dpp"
python -m alembic upgrade head

# 5) Start API
cd ../apps/api
uvicorn dpp_api.main:app --reload --port 8000

# 6) Start Worker (new terminal)
cd ../apps/worker
python -m dpp_worker.main

# 7) Start Reaper (new terminal)
cd ../apps/reaper
python -m dpp_reaper.main
```

Smoke checks:

```bash
curl -sS http://localhost:8000/health
curl -sS http://localhost:8000/readyz
```

---

## Repo layout (high level)

- `dpp/` — application code + infra + deployment assets
  - `apps/api/` — FastAPI server + tests
  - `apps/worker/` — SQS worker + heartbeat
  - `apps/reaper/` — reconciliation service
  - `alembic/` — DB migrations
  - `infra/` — local dev infrastructure (docker compose)
  - `k8s/` — production-ready Kubernetes manifests
- `ops/runbooks/` — operational runbooks (checklists / procedures)
- `.github/workflows/` — CI gates

---

## Docs

- `dpp/IMPLEMENTATION_REPORT.md` — implementation + hardening notes (MS-0 ~ MS-6)
- `dpp/PRODUCTION_DEPLOYMENT_GUIDE.md` — reference production deployment checklist
- `dpp/k8s/README.md` — Kubernetes manifests guide
- Local OpenAPI: `http://localhost:8000/docs`

---

## Contributing

Pilot-stage repo: issues and PRs are welcome, but APIs and internals may change frequently.

---

## License

Decisionproof is licensed under the **Apache License 2.0**.

- Apache-2.0 summary: https://www.apache.org/licenses/LICENSE-2.0
- If a `LICENSE` file exists in this repository, it is the source of truth for the full legal text.

