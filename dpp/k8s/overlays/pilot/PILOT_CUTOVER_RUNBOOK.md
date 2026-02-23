# Pilot Cutover Runbook — api.decisionproof.io.kr 신규 호스트 추가

## Preflight: dpp-demo-secrets 필수 점검 (Fail-Closed)

> **STOP** — 이 섹션을 완료하지 않으면 demo 엔드포인트가 503으로 막힙니다.

### 필수 Secret 3키 (모두 필수, `optional: false`)

| Key | 출처 | 설명 |
|-----|------|------|
| `RAPIDAPI_PROXY_SECRET` | RapidAPI 대시보드 → API → Configuration → Security | RapidAPI가 모든 요청에 주입하는 Proxy Secret |
| `DP_DEMO_SHARED_TOKEN` | 직접 생성 (`python3 -c "import secrets; print(secrets.token_hex(32))"`) | Bearer 토큰 검증용 |
| `DEMO_ACTOR_KEY_SALT` | 직접 생성 (`python3 -c "import secrets; print(secrets.token_hex(24))"`) | HMAC Actor Key 파생용 salt (설정 후 불변) |

### Secret 생성 / 갱신 커맨드 (권장: dry-run | apply)

```bash
# 신규 생성 (3키 모두 한 번에)
kubectl -n dpp-pilot create secret generic dpp-demo-secrets \
  --from-literal=RAPIDAPI_PROXY_SECRET=<RapidAPI에서_복사> \
  --from-literal=DP_DEMO_SHARED_TOKEN=<직접_생성> \
  --from-literal=DEMO_ACTOR_KEY_SALT=<직접_생성> \
  --dry-run=client -o yaml | kubectl apply -f -

# 단일 키 업데이트 (JSON patch — base64 주의사항 참조)
ENCODED=$(echo -n "<새_값>" | base64 -w 0)
kubectl -n dpp-pilot patch secret dpp-demo-secrets \
  --type='json' \
  -p="[{\"op\":\"replace\",\"path\":\"/data/RAPIDAPI_PROXY_SECRET\",\"value\":\"${ENCODED}\"}]"
```

> **base64 주의**: `kubectl patch`에서 `/data` 값은 base64 인코딩이어야 합니다.
> `echo -n "<값>" | base64 -w 0` 처럼 **개행 없이(`-w 0`)** 인코딩하세요.
> 개행이 포함되면 시크릿 값이 달라져 인증이 실패합니다.

### Preflight 확인

```bash
# Secret 존재 여부 확인 (값은 표시 안 됨)
kubectl -n dpp-pilot get secret dpp-demo-secrets -o jsonpath='{.data}' \
  | python3 -c "import json,sys,base64; d=json.load(sys.stdin); [print(f'{k}: {len(base64.b64decode(v))} bytes') for k,v in d.items()]"

# 기대 출력 (3키 모두 표시):
# DEMO_ACTOR_KEY_SALT: 48 bytes
# DP_DEMO_SHARED_TOKEN: 64 bytes
# RAPIDAPI_PROXY_SECRET: <RapidAPI 값 길이> bytes
```

---

## Image Pin Policy (Option B: digest)

> **정책**: Pilot 배포는 반드시 `image@sha256:<digest>` 형식으로 고정한다.
> Tag(`:0.4.2.2`)는 mutable이므로 ECR에서 덮어쓰기 가능 — 배포 드리프트 발생 위험.
> Digest는 immutable — 동일 SHA 보장.

### Digest 조회 (배포 전 필수)

```bash
# dpp-api digest
aws ecr describe-images --profile dpp-admin --region ap-northeast-2 \
  --repository-name dpp-api \
  --image-ids imageTag=0.4.2.2 \
  --query 'imageDetails[0].imageDigest' --output text

# dpp-worker digest
aws ecr describe-images --profile dpp-admin --region ap-northeast-2 \
  --repository-name dpp-worker \
  --image-ids imageTag=0.4.2.2 \
  --query 'imageDetails[0].imageDigest' --output text

# dpp-reaper digest
aws ecr describe-images --profile dpp-admin --region ap-northeast-2 \
  --repository-name dpp-reaper \
  --image-ids imageTag=0.4.2.2 \
  --query 'imageDetails[0].imageDigest' --output text
```

### 적용 형식

```yaml
# patch-api-deployment-pilot.yaml
image: 783268398937.dkr.ecr.ap-northeast-2.amazonaws.com/dpp-api@sha256:<digest>
```

### 적용 후 확인

```bash
# dpp-api 이미지가 @sha256 형식인지 확인
kubectl -n dpp-pilot get deploy dpp-api \
  -o jsonpath='{.spec.template.spec.containers[0].image}'

# RC-15 gate 통과 확인
pytest -q -o addopts= apps/api/tests/test_rc15_k8s_image_digest_pin.py
```

---

## 전제조건 (Preconditions — 완료 기준 + 증거 위치)

- [ ] 올바른 AWS 계정/프로파일로 로그인 (`AWS_PROFILE=dpp-admin` 또는 `--profile dpp-admin`)
- [ ] `kubectl` 컨텍스트 = `dpp-pilot` 클러스터
- [ ] **`dpp-demo-secrets` 3키 모두 생성 완료** (위 Preflight 섹션 참조)
- [ ] `sync_pilot_values.sh` 실행 완료 — ingress-pilot.yaml에 `api.decisionproof.io.kr` host rule 존재 확인
- [ ] ACM 인증서가 `api.decisionproof.io.kr` 을 SAN으로 포함하는지 확인 (아래 TLS 섹션 참조)
- [ ] Route 53에 `api.decisionproof.io.kr` A(ALIAS) 레코드가 Pilot ALB를 가리키는지 확인

---

## Phase 1: Ingress 호스트 동기화

### 1-1. sync_pilot_values.sh 실행

```bash
cd dpp/k8s/overlays/pilot
chmod +x sync_pilot_values.sh
./sync_pilot_values.sh
```

기대 출력:
```
✅ host rules after sync: 2
✅ sync complete: ingress-pilot.yaml
   Synced hosts:
   - api-pilot.decisionproof.io.kr
   - api.decisionproof.io.kr
```

### 1-2. ingress 적용

```bash
kubectl --profile dpp-admin -n dpp-pilot apply -k dpp/k8s/overlays/pilot/
```

### 1-3. Ingress host rules 확인 (C2)

```bash
kubectl --profile dpp-admin -n dpp-pilot get ingress dpp-api-ingress -o wide
```

기대: `HOSTS` 컬럼에 두 호스트가 모두 표시
```
NAME              CLASS  HOSTS                                                        ...
dpp-api-ingress   alb    api-pilot.decisionproof.io.kr,api.decisionproof.io.kr       ...
```

---

## Phase 2: TLS(ACM cert) 커버리지 확인

현재 `certificate-arn`: `arn:aws:acm:ap-northeast-2:783268398937:certificate/8b0225d0-8ff7-40ea-80fb-2b11167ca77c`

### 2-1. cert SAN 확인

```bash
aws acm describe-certificate \
  --profile dpp-admin \
  --region ap-northeast-2 \
  --certificate-arn arn:aws:acm:ap-northeast-2:783268398937:certificate/8b0225d0-8ff7-40ea-80fb-2b11167ca77c \
  --query 'Certificate.SubjectAlternativeNames'
```

기대: `api.decisionproof.io.kr` 또는 `*.decisionproof.io.kr` 이 목록에 포함

### 2-2. cert에 해당 도메인이 없는 경우 (TLS 대안)

**옵션 A: 새 cert 요청 (SAN 추가)**
```bash
aws acm request-certificate \
  --profile dpp-admin \
  --region ap-northeast-2 \
  --domain-name "api.decisionproof.io.kr" \
  --subject-alternative-names "api-pilot.decisionproof.io.kr" "api.decisionproof.io.kr" \
  --validation-method DNS
# → 새 cert ARN을 ingress-pilot.yaml certificate-arn에 교체
```

**옵션 B: 기존 cert ARN 콤마 나열 (SNI)**
```yaml
# ingress-pilot.yaml annotations:
alb.ingress.kubernetes.io/certificate-arn: >-
  arn:aws:acm:...:certificate/기존ARN,
  arn:aws:acm:...:certificate/신규ARN
```

---

## Phase 3: DNS (Route 53) 설정

### 3-1. Pilot ALB DNS 확인

```bash
kubectl --profile dpp-admin -n dpp-pilot get ingress dpp-api-ingress \
  -o jsonpath='{.status.loadBalancer.ingress[0].hostname}'
```

출력 예: `k8s-dpppilot-dppapiin-xxxxxxxxxx.ap-northeast-2.elb.amazonaws.com`

### 3-2. Route 53 레코드 생성

Route 53 콘솔 또는 CLI:
```bash
# Hosted Zone ID 확인
aws route53 list-hosted-zones-by-name \
  --profile dpp-admin \
  --dns-name "decisionproof.io.kr" \
  --query 'HostedZones[0].Id' --output text

# A (ALIAS) 레코드 생성 예시 (change-batch.json 파일 필요)
aws route53 change-resource-record-sets \
  --profile dpp-admin \
  --hosted-zone-id <HOSTED_ZONE_ID> \
  --change-batch file://change-batch.json
```

`change-batch.json` 예시:
```json
{
  "Changes": [{
    "Action": "UPSERT",
    "ResourceRecordSet": {
      "Name": "api.decisionproof.io.kr",
      "Type": "A",
      "AliasTarget": {
        "HostedZoneId": "Z35SXDOTRQ7X7K",
        "DNSName": "<ALB_DNS_NAME>",
        "EvaluateTargetHealth": true
      }
    }
  }]
}
```

---

## Phase 4: 검증 (Verification — 최소 3개 커맨드)

DNS 전파 완료 후 (보통 1~5분):

### 검증 1: DNS 해석

```bash
nslookup api.decisionproof.io.kr
# 기대: ALB IP 또는 ALB CNAME 반환
```

### 검증 2: readyz 200

```bash
curl -skI https://api.decisionproof.io.kr/readyz
# 기대: HTTP/2 200
```

### 검증 3: openapi-demo servers 확인

```bash
curl -sk https://api.decisionproof.io.kr/.well-known/openapi-demo.json \
  | python3 -m json.tool | python3 -c "
import json, sys
d = json.load(sys.stdin)
print('servers:', d.get('servers'))
print('paths:', sorted(d.get('paths', {}).keys()))
"
```

기대:
```
servers: [{'url': 'https://api.decisionproof.io.kr', 'description': 'Mini Demo (Marketplace)'}]
paths: ['/v1/demo/runs', '/v1/demo/runs/{run_id}']
```

### 검증 4: 기존 pilot 호스트 동작 유지 확인

```bash
curl -skI https://api-pilot.decisionproof.io.kr/readyz
# 기대: HTTP/2 200 (기존 host 영향 없음)
```

---

## Phase 5: 롤백 포인트

문제 발생 시:
1. **Ingress 호스트만 되돌리기**: `ingress-pilot.yaml`에서 `api.decisionproof.io.kr` host rule 제거 후 재apply
2. **DNS 롤백**: Route 53에서 `api.decisionproof.io.kr` A 레코드 삭제
3. **전체 롤백**: `git revert <commit>` 후 `kubectl apply`

---

## DO NOT

- `api-pilot.decisionproof.io.kr` host rule 제거 금지 (기존 호스트 유지)
- `ingress-pilot.yaml` 마커 구간(`BEGIN/END:SYNC_HOST_RULES`) 직접 편집 금지 (sync 스크립트 통해서만)
- TLS 인증서 없이 HTTPS 트래픽 수신 시도 금지 (SNI 핸드셰이크 실패)
