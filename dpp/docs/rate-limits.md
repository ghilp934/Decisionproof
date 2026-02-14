# Rate Limits

Decisionproof API uses **IETF RateLimit header fields** for standardized rate limiting.

## SSOT (Single Source of Truth) - Header Specification

All `/v1/*` API endpoints include the following headers:

### Successful Responses (2xx)
```
RateLimit-Policy: "default"; q=60; w=60
RateLimit: "default"; r=<int>; t=<int>
```

### Rate Limit Exceeded (429)
```
RateLimit-Policy: "default"; q=60; w=60
RateLimit: "default"; r=0; t=<int>
Retry-After: 60
```

## Header Field Definitions

### `RateLimit-Policy`
Describes the rate limit policy applied to the request.

- **Format**: `"<policy_id>"; q=<quota>; w=<window>`
- **Parameters**:
  - `policy_id`: Policy identifier (always `"default"`)
  - `q`: Quota (maximum requests allowed per window)
  - `w`: Window size in seconds

**Example**:
```
RateLimit-Policy: "default"; q=60; w=60
```
This means: 60 requests per 60-second window.

### `RateLimit`
Provides current rate limit state for the request.

- **Format**: `"<policy_id>"; r=<remaining>; t=<reset>`
- **Parameters**:
  - `policy_id`: Policy identifier (matches `RateLimit-Policy`)
  - `r`: Remaining quota in current window
  - `t`: Seconds until quota resets

**Example**:
```
RateLimit: "default"; r=45; t=30
```
This means: 45 requests remaining, resets in 30 seconds.

### `Retry-After`
Indicates when the client should retry after a 429 response.

- **Format**: Integer (seconds)
- **Present on**: 429 responses only

**Example**:
```
Retry-After: 60
```
This means: Retry after 60 seconds.

## Client Implementation Guide

### Handling Rate Limits

Clients should monitor `RateLimit` headers and implement backoff before hitting 429:

```python
import time
import requests

def call_api(url, headers):
    response = requests.get(url, headers=headers)

    # Check rate limit headers
    rate_limit = response.headers.get("RateLimit")
    if rate_limit:
        # Parse: "default"; r=<remaining>; t=<reset>
        parts = {p.split("=")[0].strip(): p.split("=")[1].strip()
                 for p in rate_limit.split(";")[1:]}
        remaining = int(parts.get("r", 999))
        reset = int(parts.get("t", 60))

        # Proactive backoff when quota is low
        if remaining < 5:
            print(f"Low quota ({remaining}), waiting {reset}s")
            time.sleep(reset)

    # Handle 429
    if response.status_code == 429:
        retry_after = int(response.headers.get("Retry-After", 60))
        print(f"Rate limited, retrying after {retry_after}s")
        time.sleep(retry_after)
        return call_api(url, headers)  # Retry

    return response
```

### Key Points

1. **Monitor `r` (remaining)**: When `r` drops below a threshold (e.g., 5), consider delaying requests.
2. **Use `t` (reset)**: Wait `t` seconds before the quota replenishes.
3. **Always respect `Retry-After`**: On 429, wait the exact duration specified.
4. **Implement exponential backoff**: If multiple 429s occur, increase wait time.

## Standard Compliance

Decisionproof follows the **IETF draft specification** for RateLimit headers:
- Draft: [draft-ietf-httpapi-ratelimit-headers](https://datatracker.ietf.org/doc/draft-ietf-httpapi-ratelimit-headers/)
- Format: Structured Fields (RFC 8941)

### Migration Notes

**Deprecated Headers** (not used):
- `X-RateLimit-*` (GitHub style)
- `X-Rate-Limit-*` (Twitter style)

All rate limit information is provided via standard `RateLimit-Policy` and `RateLimit` headers only.

## Current Limits

| Policy    | Quota | Window  | Scope          |
|-----------|-------|---------|----------------|
| `default` | 60    | 60s     | Per API key    |

**Note**: Limits may vary based on plan tier in the future. Always parse headers dynamically.

## Support

If you encounter rate limiting issues or need higher limits, contact support at support@decisionproof.ai.
