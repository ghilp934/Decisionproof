# Runbook: Secrets 동적 주입 (ASCP + IRSA)

Doc ID: DEC-P04-SECRETS-RUNBOOK
Phase: 4.0
Last Updated: 2026-02-20

---

## 개요

DPP는 AWS Secrets Manager → ASCP(Secrets Store CSI Driver + AWS Provider) → Pod 마운트 방식으로 시크릿을 주입합니다.

```
AWS Secrets Manager
  └─ decisionproof/prod/dpp-secrets (JSON)
       └─ SecretProviderClass (dpp-secrets-aws)
            └─ Pod CSI 볼륨 마운트 (/mnt/secrets-store)
                 └─ dpp-secrets Kubernetes Secret (secretObjects sync)
                      └─ Deployment secretKeyRef (변경 없음)
```

> ⚠️ **sync 주의**: `dpp-secrets` Kubernetes Secret은 Pod가 CSI 볼륨을 마운트해야 생성됩니다.
> Pod 배포 없이 SecretProviderClass만 apply해서는 Secret이 생성되지 않습니다.

---

## 섹션 1: ASCP 설치 확인

### CRD 존재 확인

```bash
kubectl get crd secretproviderclasses.secrets-store.csi.x-k8s.io
# 예상: secretproviderclasses.secrets-store.csi.x-k8s.io   2026-XX-XXTXX:XX:XXZ
```

### CSI Driver DaemonSet 확인

```bash
kubectl get daemonset -n kube-system | grep secrets-store
# 예상: csi-secrets-store   ... 3   3   3   3   3   ...
```

### AWS Provider DaemonSet 확인

```bash
kubectl get daemonset csi-secrets-store-provider-aws -n kube-system
# 예상: DESIRED == READY
```

### 미설치 시 설치 명령

```bash
# CSI Driver 설치
helm repo add secrets-store-csi-driver \
  https://kubernetes-sigs.github.io/secrets-store-csi-driver/charts
helm install csi-secrets-store secrets-store-csi-driver/secrets-store-csi-driver \
  --namespace kube-system \
  --set syncSecret.enabled=true \
  --set enableSecretRotation=true

# AWS Provider 설치
kubectl apply -f \
  https://raw.githubusercontent.com/aws/secrets-store-csi-driver-provider-aws/main/deployment/aws-provider-installer.yaml
```

---

## 섹션 2: AWS Secrets Manager Secret 생성

> ⚠️ 값은 절대 터미널/로그에 출력하지 마십시오.
> AWS 콘솔 또는 아래 CLI에서 --secret-string 값은 실제 환경에서만 입력하십시오.

### Secret 구조 (JSON)

```json
{
  "database-url": "<SUPABASE_POOLER_TRANSACTION_URL_PORT_6543>",
  "redis-url": "<REDIS_URL>",
  "redis-password": "<REDIS_PASSWORD>",
  "sentry-dsn": "<SENTRY_DSN>",
  "supabase-url": "<SUPABASE_PROJECT_URL>",
  "sb-publishable-key": "<SUPABASE_ANON_KEY>",
  "sb-secret-key": "<SUPABASE_SERVICE_ROLE_KEY>"
}
```

> ⚠️ `database-url` 필수 형식:
> `postgres://postgres.<PROJECT_REF>:<PASSWORD>@aws-0-ap-northeast-2.pooler.supabase.com:6543/postgres?sslmode=require`
> - Pooler Transaction Mode: port **6543** (IPv4)
> - IPv6 Supabase direct endpoint 사용 금지

### CLI 생성 명령 (값은 실제 환경에서 직접 입력)

```bash
# 신규 생성
aws secretsmanager create-secret \
  --name "decisionproof/prod/dpp-secrets" \
  --description "DPP production secrets — managed by ASCP" \
  --region ap-northeast-2 \
  --profile dpp-admin
# ※ --secret-string 값은 AWS 콘솔에서 직접 입력 권장

# 기존 Secret 존재 확인 (값 출력 없이)
aws secretsmanager describe-secret \
  --secret-id "decisionproof/prod/dpp-secrets" \
  --region ap-northeast-2 \
  --profile dpp-admin \
  --query "[Name, ARN, LastChangedDate]"
```

---

## 섹션 3: IRSA 정책 attach

정책 파일: `infra/policies/irsa_minimal_secretsmanager_read.json`

> `<ACCOUNT_ID>` 와 (CMK 사용 시) `<CMK_KEY_ID>` 를 실제 값으로 치환하십시오.
> CMK를 사용하지 않는 경우 `AllowCMKDecryptIfUsed` Statement는 제거합니다.

### 콘솔 (권장)

1. IAM → Roles → `dpp-api-role` 선택
2. Add permissions → Create inline policy 또는 Attach policies
3. 정책 내용: `infra/policies/irsa_minimal_secretsmanager_read.json` 참조
4. `dpp-worker-role`, `dpp-reaper-role`, SES feedback IRSA 역할도 동일하게 적용

### CLI

```bash
# 정책 생성
aws iam create-policy \
  --policy-name DppSecretsManagerRead \
  --policy-document file://infra/policies/irsa_minimal_secretsmanager_read.json \
  --profile dpp-admin

# 역할에 attach (각 역할에 반복)
for ROLE in dpp-api-role dpp-worker-role dpp-reaper-role; do
  aws iam attach-role-policy \
    --role-name "${ROLE}" \
    --policy-arn "arn:aws:iam::<ACCOUNT_ID>:policy/DppSecretsManagerRead" \
    --profile dpp-admin
done
```

---

## 섹션 4: SecretProviderClass apply

```bash
# production 네임스페이스
kubectl apply -f k8s/secretproviderclass-dpp-secrets.yaml

# pilot 네임스페이스 (별도 적용 필요)
# metadata.namespace를 dpp-pilot으로 변경하여 적용
kubectl apply -f <(sed 's/namespace: dpp-production/namespace: dpp-pilot/' \
  k8s/secretproviderclass-dpp-secrets.yaml)

# 적용 확인
kubectl get secretproviderclass dpp-secrets-aws -n dpp-production
```

---

## 섹션 5: 검증 (값 출력 절대 금지)

### 5-1. Pod 내 파일 존재/크기 확인

```bash
# ✅ 안전한 방법: 파일 존재+크기만 확인 (내용 출력 없음)
kubectl exec -it <pod-name> -n dpp-production -- \
  sh -c '
    for f in database-url redis-url redis-password sentry-dsn supabase-url sb-publishable-key sb-secret-key; do
      if test -s /mnt/secrets-store/$f; then
        echo "OK: $f"
      else
        echo "MISSING or EMPTY: $f"
      fi
    done
  '

# ❌ 절대 사용 금지
# cat /mnt/secrets-store/database-url
# printenv DATABASE_URL
```

### 5-2. Kubernetes Secret 존재 확인 (메타데이터만)

```bash
# ✅ 안전한 방법: 존재 여부만 확인
kubectl get secret dpp-secrets -n dpp-production

# ❌ 절대 사용 금지
# kubectl get secret dpp-secrets -n dpp-production -o yaml
# kubectl get secret dpp-secrets -n dpp-production -o json
```

### 5-3. 애플리케이션 readiness 확인

```bash
# API /readyz 응답으로 DB/Redis 연결 정상 여부 간접 확인
kubectl port-forward svc/dpp-api 8000:80 -n dpp-production &
curl -s http://localhost:8000/readyz | python3 -m json.tool
# 예상: {"status": "ready", "services": {"database": "up", "redis": "up", ...}}
```

### 5-4. SecretProviderClass sync 상태 확인

```bash
kubectl describe secretproviderclass dpp-secrets-aws -n dpp-production
# Events 섹션에 오류 없는지 확인 (값 출력 없음)
```

---

## 섹션 6: 트러블슈팅

> ⚠️ provider pod 로그 확인 시 시크릿 값 노출 위험이 있습니다.
> 로그 출력 범위를 최소화하고, 값 포함 줄은 grep으로 필터링하십시오.

### Pod가 CrashLoopBackOff / Pending

```bash
kubectl describe pod <pod-name> -n dpp-production | grep -A5 "Volumes\|Events"
# CSI 마운트 실패 메시지 확인
```

### SecretProviderClass sync 실패

```bash
# provider pod 로그 (에러 라인만)
kubectl logs -n kube-system \
  -l app=csi-secrets-store-provider-aws \
  --tail=50 | grep -i "error\|fail\|denied"
# ※ 값이 포함된 라인이 출력될 수 있으므로 신중하게 확인
```

**주요 원인**:
- IRSA 권한 미부여 → Sections 3 재수행
- Secret Name 오타 → `decisionproof/prod/dpp-secrets` 정확히 확인
- JSON 키 누락 → Secrets Manager에서 키 목록 확인 (값 확인 금지)
- CRD 미설치 → Section 1 재수행

### dpp-secrets Secret이 생성되지 않음

가장 흔한 원인: **Pod가 secrets-store 볼륨을 마운트하지 않음**

```bash
# Pod의 volumes 섹션 확인
kubectl get pod <pod-name> -n dpp-production -o jsonpath='{.spec.volumes[*].name}'
# secrets-store-inline 이 목록에 있어야 함
```

---

**End of Runbook**
