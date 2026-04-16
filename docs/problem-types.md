# Problem Types (RFC 9457)

Decisionproof uses [RFC 9457 Problem Details](https://www.rfc-editor.org/rfc/rfc9457.html) for structured error responses.

## Problem Details Format

All error responses use `application/problem+json` content type with standard fields:

```json
{
  "type": "https://iana.org/assignments/http-problem-types#quota-exceeded",
  "title": "Request cannot be satisfied as assigned quota has been exceeded",
  "status": 429,
  "detail": "RPM limit of 600 requests per minute exceeded",
  "instance": "/v1/runs/run_abc123"
}
```

### Standard Fields

- **type**: URI identifying the problem type (machine-readable)
- **title**: Short, human-readable summary
- **status**: HTTP status code
- **detail** (optional): Human-readable explanation specific to this occurrence
- **instance** (optional): URI reference identifying this specific occurrence

### Extension Fields

Decisionproof adds extension fields for specific problem types:

- **violated-policies**: Array of policy violations (quota-exceeded, budget-exceeded)

## Decisionproof Problem Types

### quota-exceeded (429)

**Type**: `https://iana.org/assignments/http-problem-types#quota-exceeded`

**When**: RPM limit, monthly budget quota, or hard overage cap exceeded.

**Client Action**: Wait for `Retry-After` seconds (check `violated-policies` to identify which limit). Upgrade tier if frequently hitting limits.

**Extension**: `violated-policies` array with policy name, limit, current usage, window.

---

### budget-exceeded (429)

**Type**: `https://iana.org/assignments/http-problem-types#budget-exceeded`

**When**: Workspace budget cap exceeded (configurable hard spending limit).

**Client Action**: Increase budget allocation or increase budget cap. Contact billing support.

---

### invalid-request (400)

**Type**: `https://iana.org/assignments/http-problem-types#invalid-request`

**When**: Malformed request body, invalid JSON, missing required fields.

**Client Action**: Fix request payload per OpenAPI schema. Check `detail` field for specifics.

---

### unauthorized (401)

**Type**: `https://iana.org/assignments/http-problem-types#unauthorized`

**When**: Missing or invalid Bearer token.

**Client Action**: Verify `Authorization` header is present with format: `Bearer sk_{key_id}_{secret}`. Check token is not expired.

---

### forbidden (403)

**Type**: `https://iana.org/assignments/http-problem-types#forbidden`

**When**: Valid API key but lacks permission for requested resource.

**Client Action**: Check workspace access permissions. Contact account owner.

---

### not-found (404)

**Type**: `https://iana.org/assignments/http-problem-types#not-found`

**When**: Requested resource (plan, run, workspace) does not exist.

**Client Action**: Verify resource ID is correct. Check if resource was deleted.

---

## Billability

All Problem Details responses (4xx, 5xx) are **non-billable** except:
- **422 Unprocessable Entity**: Billable (valid request, invalid business logic)

See [Metering & Billing](/docs/metering-billing.md) for complete rules.
