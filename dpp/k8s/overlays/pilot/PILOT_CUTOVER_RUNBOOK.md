# Pilot Cutover Runbook — api.decisionproof.io.kr 신규 호스트 추가

## 전제조건 (Preconditions — 완료 기준 + 증거 위치)

- [ ] 올바른 AWS 계정/프로파일로 로그인 (`AWS_PROFILE=dpp-admin` 또는 `--profile dpp-admin`)
- [ ] `kubectl` 컨텍스트 = `dpp-pilot` 클러스터
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
