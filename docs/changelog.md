# Changelog

## Documentation Version

**docs_version**: 2026-02-14.v1.0.0

## Breaking Change Markers

- **[BREAKING]**: Requires client code changes
- **[DEPRECATION]**: Feature will be removed in future version (grace period provided)
- **[NEW]**: New feature or endpoint
- **[FIX]**: Bug fix or correction

---

## 2026-02-14.v1.0.0

**Release Date**: 2026-02-14

### [NEW] AI-Friendly Documentation

- Added `/llms.txt` and `/llms-full.txt` for AI/Agent integration
- Published structured documentation at `/docs/*.md`:
  - Quickstart guide with curl examples
  - Authentication (API Key)
  - Rate Limits (IETF RateLimit headers)
  - Problem Types (RFC 9457)
  - Metering & Billing rules
  - Pricing SSoT reference
- All documentation follows "short, accurate, no marketing fluff" principle

### [NEW] Runtime Endpoints

- **GET /.well-known/openapi.json**: OpenAPI 3.1.0 specification
- **GET /pricing/ssot.json**: Canonical pricing configuration (v0.2.1)

### [NEW] Static File Serving

- Enabled static file serving for `/llms.txt`, `/llms-full.txt`, and `/docs/*.md`
- Documentation served from `/public` directory

### [NEW] Documentation Tests

- Added link integrity tests for `/llms.txt`
- Added OpenAPI endpoint validation
- Added Pricing SSoT endpoint validation
- Added 429 Problem Details regression test

### Contract Guarantees

- OpenAPI version locked to 3.1.0
- RFC 9457 Problem Details for all errors
- IETF RateLimit headers on all responses
- Idempotent metering (45-day retention)
- Billable: 2xx + 422 only
- Non-billable: 400/401/403/404/409/412/413/415/429 + 5xx

---

## Previous Versions

### 2026-02-14.v0.2.1 (Pricing SSoT)

**Release Date**: 2026-02-14
**Effective From**: 2026-03-01

- Pricing version: 2026-02-14.v0.2.1
- Four tiers: SANDBOX, STARTER, GROWTH, ENTERPRISE
- Grace overage: min(1%, 100 DC)
- Idempotency retention: 45 days

### 2026-02-14.v0.1.0 (Initial Release)

**Release Date**: 2026-02-14

- Initial API release
- FastAPI-based platform
- RFC 9457 Problem Details
- Atomic idempotency (Redis SET NX EX)
