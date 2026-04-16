# DB SSL verify-full 활성화 런북

> **패치**: P0-SSL Phase 2 (2026-02-18)
> **대상 환경**: dpp-production / dpp-pilot
> **사전 조건**: Supabase 대시보드에서 "Enforce SSL on incoming connections" 토글 ON 완료

---

## 0. 개요

이 런북은 Supabase CA 인증서를 Kubernetes ConfigMap으로 배포하고,
모든 DB 직접 연결 파드(API / Worker / Reaper / SES Feedback Worker)에
`sslmode=verify-full + sslrootcert` 를 적용하는 절차를 설명합니다.

**CA 인증서는 Git에 커밋하지 않습니다.** 운영자가 로컬에 `prod-ca-2021.crt`를
보유한 상태에서 kubectl로 ConfigMap을 직접 생성합니다.

---

## 1. 사전 준비

### 1-1. CA 인증서 파일 확보

Supabase Dashboard → **Project Settings → Database → Download CA certificate**
→ `prod-ca-2021.crt` 다운로드 (운영자 로컬 보관; 절대 Git 커밋 금지)

### 1-2. kubectl 컨텍스트 확인

```bash
kubectl config current-context
kubectl get nodes -n dpp-production
```

---

## 2. ConfigMap 생성 (prod 네임스페이스)

```bash
# CA 파일을 ConfigMap으로 생성 — cert 본문은 클러스터에만 존재, Git 미포함
kubectl -n dpp-production create configmap dpp-supabase-ca \
  --from-file=supabase-ca.crt=prod-ca-2021.crt \
  --dry-run=client -o yaml | kubectl apply -f -
```

### 확인

```bash
kubectl -n dpp-production get configmap dpp-supabase-ca
kubectl -n dpp-production describe configmap dpp-supabase-ca
```

---

## 3. ConfigMap 생성 (pilot 네임스페이스, 필요 시)

```bash
kubectl -n dpp-pilot create configmap dpp-supabase-ca \
  --from-file=supabase-ca.crt=prod-ca-2021.crt \
  --dry-run=client -o yaml | kubectl apply -f -
```

---

## 4. K8s Manifest 적용

P0-SSL Phase 2 패치로 이미 아래 변경이 적용되어 있습니다:
- `volumes: supabase-ca (ConfigMap: dpp-supabase-ca)`
- `volumeMounts: /etc/ssl/certs/supabase-ca (readOnly)`
- `env: DPP_DB_SSLMODE=verify-full`
- `env: DPP_DB_SSLROOTCERT=/etc/ssl/certs/supabase-ca/supabase-ca.crt`

Manifest를 클러스터에 적용:

```bash
kubectl apply -f k8s/api-deployment.yaml
kubectl apply -f k8s/worker-deployment.yaml
kubectl apply -f k8s/reaper-deployment.yaml
kubectl apply -f k8s/deploy-ses-feedback-worker.yaml
# Pilot (필요 시)
kubectl apply -f k8s/overlays/pilot/worker-deployment-pilot.yaml
```

---

## 5. 롤아웃 실행 및 모니터링

```bash
# 롤링 재시작 (신규 파드에 CA 마운트 + verify-full 적용)
kubectl -n dpp-production rollout restart \
  deploy/dpp-api \
  deploy/dpp-worker \
  deploy/dpp-reaper \
  deploy/dpp-ses-feedback-worker

# 롤아웃 완료 대기
kubectl -n dpp-production rollout status deploy/dpp-api
kubectl -n dpp-production rollout status deploy/dpp-worker
kubectl -n dpp-production rollout status deploy/dpp-reaper
kubectl -n dpp-production rollout status deploy/dpp-ses-feedback-worker
```

---

## 6. 클러스터 검증 커맨드

### 6-1. ConfigMap 존재 확인

```bash
kubectl -n dpp-production get configmap dpp-supabase-ca
```

### 6-2. 파드 내 CA 파일 마운트 확인

```bash
kubectl -n dpp-production exec deploy/dpp-api -- ls -al /etc/ssl/certs/supabase-ca
```

기대 출력: `supabase-ca.crt` 파일 존재

### 6-3. 환경변수 확인 (민감정보 제외)

```bash
kubectl -n dpp-production exec deploy/dpp-api -- \
  printenv | grep -E 'DPP_DB_SSLMODE|DPP_DB_SSLROOTCERT'
```

기대 출력:
```
DPP_DB_SSLMODE=verify-full
DPP_DB_SSLROOTCERT=/etc/ssl/certs/supabase-ca/supabase-ca.crt
```

### 6-4. 실제 DB SSL 연결 확인 (Supabase SQL)

Supabase Dashboard → SQL Editor 실행:

```sql
SELECT ssl, version, cipher
FROM pg_stat_ssl
WHERE pid = pg_backend_pid();
```

기대값:

| 컬럼 | 기대값 |
|------|--------|
| `ssl` | `true` |
| `version` | `TLSv1.3` |
| `cipher` | `TLS_AES_256_GCM_SHA384` 등 |

---

## 7. CA 인증서 교체 절차

CA 만료 또는 갱신 시:

```bash
# 1. 신규 CA 파일로 ConfigMap 교체
kubectl -n dpp-production create configmap dpp-supabase-ca \
  --from-file=supabase-ca.crt=new-ca-2026.crt \
  --dry-run=client -o yaml | kubectl apply -f -

# 2. 파드 재시작으로 신규 CA 반영
kubectl -n dpp-production rollout restart \
  deploy/dpp-api \
  deploy/dpp-worker \
  deploy/dpp-reaper \
  deploy/dpp-ses-feedback-worker

# 3. 상태 확인
kubectl -n dpp-production rollout status deploy/dpp-api
```

---

## 8. 트러블슈팅

### 파드 기동 실패: `RuntimeError: SSL POLICY: sslmode='verify-full' requires a CA bundle`

원인: `DPP_DB_SSLROOTCERT` 미설정 또는 ConfigMap 미마운트
해결:
1. `kubectl -n dpp-production get configmap dpp-supabase-ca` → ConfigMap 존재 확인
2. `kubectl -n dpp-production describe pod <pod-name>` → Volume 마운트 상태 확인
3. 섹션 2 절차 재실행

### 파드 기동 실패: `RuntimeError: ... sslrootcert file is not found`

원인: CA 파일이 마운트 경로에 없음
해결:
```bash
kubectl -n dpp-production exec deploy/dpp-api -- ls -al /etc/ssl/certs/supabase-ca/
```
파일이 없으면 ConfigMap이 제대로 생성/마운트되지 않은 것 → 섹션 2, 4, 5 재실행

### 파드 기동 실패: `PRODUCTION GUARDRAIL: ... requires sslmode=verify-full`

원인: `DPP_DB_SSLMODE` 미설정 또는 `require`로 설정됨
해결: Manifest의 `DPP_DB_SSLMODE=verify-full` 환경변수 확인 후 `kubectl apply`

---

## 9. 롤백 절차

verify-full 적용 이전 상태로 일시 복구:

```bash
# 1. DPP_DB_SSLMODE를 require로 임시 패치 (kubectl set env)
kubectl -n dpp-production set env deploy/dpp-api DPP_DB_SSLMODE=require
kubectl -n dpp-production set env deploy/dpp-worker DPP_DB_SSLMODE=require
kubectl -n dpp-production set env deploy/dpp-reaper DPP_DB_SSLMODE=require
kubectl -n dpp-production set env deploy/dpp-ses-feedback-worker DPP_DB_SSLMODE=require

# 2. 롤아웃 확인
kubectl -n dpp-production rollout status deploy/dpp-api

# 주의: 이 상태는 임시입니다. 근본 원인 해결 후 반드시 verify-full로 복원하세요.
```

---

**이 런북은 ops/runbooks/db_ssl_verify_full.md 입니다.**
**CA 인증서 원본은 절대 이 파일 또는 Git 저장소에 포함하지 마십시오.**
