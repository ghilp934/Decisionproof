# Supabase SSL Enforcement — 운영 가이드

> **관련 패치**: P0-SSL (2026-02-18)
> **대상 컴포넌트**: API / Alembic / SES Feedback Worker

---

## 1. 대시보드 토글 — "Enforce SSL on incoming connections"

Supabase Dashboard → **Project Settings → Database → SSL** 에서
"Enforce SSL on incoming connections" 옵션을 켜면:

- Supabase 측에서 **비-SSL 연결을 거부**하기 시작합니다.
- 토글 직후 DB가 **수 초 재부팅** 됩니다 (연결 순간 끊김 발생).
- `sslmode=require` 이상으로 이미 연결 중인 클라이언트는 재연결 후 정상 동작합니다.

### 권장 순서

1. `DPP_DB_SSLMODE=require` 가 모든 컴포넌트에 설정되어 있는지 확인 (또는 DATABASE_URL에 포함)
2. 대시보드에서 SSL Enforcement **ON**
3. 재부팅 완료 후 아래 검증 SQL 실행

---

## 2. 환경 변수 (Spec Lock)

| 변수 | 기본값 | 설명 |
|------|--------|------|
| `DPP_DB_SSLMODE` | `require` | SSL 모드. `require` \| `verify-ca` \| `verify-full` 허용 |
| `DPP_DB_SSLROOTCERT` | (없음) | CA 번들 파일 경로. `verify-ca` / `verify-full` 시 필수 |

**허용 값**: `require`, `verify-ca`, `verify-full`
**금지 값**: `disable`, `allow`, `prefer` (프로덕션 가드레일이 기동 차단)

### SSL 모드 우선순위 (Spec Lock)

```
URL에 sslmode= 포함  →  URL이 SSOT (ENV 무시)
URL에 sslmode 없음   →  DPP_DB_SSLMODE ENV 사용 (기본: require)
```

---

## 3. CA 번들 적용 (verify-full 업그레이드)

Supabase CA 인증서를 적용하면 중간자 공격(MITM)을 완전히 방지할 수 있습니다.

### 절차

1. **CA 파일 다운로드**
   - Supabase Dashboard → Project Settings → Database → **Download CA certificate**
   - 파일명: `prod-ca-2021.crt`

2. **쿠버네티스 Secret에 마운트**
   ```yaml
   # k8s/secrets.yaml 에 추가
   stringData:
     supabase-ca.crt: |
       -----BEGIN CERTIFICATE-----
       ...
       -----END CERTIFICATE-----
   ```

3. **Deployment에 볼륨 마운트**
   ```yaml
   volumes:
     - name: supabase-ca
       secret:
         secretName: dpp-secrets
         items:
           - key: supabase-ca.crt
             path: supabase-ca.crt
   volumeMounts:
     - name: supabase-ca
       mountPath: /etc/ssl/supabase
       readOnly: true
   ```

4. **환경 변수 설정**
   ```yaml
   env:
     - name: DPP_DB_SSLMODE
       value: "verify-full"
     - name: DPP_DB_SSLROOTCERT
       value: "/etc/ssl/supabase/supabase-ca.crt"
   ```

---

## 4. 검증 SQL

SSL 활성화 확인:

```sql
-- 현재 연결의 SSL 상태 확인
SELECT ssl, version, cipher
FROM pg_stat_ssl
WHERE pid = pg_backend_pid();
```

| 컬럼 | 기대값 |
|------|--------|
| `ssl` | `true` |
| `version` | `TLSv1.3` (또는 1.2) |
| `cipher` | `TLS_AES_256_GCM_SHA384` 등 |

---

## 5. 컴포넌트별 SSL 적용 방식 요약

| 컴포넌트 | 적용 방식 | 파일 |
|---------|---------|------|
| **API runtime** | `connect_args["sslmode"]` via `build_engine()` | `dpp_api/db/engine.py` |
| **Alembic 마이그레이션** | `ensure_sslmode()` → URL에 직접 주입 | `alembic/env.py` |
| **SES Feedback Worker** | `_ensure_sslmode()` (inline) → URL에 직접 주입 | `worker_ses_feedback/worker.py` |
| **공통 헬퍼** | `is_supabase_host`, `ensure_sslmode`, `get_sslmode_from_url` | `dpp_api/db/url_policy.py` |

---

## 6. 트러블슈팅

### 연결 실패: `SSL connection is required`
- 원인: `sslmode` 미설정 상태에서 SSL Enforcement 활성화
- 해결: `DATABASE_URL`에 `?sslmode=require` 추가 또는 `DPP_DB_SSLMODE=require` 설정

### 앱 기동 실패: `PRODUCTION GUARDRAIL: Supabase sslmode must be one of ...`
- 원인: `DPP_DB_SSLMODE=disable` (또는 URL에 `sslmode=disable`) 상태로 프로덕션 기동 시도
- 해결: 안전한 SSL 모드(`require`, `verify-ca`, `verify-full`)로 변경

### CA 검증 실패: `SSL certificate verify failed`
- 원인: `verify-full` 설정이지만 CA 파일 경로(`DPP_DB_SSLROOTCERT`)가 잘못됨
- 해결: CA 파일 경로 확인, Supabase CA 재다운로드
