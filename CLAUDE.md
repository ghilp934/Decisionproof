# Decisionproof Mini Demo (Marketplace) — Claude Instructions

## 0) Mission
RapidAPI + Postman 공개용 “Mini Demo” 표면을 안정적으로 제공한다.
목표는 “작동하는 데모 경로 1개 + 계약(스펙) 불변 + 회귀 테스트로 재발 방지”다.

## 1) Non-Negotiables (LOCK)
- Public Base URL (Marketplace): https://api.decisionproof.io.kr
- Public Demo OpenAPI: GET /.well-known/openapi-demo.json
- Public Demo Endpoints (ONLY):
  1) POST /v1/demo/runs
  2) GET  /v1/demo/runs/{run_id}
- openapi-demo.json MUST satisfy:
  - servers MUST be length=1
  - servers[0].url MUST equal "https://api.decisionproof.io.kr"
  - paths MUST be exactly the 2 demo paths above (no leakage)

## 2) Scope (What you MAY change) — 정확한 경로 리스트 (ZIP 기준)

### 2.1 K8S / Pilot Overlay (Ingress + Env + Runbook)
(호스트 추가, env 주입, runbook 업데이트는 여기서만 한다 — “pilot overlay SSOT” 유지)

- dpp/k8s/overlays/pilot/pilot.params.yaml
  - PILOT_HOST_ALIASES 등 host alias 파라미터(필요 시) 추가/수정

- dpp/k8s/overlays/pilot/ingress-pilot.yaml
  - api.decisionproof.io.kr host rule 추가(기존 api-pilot 유지)
  - 다중 host rule을 “SYNC_MARKER(마커) 기반 치환” 방식으로 운영(필요 시)

- dpp/k8s/overlays/pilot/sync_pilot_values.sh
  - pilot.params.yaml의 host(기본+aliases)를 읽어 ingress-pilot.yaml의 마커 구간을 “완전 치환”
  - 마커 미존재/REPLACE_ME 잔존/치환 실패 시 FAIL-FAST

- dpp/k8s/overlays/pilot/patch-configmap-pilot.yaml
  - DP_DEMO_PUBLIC_BASE_URL="https://api.decisionproof.io.kr" (demo 공개 URL 고정값) 주입

- dpp/k8s/overlays/pilot/patch-api-deployment-pilot.yaml
  - API Deployment에 DP_DEMO_PUBLIC_BASE_URL env가 주입되도록(예: configMapKeyRef) 연결

- dpp/k8s/overlays/pilot/PILOT_CUTOVER_RUNBOOK.md
  - DNS(Route53) + TLS(ACM/SNI) + curl 검증 절차를 runbook에 반영
  - 최소 검증 커맨드 3개 포함:
    - curl -skI https://api.decisionproof.io.kr/readyz  (200)
    - curl -sk https://api.decisionproof.io.kr/.well-known/openapi-demo.json
    - jq로 servers/paths keys 확인

(필요 시, base에도 env “키”만 추가할 수 있음 — 단, pilot override가 SSOT)
- dpp/k8s/base/configmap.yaml
- dpp/k8s/base/api-deployment.yaml
- dpp/k8s/base/ingress.yaml
- dpp/k8s/base/kustomization.yaml

### 2.2 API App (openapi-demo endpoint + demo router)
- dpp/apps/api/dpp_api/main.py
  - GET "/.well-known/openapi-demo.json" 엔드포인트 추가
  - DP_DEMO_PUBLIC_BASE_URL (default: https://api.decisionproof.io.kr) 읽어서
    - openapi_schema["servers"] 강제 덮어쓰기(servers 길이=1)
    - openapi_schema["paths"] allowlist 필터(2개 경로만 남기기)

- dpp/apps/api/dpp_api/routers/ (필요한 경우에 한해 최소 추가/수정)
  - dpp/apps/api/dpp_api/routers/demo.py   (신규 생성 가능)
    - POST /v1/demo/runs, GET /v1/demo/runs/{run_id} 라우트 제공(공개 표면 2개만)
  - dpp/apps/api/dpp_api/routers/__init__.py (필요 시 demo router export)
  - dpp/apps/api/dpp_api/routers/health.py (readyz 경로가 이미 있다면 그대로 사용)

(스키마/상수/유틸이 필요한 경우 최소 침습으로만)
- dpp/apps/api/dpp_api/schemas.py
- dpp/apps/api/dpp_api/constants.py

### 2.3 Tests (AC: Base URL/paths 흔들림 즉시 FAIL)
- dpp/apps/api/tests/test_openapi_demo.py (신규 생성 권장)
  - GET /.well-known/openapi-demo.json -> 200
  - servers == [{"url":"https://api.decisionproof.io.kr", ...}] (len=1)
  - paths keys == {"/v1/demo/runs", "/v1/demo/runs/{run_id}"} (정확히 일치)
  - 다른 path가 섞이면 FAIL

(필요 시, 기존 계약/문서 관련 테스트에 보조 assertion 추가 가능)
- dpp/apps/api/tests/test_rc1_contract.py  (선택)

## 3) Out of Scope (Do NOT)
- 기존 전체 OpenAPI 문서(예: /.well-known/openapi.json 등)의 동작/스키마 변경 금지
- 기존 pilot host(api-pilot.decisionproof.io.kr) 제거 금지
- Secrets/키/토큰을 repo에 커밋 금지 (env 파일/credentials/kube secret 포함)
- “일단 되게” 식 임시 우회(placeholder, 평문 secret, 무근거 ACK) 금지

## 4) Workflow (Do the work in this order)
1) Gap-First: 현재 상태 vs 목표(LOCK) vs 갭을 10줄 내로 적고 시작
2) Pilot Ingress/Host → App openapi-demo endpoint → AC tests → Runbook 순으로 고정
3) 모든 변경은 FAIL-FAST: 조건이 불충분하면 조용히 넘어가지 말고 즉시 실패하게 만든다.

## 5) Evidence-First Completion Report (Mandatory)
PR/완료 보고에 아래를 반드시 포함:
- 변경 파일 목록(경로 + 변경 요약 1줄)
- AC 테스트 결과(테스트명 + PASS 로그 1줄)
- curl 검증 커맨드 3개 + 기대값:
  - curl -skI https://api.decisionproof.io.kr/readyz  (200)
  - curl -sk https://api.decisionproof.io.kr/.well-known/openapi-demo.json | jq '.servers'
  - curl -sk https://api.decisionproof.io.kr/.well-known/openapi-demo.json | jq '.paths | keys'
- 롤백 포인트(무엇을 되돌리면 되는지)

## 6) Definition of Done (DoD)
PASS iff all are true:
- Unit tests PASS and enforce:
  - servers[0].url == https://api.decisionproof.io.kr
  - paths == { /v1/demo/runs, /v1/demo/runs/{run_id} }
- Pilot ingress에 api.decisionproof.io.kr host rule이 존재
- DNS가 올바르게 연결되고, HTTPS 핸드셰이크가 성공
- openapi-demo.json이 200으로 응답하며, allowlist 외 path가 0개