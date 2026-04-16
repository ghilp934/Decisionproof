# P0-1: Paid Pilot Scorecard & Kill Switch Specification

**Version**: v0.1
**Date**: 2026-02-18
**Timezone**: Asia/Seoul (KST)
**Purpose**: Lock success criteria + kill switch for Paid Pilot operation

---

## 1. Scorecard Metrics

All time windows are calculated in **KST (Asia/Seoul)** timezone.

| Metric ID | Metric Name | Definition | Numerator / Denominator | Time Window | Target | Redline | Action on Breach |
|-----------|-------------|------------|------------------------|-------------|--------|---------|------------------|
| SC-01 | Payment Success Rate | Successful payments / Total payment attempts | payment_success_total / payment_attempt_total | 24h | ≥98% | <90% (consecutive 1h) | SAFE_MODE |
| SC-02 | Dispute/Chargeback Count | Number of disputes or chargebacks | dispute_count + chargeback_count | 7d | 0 | ≥1 | HARD_STOP + Stop new onboarding |
| SC-03 | Refund Rate | Refund requests / Total completed payments | refund_total / payment_success_total | 14d | ≤5% | >15% | SAFE_MODE + Stop new onboarding |
| SC-04 | 5xx Error Rate | 5xx responses / Total requests | request_5xx_total / request_total | 30m | ≤0.5% | ≥2% (consecutive 15m) | SAFE_MODE |
| SC-05 | P95 Latency | 95th percentile request latency | p95(request_latency_ms) | 30m | ≤800ms | ≥2000ms (consecutive 15m) | SAFE_MODE |
| SC-06 | Rate Limit Hit Rate | 429 responses / Total requests | rate_limited_total / request_total | 24h | ≤5% | ≥20% | SAFE_MODE (rate limit/plan adjustment) |
| SC-07 | API Key Leak Suspicion | Unique IPs per key_id | count_distinct(ip) per key_id | 24h | ≤5 | ≥20 | Revoke key + HARD_STOP (for account) |
| SC-08 | Support Tickets / Paid Accounts | Support tickets / Active paid accounts | support_ticket_total / active_paid_accounts | 7d | ≤1.0 | ≥3.0 | Stop new onboarding |

**Notes**:
- **SC-02 (Dispute/Chargeback)**: Zero tolerance policy. Any dispute triggers immediate investigation + HARD_STOP.
- **SC-07 (Key Leak)**: Monitors for abnormal key usage patterns. Threshold of 20 unique IPs indicates potential leak.
- **SC-08 (Support Tickets)**: Placeholder metric. Mark as OPEN if no ticketing system exists yet.

---

## 2. Kill Switch Modes

### 2.1 NORMAL
**Description**: Normal operation. All features enabled.

**Behavior**:
- All endpoints operational
- No restrictions

**Entry Conditions**:
- Default startup mode
- Manual reset via admin API
- Automatic restore after ttl expiration

---

### 2.2 SAFE_MODE
**Description**: Defensive mode. Restricts high-risk operations while maintaining core service.

**Behavior**:
- ✅ **Allowed**:
  - Health checks (`/health`, `/readyz`)
  - Status endpoints (`/v1/runs/{run_id}` GET)
  - Existing API key usage (read-only operations)
- ❌ **Blocked** (Returns 503 with RFC9457 Problem Details):
  - New onboarding (`/v1/auth/signup`)
  - New API key issuance (`/v1/keys` POST)
  - Plan upgrades (`/v1/plans/upgrade`)
  - High-cost exports (if implemented)
  - Large batch jobs (if implemented)

**Entry Conditions**:
- SC-01: Payment success rate <90% (1h consecutive)
- SC-03: Refund rate >15% (14d window)
- SC-04: 5xx error rate ≥2% (15m consecutive)
- SC-05: P95 latency ≥2000ms (15m consecutive)
- SC-06: Rate limit hit rate ≥20% (24h window)
- Manual trigger via admin API

**Exit Conditions**:
- Manual reset to NORMAL via admin API
- Automatic restore after ttl expiration (if ttl set)

---

### 2.3 HARD_STOP
**Description**: Emergency shutdown. Only essential endpoints remain operational.

**Behavior**:
- ✅ **Allowed**:
  - Health checks (`/health`, `/readyz`)
  - Status checks (`/status`)
- ❌ **Blocked** (Returns 503 with RFC9457 Problem Details):
  - All payment operations
  - All API key operations (issue/revoke)
  - All run creation (`/v1/runs` POST)
  - All onboarding/auth operations

**Entry Conditions**:
- SC-02: Any dispute or chargeback detected
- SC-07: API key leak suspected (≥20 unique IPs per key in 24h)
- Manual trigger via admin API (emergency response)

**Exit Conditions**:
- Manual reset to NORMAL or SAFE_MODE via admin API (after investigation)
- **No automatic restore** (requires human intervention)

---

## 3. Admin API Operations

### 3.1 Authentication
**Method**: Header-based token authentication

**Header**: `X-Admin-Token`

**Validation**:
- Compare with `ADMIN_TOKEN` environment variable
- Use constant-time comparison (`secrets.compare_digest()`)
- Return 401 Unauthorized on mismatch

---

### 3.2 POST /admin/kill-switch
**Purpose**: Set kill switch mode

**Request Body**:
```json
{
  "mode": "NORMAL|SAFE_MODE|HARD_STOP",
  "reason": "Brief explanation (max 200 chars)",
  "ttl_minutes": 0
}
```

**Parameters**:
- `mode` (required): Target kill switch mode
- `reason` (required): Audit trail explanation
- `ttl_minutes` (optional): Auto-restore to NORMAL after N minutes (0 = no auto-restore, only for SAFE_MODE)

**Response** (200 OK):
```json
{
  "mode": "SAFE_MODE",
  "reason": "High refund rate detected",
  "set_at": "2026-02-18T14:30:00+09:00",
  "ttl_minutes": 60,
  "expires_at": "2026-02-18T15:30:00+09:00"
}
```

**Audit Log Fields** (structured log):
- `event`: "kill_switch.mode_changed"
- `request_id`: Request tracking ID
- `actor_ip`: Client IP address
- `mode_from`: Previous mode
- `mode_to`: New mode
- `reason`: Provided reason
- `ttl_minutes`: TTL value
- `timestamp_kst`: Current time in KST

---

### 3.3 GET /admin/kill-switch
**Purpose**: Get current kill switch state

**Response** (200 OK):
```json
{
  "mode": "SAFE_MODE",
  "reason": "High refund rate detected",
  "set_at": "2026-02-18T14:30:00+09:00",
  "set_by_ip": "192.168.1.100",
  "ttl_minutes": 60,
  "expires_at": "2026-02-18T15:30:00+09:00"
}
```

---

## 4. Observability Metrics

### 4.1 Required Metrics (Minimum)
Log the following metrics using existing structured logging infrastructure:

| Metric | Log Event | Fields | Notes |
|--------|-----------|--------|-------|
| Payment Attempts | `payment.attempt` | `tenant_id`, `amount_usd`, `status` | Increment payment_attempt_total |
| Payment Success | `payment.success` | `tenant_id`, `amount_usd` | Increment payment_success_total |
| Request Count | `http.request.completed` | `method`, `path`, `status_code` | Already logged by middleware |
| 5xx Errors | `http.request.completed` | `status_code >= 500` | Filter from existing logs |
| Request Latency | `http.request.completed` | `duration_ms` | Already logged by middleware |
| Rate Limited | `rate_limit.exceeded` | `tenant_id`, `key_id_prefix` | Increment rate_limited_total |
| Support Tickets | `support.ticket.created` | `tenant_id`, `ticket_id` | Placeholder (mark OPEN if not implemented) |
| Key Leak Suspicion | `security.key_leak_suspected` | `key_id_hash`, `unique_ip_count` | Log when unique_ip_count >= 20 in 24h |

**Security Rules**:
- ✅ **Log**: `key_id` (first 8 chars or hash), `tenant_id`, IP address
- ❌ **NEVER log**: Full API key value, secrets, passwords

---

## 5. Operational Procedures

### 5.1 Who Can Trigger Kill Switch?
- **Authorized Roles**: Operations team, On-call engineer, CTO
- **Authentication**: `ADMIN_TOKEN` environment variable (rotate quarterly)

### 5.2 How to Trigger (Manual)
```bash
# Set SAFE_MODE with 1-hour auto-restore
curl -X POST https://api.decisionproof.ai/admin/kill-switch \
  -H "X-Admin-Token: $ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "mode": "SAFE_MODE",
    "reason": "High 5xx rate detected during deployment",
    "ttl_minutes": 60
  }'

# Set HARD_STOP (no auto-restore)
curl -X POST https://api.decisionproof.ai/admin/kill-switch \
  -H "X-Admin-Token: $ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "mode": "HARD_STOP",
    "reason": "Chargeback dispute received - investigating",
    "ttl_minutes": 0
  }'

# Restore to NORMAL
curl -X POST https://api.decisionproof.ai/admin/kill-switch \
  -H "X-Admin-Token: $ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "mode": "NORMAL",
    "reason": "Issue resolved - resuming normal operations",
    "ttl_minutes": 0
  }'
```

### 5.3 How to Check Current State
```bash
curl -X GET https://api.decisionproof.ai/admin/kill-switch \
  -H "X-Admin-Token: $ADMIN_TOKEN"
```

### 5.4 Record Keeping
All kill switch mode changes are automatically logged with:
- Timestamp (KST)
- Actor IP address
- Mode transition (from → to)
- Reason
- Request ID for correlation

**Log Retention**: Minimum 90 days (compliance requirement)

---

## 6. Implementation Checklist

- [x] Document created (this file)
- [ ] `config/kill_switch.yaml` created (default: NORMAL)
- [ ] `config/kill_switch.py` loader implemented (env override support)
- [ ] `routers/admin.py` created (POST/GET endpoints)
- [ ] `middleware/kill_switch.py` enforcement logic
- [ ] Admin authentication with `X-Admin-Token` header
- [ ] Audit logging for mode changes
- [ ] Observability metrics instrumentation
- [ ] Unit tests (5 minimum)
- [ ] Integration test with admin API
- [ ] Local execution guide (README update)

---

## 7. Open Issues

- [ ] **SC-08 (Support Tickets)**: No ticketing system yet. Mark metric as OPEN.
- [ ] **TTL Auto-Restore**: Requires background task or cron job for expiration check.
- [ ] **Metric Aggregation**: Current implementation uses log-based metrics. Consider time-series DB for production.

---

## 8. References

- **RFC 9457**: Problem Details for HTTP APIs
- **IETF RateLimit Headers**: draft-ietf-httpapi-ratelimit-headers
- **DPP Auth Patterns**: `apps/api/dpp_api/auth/api_key.py`
- **DPP Error Patterns**: `apps/api/dpp_api/pricing/problem_details.py`

---

**Document Owner**: Operations Team
**Review Cycle**: Monthly during Paid Pilot phase
