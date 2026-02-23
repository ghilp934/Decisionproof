# PR: Mini Demo Marketplace — P0 Lockdown + Digest Pin + RC-14/15

## 브랜치
`feature/mini-demo-marketplace` → `main`
**머지 방식**: Squash merge (히스토리 단일 커밋 유지)

---

## 변경 요약

### 1) Mini Demo 엔드포인트 구현
- `POST /v1/demo/runs` — 데모 실행 생성 (202 Accepted)
- `GET /v1/demo/runs/{run_id}` — 폴링 (3s rate limit)
- `GET /.well-known/openapi-demo.json` — Public surface lock (서버 1개, 경로 2개)

### 2) P0 Lockdown (Fail-Closed Auth)
- `RAPIDAPI_PROXY_SECRET` 미설정 시 → 503 반환 (silent bypass 제거)
- K8s `secretKeyRef optional: false` — Secret 미존재 시 Pod 기동 실패
- RFC 9457 Problem Details: `type/title/status/detail/instance` 모든 에러 응답 포함

### 3) Image Digest Pin (Option B)
- pilot overlay 3개 Deployment를 `@sha256:` digest로 고정
  - dpp-api: `sha256:29b5e1c4...fb` (bearer-optional 빌드)
  - dpp-worker: `sha256:12cc60dd...40d`
  - dpp-reaper: `sha256:ecd3f17d...701`
- RC-15 gate 추가 → tag drift 재발 시 CI 즉시 FAIL

### 4) RC Gates (RC-14, RC-15)
- **RC-14** (`test_rc14_demo_marketplace_gate.py`): Fail-Closed 503, auth 401, poll 429, openapi-demo lock
- **RC-15** (`test_rc15_k8s_image_digest_pin.py`): K8s image digest pin 강제 (파일 읽기 전용, 0.05s)

### 5) Runbook / Ops
- `PILOT_CUTOVER_RUNBOOK.md`: Preflight 섹션 + Image Pin Policy 섹션 추가
- `README_RC_GATES.md`: RC-14, RC-15 항목 추가
- Ingress `$patch:replace` SMP 버그 수정 → JSON 6902 patch 분리

---

## 증거: RC Gate 결과

```
RC-14 + RC-15: 27 passed, 0 failed, 2 warnings in 3.74s
RC-14 단독   : 12 passed (Bearer optional 변경으로 1개 삭제)
RC-15 단독   : 15 passed (0.05s, 파일 읽기 전용)
```

전체 demo 테스트 (`test_demo_smoke + test_demo_contracts + test_rc14`):
```
86 passed, 0 failed in 197s
```

Rapid Runtime 블랙박스 스모크 (`rapid_runtime_smoke.ps1`):
```
C1: SKIP (not in RapidAPI spec by design)
C2-1 POST /v1/demo/runs   → 202, run_id 확인, x-dp-ai-* headers
C2-2 GET poll 1           → 200, status=COMPLETED
C2-3 GET poll 2 immediate → 429, Retry-After=3, problem+json, instance/detail 확인
PASS: 8  FAIL: 0  — ALL PASS
```

---

## 롤백 방법 (3줄)

```bash
# 1) 이전 digest 값으로 patch yaml 3개 복원 후:
git revert <this-squash-commit>
# 2) kustomize apply
kubectl apply -k k8s/overlays/pilot
# 3) rollout 완료 대기
kubectl -n dpp-pilot rollout status deployment/dpp-api
```

---

## 체크리스트 (Squash merge 전 확인)

- [x] RC-14 passed (28/28)
- [x] RC-15 passed (15/15)
- [x] Live smoke: `api.decisionproof.io.kr` PASS (7/7 체크)
- [x] Live smoke: `api-pilot.decisionproof.io.kr` PASS (7/7 체크)
- [x] image digest pin 3개 kustomize render 검증 완료
- [x] Secret 3키 K8s 적용 완료 (RAPIDAPI: 실제 키)
- [ ] GitHub PR "Squash and merge" 설정 확인
- [x] Rapid Runtime 경유 블랙박스 스모크 PASS (8/8)
