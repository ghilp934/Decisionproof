# Supabase 프로젝트 리전 마이그레이션 가이드

**버전**: v1.0
**작성일**: 2026-02-17
**작업 종류**: 운영 절차 (Operational Procedure)

---

## 📋 작업 개요

### 목표
기존 Supabase 프로젝트 `Decisionproof_Paid_Pilot` (AP Southeast 2)에서
신규 `Decisionproof` (Seoul/ap-northeast-2)로 완전 마이그레이션

### 이유
- Supabase 프로젝트는 생성 후 리전 변경 불가
- 리전 이동 = 새 프로젝트 생성 + 데이터 마이그레이션

### 범위
- ✅ DB: roles / schema / data (+ migrations history 옵션)
- ✅ Auth users: auth 스키마 전체 (비밀번호 해시 포함)
- ✅ Storage: 버킷 메타 + **실제 객체 파일**
- ✅ Edge Functions: 전체 함수 재배포
- ✅ 수동 설정: Auth providers, API keys, Extensions, Realtime

---

## ⚠️ STOP RULES — 즉시 중단 조건

다음 중 하나라도 해당하면 작업 즉시 중단:

1. **NEW 프로젝트 리전이 Seoul (ap-northeast-2)이 아님**
2. **OLD/NEW DATABASE_URL 서로 바뀌어있거나 접근 권한 부족**
3. **쓰기 차단(Maintenance Mode) 없이 덤프 시작**
   - 데이터 분기(divergence) 위험
4. **JWT 전략 미결정** (FORCE_RELOGIN vs KEEP_SESSIONS)
5. **백업 파일 무결성 검증 실패**

---

## 📝 [INPUTS REQUIRED] — 작업 전 필수 입력

다음 정보를 준비하세요. **민감 정보는 절대 로그/문서에 기록하지 말 것.**

```bash
# === OLD 프로젝트 (Decisionproof_Paid_Pilot) ===
export OLD_PROJECT_REF="xxxxxxxxxxxxxxxxxxxxx"  # Supabase Dashboard에서 확인
export OLD_DB_URL="postgresql://postgres.${OLD_PROJECT_REF}:[PASSWORD]@aws-0-ap-southeast-2.pooler.supabase.com:6543/postgres?sslmode=require"
export OLD_PROJECT_URL="https://${OLD_PROJECT_REF}.supabase.co"
export OLD_SERVICE_ROLE_KEY="[SECRET]"  # Settings → API → service_role key

# === NEW 프로젝트 (Decisionproof / Seoul) ===
export NEW_PROJECT_REF="yyyyyyyyyyyyyyyyyyyyyyy"  # Supabase Dashboard에서 확인
export NEW_DB_URL="postgresql://postgres.${NEW_PROJECT_REF}:[PASSWORD]@aws-0-ap-northeast-2.pooler.supabase.com:6543/postgres?sslmode=require"
export NEW_PROJECT_URL="https://${NEW_PROJECT_REF}.supabase.co"
export NEW_SERVICE_ROLE_KEY="[SECRET]"  # Settings → API → service_role key
export NEW_ANON_KEY="[PUBLIC]"          # Settings → API → anon key

# === JWT 전략 결정 (필수) ===
# 옵션 A: FORCE_RELOGIN (권장) — 새 JWT Secret 유지, 기존 토큰 무효화
# 옵션 B: KEEP_SESSIONS — OLD JWT Secret을 NEW에 복제 (보안 주의)
export JWT_STRATEGY="FORCE_RELOGIN"

# === Feature Flags ===
export HAS_CUSTOM_AUTH_OR_STORAGE_CHANGES="no"  # Auth/Storage 스키마 커스텀 변경 여부
export MIGRATION_HISTORY_NEEDED="yes"           # supabase_migrations 테이블 복사 여부
export HAS_EDGE_FUNCTIONS="no"                  # Edge Functions 존재 여부
export HAS_STORAGE_OBJECTS="yes"                # Storage 실제 객체 존재 여부

# === 백업 디렉토리 ===
export MIGRATION_BACKUP_DIR="./supabase_migration_backups/$(date +%Y%m%d_%H%M%S)"
mkdir -p "$MIGRATION_BACKUP_DIR"
```

### 정보 수집 방법

1. **PROJECT_REF 확인**:
   - Supabase Dashboard → 프로젝트 선택 → Settings → General → Reference ID

2. **DATABASE_URL 확인**:
   - Settings → Database → Connection string → **Transaction** (Port 6543)

3. **SERVICE_ROLE_KEY / ANON_KEY 확인**:
   - Settings → API → Project API keys

4. **리전 확인**:
   - Settings → General → Region
   - OLD: `ap-southeast-2` (Sydney)
   - NEW: `ap-northeast-2` (Seoul)

---

## 🚀 Phase 0 — PRE-FLIGHT / MAINTENANCE MODE

### 0.1. 앱 쓰기 차단 (CRITICAL)

```bash
# DPP API/Worker 모두 MAINTENANCE MODE 진입
# Kubernetes: Deployment replicas=0 또는 maintenance 페이지 프록시
kubectl scale deployment -n dpp-production dpp-api --replicas=0
kubectl scale deployment -n dpp-production dpp-worker --replicas=0
kubectl scale deployment -n dpp-production dpp-reaper --replicas=0

# 확인: 모든 Pod 종료
kubectl get pods -n dpp-production
```

### 0.2. Supabase Dashboard 접근 확인

```bash
# Supabase CLI 설치 확인
supabase --version

# OLD 프로젝트 링크 (테스트)
supabase link --project-ref "$OLD_PROJECT_REF"

# NEW 프로젝트 링크 (테스트)
supabase link --project-ref "$NEW_PROJECT_REF"
```

### 0.3. OLD 프로젝트 인벤토리 스냅샷

Supabase Dashboard에서 다음 항목 확인 및 문서화:

- [ ] **Extensions**: `Settings → Database → Extensions`
  - 예: `pg_stat_statements`, `uuid-ossp`, `pgcrypto` 등
- [ ] **Database Webhooks**: `Database → Webhooks`
- [ ] **Realtime**: `Database → Replication` (Publication 확인)
- [ ] **Edge Functions**: `Edge Functions` 메뉴
- [ ] **Storage Buckets**: `Storage` 메뉴 (버킷 목록/정책)
- [ ] **Auth Providers**: `Authentication → Providers`
  - 예: Email, Google OAuth, etc.
- [ ] **Auth Redirect URLs**: `Authentication → URL Configuration`
- [ ] **Secrets/Environment Variables**: `Settings → Vault` (있는 경우)

**결과를 파일로 저장**:
```bash
cat > "$MIGRATION_BACKUP_DIR/old_project_inventory.txt" <<EOF
프로젝트: Decisionproof_Paid_Pilot
리전: ap-southeast-2
확인일: $(date)

Extensions:
- [확인 후 기입]

Edge Functions:
- [확인 후 기입]

Storage Buckets:
- [확인 후 기입]

Auth Providers:
- [확인 후 기입]
EOF
```

---

## 🗄️ Phase 1 — DB BACKUP (OLD 프로젝트)

### 1.1. Roles 덤프

```bash
supabase db dump \
  --db-url "$OLD_DB_URL" \
  -f "$MIGRATION_BACKUP_DIR/roles.sql" \
  --role-only

# 검증
ls -lh "$MIGRATION_BACKUP_DIR/roles.sql"
```

### 1.2. Schema 덤프

```bash
supabase db dump \
  --db-url "$OLD_DB_URL" \
  -f "$MIGRATION_BACKUP_DIR/schema.sql"

# 검증
ls -lh "$MIGRATION_BACKUP_DIR/schema.sql"
```

### 1.3. Data 덤프

```bash
supabase db dump \
  --db-url "$OLD_DB_URL" \
  -f "$MIGRATION_BACKUP_DIR/data.sql" \
  --use-copy \
  --data-only

# 검증
ls -lh "$MIGRATION_BACKUP_DIR/data.sql"
```

### 1.4. Migration History 덤프 (옵션)

```bash
if [ "$MIGRATION_HISTORY_NEEDED" = "yes" ]; then
  supabase db dump \
    --db-url "$OLD_DB_URL" \
    -f "$MIGRATION_BACKUP_DIR/history_schema.sql" \
    --schema supabase_migrations

  supabase db dump \
    --db-url "$OLD_DB_URL" \
    -f "$MIGRATION_BACKUP_DIR/history_data.sql" \
    --use-copy \
    --data-only \
    --schema supabase_migrations

  ls -lh "$MIGRATION_BACKUP_DIR"/history_*.sql
fi
```

### 1.5. 덤프 파일 무결성 검증

```bash
# 빈 파일 확인 (FAIL 조건)
for file in roles.sql schema.sql data.sql; do
  size=$(stat -f%z "$MIGRATION_BACKUP_DIR/$file" 2>/dev/null || stat -c%s "$MIGRATION_BACKUP_DIR/$file")
  if [ "$size" -eq 0 ]; then
    echo "ERROR: $file is empty! Aborting migration."
    exit 1
  fi
  echo "✅ $file: ${size} bytes"
done

# SHA256 체크섬 생성 (복원 검증용)
cd "$MIGRATION_BACKUP_DIR"
sha256sum *.sql > checksums.txt
cat checksums.txt
```

---

## 🎯 Phase 2 — TARGET PROJECT PREP (NEW 프로젝트)

### 2.1. NEW 프로젝트 리전 확인 (STOP RULE)

```bash
# Supabase Dashboard → Settings → General → Region
# 반드시 "ap-northeast-2" 확인
# 다른 리전이면 작업 중단!
```

### 2.2. Extensions 사전 활성화

OLD 프로젝트에서 사용하던 Extensions를 NEW에서 미리 활성화:

```bash
# Supabase Dashboard → Settings → Database → Extensions
# 또는 psql로 직접 활성화

psql "$NEW_DB_URL" -c "CREATE EXTENSION IF NOT EXISTS \"uuid-ossp\";"
psql "$NEW_DB_URL" -c "CREATE EXTENSION IF NOT EXISTS \"pg_stat_statements\";"
# (필요한 다른 Extensions 추가)
```

### 2.3. Realtime Publication 설정 (필요 시)

```bash
# OLD에서 사용하던 테이블의 Realtime publication 확인
# NEW에서도 동일하게 설정 (또는 복원 후 설정)
```

---

## 📥 Phase 3 — DB RESTORE (NEW 프로젝트)

### 3.1. psql로 Roles/Schema/Data 복원

```bash
cd "$MIGRATION_BACKUP_DIR"

# 단일 트랜잭션으로 복원 (실패 시 롤백)
psql \
  --single-transaction \
  --variable ON_ERROR_STOP=1 \
  --dbname "$NEW_DB_URL" \
  <<EOF
-- Roles 복원
\i roles.sql

-- Schema 복원
\i schema.sql

-- RLS 트리거 일시 비활성화 (데이터 복원 속도 향상)
SET session_replication_role = replica;

-- Data 복원
\i data.sql

-- RLS 트리거 재활성화
SET session_replication_role = DEFAULT;
EOF

echo "✅ DB 복원 완료"
```

### 3.2. Migration History 복원 (옵션)

```bash
if [ "$MIGRATION_HISTORY_NEEDED" = "yes" ]; then
  psql \
    --single-transaction \
    --variable ON_ERROR_STOP=1 \
    --dbname "$NEW_DB_URL" \
    <<EOF
\i history_schema.sql
\i history_data.sql
EOF

  echo "✅ Migration history 복원 완료"
fi
```

### 3.3. TROUBLESHOOTING — OWNER 권한 에러

복원 중 `supabase_admin` OWNER 에러 발생 시:

```bash
# schema.sql 파일에서 OWNER 변경 라인 주석 처리
sed -i 's/^ALTER .* OWNER TO "supabase_admin"/-- &/' schema.sql

# 다시 복원 시도
```

### 3.4. 복원 검증

```bash
# 테이블 카운트 확인
psql "$NEW_DB_URL" -c "\dt"

# 주요 테이블 레코드 수 확인
psql "$NEW_DB_URL" -c "SELECT 'tenants' AS table_name, COUNT(*) FROM tenants
UNION ALL SELECT 'runs', COUNT(*) FROM runs
UNION ALL SELECT 'api_keys', COUNT(*) FROM api_keys;"

# Auth users 확인
psql "$NEW_DB_URL" -c "SELECT COUNT(*) FROM auth.users;"
```

---

## 🔐 Phase 4 — AUTH/STORAGE SCHEMA DIFF (커스텀 변경 있는 경우)

```bash
if [ "$HAS_CUSTOM_AUTH_OR_STORAGE_CHANGES" = "yes" ]; then
  # OLD 프로젝트 링크
  supabase link --project-ref "$OLD_PROJECT_REF"

  # auth, storage 스키마 diff 추출
  supabase db diff --linked --schema auth,storage > "$MIGRATION_BACKUP_DIR/auth_storage_changes.sql"

  # 수동 리뷰 후 NEW 프로젝트에 적용
  echo "⚠️ auth_storage_changes.sql 파일을 검토 후 NEW 프로젝트에 적용하세요."
  # psql --variable ON_ERROR_STOP=1 --file "$MIGRATION_BACKUP_DIR/auth_storage_changes.sql" --dbname "$NEW_DB_URL"
fi
```

---

## 🔑 Phase 5 — AUTH TOKEN POLICY (JWT Strategy)

### 옵션 A: FORCE_RELOGIN (권장)

**장점**: 보안 최우선, JWT Secret 새로 발급
**단점**: 모든 사용자 재로그인 필요

```bash
if [ "$JWT_STRATEGY" = "FORCE_RELOGIN" ]; then
  echo "✅ JWT Strategy: FORCE_RELOGIN"
  echo "   → NEW 프로젝트의 기본 JWT Secret 유지"
  echo "   → 사용자 공지: 마이그레이션 후 재로그인 필요"

  # 추가 작업 없음 (NEW 프로젝트 기본 설정 사용)
fi
```

### 옵션 B: KEEP_SESSIONS (고급)

**장점**: 기존 세션 유지
**단점**: JWT Secret 복사 필요 (보안 주의)

```bash
if [ "$JWT_STRATEGY" = "KEEP_SESSIONS" ]; then
  echo "⚠️ JWT Strategy: KEEP_SESSIONS"
  echo "   → OLD 프로젝트의 JWT Secret을 NEW에 수동 복사 필요"
  echo "   → Supabase Dashboard → Settings → API → JWT Settings"
  echo "   → anon/service_role 키도 재확인 필요"

  # 수동 작업:
  # 1. OLD: Settings → API → JWT Secret 복사
  # 2. NEW: Settings → API → JWT Settings → Custom JWT Secret 설정
  # 3. NEW: anon/service_role 키가 OLD와 일치하는지 확인
fi
```

---

## 📦 Phase 6 — STORAGE OBJECTS MIGRATION

### 6.1. Storage 버킷 목록 확인

```bash
# OLD 프로젝트의 Storage 버킷 확인
# Supabase Dashboard → Storage
# 또는 API로 조회

curl -X GET "$OLD_PROJECT_URL/storage/v1/bucket" \
  -H "Authorization: Bearer $OLD_SERVICE_ROLE_KEY" \
  | jq '.'
```

### 6.2. Storage Objects 마이그레이션 스크립트

**주의**: Storage 객체는 DB 복원에 포함되지 않음. 수동 복사 필요.

```bash
# 스크립트 위치: ops/scripts/migrate_storage_objects.sh
# (다음 섹션에서 스크립트 생성)

if [ "$HAS_STORAGE_OBJECTS" = "yes" ]; then
  bash ops/scripts/migrate_storage_objects.sh \
    "$OLD_PROJECT_URL" \
    "$OLD_SERVICE_ROLE_KEY" \
    "$NEW_PROJECT_URL" \
    "$NEW_SERVICE_ROLE_KEY"
fi
```

---

## ⚡ Phase 7 — EDGE FUNCTIONS MIGRATION

```bash
if [ "$HAS_EDGE_FUNCTIONS" = "yes" ]; then
  # OLD 프로젝트에서 Edge Functions 목록 확인
  supabase functions list --project-ref "$OLD_PROJECT_REF"

  # 각 함수 다운로드
  mkdir -p "$MIGRATION_BACKUP_DIR/edge_functions"
  cd "$MIGRATION_BACKUP_DIR/edge_functions"

  # (함수별로 수동 다운로드 또는 Supabase Dashboard에서 Export)

  # NEW 프로젝트에 배포
  supabase functions deploy function_name --project-ref "$NEW_PROJECT_REF"
fi
```

---

## 🔧 Phase 8 — MANUAL SETTINGS MIGRATION

### 8.1. Auth Providers 설정

OLD 프로젝트와 동일하게 NEW에서 설정:

```bash
# Supabase Dashboard → Authentication → Providers
# - Email 활성화 여부
# - OAuth Providers (Google, GitHub 등)
#   → Client ID / Secret 재입력 필요
# - Redirect URLs 설정
```

### 8.2. API Keys 갱신 (앱 환경변수 준비)

```bash
# NEW 프로젝트의 API Keys 확인
# Settings → API → Project API keys

export NEW_SUPABASE_URL="$NEW_PROJECT_URL"
export NEW_SUPABASE_ANON_KEY="$NEW_ANON_KEY"
export NEW_SUPABASE_SERVICE_ROLE_KEY="$NEW_SERVICE_ROLE_KEY"

# DPP 앱 환경변수 업데이트 (다음 Phase에서 적용)
```

### 8.3. Database Webhooks 재생성

```bash
# OLD: Database → Webhooks 확인
# NEW: 동일한 Webhook 수동 재생성
```

### 8.4. Realtime Settings

```bash
# OLD: Database → Replication 확인
# NEW: 동일한 Publication 설정
```

---

## 🚀 Phase 9 — CUTOVER (앱 환경변수 교체)

### 9.1. DPP Kubernetes Secrets 업데이트 (Phase 4.0: ASCP 방식)

> ⚠️ **Phase 4.0 이후 변경**: `kubectl create secret` 방식은 폐기되었습니다.
> `database-url` 변경은 AWS Secrets Manager에서 직접 수행하십시오.
> 값 출력 명령(`-o jsonpath | base64 -d` 등)은 절대 사용하지 마십시오.
> 상세: `ops/runbooks/secrets_ascp_irsa.md`

```bash
# NEW 프로젝트 DATABASE_URL을 AWS Secrets Manager에서 업데이트 (콘솔 권장)
# CLI 확인 (값 출력 없이 메타데이터만)
aws secretsmanager describe-secret \
  --secret-id "decisionproof/prod/dpp-secrets" \
  --region ap-northeast-2 \
  --profile dpp-admin \
  --query "[Name, LastChangedDate]"

# ASCP sync: Pod rollout restart
kubectl rollout restart deployment/dpp-api deployment/dpp-worker deployment/dpp-reaper \
  -n dpp-production

# 확인 (메타데이터만, 값 출력 절대 금지)
kubectl get secret dpp-secrets -n dpp-production
```

### 9.2. API/Worker Deployment 재시작

```bash
# Replicas 복구 (Maintenance Mode 해제)
kubectl scale deployment -n dpp-production dpp-api --replicas=2
kubectl scale deployment -n dpp-production dpp-worker --replicas=1
kubectl scale deployment -n dpp-production dpp-reaper --replicas=1

# Pod 시작 확인
kubectl get pods -n dpp-production -w
```

### 9.3. Health Check

```bash
# API Health Endpoint 확인
curl -f https://api.decisionproof.com/health || echo "FAIL"

# DB 연결 확인 (psql)
psql "$NEW_DB_URL" -c "SELECT version();"
```

---

## ✅ Phase 10 — POST-CUTOVER VALIDATION

### 10.1. 기능 스모크 테스트

```bash
# Tenant 생성 테스트
# Run 생성/실행 테스트
# API Key 발급 테스트
# (내부 테스트 체크리스트 참조)
```

### 10.2. Auth 확인

```bash
# 사용자 로그인 테스트
# - FORCE_RELOGIN: 재로그인 정상 동작 확인
# - KEEP_SESSIONS: 기존 토큰 유지 확인
```

### 10.3. Storage 확인

```bash
# Storage 객체 접근 테스트
# Presigned URL 생성 테스트
```

### 10.4. 모니터링 확인

```bash
# Supabase Dashboard → Reports
# - Database connections
# - API requests
# - Error logs
```

---

## 🧹 POST-MIGRATION CLEANUP

### OLD 프로젝트 처리

```bash
# 1. OLD 프로젝트 READ-ONLY 전환
#    → Supabase Dashboard → Settings → General → Pause Project

# 2. 7일간 유지 후 완전 삭제
#    → 복구 가능 기간 확보

# 3. 백업 파일 안전 보관
#    → S3/Glacier로 아카이브
aws s3 cp "$MIGRATION_BACKUP_DIR" s3://dpp-backups/supabase_migration/ --recursive
```

---

## 📚 참고 자료

- [Supabase Migration Guide](https://supabase.com/docs/guides/platform/migrating-and-upgrading-projects)
- [Supabase Database Dumps](https://supabase.com/docs/guides/cli/managing-environments#dump-and-restore)
- [DPP Supabase SSOT](../docs/supabase/00_supabase_ssot.md)
- [DPP DB Backup Runbook](./db_backup_restore.md)

---

**작성자**: Claude (DPP DevOps Agent)
**검토 필요**: DevOps Team Lead
**승인 필요**: CTO
