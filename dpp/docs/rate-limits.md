# Rate Limits

Decisionproof implements IETF draft-ietf-httpapi-ratelimit-headers compliant rate limiting.

## RateLimit Headers

All responses include RateLimit headers:

### RateLimit-Policy

Describes the rate limit policy applied to this request.

```http
RateLimit-Policy: "post_runs";w=60;q=600
```

- `post_runs`: Policy name (e.g., "post_runs", "poll_runs")
- `w=60`: Window size in seconds
- `q=600`: Quota (maximum requests allowed in the window)

### RateLimit

Current rate limit status.

```http
RateLimit: "post_runs";r=599;t=42
```

- `post_runs`: Policy name (matches RateLimit-Policy)
- `r=599`: Remaining requests in current window
- `t=42`: TTL in seconds until window resets

## 429 Too Many Requests

When you exceed the rate limit, you receive a 429 response:

```http
HTTP/1.1 429 Too Many Requests
Content-Type: application/problem+json
RateLimit-Policy: "post_runs";w=60;q=600
RateLimit: "post_runs";r=0;t=30
Retry-After: 30
```

```json
{
  "type": "https://iana.org/assignments/http-problem-types#quota-exceeded",
  "title": "Request cannot be satisfied as assigned quota has been exceeded",
  "status": 429,
  "detail": "RPM limit of 600 requests per minute exceeded",
  "violated-policies": [
    {
      "policy": "rpm",
      "limit": 600,
      "current": 601,
      "window_seconds": 60
    }
  ]
}
```

## Retry-After Header

The `Retry-After` header indicates how many seconds to wait before retrying.

**IMPORTANT**: `Retry-After` takes precedence over `RateLimit: reset`.

## Client Handling

1. **Parse RateLimit headers** on every response
2. **Track remaining requests** to avoid hitting limits
3. **On 429**: Read `Retry-After`, wait, then retry
4. **Exponential backoff**: Recommended for repeated 429s

## Tier-Specific Limits

See [Pricing SSoT](/pricing/ssot.json) for tier-specific RPM limits:

- SANDBOX: 60 RPM
- STARTER: 600 RPM
- GROWTH: 3000 RPM
- ENTERPRISE: Unlimited (0 = unlimited)
