# Kill-Switch WORM Audit Runbook

**Phase**: P5.3
**Status**: Active
**Owner**: Ops / SRE

---

## Overview

Every kill-switch state change writes an immutable audit record to S3 (Object Lock,
GOVERNANCE mode) **before** the state is mutated. This ensures a tamper-evident trail
even if the application is compromised after the fact.

---

## S3 Bucket Requirements

| Requirement | Value |
|---|---|
| Object Lock | **Enabled** (must be set at bucket creation) |
| Lock mode | GOVERNANCE (allows lock-bypass with special IAM permission) |
| Retention period | **7 years** (2555 days) |
| Versioning | Must be enabled (Object Lock requires versioning) |
| Encryption | SSE-S3 minimum (SSE-KMS recommended for regulated environments) |

### Terraform snippet (informational)

```hcl
resource "aws_s3_bucket" "kill_switch_audit" {
  bucket = "dpp-kill-switch-audit-prod"

  object_lock_enabled = true
}

resource "aws_s3_bucket_versioning" "kill_switch_audit" {
  bucket = aws_s3_bucket.kill_switch_audit.id
  versioning_configuration { status = "Enabled" }
}

resource "aws_s3_bucket_object_lock_configuration" "kill_switch_audit" {
  bucket = aws_s3_bucket.kill_switch_audit.id
  rule {
    default_retention {
      mode = "GOVERNANCE"
      days = 2555
    }
  }
}
```

---

## IAM Permissions Required

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "s3:PutObject",
        "s3:PutObjectRetention"
      ],
      "Resource": "arn:aws:s3:::dpp-kill-switch-audit-prod/*"
    }
  ]
}
```

---

## Environment Variables

| Variable | Required | Description |
|---|---|---|
| `KILL_SWITCH_AUDIT_BUCKET` | **Yes** (prod) | S3 bucket name with Object Lock |
| `KILL_SWITCH_AUDIT_REGION` | No | AWS region (falls back to `AWS_DEFAULT_REGION`) |
| `KILL_SWITCH_AUDIT_FILE_DIR` | No | Local fallback directory (CI / dev only) |
| `KILL_SWITCH_AUDIT_STRICT` | No | `1` = fail-closed (block change if audit write fails) |

---

## STRICT Mode (`KILL_SWITCH_AUDIT_STRICT=1`)

When `STRICT=1`:

1. Audit record write is attempted **before** state change.
2. If write fails → `HTTP 500 KILL_SWITCH_AUDIT_SINK_FAILED` is returned.
3. Kill-switch state is **not changed**.
4. Operator must resolve the audit sink issue before retrying.

**Recommended**: Enable `STRICT=1` in production for maximum compliance.

### Diagnosing STRICT failures

```bash
# Check S3 connectivity
aws s3 ls s3://${KILL_SWITCH_AUDIT_BUCKET}/ --profile dpp-admin

# Check recent audit records
aws s3 ls s3://${KILL_SWITCH_AUDIT_BUCKET}/kill-switch/ --profile dpp-admin --recursive

# Verify Object Lock is enabled
aws s3api get-object-lock-configuration \
  --bucket ${KILL_SWITCH_AUDIT_BUCKET} \
  --profile dpp-admin
```

---

## Audit Record Schema

```json
{
  "schema_version": "1.0",
  "timestamp": "2026-02-20T12:34:56.789Z",
  "request_id": "req-uuid-...",
  "actor": {
    "token_fingerprint": "abc123def456",
    "ip_hash": "9f8e7d6c5b4a"
  },
  "change": {
    "mode_from": "NORMAL",
    "mode_to": "SAFE_MODE",
    "reason": "Suspected abuse spike",
    "ttl_minutes": 30
  },
  "result": "ok",
  "error": null
}
```

**Note**: Raw IP addresses and tokens are **never** stored. Only 12-character sha256
fingerprints are recorded for operator correlation without exposing credentials.

---

## Retrieving Audit Records

```bash
# List all audit records for a date range
aws s3 ls s3://${KILL_SWITCH_AUDIT_BUCKET}/kill-switch/ \
  --profile dpp-admin \
  --recursive | grep "2026-02-"

# Download a specific record
aws s3 cp s3://${KILL_SWITCH_AUDIT_BUCKET}/kill-switch/2026-02-20T12_34_56.789Z-req-uuid.json \
  ./audit_record.json \
  --profile dpp-admin
```

---

## Escalation Path

| Condition | Action |
|---|---|
| `KILL_SWITCH_AUDIT_SINK_FAILED` in logs | Check IAM permissions + bucket Object Lock config |
| Bucket missing or inaccessible | Create bucket with Object Lock before re-enabling STRICT mode |
| Need to emergency-change kill-switch without audit | Set `STRICT=0` temporarily; document in incident report |
| Compliance audit request | Retrieve records from S3; records retain for 7 years |
