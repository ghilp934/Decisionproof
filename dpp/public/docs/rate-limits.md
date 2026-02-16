# Rate Limits

Decisionproof implements IETF draft-ietf-httpapi-ratelimit-headers compliant rate limiting.

## RateLimit Headers (SSOT - Single Source of Truth)

All responses include RateLimit headers per IETF draft-ietf-httpapi-ratelimit-headers:

### RateLimit-Policy

Describes the rate limit policy applied to this request.

```http
RateLimit-Policy: "default"; q=60; w=60
```

- `"default"`: Policy identifier
- `q=60`: Quota (requests allowed per window)
- `w=60`: Window size in seconds

### RateLimit

Current rate limit status.

```http
RateLimit: limit=600, remaining=599, reset=42
```

- `limit`: Maximum requests allowed in the window
- `remaining`: Requests remaining in current window
- `reset`: Seconds until window resets

## 429 Too Many Requests

When you exceed the rate limit, you receive a 429 response:

```http
HTTP/1.1 429 Too Many Requests
Content-Type: application/problem+json
RateLimit-Policy: "default"; q=60; w=60
RateLimit: limit=600, remaining=0, reset=60
Retry-After: 60
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
