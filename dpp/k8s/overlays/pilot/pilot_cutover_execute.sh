#!/usr/bin/env bash
# pilot_cutover_execute.sh
# DPP Pilot Cutover 원클릭 실행 스크립트
#
# 실행 순서:
#   (0) pilot_cutover.env 로딩 → kube-context guard → namespace guard
#   (1) SSOT sync + PRE-GATE PASS 확인
#   (2) dpp-supabase-ca ConfigMap 존재 확인
#   (3) DB 마이그레이션 Job → wait → logs
#   (4) App 배포 (kubectl apply -k)
#   (5) Rollout 상태 확인 (api/reaper/worker)
#   (6) 상태 요약 출력
#
# 사용법:
#   chmod +x pilot_cutover_execute.sh
#   ./pilot_cutover_execute.sh
#
# 전제조건:
#   - pilot_cutover.env 의 EXPECTED_KUBE_CONTEXT 를 실제 값으로 채울 것
#   - dpp-supabase-ca ConfigMap 사전 생성: ./create_supabase_ca_configmap.sh /path/to/supabase-ca.crt
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
TIMESTAMP="$(date +%Y%m%d_%H%M%S)"
LOG_FILE="/tmp/dpp_pilot_cutover_${TIMESTAMP}.log"

# 모든 출력을 터미널과 로그 파일에 동시 기록
exec > >(tee -a "$LOG_FILE") 2>&1

# Windows(git bash) 환경에서 kubectl에 전달할 경로를 Windows 형식으로 변환
if command -v cygpath >/dev/null 2>&1; then
  KUBECTL_PATH="$(cygpath -w "$SCRIPT_DIR")"
else
  KUBECTL_PATH="$SCRIPT_DIR"
fi

echo "================================================================"
echo " DPP Pilot Cutover Execute"
echo "================================================================"
echo " Started : $(date)"
echo " Log     : $LOG_FILE"
echo " Overlay : $KUBECTL_PATH"
echo "================================================================"
echo ""

# ── Step 0: pilot_cutover.env 로딩 + Guards ──────────────────────────────
echo "=== [Step 0/6] Environment Guard ==="

ENV_FILE="$SCRIPT_DIR/pilot_cutover.env"
if [ ! -f "$ENV_FILE" ]; then
  echo "FAIL: pilot_cutover.env not found at $ENV_FILE"
  echo "  Create it and set EXPECTED_KUBE_CONTEXT before running this script."
  exit 1
fi

# shellcheck disable=SC1090
source "$ENV_FILE"

# ── Guard 0-A: EXPECTED_KUBE_CONTEXT 설정 확인 ───────────────────────────
if [ -z "${EXPECTED_KUBE_CONTEXT:-}" ] || [[ "${EXPECTED_KUBE_CONTEXT}" == REPLACE_ME* ]]; then
  echo "FAIL: EXPECTED_KUBE_CONTEXT is not configured in pilot_cutover.env"
  echo ""
  echo "  (1) List available kube contexts:"
  echo "      kubectl config get-contexts -o name"
  echo ""
  echo "      Available now:"
  kubectl config get-contexts -o name 2>/dev/null | sed 's/^/        /' || echo "        (kubectl unreachable)"
  echo ""
  echo "  (2) Switch to the target context:"
  echo "      kubectl config use-context <CONTEXT_NAME>"
  echo ""
  echo "  (3) Set EXPECTED_KUBE_CONTEXT in pilot_cutover.env:"
  echo "      EXPECTED_KUBE_CONTEXT=\"<CONTEXT_NAME>\""
  exit 1
fi

# ── Guard 0-B: kube-context 불일치 검사 ──────────────────────────────────
CURRENT_CONTEXT="$(kubectl config current-context 2>/dev/null || echo '__NONE__')"
if [ "$CURRENT_CONTEXT" != "$EXPECTED_KUBE_CONTEXT" ]; then
  echo "FAIL: Kube context mismatch"
  echo "  Current context:  $CURRENT_CONTEXT"
  echo "  Expected context: $EXPECTED_KUBE_CONTEXT"
  echo ""
  echo "  To switch: kubectl config use-context $EXPECTED_KUBE_CONTEXT"
  echo ""
  echo "  Available contexts:"
  kubectl config get-contexts -o name 2>/dev/null | sed 's/^/    /' || echo "    (kubectl unreachable)"
  exit 1
fi
echo "  OK [ctx]  context = $CURRENT_CONTEXT"

# ── Guard 0-C: namespace 존재 확인 ───────────────────────────────────────
if ! kubectl get ns "${EXPECTED_NAMESPACE}" >/dev/null 2>&1; then
  if [ "${ALLOW_CREATE_NAMESPACE:-0}" = "1" ]; then
    echo "  WARN: Namespace ${EXPECTED_NAMESPACE} not found — ALLOW_CREATE_NAMESPACE=1, creating..."
    kubectl apply -f "$KUBECTL_PATH/namespace.yaml"
  else
    echo "FAIL: Namespace '${EXPECTED_NAMESPACE}' not found in the cluster."
    echo ""
    echo "  The namespace must exist before this script runs."
    echo "  If this is a fresh install, set ALLOW_CREATE_NAMESPACE=1 in pilot_cutover.env"
    echo "  to allow automatic creation from namespace.yaml."
    exit 1
  fi
fi
echo "  OK [ns]   namespace = ${EXPECTED_NAMESPACE}"
echo ""

# ── Step 1: SSOT sync + PRE-GATE ─────────────────────────────────────────
echo "=== [Step 1/6] SSOT Sync + PRE-GATE ==="
bash "$SCRIPT_DIR/sync_pilot_values.sh"
echo ""
bash "$SCRIPT_DIR/pre_gate_check.sh"
echo ""

# ── Step 2: dpp-supabase-ca ConfigMap 확인 ───────────────────────────────
echo "=== [Step 2/6] Supabase CA ConfigMap ==="
if ! kubectl get configmap dpp-supabase-ca -n "${EXPECTED_NAMESPACE}" >/dev/null 2>&1; then
  echo "FAIL: ConfigMap 'dpp-supabase-ca' not found in namespace ${EXPECTED_NAMESPACE}"
  echo ""
  echo "  Create it first:"
  echo "    $SCRIPT_DIR/create_supabase_ca_configmap.sh /path/to/supabase-ca.crt"
  echo ""
  echo "  See: ops/runbooks/db_ssl_verify_full.md"
  exit 1
fi
echo "  OK: dpp-supabase-ca ConfigMap exists"
echo ""

# ── Step 3: DB 마이그레이션 Job ──────────────────────────────────────────
echo "=== [Step 3/6] DB Migration Job ==="
echo "  Applying alembic-migrate job..."
kubectl apply -n "${EXPECTED_NAMESPACE}" -f "$KUBECTL_PATH/job-alembic-migrate.yaml"
echo ""
echo "  Waiting for completion (timeout: 600s)..."
if ! kubectl wait -n "${EXPECTED_NAMESPACE}" \
    --for=condition=complete \
    --timeout=600s \
    job/alembic-migrate; then
  echo ""
  echo "FAIL: alembic-migrate job did not complete within 600s"
  echo ""
  echo "--- kubectl describe job/alembic-migrate ---"
  kubectl describe -n "${EXPECTED_NAMESPACE}" job/alembic-migrate || true
  echo ""
  echo "--- kubectl logs job/alembic-migrate (last 200 lines) ---"
  kubectl logs -n "${EXPECTED_NAMESPACE}" job/alembic-migrate --tail=200 || true
  exit 1
fi
echo ""
echo "  Migration logs (last 50 lines):"
kubectl logs -n "${EXPECTED_NAMESPACE}" job/alembic-migrate --tail=50 | sed 's/^/  | /' || true
echo ""

# ── Step 4: App 배포 ──────────────────────────────────────────────────────
echo "=== [Step 4/6] App Deploy ==="

# ingress manage-backend-security-group-rules 누락 경고
if ! grep -q "manage-backend-security-group-rules" "$SCRIPT_DIR/ingress-pilot.yaml" 2>/dev/null; then
  echo "  WARN: 'manage-backend-security-group-rules' annotation missing in ingress-pilot.yaml"
  echo "        Custom SG 사용 시 ALB Controller가 백엔드 규칙을 관리하지 못할 수 있습니다."
fi

echo "  Running: kubectl apply -k $KUBECTL_PATH"
kubectl apply -k "$KUBECTL_PATH"
echo ""

# ── Step 5: Rollout 상태 확인 ────────────────────────────────────────────
echo "=== [Step 5/6] Rollout Status ==="
for deploy in dpp-api dpp-reaper dpp-worker; do
  echo "  Waiting: $deploy (timeout: 300s)..."
  if ! kubectl rollout status \
      deployment/"$deploy" \
      -n "${EXPECTED_NAMESPACE}" \
      --timeout=300s; then
    echo ""
    echo "FAIL: rollout failed — $deploy"
    echo ""
    echo "--- kubectl describe deploy/$deploy ---"
    kubectl describe -n "${EXPECTED_NAMESPACE}" deployment/"$deploy" || true
    echo ""
    echo "--- Recent pod logs ($deploy) ---"
    POD="$(kubectl get pod \
      -n "${EXPECTED_NAMESPACE}" \
      -l "app=$deploy" \
      --sort-by=.metadata.creationTimestamp \
      -o jsonpath='{.items[-1:].metadata.name}' 2>/dev/null || true)"
    if [ -n "$POD" ]; then
      kubectl logs -n "${EXPECTED_NAMESPACE}" "$POD" --tail=100 | sed 's/^/  | /' || true
    else
      echo "  (no pod found for app=$deploy)"
    fi
    exit 1
  fi
  echo "    OK: $deploy rollout complete"
done
echo ""

# ── Step 6: 상태 요약 ────────────────────────────────────────────────────
echo "=== [Step 6/6] Status Summary ==="
echo "--- ALB Controller (kube-system) ---"
kubectl get deployment -n kube-system aws-load-balancer-controller \
  --no-headers 2>/dev/null \
  | awk '{printf "  %-45s READY=%s/%s\n", $1, $2, $3}' \
  || echo "  (not found)"
echo ""
echo "--- Ingress (${EXPECTED_NAMESPACE}) ---"
kubectl get ingress -n "${EXPECTED_NAMESPACE}" 2>/dev/null || echo "  (not found)"
echo ""
echo "--- Deployments (${EXPECTED_NAMESPACE}) ---"
kubectl get deployment -n "${EXPECTED_NAMESPACE}" 2>/dev/null || echo "  (not found)"
echo ""

echo "================================================================"
echo " PILOT CUTOVER COMPLETE"
echo " Finished : $(date)"
echo " Log      : $LOG_FILE"
echo "================================================================"
