# Decisionproof API Platform (Pilot) — v0.4.2.2
## Decision Pack Platform — Agent-Centric API Platform

[![Status](https://img.shields.io/badge/Status-PILOT-orange)](#status)
[![Tests](https://img.shields.io/badge/Tests-133%20passing-success)](dpp/IMPLEMENTATION_REPORT.md#test-coverage-summary)
[![Money](https://img.shields.io/badge/Money-Zero%20leak%20design-critical)](#money-flow-zero-leak-design)
[![License](https://img.shields.io/badge/License-Apache--2.0-blue)](#license)

**Payment-based API platform for AI agents** — designed for **predictable execution** and **auditable cost settlement**.

---

## Status

- **PILOT (Not GA):** APIs, limits, and operational defaults may change without notice.
- This repository currently documents the **engineering design** and **reference deployment** used for the pilot.

Repository: https://github.com/ghilp934/Decisionproof

---

## What is Decisionproof?

Decisionproof runs *decision packs* (units of work) as **runs** through a distributed pipeline:

1) **API** validates the request, enforces plan/budget limits, and enqueues work.
2) **Worker** executes the pack from a queue, producing a result artifact.
3) **Reaper** continuously reconciles edge cases (stuck runs, lease expiry, partial commits).

The core design goal is to remain **failure-safe** under retries, timeouts, and worker crashes by combining:

- **Idempotency** at the API boundary (per-tenant idempotency key).
- **Leases + heartbeats** for long-running work.
- **Receipt-based settlement** where the result store metadata acts as an authoritative proof for reconciliation.

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
  -H "Authorization: Bearer dpp_live_abc123..." \
  -H "Content-Type: application/json" \
  -H "Idempotency-Key: my-unique-key-123" \
  -d '{
    "pack_type": "decision",
    "inputs": {
      "question": "Should I launch this product?",
      "mode": "detailed"
    },
    "max_cost_usd": "5.00"
  }'
```

Get run status:

```bash
curl http://localhost:8000/v1/runs/<RUN_ID> \
  -H "Authorization: Bearer dpp_live_abc123..."
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

- **API keys:** stored hashed (SHA-256), validated with format + checksum.
- **Errors:** RFC 9457 `application/problem+json` for consistent error handling.
- **CORS:** production uses an explicit allowlist via `CORS_ALLOWED_ORIGINS` (dev fallback uses localhost variants; never `"*"` with credentials).
- **Supabase / Postgres SSOT:** the production guide assumes Supabase Postgres as the primary DB. RLS is enabled for public tables with a default-deny stance.

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

