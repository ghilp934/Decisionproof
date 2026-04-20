# Rate Limit Headers — Internal SSOT

> **Internal reference only.** This file is not served as a public documentation surface.
> The public API reference is /docs/quickstart.html.

## SSOT (Single Source of Truth) — runtime header values

The following header values are the authoritative specification for RC-3 compliance.
They MUST match the runtime configuration in `dpp_api/rate_limiter.py`.

```
RateLimit-Policy: "default"; q=60; w=60
RateLimit: "default"; r=<remaining>; t=<reset_seconds>
Retry-After: 60
```

### Header semantics

| Header | On 2xx | On 429 | Notes |
|---|---|---|---|
| `RateLimit-Policy` | Required | Required | Policy id `"default"`, quota `q=60`, window `w=60` |
| `RateLimit` | Required | Required | Remaining quota `r=`, time-to-reset `t=` |
| `Retry-After` | Not sent | Required | Integer seconds; MUST be `60` (matches window) |
| `X-RateLimit-*` | Forbidden | Forbidden | Legacy headers MUST NOT appear |

### 429 response example

```
HTTP/1.1 429 Too Many Requests
RateLimit-Policy: "default"; q=60; w=60
RateLimit: "default"; r=0; t=60
Retry-After: 60
Content-Type: application/problem+json
```

### Maintenance

When changing quota (`q`) or window (`w`) values in `rate_limiter.py`, update this file to match.
`test_rc3_rate_limit_headers.py::test_t5_documentation_ssot_matches_runtime` enforces alignment.
