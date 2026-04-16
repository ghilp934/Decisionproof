# Supabase Region Migration - Quick Checklist

**프로젝트**: Decisionproof_Paid_Pilot (AP Southeast 2) → Decisionproof (Seoul)
**작업자**: __________________
**날짜**: __________________

---

## ✅ Pre-Migration (D-7)

- [ ] **NEW 프로젝트 생성 확인**
  - Region: Seoul (ap-northeast-2) ✓
  - Plan: Pro (or higher)
  - Project Name: Decisionproof

- [ ] **마이그레이션 팀 구성**
  - DevOps Lead: __________________
  - Backend Engineer: __________________
  - QA Engineer: __________________

- [ ] **백업 정책 승인**
  - 승인자: __________________
  - 백업 보관 기간: 30 days
  - S3 아카이브 버킷: __________________

- [ ] **다운타임 공지**
  - 공지일: __________________
  - 예상 다운타임: 2-4 hours
  - 영향받는 사용자: __________________

---

## ✅ D-Day 준비 (작업 당일)

### 1. 환경변수 파일 준비

- [ ] `migration_env.sh` 파일 생성 완료
- [ ] OLD_PROJECT_REF 확인: __________________
- [ ] NEW_PROJECT_REF 확인: __________________
- [ ] JWT_STRATEGY 결정: `FORCE_RELOGIN` / `KEEP_SESSIONS`
- [ ] 민감정보 보안 확인 (password manager 저장)

### 2. Maintenance Mode 진입

- [ ] 사용자 공지 발송 (30분 전)
- [ ] API 헬스체크 확인: `curl https://api.decisionproof.com/health`
- [ ] Kubernetes Deployment replicas=0
  ```bash
  kubectl scale deployment -n dpp-production dpp-api --replicas=0
  kubectl scale deployment -n dpp-production dpp-worker --replicas=0
  kubectl scale deployment -n dpp-production dpp-reaper --replicas=0
  ```
- [ ] 모든 Pod 종료 확인: `kubectl get pods -n dpp-production`
- [ ] 마지막 트랜잭션 완료 대기 (5분)

### 3. OLD 프로젝트 인벤토리 스냅샷

Supabase Dashboard 확인:

- [ ] Extensions 목록: __________________ *(예: uuid-ossp, pgcrypto)*
- [ ] Storage Buckets: __________________ *(예: user-uploads, results)*
- [ ] Edge Functions: __________________ *(예: process-payment)*
- [ ] Auth Providers: __________________ *(예: Email, Google OAuth)*
- [ ] Auth Redirect URLs: __________________
- [ ] Database Webhooks: __________________
- [ ] Realtime Publications: __________________

---

## ✅ Migration Execution

### Phase 1: DB Backup (OLD)

```bash
# Execute master script
bash ops/scripts/execute_supabase_migration.sh ops/scripts/migration_env.sh
```

실행 중 확인:

- [ ] roles.sql 생성 완료 (Size: _______ KB)
- [ ] schema.sql 생성 완료 (Size: _______ KB)
- [ ] data.sql 생성 완료 (Size: _______ KB)
- [ ] checksums.txt 생성 완료

백업 파일 검증:

- [ ] `ls -lh supabase_migration_backups/[TIMESTAMP]/*.sql`
- [ ] 모든 파일 크기 > 0 bytes
- [ ] SHA256 체크섬 확인

---

### Phase 2: DB Restore (NEW)

복원 진행 전 최종 확인:

- [ ] NEW 프로젝트 리전: **ap-northeast-2** (Seoul)
- [ ] NEW_DB_URL 올바른지 재확인
- [ ] Extensions 사전 활성화 (uuid-ossp, pgcrypto 등)

복원 실행:

- [ ] Roles 복원 완료
- [ ] Schema 복원 완료
- [ ] Data 복원 완료 (with RLS bypass)
- [ ] Migration history 복원 완료 (옵션)

데이터 검증:

- [ ] tenants 테이블 레코드 수: _______ (OLD와 일치)
- [ ] runs 테이블 레코드 수: _______ (OLD와 일치)
- [ ] api_keys 테이블 레코드 수: _______ (OLD와 일치)
- [ ] auth.users 테이블 레코드 수: _______ (OLD와 일치)

---

### Phase 3: Storage Objects Migration

실행 (자동):

- [ ] Storage 버킷 생성 완료
- [ ] Objects 마이그레이션 시작 시간: __________
- [ ] Objects 마이그레이션 완료 시간: __________
- [ ] 총 객체 수: _______
- [ ] 성공: _______
- [ ] 실패: _______

검증:

- [ ] `storage_migration.log` 파일 확인
- [ ] 실패 객체 수 = 0 또는 재시도 완료

---

### Phase 4: Manual Settings

NEW 프로젝트 Supabase Dashboard 설정:

- [ ] **Extensions 활성화**
  - Extensions → 필요한 extensions enable
  - 완료 시간: __________

- [ ] **Auth Providers 설정**
  - Email provider 활성화
  - Google OAuth (Client ID/Secret 입력)
  - 기타: __________________
  - 완료 시간: __________

- [ ] **Auth Redirect URLs**
  - Redirect URLs 추가: __________________
  - 완료 시간: __________

- [ ] **Database Webhooks 재생성** (있는 경우)
  - Webhook: __________________ 생성 완료
  - 완료 시간: __________

- [ ] **Realtime Settings**
  - Publication 설정: __________________
  - 완료 시간: __________

---

## ✅ Cutover (앱 환경변수 교체)

### 1. Kubernetes Secrets 업데이트 (Phase 4.0: ASCP 방식)

> ⚠️ **Phase 4.0 이후 변경**: `kubectl create secret` 방식은 폐기되었습니다.
> `database-url` 변경은 AWS Secrets Manager에서 직접 수행하십시오.
> 변경 후 Pod rollout restart 시 ASCP가 자동 반영합니다.
> 상세: `ops/runbooks/secrets_ascp_irsa.md`

```bash
# AWS Secrets Manager에서 database-url 값 업데이트 (콘솔 권장)
# CLI 확인 (값 출력 없이 메타데이터만)
aws secretsmanager describe-secret \
  --secret-id "decisionproof/prod/dpp-secrets" \
  --region ap-northeast-2 \
  --profile dpp-admin \
  --query "[Name, LastChangedDate]"

# ASCP sync: Pod rollout restart
kubectl rollout restart deployment/dpp-api deployment/dpp-worker deployment/dpp-reaper \
  -n dpp-production

# 확인 (값 출력 없이 메타데이터만)
kubectl get secret dpp-secrets -n dpp-production
```

- [ ] AWS Secrets Manager database-url 업데이트 완료
- [ ] Pod rollout restart 완료
- [ ] dpp-secrets Secret 존재 확인 (값 출력 없이)

### 2. Deployment 재시작

```bash
kubectl scale deployment -n dpp-production dpp-api --replicas=2
kubectl scale deployment -n dpp-production dpp-worker --replicas=1
kubectl scale deployment -n dpp-production dpp-reaper --replicas=1
```

- [ ] API Pod 시작 완료 (2/2 Ready)
- [ ] Worker Pod 시작 완료 (1/1 Ready)
- [ ] Reaper Pod 시작 완료 (1/1 Ready)
- [ ] Pod 시작 시간: __________

### 3. Health Check

```bash
# API Health
curl -f https://api.decisionproof.com/health

# Readiness
curl -f https://api.decisionproof.com/readyz
```

- [ ] Health Check: ✅ PASS
- [ ] Readiness Check: ✅ PASS
- [ ] 응답 시간: _______ ms

---

## ✅ Post-Migration Validation (D+0)

### Smoke Tests

- [ ] **Tenant 생성 테스트**
  - Tenant ID: __________________
  - 결과: PASS / FAIL

- [ ] **Run 생성/실행 테스트**
  - Run ID: __________________
  - 결과: PASS / FAIL

- [ ] **API Key 발급 테스트**
  - API Key (first 10 chars): __________________
  - 결과: PASS / FAIL

- [ ] **Storage Upload/Download 테스트**
  - Object Key: __________________
  - Presigned URL 생성: PASS / FAIL

### Auth 확인

- [ ] **사용자 로그인 테스트**
  - JWT_STRATEGY=FORCE_RELOGIN인 경우:
    - 기존 토큰 무효화 확인: PASS / FAIL
    - 재로그인 정상 작동: PASS / FAIL
  - JWT_STRATEGY=KEEP_SESSIONS인 경우:
    - 기존 토큰 유지 확인: PASS / FAIL

### Monitoring (첫 2시간)

- [ ] **Supabase Dashboard → Reports 확인**
  - Database connections: _______ (정상 범위)
  - API requests/min: _______
  - Error rate: _______ %

- [ ] **Application 로그 확인**
  - ERROR 로그 수: _______
  - CRITICAL 로그 수: _______

- [ ] **Sentry/Monitoring 확인**
  - 에러 트렌드: 증가 / 안정
  - Latency p50: _______ ms
  - Latency p99: _______ ms

---

## ✅ Cleanup (D+7)

### OLD 프로젝트 처리

- [ ] **READ-ONLY 전환**
  - Supabase Dashboard → Settings → Pause Project
  - 일시: __________

- [ ] **7일 대기 완료**
  - 복구 가능 기간 유지
  - 모니터링 이상 없음 확인

- [ ] **OLD 프로젝트 완전 삭제**
  - Supabase Dashboard → Settings → Delete Project
  - 삭제 일시: __________

### 백업 아카이브

- [ ] **S3 Glacier 아카이브**
  ```bash
  aws s3 cp supabase_migration_backups/ s3://dpp-backups/supabase_migration/ --recursive
  ```
  - 업로드 완료 일시: __________
  - S3 URI: __________________

- [ ] **로컬 백업 파일 삭제** (S3 업로드 확인 후)

---

## ✅ Post-Mortem (D+14)

### 검토 미팅

- [ ] **Post-Mortem 미팅 개최**
  - 날짜: __________
  - 참석자: __________________

- [ ] **문서 작성**
  - What went well: __________________
  - What went wrong: __________________
  - Action items: __________________

- [ ] **Runbook 업데이트**
  - 개선사항 반영 완료

---

## 📞 Emergency Contacts

| Role | Name | Contact |
|------|------|---------|
| DevOps Lead | _____________ | _______________ |
| Backend Engineer | _____________ | _______________ |
| CTO | _____________ | _______________ |
| Supabase Support | N/A | support@supabase.com |

---

## 📝 Notes / Issues

```
(마이그레이션 중 발생한 이슈, 예상외 문제, 해결 방법 기록)








```

---

**Checklist Completed**: __________
**Sign-Off**: __________________
