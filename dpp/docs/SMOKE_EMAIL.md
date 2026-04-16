# SMTP Smoke Test Guide

**목적**: Supabase Custom SMTP (AWS SES) 실발송 검증 (Dashboard 없이)

**방법**: 로컬 스크립트로 Supabase Auth 이메일 트리거 + SES 통계 delta 확인

---

## 📋 Overview

이 문서는 다음을 검증합니다:
1. Supabase Auth가 AWS SES SMTP를 통해 이메일을 발송하는지
2. SES 통계에서 발송 증가를 확인할 수 있는지
3. Maintenance Mode 예외 경로(`/internal/smoke/email`)가 동작하는지

**주의**: 이 테스트는 **실제 이메일 발송**을 트리거합니다. SES Sandbox 모드에서는 수신자도 Verified Identity여야 합니다.

---

## 🚀 Quick Start

### 1. 환경 설정

```bash
cd scripts
cp smoke_smtp_email.env.example smoke_smtp_email.env
```

`smoke_smtp_email.env` 파일 편집:
```bash
# Supabase (NEW Seoul project)
SUPABASE_URL="https://[your-ref].supabase.co"

# Supabase API Key (NEW standard, 2024+ UI)
SB_PUBLISHABLE_KEY="eyJhbGci..."

# LEGACY (optional, for backward compatibility)
# SUPABASE_ANON_KEY="eyJhbGci..."

# AWS SES
AWS_REGION="ap-northeast-2"
AWS_PROFILE="default"

# Test recipient (must be Verified if SES Sandbox)
TEST_RECIPIENT_EMAIL="test@example.com"

# Optional: Internal smoke endpoint
DP_INTERNAL_SMOKE_KEY="your-secret-key"
DP_API_BASE_URL="http://localhost:8000"
```

### 2. 스크립트 실행

```bash
bash scripts/smoke_smtp_email.sh
```

### 3. 결과 확인

**PASS**: SES send delta 증가 확인
```
✅ PASS: SES send delta detected (2)
✅ SMTP integration is working
```

**WARN**: Delta 미확인 (15분 대기 후 재시도)
```
⚠️  WARN: No SES send delta detected yet
⚠️  SES statistics have 15-minute bucket delay
```

---

## 🔧 Prerequisites

### 1. AWS CLI 설정
```bash
aws configure
# AWS Access Key ID: [your-key]
# AWS Secret Access Key: [your-secret]
# Default region name: ap-northeast-2
```

### 2. SES Verified Identity
SES Sandbox 모드인 경우:
- From 주소 (예: `noreply@decisionproof.ai`) → Verified
- Test recipient 주소 → Verified

확인 방법:
```bash
aws ses list-identities --region ap-northeast-2
```

### 3. Supabase Custom SMTP 설정
Supabase Dashboard에서 이미 설정 완료:
- SMTP Host: `email-smtp.ap-northeast-2.amazonaws.com`
- Port: `587` (STARTTLS)
- Username/Password: AWS SES SMTP Credentials

참조: `PHASE2_SUPABASE_DASHBOARD_SETUP.md`

---

## 📊 스크립트 동작 Flow

```
Step 1: Supabase Auth Health Check
  ↓
Step 2: SES Baseline Statistics (before)
  ↓
Step 3: Trigger Email
  - Option A: POST /internal/smoke/email (preferred during maintenance)
  - Option B: Direct Supabase Auth API (signup + resend)
  ↓
Step 4: SES After Statistics
  ↓
Step 5: PASS/WARN Logic
  - PASS: send delta > 0
  - WARN: delta not visible yet (15-min bucket delay)
```

---

## 🧪 Maintenance Mode Testing

Maintenance Mode는 `/internal/smoke/email`을 제외한 모든 엔드포인트를 503으로 차단합니다.

### 1. Maintenance Mode 활성화

```bash
export DP_MAINTENANCE_MODE=1
export DP_INTERNAL_SMOKE_KEY="test-secret-key-123"
```

API 서버 재시작:
```bash
cd apps/api
uvicorn dpp_api.main:app --reload --port 8000
```

### 2. 일반 엔드포인트 차단 확인

```bash
curl -i http://localhost:8000/v1/runs
# Expected: HTTP/1.1 503 Service Unavailable
# Content-Type: application/problem+json
```

응답 예시:
```json
{
  "type": "https://api.decisionproof.ai/problems/maintenance",
  "title": "Service Unavailable",
  "status": 503,
  "detail": "Decisionproof is in maintenance mode.",
  "instance": "urn:decisionproof:trace:..."
}
```

### 3. Allowlist 경로 허용 확인

```bash
# Health endpoint (allowlist)
curl -i http://localhost:8000/health
# Expected: HTTP/1.1 200 OK

# Internal smoke endpoint (allowlist)
curl -i http://localhost:8000/internal/smoke/email \
  -X POST \
  -H "X-Internal-Smoke-Key: test-secret-key-123" \
  -H "Content-Type: application/json" \
  -d '{"recipient_email":"test@example.com","mode":"signup+resend"}'
# Expected: HTTP/1.1 200 OK
```

### 4. Secret Header 보호 확인

```bash
# Without header
curl -i http://localhost:8000/internal/smoke/email \
  -X POST \
  -H "Content-Type: application/json" \
  -d '{"recipient_email":"test@example.com"}'
# Expected: HTTP/1.1 401 Unauthorized

# Wrong key
curl -i http://localhost:8000/internal/smoke/email \
  -X POST \
  -H "X-Internal-Smoke-Key: wrong-key" \
  -H "Content-Type: application/json" \
  -d '{"recipient_email":"test@example.com"}'
# Expected: HTTP/1.1 401 Unauthorized
```

### 5. Rate Limiting 확인

```bash
# First call
curl http://localhost:8000/internal/smoke/email \
  -X POST \
  -H "X-Internal-Smoke-Key: test-secret-key-123" \
  -H "Content-Type: application/json" \
  -d '{"recipient_email":"test@example.com"}'
# Expected: HTTP/1.1 200 OK

# Immediate second call
curl http://localhost:8000/internal/smoke/email \
  -X POST \
  -H "X-Internal-Smoke-Key: test-secret-key-123" \
  -H "Content-Type: application/json" \
  -d '{"recipient_email":"test@example.com"}'
# Expected: HTTP/1.1 429 Too Many Requests
```

---

## 🔍 Troubleshooting

### 문제 1: "WARN: No SES send delta detected"

**원인**: SES 통계는 15분 bucket 단위로 집계됨

**해결**:
1. 15분 대기 후 재실행
2. SES Dashboard에서 수동 확인:
   ```
   AWS Console → SES → Sending Statistics
   ```
3. 또는 직접 확인:
   ```bash
   aws ses get-send-statistics --region ap-northeast-2 | jq '.SendDataPoints[-5:]'
   ```

---

### 문제 2: "Signup API call returned HTTP 422"

**원인**: 이메일 형식 오류 또는 Supabase 설정 문제

**해결**:
1. `TEST_RECIPIENT_EMAIL` 형식 확인 (valid email)
2. Supabase Dashboard → Authentication → Providers → Email: ON
3. Supabase Dashboard → Authentication → SMTP Settings: Custom SMTP ON

---

### 문제 3: "SES Sandbox recipient not verified"

**원인**: SES Sandbox 모드에서 수신자가 Verified Identity가 아님

**해결**:
1. SES Console → Verified Identities → Create identity
2. Email address 선택 → `TEST_RECIPIENT_EMAIL` 입력
3. 검증 이메일 확인 → 링크 클릭
4. Status가 "Verified"로 변경될 때까지 대기

또는 SES Mailbox Simulator 사용:
```bash
TEST_RECIPIENT_EMAIL="success@simulator.amazonses.com"
```

---

### 문제 4: "Failed to get SES statistics (access denied)"

**원인**: AWS CLI credentials 부족 또는 SES 권한 없음

**해결**:
1. AWS CLI 설정 확인:
   ```bash
   aws sts get-caller-identity
   ```
2. SES 권한 확인 (IAM Policy):
   ```json
   {
     "Effect": "Allow",
     "Action": [
       "ses:GetSendStatistics",
       "ses:ListIdentities"
     ],
     "Resource": "*"
   }
   ```

---

### 문제 5: Redirect URL mismatch

**현상**: 이메일 링크 클릭 시 "Invalid Redirect URL" 에러

**원인**: Supabase Dashboard의 Redirect URLs allow list에 등록되지 않음

**해결**:
1. Supabase Dashboard → Authentication → URL Configuration
2. Redirect URLs에 추가:
   - `http://localhost:8000/v1/auth/confirmed` (로컬)
   - `{PRODUCTION_URL}/v1/auth/confirmed` (프로덕션)

**참고**: 이 에러는 이메일 **발송**에는 영향 없음. 발송은 성공하고 SES 통계에 나타남.

---

## 📋 Expected Output (PASS)

```
ℹ️  Step 1: Supabase Auth Health Check
✅ Supabase Auth health OK (version: v2.143.0)

ℹ️  Step 2: Capturing SES baseline statistics
✅ SES baseline captured (12 datapoints)

ℹ️  Step 3: Triggering email via Supabase Auth API
ℹ️    Using /internal/smoke/email endpoint
✅ Email trigger successful: HTTP 200
{
  "supabase_auth": {
    "health_version": "v2.143.0",
    "health_ok": true
  },
  "actions": [
    {"name": "signup", "http_status": 200, "ok": true},
    {"name": "resend_signup", "http_status": 200, "ok": true}
  ],
  "note": "SES metrics are 15-min buckets; run the local smoke script to confirm send delta."
}

ℹ️  Step 4: Capturing SES statistics after trigger (15-min bucket delay)
✅ SES after statistics captured (12 datapoints)

ℹ️  Step 5: Analyzing SES statistics delta

════════════════════════════════════════════════════════════════
  SMTP Smoke Test Results
════════════════════════════════════════════════════════════════

  Supabase Auth Health:   ✅ OK (version: v2.143.0)
  Email Trigger:          ✅ API calls succeeded

  SES Statistics:
    Before:  10 sends (max in recent buckets)
    After:   12 sends (max in recent buckets)
    Delta:   2

✅ PASS: SES send delta detected (2)

  ✅ SMTP integration is working
  ✅ AWS SES is sending emails via Supabase Custom SMTP

════════════════════════════════════════════════════════════════

  📧 Manual inbox check:
     Check test@example.com inbox (and spam folder)
     Subject: "Confirm your signup" (Supabase default)

  📊 SES statistics files:
     Before: /tmp/dpp_smoke_smtp_12345/ses_before.json
     After:  /tmp/dpp_smoke_smtp_12345/ses_after.json

════════════════════════════════════════════════════════════════
```

---

## 🔑 Security Notes

1. **Secret Management**:
   - `DP_INTERNAL_SMOKE_KEY`: Never commit to git
   - `SUPABASE_ANON_KEY`: Public key (OK to expose, but minimize)
   - AWS credentials: Use AWS CLI profiles, never hardcode

2. **Rate Limiting**:
   - `/internal/smoke/email`: 1 request per minute
   - Prevents abuse during maintenance mode

3. **Maintenance Mode Allowlist**:
   - Hardcoded: `/health`, `/readyz`, `/internal/smoke/email`
   - Custom: `DP_MAINTENANCE_ALLOWLIST` env var

4. **Logging**:
   - No secrets logged
   - Email addresses logged only in dev mode
   - SES statistics: public (no PII)

---

## 📚 References

- AWS SES `get-send-statistics`: [AWS CLI Docs](https://awscli.amazonaws.com/v2/documentation/api/latest/reference/ses/get-send-statistics.html)
- Supabase Auth API: [Supabase Docs](https://supabase.com/docs/reference/auth)
- Supabase Auth health: [GET /auth/v1/health](https://supabase.com/docs/reference/auth/health)
- Supabase Auth resend: [Official API](https://supabase.com/docs/reference/javascript/auth-resend)

---

**문서 버전**: v1.0
**최종 업데이트**: 2026-02-17
**작성자**: Claude (DPP DevOps Agent)
