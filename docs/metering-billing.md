# Metering & Billing

## Overview

Decisionproof charges in **USD (United States Dollars)** based on actual execution costs. Billing is precise to 4 decimal places (e.g., `$0.0050` = half a cent).

**Key Principles**:
- **Reservation → Settlement**: Budget is reserved upfront, actual cost charged after execution
- **No Duplicate Charges**: Idempotency-Key prevents double billing
- **Transparent Costs**: Real-time cost breakdown in every response
- **Non-billable Errors**: Authentication and validation errors are free

---

## What Gets Billed

### Billable Operations

Charges apply when:
- ✅ **Successful execution** (`status: completed`)
- ✅ **Business logic failures** (`status: failed` with pack-level errors)

### Non-Billable Operations

No charges for:
- ❌ **Authentication errors** (401 Unauthorized)
- ❌ **Authorization errors** (403 Forbidden)
- ❌ **Validation errors** (422 Unprocessable Entity - request format issues)
- ❌ **Rate limiting** (429 Too Many Requests)
- ❌ **Server errors** (5xx)
- ❌ **Duplicate requests** (same Idempotency-Key within 7 days)

---

## Cost Breakdown

Every run response includes detailed cost information:

```json
{
  "cost": {
    "reserved_usd": "0.0500",           // Budget locked at submission
    "used_usd": "0.0120",                // Actual cost (execution + minimum fee)
    "minimum_fee_usd": "0.0010",         // Base fee per run
    "budget_remaining_usd": "99.9880"    // Tenant budget after this run
  }
}
```

**Components**:
- **Reserved**: Maximum budget locked when run is submitted (request field: `reservation.max_cost_usd`)
- **Used**: Actual cost = execution cost + minimum fee
- **Minimum Fee**: Base charge per run (applies even if execution cost is zero)
- **Budget Remaining**: Total tenant budget minus all settled costs

---

## Idempotency (Duplicate Prevention)

### How It Works

Use `Idempotency-Key` header to prevent duplicate charges:

```http
POST /v1/runs HTTP/1.1
Authorization: Bearer sk_abc123_xyz789...
Idempotency-Key: unique-request-id-001
```

**Behavior**:
- Same key within **7 days** → Returns existing `run_id`, **no charge**
- Same key after 7 days → Treated as new request, **billable**
- Different key → Always treated as new request, **billable**

### Example

```bash
# First request
curl -X POST https://api.decisionproof.ai/v1/runs \
  -H "Authorization: Bearer sk_abc123_xyz789..." \
  -H "Idempotency-Key: request-2026-02-17-001" \
  -d '{"pack_type": "decision", ...}'

# Response: 202 Accepted
# run_id: run_abc123
# cost.used_usd: "0.0120"

# Duplicate request (within 7 days)
curl -X POST https://api.decisionproof.ai/v1/runs \
  -H "Authorization: Bearer sk_abc123_xyz789..." \
  -H "Idempotency-Key: request-2026-02-17-001" \
  -d '{"pack_type": "decision", ...}'

# Response: 202 Accepted
# run_id: run_abc123 (same)
# cost.used_usd: "0.0000" (no charge)
# deduplication_status: "duplicate"
```

### Best Practices

**Recommended Key Format**:
```python
import uuid

# Option 1: UUID v4 (random)
idempotency_key = str(uuid.uuid4())

# Option 2: Context-based (deterministic retry)
idempotency_key = f"{tenant_id}-{user_id}-{timestamp}-{counter}"

# Option 3: Request hash (for exact retry)
import hashlib
request_hash = hashlib.sha256(json.dumps(request_body, sort_keys=True).encode()).hexdigest()
idempotency_key = f"hash-{request_hash[:16]}"
```

**Storage**:
- Store keys in database or cache with 7-day TTL
- Include in audit logs for charge disputes
- Use for retry logic after network failures

---

## Billing Status Codes

| Status | Billable | Example |
|--------|----------|---------|
| **202 Accepted → 200 OK (completed)** | ✅ Yes | Successful execution |
| **202 Accepted → 200 OK (failed, business error)** | ✅ Yes | Pack execution failure |
| **401 Unauthorized** | ❌ No | Missing or invalid token |
| **402 Payment Required** | ❌ No | Insufficient budget (not charged) |
| **403 Forbidden** | ❌ No | Tenant mismatch |
| **422 Unprocessable Entity** | ❌ No | Invalid request format |
| **429 Too Many Requests** | ❌ No | Rate limit exceeded |
| **500 Internal Server Error** | ❌ No | Server error |

---

## Budget Management

### Checking Budget

```bash
GET /v1/tenants/{tenant_id}/usage HTTP/1.1
Authorization: Bearer sk_abc123_xyz789...
```

**Response**:
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

### Budget Exhaustion

When budget is insufficient, requests are rejected **before** execution:

**402 Payment Required**:
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

**Client Action**:
1. Check current budget: `GET /v1/tenants/{tenant_id}/usage`
2. Reduce `reservation.max_cost_usd` in request
3. Contact support to increase budget limit

---

## Cost Optimization

### Strategies

1. **Set Realistic Budgets**
   - Use `reservation.max_cost_usd` to cap per-run costs
   - Start with conservative estimates, adjust based on usage

2. **Use Idempotency**
   - Always include `Idempotency-Key` to prevent accidental duplicates
   - Store keys for 7+ days to support retries

3. **Monitor Usage**
   - Check `/v1/tenants/{tenant_id}/usage` daily
   - Set up alerts for budget thresholds (80%, 90%)

4. **Handle Errors Early**
   - Validate inputs before submission to avoid billable failures
   - Use test environments for development

### Example: Budget-Aware Client

```python
import requests

def submit_run_with_budget_check(token, tenant_id, request_body):
    # 1. Check budget
    usage = requests.get(
        f"https://api.decisionproof.ai/v1/tenants/{tenant_id}/usage",
        headers={"Authorization": f"Bearer {token}"}
    ).json()

    remaining = float(usage["budget_remaining_usd"])
    requested = float(request_body["reservation"]["max_cost_usd"])

    if remaining < requested:
        raise ValueError(f"Insufficient budget: {remaining} < {requested}")

    # 2. Submit run
    response = requests.post(
        "https://api.decisionproof.ai/v1/runs",
        headers={
            "Authorization": f"Bearer {token}",
            "Idempotency-Key": generate_idempotency_key()
        },
        json=request_body
    )

    return response.json()
```

---

## Refunds

### Automatic Refunds

Refunds are issued automatically for:
- **Cancellation**: Run cancelled before execution starts
- **System Failure**: Server errors (5xx) during execution
- **Timeout**: Execution exceeds `reservation.timebox_sec`

**Refund Breakdown**:
```json
{
  "money_state": "refunded",
  "cost": {
    "reserved_usd": "0.0500",
    "used_usd": "0.0010",           // Minimum fee charged
    "refunded_usd": "0.0490",       // 0.0500 - 0.0010
    "budget_remaining_usd": "100.0490"
  }
}
```

### Dispute Process

For billing disputes:
1. Email: support@decisionproof.ai
2. Include: `run_id`, `tenant_id`, `timestamp`
3. Describe issue and expected charge
4. Response within 2 business days

---

## Billing Cycle

- **Period**: Calendar month (e.g., 2026-02-01 to 2026-02-28)
- **Settlement**: Real-time (charges applied immediately after run completion)
- **Invoicing**: Monthly invoice sent on 1st of following month
- **Payment Terms**: Net 15 (due 15 days after invoice date)

---

## FAQ

### Q: Why was I charged for a failed run?

**A**: Charges apply for **business logic failures** (pack execution errors) because computational resources were consumed. Authentication/validation errors (401, 422) are not charged.

### Q: How do I avoid duplicate charges?

**A**: Always include `Idempotency-Key` header. Same key within 7 days returns existing `run_id` with no charge.

### Q: What if my budget runs out mid-month?

**A**: New runs will be rejected with `402 Payment Required`. Existing runs will complete normally. Contact support to increase budget limit.

### Q: How precise is billing?

**A**: All costs are precise to 4 decimal places (e.g., `$0.0001` = 1/100th of a cent). Internal storage uses microdollars (1 microdollar = $0.000001).

### Q: Can I get a refund for accidental runs?

**A**: Contact support with `run_id` and details. Manual refunds are evaluated case-by-case.

---

## Related Documentation

- [Quickstart Guide](quickstart.md) - Complete API usage examples
- [Error Responses](problem-types.md) - All error codes and handling
- [Rate Limiting](rate-limits.md) - Request limits and best practices
- [Authentication](auth.md) - Token management

---

## Support

For billing questions or disputes:
- **Email**: support@decisionproof.ai
- **Documentation**: [https://docs.decisionproof.ai](https://docs.decisionproof.ai)
- **Status**: [https://status.decisionproof.ai](https://status.decisionproof.ai)
