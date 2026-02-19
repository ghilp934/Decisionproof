# Supabase Production Hardening Runbook

**목적**: Supabase 프로덕션 환경 보안 강화 (Network Restrictions 설정)

**담당자**: DevOps/운영팀

**빈도**: 프로덕션 배포 전 1회 (이후 변경 시 재검증)

---

## P0-2: Network Restrictions (IP Allowlist) 설정

### 1. Supabase Dashboard 접속

1. [https://supabase.com/dashboard](https://supabase.com/dashboard) 로그인
2. 프로덕션 프로젝트 선택
3. **Settings** → **Database** 메뉴 이동

### 2. Network Restrictions 활성화

**경로**: Settings → Database → **Network Restrictions** 섹션

**설정 방법**:
1. **Enable Network Restrictions** 토글 활성화
2. **Add IP Address** 클릭하여 허용 IP/CIDR 추가

**허용 IP 예시**:
```
# 프로덕션 API 서버 (예시)
52.123.45.67/32

# Kubernetes 클러스터 NAT Gateway (예시)
10.0.0.0/16

# VPN/Bastion Host (관리자 접근)
203.0.113.0/24
```

**주의사항**:
- ⚠️ **0.0.0.0/0 (전체 허용) 절대 금지**
- ⚠️ 설정 후 반드시 테스트 (허용 IP에서 연결 확인)
- ⚠️ 배포 전 모든 런타임 환경 IP 사전 확보

### 3. 연결 테스트

**env-var SSOT로 설정 후 스크립트 실행**:
```bash
# 환경변수 설정 (SSOT — 반드시 verify-full 사용)
export PGHOST=db.<ref>.supabase.co
export PGHOST_POOLER=aws-1-ap-northeast-2.pooler.supabase.com
export PGPORT=5432
export PGPORT_POOLER=5432
export PGDATABASE=postgres
export PGUSER=postgres
export PGUSER_POOLER=postgres.<ref>
export PGPASSWORD=<db-password>
export PGSSLMODE=verify-full
export PGSSLROOTCERT=/path/to/prod-ca-2021.crt   # CA cert 경로

# verify-full 연결 테스트 (Direct + Pooler)
bash dpp/ops/scripts/psql_verify_full.sh
```

**성공 시**: `=== All checks PASSED ===` 출력
**실패 시**: IP allowlist에 해당 IP 추가 누락 → Supabase Dashboard에서 추가

> ⚠️ `sslmode=require` 사용 금지 — 반드시 `verify-full` 사용 (CA 인증서 검증 포함)

### 4. ACK 변수 설정 (배포 환경)

**Network Restrictions 설정 완료 후**:
```bash
# Kubernetes Secret 또는 환경변수에 추가
DPP_ACK_SUPABASE_NETWORK_RESTRICTIONS=1
```

**목적**: 엔진 시작 시 production guardrail에서 Network Restrictions 설정 완료 확인

**에러 발생 시**:
```
RuntimeError: DPP_ACK_SUPABASE_NETWORK_RESTRICTIONS=1 required.
This confirms Supabase Network Restrictions (IP allowlist) configured in dashboard.
```

→ 이 runbook 체크리스트 완료 후 ACK 변수 설정

---

## 체크리스트

배포 전 아래 항목 모두 확인:

- [ ] Supabase Dashboard → Settings → Database → Network Restrictions 활성화
- [ ] 프로덕션 런타임 IP/CIDR 모두 추가 (0.0.0.0/0 절대 금지)
- [ ] 허용 IP에서 연결 테스트 성공 확인
- [ ] 미허용 IP에서 연결 차단 확인 (선택 사항, 안전 검증)
- [ ] `DPP_ACK_SUPABASE_NETWORK_RESTRICTIONS=1` 환경변수 설정
- [ ] `python scripts/supabase_preflight.py` 실행 → PASS 확인

---

## 참고 자료

- Supabase 공식 문서: [Network Restrictions](https://supabase.com/docs/guides/database/network-restrictions)
- DPP Preflight Validator: `dpp/scripts/supabase_preflight.py`
- Supabase SSOT 문서: `dpp/docs/supabase/00_supabase_ssot.md`

---

**마지막 업데이트**: 2026-02-17
**리뷰 주기**: 분기 1회 (IP 변경 시 즉시 업데이트)
