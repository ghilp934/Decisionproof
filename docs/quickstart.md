# Quickstart Guide

Get started with the Decisionproof API in minutes.

## Prerequisites

- API token (format: `sk_{key_id}_{secret}`)
- HTTP client (curl, Python requests, Node.js fetch, etc.)
- Basic understanding of asynchronous APIs (202 + polling pattern)

## Base URL

```
https://api.decisionproof.ai
```

For local development:
```
http://localhost:8000
```

## Authentication

All requests require a `Bearer` token in the `Authorization` header:

```http
Authorization: Bearer sk_abc123_xyz789def456...
```

See [Authentication Guide](auth.md) for token management and security best practices.

---

## Quick Example: Submit & Poll a Run

The Decisionproof API is **asynchronous**:
1. **Submit** a run → Receive `202 Accepted` with `run_id`
2. **Poll** for status → Check `/v1/runs/{run_id}` until `status: completed`
3. **Retrieve** result → Download from `presigned_url`

### Step 1: Submit a Run (202 Accepted)

<details>
<summary><strong>cURL</strong></summary>

```bash
curl -X POST https://api.decisionproof.ai/v1/runs \
  -H "Authorization: Bearer sk_abc123_xyz789..." \
  -H "Idempotency-Key: unique-request-id-001" \
  -H "Content-Type: application/json" \
  -d '{
    "pack_type": "decision",
    "inputs": {
      "question": "Should we proceed with Plan A?",
      "context": {"budget": 50000, "timeline": "Q2"}
    },
    "reservation": {
      "max_cost_usd": "0.0500",
      "timebox_sec": 90,
      "min_reliability_score": 0.8
    }
  }'
```
</details>

<details>
<summary><strong>Python</strong></summary>

```python
import requests
import time

# Submit run
response = requests.post(
    "https://api.decisionproof.ai/v1/runs",
    headers={
        "Authorization": "Bearer sk_abc123_xyz789...",
        "Idempotency-Key": "unique-request-id-001",
        "Content-Type": "application/json"
    },
    json={
        "pack_type": "decision",
        "inputs": {
            "question": "Should we proceed with Plan A?",
            "context": {"budget": 50000, "timeline": "Q2"}
        },
        "reservation": {
            "max_cost_usd": "0.0500",
            "timebox_sec": 90,
            "min_reliability_score": 0.8
        }
    }
)

assert response.status_code == 202, f"Expected 202, got {response.status_code}"
receipt = response.json()
run_id = receipt["run_id"]
poll_url = f"https://api.decisionproof.ai{receipt['poll']['href']}"
interval_ms = receipt['poll']['recommended_interval_ms']

print(f"Run submitted: {run_id}")
print(f"Status: {receipt['status']}")
```
</details>

<details>
<summary><strong>Node.js</strong></summary>

```javascript
const response = await fetch("https://api.decisionproof.ai/v1/runs", {
  method: "POST",
  headers: {
    "Authorization": "Bearer sk_abc123_xyz789...",
    "Idempotency-Key": "unique-request-id-001",
    "Content-Type": "application/json"
  },
  body: JSON.stringify({
    pack_type: "decision",
    inputs: {
      question: "Should we proceed with Plan A?",
      context: { budget: 50000, timeline: "Q2" }
    },
    reservation: {
      max_cost_usd: "0.0500",
      timebox_sec: 90,
      min_reliability_score: 0.8
    }
  })
});

if (response.status !== 202) {
  throw new Error(`Expected 202, got ${response.status}`);
}

const receipt = await response.json();
const runId = receipt.run_id;
const pollUrl = `https://api.decisionproof.ai${receipt.poll.href}`;
const intervalMs = receipt.poll.recommended_interval_ms;

console.log(`Run submitted: ${runId}`);
console.log(`Status: ${receipt.status}`);
```
</details>

**Response (202 Accepted)**:

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
    "trace_id": null,
    "profile_version": "v0.4.2.2"
  }
}
```

**Key Fields**:
- `run_id`: Unique identifier for this run (server-generated)
- `status`: Current state (`queued`, `processing`, `completed`, `failed`)
- `poll.href`: Endpoint to poll for status updates
- `poll.recommended_interval_ms`: How often to poll (default: 1500ms)
- `reservation.reserved_usd`: Budget locked for this run

---

### Step 2: Poll for Completion

Poll the run status endpoint until `status` is `completed` or `failed`:

<details>
<summary><strong>Python (Polling Loop)</strong></summary>

```python
# Continue from Step 1...
while True:
    time.sleep(interval_ms / 1000)  # Convert ms to seconds

    status_response = requests.get(
        poll_url,
        headers={"Authorization": "Bearer sk_abc123_xyz789..."}
    )

    if status_response.status_code != 200:
        print(f"Error polling: {status_response.status_code}")
        break

    run_status = status_response.json()
    print(f"Status: {run_status['status']}")

    if run_status['status'] in ['completed', 'failed']:
        break

# Run is complete
print(f"Final status: {run_status['status']}")
print(f"Cost breakdown: {run_status['cost']}")

if run_status['status'] == 'completed':
    print(f"Result URL: {run_status['result']['presigned_url']}")
elif run_status['status'] == 'failed':
    print(f"Error: {run_status['error']}")
```
</details>

<details>
<summary><strong>Node.js (Polling Loop)</strong></summary>

```javascript
// Continue from Step 1...
while (true) {
  await new Promise(resolve => setTimeout(resolve, intervalMs));

  const statusResponse = await fetch(pollUrl, {
    headers: { "Authorization": "Bearer sk_abc123_xyz789..." }
  });

  if (statusResponse.status !== 200) {
    console.error(`Error polling: ${statusResponse.status}`);
    break;
  }

  const runStatus = await statusResponse.json();
  console.log(`Status: ${runStatus.status}`);

  if (['completed', 'failed'].includes(runStatus.status)) {
    break;
  }
}

// Run is complete
console.log(`Final status: ${runStatus.status}`);
console.log(`Cost breakdown:`, runStatus.cost);

if (runStatus.status === 'completed') {
  console.log(`Result URL: ${runStatus.result.presigned_url}`);
} else if (runStatus.status === 'failed') {
  console.error(`Error:`, runStatus.error);
}
```
</details>

**Response (200 OK - Completed)**:

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
    "sha256": "abc123def456...",
    "expires_at": "2026-02-17T12:00:00Z"
  },
  "error": null,
  "meta": {
    "trace_id": null,
    "profile_version": "v0.4.2.2"
  }
}
```

**Key Fields**:
- `status`: `completed` (success) or `failed` (error)
- `money_state`: `settled` (final charge applied) or `refunded` (partial/full refund)
- `cost.used_usd`: Actual cost charged (execution + minimum fee)
- `cost.budget_remaining_usd`: Remaining budget after this run
- `result.presigned_url`: Download link for result file (expires in 24 hours)
- `result.sha256`: SHA-256 checksum for result file integrity

---

### Step 3: Download Result

```bash
# Download result file (valid for 24 hours)
curl -o result.json "https://s3.amazonaws.com/..."

# Verify checksum (optional)
sha256sum result.json
# Compare with result.sha256 from response
```

**Result Format** (JSON):

```json
{
  "decision": "proceed",
  "confidence": 0.92,
  "reasoning": "Budget and timeline align with project requirements...",
  "risks": ["market volatility", "resource constraints"],
  "alternatives": ["Plan B", "Phased rollout"]
}
```

---

## Idempotency

To prevent duplicate charges, use the `Idempotency-Key` header:

```http
Idempotency-Key: unique-request-id-001
```

**How it works**:
- Same `Idempotency-Key` within 7 days → Returns existing `run_id` (no charge)
- Same key after 7 days → Treated as new request (billable)

**Example**:

```bash
# First request
curl -X POST https://api.decisionproof.ai/v1/runs \
  -H "Authorization: Bearer sk_abc123_xyz789..." \
  -H "Idempotency-Key: request-20260217-001" \
  -d '{"pack_type": "decision", ...}'

# Response: 202 Accepted, run_id: run_abc123

# Duplicate request (within 7 days)
curl -X POST https://api.decisionproof.ai/v1/runs \
  -H "Authorization: Bearer sk_abc123_xyz789..." \
  -H "Idempotency-Key: request-20260217-001" \
  -d '{"pack_type": "decision", ...}'

# Response: 202 Accepted, run_id: run_abc123 (same, no charge)
```

**Best Practices**:
- Use client-generated UUIDs: `f"{client_id}-{timestamp}-{counter}"`
- Include request context in key: `f"decision-{user_id}-{session_id}"`
- Store keys for audit/retry: Database or cache with 7-day TTL

---

## Error Handling

All errors follow [RFC 9457 Problem Details](https://datatracker.ietf.org/doc/html/rfc9457):

```json
{
  "type": "https://docs.decisionproof.ai/errors/budget-exceeded",
  "title": "Budget Exceeded",
  "status": 402,
  "detail": "Requested max_cost_usd (0.0500) exceeds remaining budget (0.0200)",
  "instance": "/v1/runs",
  "reason_code": "BUDGET_EXCEEDED",
  "trace_id": "abc123-def456-789"
}
```

**Common Errors**:

| Status | Reason Code | Description | Billable |
|--------|-------------|-------------|----------|
| 401 | `AUTH_MISSING` | Missing `Authorization` header | No |
| 402 | `BUDGET_EXCEEDED` | Insufficient budget | No |
| 422 | `INVALID_MONEY_SCALE` | `max_cost_usd` has >4 decimal places | No |
| 422 | `INVALID_PACK_TYPE` | Unknown `pack_type` | No |
| 429 | `RATE_LIMIT_EXCEEDED` | Too many requests | No |

See [Error Responses](problem-types.md) for complete error catalog and handling strategies.

---

## Rate Limiting

Response headers indicate rate limit status:

```http
RateLimit-Limit: 100
RateLimit-Remaining: 87
RateLimit-Reset: 1708156800
```

**429 Too Many Requests**:

```json
{
  "type": "https://docs.decisionproof.ai/errors/rate-limit-exceeded",
  "title": "Rate Limit Exceeded",
  "status": 429,
  "detail": "Request rate limit exceeded (100 req/min). Retry after 60 seconds.",
  "instance": "/v1/runs",
  "reason_code": "RATE_LIMIT_EXCEEDED",
  "trace_id": "abc123-def456-789"
}
```

**Headers**:
- `Retry-After: 60` (seconds until retry allowed)

**Handling**:

```python
import time

response = requests.post(url, headers=headers, json=body)

if response.status_code == 429:
    retry_after = int(response.headers.get("Retry-After", 60))
    print(f"Rate limited. Waiting {retry_after} seconds...")
    time.sleep(retry_after)
    # Retry request
```

See [Rate Limiting](rate-limits.md) for limits and best practices.

---

## API Schema Reference

### POST /v1/runs - Create Run

**Request Body**:

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `pack_type` | string | Yes | Pack type (`decision`, `url`, `ocr`, etc.) |
| `inputs` | object | Yes | Pack-specific inputs |
| `reservation` | object | Yes | Budget/timeout/reliability parameters |
| `reservation.max_cost_usd` | string | Yes | Max cost (4dp, e.g., `"0.0500"`) |
| `reservation.timebox_sec` | integer | No | Timeout (1-90, default: 90) |
| `reservation.min_reliability_score` | float | No | Min score (0.0-1.0, default: 0.8) |
| `meta` | object | No | Optional metadata |
| `meta.trace_id` | string | No | Distributed tracing ID |

**Response (202 Accepted)**:

| Field | Type | Description |
|-------|------|-------------|
| `run_id` | string | Unique run identifier |
| `status` | string | Current status (`queued`, `processing`, `completed`, `failed`) |
| `poll.href` | string | Polling endpoint |
| `poll.recommended_interval_ms` | integer | Polling interval (ms) |
| `poll.max_wait_sec` | integer | Max execution time (sec) |

### GET /v1/runs/{run_id} - Poll Run Status

**Response (200 OK)**:

| Field | Type | Description |
|-------|------|-------------|
| `run_id` | string | Run identifier |
| `status` | string | Current status |
| `money_state` | string | Billing state (`reserved`, `settled`, `refunded`) |
| `cost.reserved_usd` | string | Reserved budget |
| `cost.used_usd` | string | Actual cost |
| `cost.budget_remaining_usd` | string | Remaining budget |
| `result.presigned_url` | string | Result download URL (if `status: completed`) |
| `result.expires_at` | string | URL expiry (ISO 8601) |
| `error.reason_code` | string | Error code (if `status: failed`) |
| `error.detail` | string | Error description (if `status: failed`) |

---

## Next Steps

- **[Authentication Guide](auth.md)**: Token management and security
- **[Metering & Billing](metering-billing.md)**: Cost calculation and billing rules
- **[Error Handling](problem-types.md)**: Complete error catalog
- **[Rate Limiting](rate-limits.md)**: Request limits and best practices
- **[API Reference (OpenAPI)](/.well-known/openapi.json)**: Full API specification

## Need Help?

- **Documentation**: [https://docs.decisionproof.ai](https://docs.decisionproof.ai)
- **Email Support**: support@decisionproof.ai
- **Status Page**: [https://status.decisionproof.ai](https://status.decisionproof.ai)
