# Runbook: Webhook Concurrency Rehearsal (P6.3)

**Version**: 1.0
**Phase**: 6.3 — Webhook 운영 리허설 + 동시성 폭격 증명
**DEC ref**: DEC-P02-6 (Idempotency / 중복 방어)

---

## Purpose

Verify that the **atomic idempotency gate** (`INSERT ON CONFLICT DO NOTHING RETURNING id`)
prevents duplicate business processing even when 5 identical webhook events arrive
simultaneously from a PayPal or Toss retry storm.

**Expected outcome**: Exactly 1 DB commit per event_id, regardless of concurrent delivery count.

---

## Architecture: Dedup Gate

```
PayPal/Toss delivers N identical webhooks ──►  /webhooks/paypal
                                                     │
                         ┌───────────────────────────▼─────────────────────────────┐
                         │ Step 1: INSERT ON CONFLICT (provider, dedup_key) DO NOTHING │
                         │         RETURNING id                                     │
                         │                                                           │
                         │  row returned  ───► FIRST handler → business processing  │
                         │  no row        ───► conflict exists → check step 2       │
                         │                                                           │
                         │ Step 2: UPDATE WHERE status='failed' RETURNING id         │
                         │                                                           │
                         │  row returned  ───► reclaim failed event → re-process    │
                         │  no row        ───► 'done' or concurrent 'processing'    │
                         │                     → return 200 already_processed       │
                         └───────────────────────────────────────────────────────────┘
```

**Table**: `webhook_dedup_events`
**Constraint**: `UNIQUE (provider, dedup_key)` — PostgreSQL guarantees exactly one INSERT wins

---

## Step 1: Pre-requisites

```bash
# 1. Confirm migration has been applied
psql $DATABASE_URL -c "\d webhook_dedup_events"
# Expected: table exists with provider, dedup_key, status columns

# 2. Confirm unique constraint
psql $DATABASE_URL -c "
  SELECT conname FROM pg_constraint
  WHERE conrelid = 'webhook_dedup_events'::regclass AND contype = 'u';
"
# Expected: uq_webhook_dedup_events

# 3. Confirm API server is running and healthy
curl -f http://localhost:8000/health
```

---

## Step 2: Prepare Runtime Inputs

```bash
cd dpp/ops/scripts

# Copy sample files to .local/ (gitignored)
cp .local/webhook_payload.sample.json .local/webhook_payload.json
cp .local/webhook_headers.sample.json .local/webhook_headers.json

# Edit webhook_payload.json: fill in real PayPal event_id + resource data
# from a PayPal sandbox webhook simulator

# Edit webhook_headers.json: fill in real PayPal webhook headers
# (X-PAYPAL-TRANSMISSION-ID, X-PAYPAL-TRANSMISSION-SIG, etc.)
```

> **Security**: `.local/` is gitignored and must never be committed.
> Headers contain live credentials.

---

## Step 3: Run Concurrency Bomb

```bash
cd dpp/ops/scripts

python p6_3_webhook_concurrency_bomb.py \
    --url http://localhost:8000/webhooks/paypal \
    --n 5 \
    --payload .local/webhook_payload.json \
    --headers .local/webhook_headers.json \
    --timeout 10
```

### Expected Output

```
  Target URL   : http://localhost:8000/webhooks/paypal
  Concurrency  : 5 simultaneous requests
  Payload hash : a3f8c2...
  Payload size : 412 bytes

  Firing 5 identical requests...

  ================================================================
    P6.3 Webhook Concurrency Bomb Results
    payload_hash=a3f8c2d1e5b7f9...  size=412B
  ================================================================
    #  HTTP     ms  response_status        error_code
    ----------------------------------------------------------------
    1   200    42.3  processed               ◀ FIRST
    2   200    43.1  already_processed
    3   200    43.5  already_processed
    4   200    44.0  already_processed
    5   200    42.8  already_processed

  ── HTTP status summary ──────────────────────
     200 (2xx): 5

  ── Idempotency summary ──────────────────────
     already_processed: 4
     processed: 1

  Verdict: PASS
```

### Acceptance Criteria

| Criterion | Expected Value |
|---|---|
| All HTTP status codes | 200 |
| `processed` count | exactly **1** |
| `already_processed` count | exactly **4** |
| 5xx errors | **0** |
| DB commit count | **1** (verify in logs) |

---

## Step 4: Verify DB State

```sql
-- Confirm exactly 1 record for the event_id
SELECT provider, dedup_key, status, first_seen_at
FROM webhook_dedup_events
WHERE dedup_key LIKE 'ev_%'
ORDER BY first_seen_at DESC
LIMIT 5;

-- Expected: 1 row with status='done'
```

---

## Step 5: Verify Log Hygiene

```bash
# Check server logs: payload hash/size only (not raw body)
grep "WEBHOOK_RECEIVED" /var/log/dpp/api.log | tail -1 | jq .

# Expected: {"event": "WEBHOOK_RECEIVED", "payload_hash": "...", "payload_size": N}
# NOT expected: raw webhook body, event_id, raw payment data in log fields

grep "WEBHOOK_ALREADY_PROCESSED" /var/log/dpp/api.log | wc -l
# Expected: 4 (one per duplicate)
```

---

## Failure Scenarios

### 5xx on concurrent requests

**Cause**: Missing DB migration (`webhook_dedup_events` table not created).
**Fix**: Apply `migrations/20260221_create_webhook_dedup_events_p6_3.sql`

```bash
psql $DATABASE_URL -f dpp/migrations/20260221_create_webhook_dedup_events_p6_3.sql
```

### `processed` count > 1 (duplicate business processing)

**Cause**: SELECT-then-INSERT race condition (old code path). Should not occur after P6.3.
**Action**: Immediately escalate. Check if the dedup gate code was correctly deployed.

```bash
# Verify deployed code has the correct pattern
grep -n "ON CONFLICT" dpp/apps/api/dpp_api/billing/webhook_dedup.py
# Expected: INSERT ... ON CONFLICT (provider, dedup_key) DO NOTHING RETURNING id
```

### Signature verification failure (401)

**Cause**: Stale or malformed webhook headers in `.local/webhook_headers.json`.
**Fix**: Regenerate webhook headers from PayPal sandbox simulator.

---

## Evidence Collection

After a successful run, capture evidence:

```bash
# Save terminal output
python p6_3_webhook_concurrency_bomb.py \
    --url http://localhost:8000/webhooks/paypal \
    --n 5 \
    --payload .local/webhook_payload.json \
    --headers .local/webhook_headers.json \
    2>&1 | tee ../../evidence/phase6_3_webhook_rehearsal_concurrency/50_bomb_output.txt

# Save DB state (no secrets)
psql $DATABASE_URL -c "
  SELECT provider, dedup_key, status, first_seen_at, last_seen_at
  FROM webhook_dedup_events ORDER BY first_seen_at DESC LIMIT 10
" > ../../evidence/phase6_3_webhook_rehearsal_concurrency/60_db_state.txt
```

---

## Toss Variant

For TossPayments webhook rehearsal, use the Toss endpoint and supply
`Tosspayments-Webhook-Transmission-Id` header:

```bash
python p6_3_webhook_concurrency_bomb.py \
    --url http://localhost:8000/webhooks/toss \
    --n 5 \
    --payload .local/toss_webhook_payload.json \
    --headers .local/toss_webhook_headers.json \
    --timeout 10
```

Dedup key for Toss: `tx_{Tosspayments-Webhook-Transmission-Id}` → fallback `pkey_{paymentKey}`.

---

*Generated: 2026-02-21 | DPP v0.4.2.2 | Phase 6.3*
