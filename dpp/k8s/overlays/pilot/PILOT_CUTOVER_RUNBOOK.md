# DPP Pilot Cutover Runbook

**전제**: ACM 인증서 ISSUED 확인 완료 / `ops/runbooks/pilot_cutover_run.md` 사전 체크리스트 완료

---

## Phase 0 — 초기 배포 (dpp-api 단독)

### 방법 A — PowerShell (ExecutionPolicy 설정 필요, 1회만)

```powershell
# 0. PS1 실행 권한 허용 (1회만 — 이미 설정돼 있으면 생략)
Set-ExecutionPolicy RemoteSigned -Scope CurrentUser

# 1. 컨텍스트 확인 → pilot_cutover.env 에 EXPECTED_KUBE_CONTEXT 확정
kubectl config get-contexts -o name

# 2. Supabase CA ConfigMap 생성 (최초 1회만)
.\k8s\overlays\pilot\create_supabase_ca_configmap.ps1 C:\path\to\supabase-ca.crt

# 3. 원클릭 컷오버 실행
.\k8s\overlays\pilot\pilot_cutover_execute.ps1
```

### 방법 B — git bash 터미널 (권장, 권한 설정 불필요)

```bash
# 1. 컨텍스트 확인 → pilot_cutover.env 에 EXPECTED_KUBE_CONTEXT 확정
kubectl config get-contexts -o name

# 2. Supabase CA ConfigMap 생성 (최초 1회만)
bash ./k8s/overlays/pilot/create_supabase_ca_configmap.sh /path/to/supabase-ca.crt

# 3. 원클릭 컷오버 실행
bash ./k8s/overlays/pilot/pilot_cutover_execute.sh
```

```powershell
# 롤백 필요 시 (PowerShell / bash 공통)
kubectl rollout undo deployment/dpp-api -n dpp-pilot
```

**완료 기준**: Step 6 출력에서 3개 Deployment READY, Ingress ADDRESS 할당 확인

---

## Phase 1 — HTTPS 활성화

> **전제**: ACM 인증서 ISSUED 완료, ALB SG 생성 완료
> `pilot_tls.env`에 실제 ARN/SG 값이 채워져 있어야 합니다.

```bash
# Step 1: pilot_tls.env 값 확인 (REPLACE_ME가 없는지 확인)
cat k8s/overlays/pilot/pilot_tls.env

# Step 2: SSOT → ingress-pilot.yaml 동기화
bash k8s/overlays/pilot/sync_pilot_tls_values.sh
# [OK] certificate-arn → arn:aws:acm:...
# [OK] security-groups → sg-...
# [OK] ingress-pilot.yaml updated. No REPLACE_ME remaining.

# Step 3: kustomize overlay 적용
kubectl apply -k k8s/overlays/pilot/

# Step 4: ALB HTTPS 리스너 적용 확인 (1~2분 소요)
kubectl get ingress -n dpp-pilot
# ADDRESS가 채워지고 PORTS에 80,443이 표시되면 정상

# Step 5: HTTPS 동작 확인
curl -I https://api-pilot.decisionproof.io.kr/health
# HTTP/2 200
```

⚠️ `pilot_tls.env` 값이 변경될 때마다 Step 2 → Step 3을 재실행하세요.
`ingress-pilot.yaml`을 직접 편집하지 마세요.

---

## Phase 2 — Worker/Reaper 이미지 빌드 & 배포

> **전제**: Docker 실행 중, AWS CLI dpp-admin 프로파일 설정 완료
> 프로젝트 루트(Dockerfile.worker, Dockerfile.reaper 위치)에서 실행합니다.

```bash
# Step 1: ECR 리포지토리 생성 + 이미지 빌드 & 푸시 (한 번에)
bash k8s/overlays/pilot/build_push_worker_reaper_images.sh
# [OK] dpp-worker pushed: 783268398937.dkr.ecr.ap-northeast-2.amazonaws.com/dpp-worker:0.4.2.2
# [OK] dpp-reaper pushed: 783268398937.dkr.ecr.ap-northeast-2.amazonaws.com/dpp-reaper:0.4.2.2

# Step 2: kustomize overlay 적용 (patch-worker/reaper replicas=1이 활성화됨)
kubectl apply -k k8s/overlays/pilot/

# Step 3: rollout 완료 대기
kubectl rollout status deployment/dpp-worker -n dpp-pilot --timeout=3m
kubectl rollout status deployment/dpp-reaper -n dpp-pilot --timeout=3m

# Step 4: 최종 상태 확인
kubectl get pods -n dpp-pilot
# dpp-api-*     3/3 Running
# dpp-worker-*  1/1 Running
# dpp-reaper-*  1/1 Running
```

**Worker readiness 조건**: `/tmp/worker-ready` 파일이 생성되어야 Ready 상태 전환
(dpp_worker.main 초기화 완료 후 자동 생성)

**Reaper readiness 조건**: `/tmp/reaper-ready` 파일이 생성되어야 Ready 상태 전환
(dpp_reaper.main 초기화 완료 후 자동 생성)

### Dockerfile 변경 후 이미지 재빌드 절차

Dockerfile.worker 또는 Dockerfile.reaper를 수정한 경우:

```bash
# 1. 이미지 재빌드 & ECR 푸시
bash k8s/overlays/pilot/build_push_worker_reaper_images.sh

# 2. 새 이미지로 롤링 재배포
kubectl rollout restart deployment/dpp-worker -n dpp-pilot
kubectl rollout restart deployment/dpp-reaper -n dpp-pilot

# 3. 완료 대기
kubectl rollout status deployment/dpp-worker -n dpp-pilot --timeout=3m
kubectl rollout status deployment/dpp-reaper -n dpp-pilot --timeout=3m
```

> 참고: `procps` 패키지가 이미지에 포함되어 있어야 `pgrep` probe가 동작합니다.
> Dockerfile.worker/reaper의 `apt-get install` 목록에 `procps`가 있는지 확인하세요.

---

## 운영자 실행 커맨드 (복붙용)

```bash
# ── HTTPS 활성화 ────────────────────────────────────────────────────
bash k8s/overlays/pilot/sync_pilot_tls_values.sh
kubectl apply -k k8s/overlays/pilot/

# ── Worker/Reaper 이미지 빌드 & 배포 ───────────────────────────────
bash k8s/overlays/pilot/build_push_worker_reaper_images.sh
kubectl apply -k k8s/overlays/pilot/
kubectl rollout status deployment/dpp-worker -n dpp-pilot --timeout=3m
kubectl rollout status deployment/dpp-reaper -n dpp-pilot --timeout=3m
kubectl get pods -n dpp-pilot
```
