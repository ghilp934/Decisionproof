# DEC-P04-SECRETS: Secrets Management — Spec Lock

**Decision ID**: DEC-P04-SECRETS
**Phase**: Phase 4.0
**Status**: LOCKED
**Date**: 2026-02-20
**Author**: DPP DevOps

---

## 결정 (Decision)

AWS Secrets Manager → ASCP(Secrets Store CSI Driver + AWS Provider) → Pod 파일 마운트 + Kubernetes Secret sync 방식을 DPP의 유일한 시크릿 주입 방식으로 잠금.

---

## 이유 (Rationale)

| 문제 | 이유 |
|------|------|
| 정적 Secret 매니페스트(stringData) | Repo에 시크릿 패턴이 남아 실수로 apply될 위험 |
| `kubectl create secret` 수동 절차 | 키/값이 터미널 히스토리·CI 로그에 노출될 수 있음 |
| 정적 키 없이 IRSA 전제 유지 | AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY 금지 |
| 코드 변경 최소화 | 기존 secretKeyRef(name=dpp-secrets) 유지로 앱 코드 무변경 |

---

## 설계 잠금 항목

### 1. 저장소

| 항목 | 값 |
|------|----|
| 서비스 | AWS Secrets Manager |
| Secret Name | `decisionproof/prod/dpp-secrets` |
| 형식 | JSON 1개 (다중 키 포함) |
| 리전 | `ap-northeast-2` |

**필수 JSON 키**:
- `database-url` — Supabase Pooler Transaction Mode (port 6543, IPv4)
- `redis-url`
- `redis-password`
- `sentry-dsn`
- `supabase-url`
- `sb-publishable-key`
- `sb-secret-key`

> ⚠️ `database-url` 형식 필수:
> `postgres://postgres.<PROJECT>:<PWD>@aws-0-ap-northeast-2.pooler.supabase.com:6543/postgres?sslmode=require`
> - Pooler Transaction Mode (port 6543) 유지
> - IPv4 endpoint 사용 (IPv6 Supabase direct 금지)

### 2. Kubernetes

| 항목 | 값 |
|------|----|
| SecretProviderClass 이름 | `dpp-secrets-aws` |
| 네임스페이스 (production) | `dpp-production` |
| 네임스페이스 (pilot) | `dpp-pilot` (별도 apply 필요) |
| CSI 마운트 경로 | `/mnt/secrets-store` |
| 마운트 볼륨 이름 | `secrets-store-inline` |
| sync Kubernetes Secret | `dpp-secrets` (기존 secretKeyRef 유지) |
| Secret type | `Opaque` |

### 3. IRSA 권한

| 항목 | 값 |
|------|----|
| 정책 파일 | `infra/policies/irsa_minimal_secretsmanager_read.json` |
| 필수 Action | `secretsmanager:GetSecretValue`, `secretsmanager:DescribeSecret` |
| 리소스 범위 | `decisionproof/prod/dpp-secrets-*` (ARN) |
| CMK 사용 시 | `kms:Decrypt` 별도 Sid 추가 (조건부) |
| 적용 대상 역할 | `dpp-api-role`, `dpp-worker-role`, `dpp-reaper-role`, SES feedback IRSA 역할 |

---

## 금지 사항 (NON-NEGOTIABLES)

- ❌ Repo 내 `kind: Secret` + `stringData:` 조합 매니페스트 금지
- ❌ `kubectl create secret generic dpp-secrets ...` 수동 생성 금지
- ❌ 어떤 로그/스크립트/터미널에서도 시크릿 값 출력 금지
  (`cat`, `echo`, `printenv`, `kubectl get secret -o yaml` 등)
- ❌ `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` 정적 키 주입 금지 (IRSA 사용)
- ❌ Supabase IPv6 direct endpoint 사용 금지 (Pooler IPv4 port 6543 유지)
- ❌ ACK 환경변수(`DPP_ACK_*`)를 Secrets Manager에 저장 금지 (ConfigMap 사용)

---

## sync 동작 주의

> ASCP secretObjects sync는 **Pod가 secrets-store CSI 볼륨을 실제로 마운트해야 동작**합니다.
> Pod 없이 SecretProviderClass만 apply하면 `dpp-secrets` Kubernetes Secret이 생성되지 않습니다.
> 배포 순서: SecretProviderClass apply → Pod 배포 → dpp-secrets 자동 생성 확인

---

## 관련 파일

| 역할 | 파일 |
|------|------|
| ASCP 매니페스트 | `k8s/secretproviderclass-dpp-secrets.yaml` |
| IRSA 최소권한 정책 | `infra/policies/irsa_minimal_secretsmanager_read.json` |
| 운영 런북 | `ops/runbooks/secrets_ascp_irsa.md` |
| 안내(구 template) | `k8s/secrets.yaml` (apply 금지, 안내 주석만) |
