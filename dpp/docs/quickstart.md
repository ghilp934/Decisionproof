# Quickstart

## Base URL

Decisionwise API is available at the following endpoints:

| Environment | Base URL | Description |
|-------------|----------|-------------|
| **Production** | `https://api.decisionwise.ai` | Live environment with real billing |
| **Sandbox** | `https://sandbox-api.decisionwise.ai` | Testing environment (test keys: `dw_test_*`) |
| **Local** | `http://localhost:8000` | Development server |

**Note**: Base URLs are configurable via environment variables (`API_BASE_URL`, `API_SANDBOX_URL`). Always verify the endpoint from the [OpenAPI spec](/.well-known/openapi.json) `servers` field.

## Authentication

Include your API key in the `X-API-Key` header:

```bash
X-API-Key: dw_live_abc123...
```

Sandbox keys use `dw_test_` prefix.

## Example Requests

### 200 Success (Billable)

<details>
<summary><strong>cURL</strong></summary>

```bash
curl -X POST https://api.decisionwise.ai/v1/runs \
  -H "X-API-Key: dw_live_abc123..." \
  -H "Content-Type: application/json" \
  -d '{
    "workspace_id": "ws_123",
    "run_id": "run_unique_001",
    "plan_id": "plan_456",
    "input": {"question": "What is 2+2?"}
  }'
```
</details>

<details>
<summary><strong>Python</strong></summary>

```python
import requests

response = requests.post(
    "https://api.decisionwise.ai/v1/runs",
    headers={
        "X-API-Key": "dw_live_abc123...",
        "Content-Type": "application/json"
    },
    json={
        "workspace_id": "ws_123",
        "run_id": "run_unique_001",
        "plan_id": "plan_456",
        "input": {"question": "What is 2+2?"}
    }
)

print(f"Status: {response.status_code}")
print(f"Result: {response.json()}")
```
</details>

<details>
<summary><strong>Node.js</strong></summary>

```javascript
const response = await fetch("https://api.decisionwise.ai/v1/runs", {
  method: "POST",
  headers: {
    "X-API-Key": "dw_live_abc123...",
    "Content-Type": "application/json"
  },
  body: JSON.stringify({
    workspace_id: "ws_123",
    run_id: "run_unique_001",
    plan_id: "plan_456",
    input: { question: "What is 2+2?" }
  })
});

const data = await response.json();
console.log(`Status: ${response.status}`);
console.log(`Result:`, data);
```
</details>

**Response**: 200 OK + Decision result. **Billable** (charges Decision Credits).

---

### 422 Unprocessable Entity (Billable)

<details>
<summary><strong>cURL</strong></summary>

```bash
curl -X POST https://api.decisionwise.ai/v1/runs \
  -H "X-API-Key: dw_live_abc123..." \
  -H "Content-Type: application/json" \
  -d '{
    "workspace_id": "ws_123",
    "run_id": "run_unique_002",
    "plan_id": "plan_invalid",
    "input": {}
  }'
```
</details>

<details>
<summary><strong>Python</strong></summary>

```python
import requests

response = requests.post(
    "https://api.decisionwise.ai/v1/runs",
    headers={
        "X-API-Key": "dw_live_abc123...",
        "Content-Type": "application/json"
    },
    json={
        "workspace_id": "ws_123",
        "run_id": "run_unique_002",
        "plan_id": "plan_invalid",
        "input": {}
    }
)

print(f"Status: {response.status_code}")
if response.status_code == 422:
    problem = response.json()
    print(f"Error Type: {problem['type']}")
    print(f"Title: {problem['title']}")
    print(f"Detail: {problem['detail']}")
```
</details>

<details>
<summary><strong>Node.js</strong></summary>

```javascript
const response = await fetch("https://api.decisionwise.ai/v1/runs", {
  method: "POST",
  headers: {
    "X-API-Key": "dw_live_abc123...",
    "Content-Type": "application/json"
  },
  body: JSON.stringify({
    workspace_id: "ws_123",
    run_id: "run_unique_002",
    plan_id: "plan_invalid",
    input: {}
  })
});

const problem = await response.json();
if (response.status === 422) {
  console.log(`Error Type: ${problem.type}`);
  console.log(`Title: ${problem.title}`);
  console.log(`Detail: ${problem.detail}`);
}
```
</details>

**Response**: 422 Unprocessable Entity (application/problem+json). **Billable** per pricing rules.

---

### 429 Rate Limit Exceeded (Non-Billable)

<details>
<summary><strong>cURL</strong></summary>

```bash
# After exceeding 600 requests/minute (STARTER tier)
curl -X POST https://api.decisionwise.ai/v1/runs \
  -H "X-API-Key: dw_live_abc123..." \
  -H "Content-Type: application/json" \
  -d '{
    "workspace_id": "ws_123",
    "run_id": "run_unique_603",
    "plan_id": "plan_456",
    "input": {"question": "Test"}
  }'
```
</details>

<details>
<summary><strong>Python</strong></summary>

```python
import requests
import time

response = requests.post(
    "https://api.decisionwise.ai/v1/runs",
    headers={
        "X-API-Key": "dw_live_abc123...",
        "Content-Type": "application/json"
    },
    json={
        "workspace_id": "ws_123",
        "run_id": "run_unique_603",
        "plan_id": "plan_456",
        "input": {"question": "Test"}
    }
)

if response.status_code == 429:
    retry_after = int(response.headers.get("Retry-After", 60))
    print(f"Rate limit exceeded. Retry after {retry_after} seconds.")

    problem = response.json()
    print(f"Violated policies: {problem.get('violated-policies', [])}")

    # Wait and retry
    time.sleep(retry_after)
    # ... retry request
```
</details>

<details>
<summary><strong>Node.js</strong></summary>

```javascript
const response = await fetch("https://api.decisionwise.ai/v1/runs", {
  method: "POST",
  headers: {
    "X-API-Key": "dw_live_abc123...",
    "Content-Type": "application/json"
  },
  body: JSON.stringify({
    workspace_id: "ws_123",
    run_id: "run_unique_603",
    plan_id: "plan_456",
    input: { question: "Test" }
  })
});

if (response.status === 429) {
  const retryAfter = parseInt(response.headers.get("Retry-After") || "60");
  console.log(`Rate limit exceeded. Retry after ${retryAfter} seconds.`);

  const problem = await response.json();
  console.log(`Violated policies:`, problem["violated-policies"]);

  // Wait and retry
  await new Promise(resolve => setTimeout(resolve, retryAfter * 1000));
  // ... retry request
}
```
</details>

**Response**: 429 Too Many Requests (application/problem+json) with `violated-policies` field. **Non-billable**.
**Action**: Wait for `Retry-After` seconds, then retry.

## Idempotency Rule

**Include a unique `run_id` in every request.**
Same `(workspace_id, run_id)` pair is charged only once within 45 days.

Example:
- Request 1: `run_id: "run_001"` → Charged
- Request 2: `run_id: "run_001"` (duplicate) → Not charged (deduplication_status: "duplicate")
- Request 3: `run_id: "run_002"` → Charged
