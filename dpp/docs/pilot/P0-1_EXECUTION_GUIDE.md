# P0-1 Kill Switch: Execution Guide

**Version**: v0.1
**Date**: 2026-02-18

---

## Prerequisites

- Python 3.11+
- PostgreSQL (for tests)
- Redis
- Environment variables configured

---

## Environment Setup

### Required Environment Variables

```bash
# Admin authentication
export ADMIN_TOKEN="your-secure-admin-token-here"

# Database (already configured)
export DATABASE_URL="postgresql://user:pass@localhost:5432/dpp"

# Redis (already configured)
export REDIS_HOST="localhost"
export REDIS_PORT="6379"

# AWS (already configured)
export S3_RESULT_BUCKET="dpp-results"
export SQS_QUEUE_URL="https://sqs.us-east-1.amazonaws.com/123/dpp-runs"

# Optional: Override kill switch mode at startup
# export KILL_SWITCH_MODE="NORMAL"  # NORMAL | SAFE_MODE | HARD_STOP
```

### Generate Secure Admin Token

```bash
# Generate a secure random token (recommended)
python3 -c "import secrets; print(secrets.token_urlsafe(32))"
```

---

## How to Run Locally

### 1. Install Dependencies

```bash
cd apps/api
pip install -r requirements.txt
```

### 2. Set Admin Token

```bash
export ADMIN_TOKEN="your-secure-token-here"
```

### 3. Start API Server

```bash
# From dpp/ directory
uvicorn dpp_api.main:app --reload --host 0.0.0.0 --port 8000
```

Server will start at: `http://localhost:8000`

---

## Testing Kill Switch

### Check Current State

```bash
curl -X GET http://localhost:8000/admin/kill-switch \
  -H "X-Admin-Token: $ADMIN_TOKEN"
```

**Expected Response** (200 OK):
```json
{
  "mode": "NORMAL",
  "reason": "Default configuration",
  "set_at": null,
  "set_by_ip": null,
  "ttl_minutes": 0,
  "expires_at": null
}
```

---

### Set SAFE_MODE

```bash
curl -X POST http://localhost:8000/admin/kill-switch \
  -H "X-Admin-Token: $ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "mode": "SAFE_MODE",
    "reason": "Testing SAFE_MODE restrictions",
    "ttl_minutes": 60
  }'
```

**Expected Response** (200 OK):
```json
{
  "mode": "SAFE_MODE",
  "reason": "Testing SAFE_MODE restrictions",
  "set_at": "2026-02-18T14:30:00+09:00",
  "set_by_ip": "127.0.0.1",
  "ttl_minutes": 60,
  "expires_at": "2026-02-18T15:30:00+09:00"
}
```

---

### Test SAFE_MODE Enforcement

Try to access blocked endpoint:

```bash
curl -X POST http://localhost:8000/v1/keys \
  -H "Authorization: Bearer sk_test_secret123" \
  -H "Content-Type: application/json" \
  -d '{"label": "Test Key"}'
```

**Expected Response** (503 Service Unavailable):
```json
{
  "type": "https://api.decisionproof.ai/problems/kill-switch-active",
  "title": "Service Unavailable (SAFE_MODE)",
  "status": 503,
  "detail": "Service is in SAFE_MODE. High-risk operations...",
  "instance": "urn:decisionproof:trace:..."
}
```

---

### Set HARD_STOP

```bash
curl -X POST http://localhost:8000/admin/kill-switch \
  -H "X-Admin-Token: $ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "mode": "HARD_STOP",
    "reason": "Emergency testing",
    "ttl_minutes": 0
  }'
```

Note: HARD_STOP ignores `ttl_minutes` (manual intervention required).

---

### Restore to NORMAL

```bash
curl -X POST http://localhost:8000/admin/kill-switch \
  -H "X-Admin-Token: $ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "mode": "NORMAL",
    "reason": "Testing complete - restoring normal operations",
    "ttl_minutes": 0
  }'
```

---

## Running Tests

### Run All Kill Switch Tests

```bash
# From apps/api directory
pytest tests/unit/test_kill_switch.py -v
```

**Expected Output**:
```
tests/unit/test_kill_switch.py::test_safe_mode_blocks_key_issuance PASSED
tests/unit/test_kill_switch.py::test_safe_mode_allows_health_checks PASSED
tests/unit/test_kill_switch.py::test_hard_stop_blocks_general_endpoints PASSED
tests/unit/test_kill_switch.py::test_hard_stop_allows_health_checks PASSED
tests/unit/test_kill_switch.py::test_hard_stop_allows_admin_endpoints PASSED
tests/unit/test_kill_switch.py::test_admin_auth_missing_token PASSED
tests/unit/test_kill_switch.py::test_admin_auth_invalid_token PASSED
tests/unit/test_kill_switch.py::test_admin_auth_valid_token PASSED
tests/unit/test_kill_switch.py::test_ttl_auto_restore PASSED
tests/unit/test_kill_switch.py::test_ttl_zero_no_auto_restore PASSED
tests/unit/test_kill_switch.py::test_hard_stop_ignores_ttl PASSED
tests/unit/test_kill_switch.py::test_audit_log_records_mode_change PASSED
tests/unit/test_kill_switch.py::test_audit_log_includes_request_id PASSED
tests/unit/test_kill_switch.py::test_state_to_kst_display PASSED

==================== 14 passed in 2.34s ====================
```

### Run Specific Test

```bash
pytest tests/unit/test_kill_switch.py::test_safe_mode_blocks_key_issuance -v
```

---

## Troubleshooting

### Issue: "ADMIN_TOKEN not configured on server"

**Solution**: Set `ADMIN_TOKEN` environment variable:
```bash
export ADMIN_TOKEN="your-secure-token"
```

---

### Issue: Tests fail with database connection error

**Solution**: Ensure PostgreSQL is running and DATABASE_URL is correct:
```bash
# Check PostgreSQL status
pg_isready -h localhost -p 5432

# Verify DATABASE_URL
echo $DATABASE_URL
```

---

### Issue: Redis connection error

**Solution**: Ensure Redis is running:
```bash
# Check Redis status
redis-cli ping
# Expected: PONG
```

---

### Issue: Kill switch not enforcing restrictions

**Solution**: Verify middleware is loaded. Check logs for:
```
Kill switch mode loaded from environment: SAFE_MODE
```

If not present, restart server with correct `KILL_SWITCH_MODE` env var.

---

## Observability: Viewing Audit Logs

### Structured JSON Logs (Production)

```bash
# View kill switch mode changes
cat logs/app.log | jq 'select(.event == "admin.kill_switch.set")'
```

**Example Output**:
```json
{
  "timestamp": "2026-02-18T14:30:00+09:00",
  "level": "INFO",
  "event": "admin.kill_switch.set",
  "request_id": "abc-123-def",
  "actor_ip": "192.168.1.100",
  "mode_from": "NORMAL",
  "mode_to": "SAFE_MODE",
  "reason": "High refund rate detected",
  "ttl_minutes": 60
}
```

---

### Standard Logs (Development)

```bash
# Tail logs for kill switch events
tail -f logs/app.log | grep "kill_switch"
```

---

## Production Deployment Checklist

- [ ] Set `ADMIN_TOKEN` in production environment (rotate quarterly)
- [ ] Verify kill switch middleware is loaded in `main.py`
- [ ] Test admin API authentication with production token
- [ ] Verify structured JSON logging is enabled (`DPP_JSON_LOGS=true`)
- [ ] Configure log retention for 90+ days (compliance)
- [ ] Set up alerting for `kill_switch.mode_changed` events
- [ ] Document kill switch procedures in runbook
- [ ] Train on-call engineers on kill switch usage

---

## Next Steps

1. **Metric Aggregation**: Implement time-series metrics for Scorecard thresholds
2. **Auto-Trigger**: Connect Scorecard metrics to kill switch auto-trigger
3. **Dashboard**: Build real-time Scorecard monitoring dashboard
4. **Alerting**: PagerDuty integration for HARD_STOP events

---

**Document Owner**: Operations Team
**Last Updated**: 2026-02-18
