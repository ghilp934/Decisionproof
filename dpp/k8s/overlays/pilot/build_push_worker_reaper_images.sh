#!/usr/bin/env bash
# build_push_worker_reaper_images.sh
# dpp-worker / dpp-reaper ECR 이미지 빌드 & 푸시
#
# 사용법 (프로젝트 루트 또는 이 파일 위치 어디서든 실행 가능):
#   bash k8s/overlays/pilot/build_push_worker_reaper_images.sh
#
# 전제조건:
#   - Docker 실행 중
#   - AWS CLI 설치 및 dpp-admin 프로파일 설정 완료
#   - Dockerfile.worker, Dockerfile.reaper가 프로젝트 루트에 존재
#
# 동작:
#   a) ECR 리포지토리 dpp-worker / dpp-reaper 존재 확인 → 없으면 자동 생성
#   b) ECR 로그인
#   c) dpp-worker 이미지 빌드 & 푸시
#   d) dpp-reaper 이미지 빌드 & 푸시

set -euo pipefail

# ── SSOT 설정 (여기만 수정) ─────────────────────────────────────────
AWS_PROFILE="dpp-admin"
AWS_REGION="ap-northeast-2"
AWS_ACCOUNT_ID="783268398937"
VERSION_TAG="0.4.2.2"
ECR_REGISTRY="${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com"
# ───────────────────────────────────────────────────────────────────

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"

echo "[build_push] ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "[build_push] PROJECT_ROOT : ${PROJECT_ROOT}"
echo "[build_push] ECR_REGISTRY : ${ECR_REGISTRY}"
echo "[build_push] VERSION_TAG  : ${VERSION_TAG}"
echo "[build_push] ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# Dockerfile 존재 확인
for df in Dockerfile.worker Dockerfile.reaper; do
  if [[ ! -f "${PROJECT_ROOT}/${df}" ]]; then
    echo "[FAIL] ${df} not found at ${PROJECT_ROOT}/${df}" >&2
    exit 1
  fi
done
echo "[build_push] Dockerfiles verified."

# ── a) ECR 리포지토리 확인 / 자동 생성 ─────────────────────────────
for repo in dpp-worker dpp-reaper; do
  echo "[build_push] Checking ECR repo: ${repo} ..."
  EXISTING=$(aws ecr describe-repositories \
    --repository-names "${repo}" \
    --region "${AWS_REGION}" \
    --profile "${AWS_PROFILE}" \
    --query "repositories[0].repositoryName" \
    --output text 2>/dev/null || true)

  if [[ "${EXISTING}" == "${repo}" ]]; then
    echo "[build_push] ECR repo exists   : ${repo}"
  else
    echo "[build_push] Creating ECR repo : ${repo}"
    aws ecr create-repository \
      --repository-name "${repo}" \
      --region "${AWS_REGION}" \
      --profile "${AWS_PROFILE}" \
      --image-scanning-configuration scanOnPush=true \
      --query "repository.repositoryUri" \
      --output text
    echo "[build_push] ECR repo created  : ${repo}"
  fi
done

# ── b) ECR 로그인 ────────────────────────────────────────────────────
echo "[build_push] Logging in to ECR ..."
aws ecr get-login-password \
  --region "${AWS_REGION}" \
  --profile "${AWS_PROFILE}" \
| docker login \
  --username AWS \
  --password-stdin \
  "${ECR_REGISTRY}"
echo "[build_push] ECR login OK."

# ── c) dpp-worker 빌드 & 푸시 ────────────────────────────────────────
WORKER_IMAGE="${ECR_REGISTRY}/dpp-worker:${VERSION_TAG}"
echo ""
echo "[build_push] ── Building dpp-worker ──────────────────────────"
echo "[build_push] Image: ${WORKER_IMAGE}"
docker build \
  --file "${PROJECT_ROOT}/Dockerfile.worker" \
  --tag  "${WORKER_IMAGE}" \
  "${PROJECT_ROOT}"

echo "[build_push] Pushing dpp-worker ..."
docker push "${WORKER_IMAGE}"
echo "[build_push] ✓ dpp-worker pushed: ${WORKER_IMAGE}"

# ── d) dpp-reaper 빌드 & 푸시 ────────────────────────────────────────
REAPER_IMAGE="${ECR_REGISTRY}/dpp-reaper:${VERSION_TAG}"
echo ""
echo "[build_push] ── Building dpp-reaper ──────────────────────────"
echo "[build_push] Image: ${REAPER_IMAGE}"
docker build \
  --file "${PROJECT_ROOT}/Dockerfile.reaper" \
  --tag  "${REAPER_IMAGE}" \
  "${PROJECT_ROOT}"

echo "[build_push] Pushing dpp-reaper ..."
docker push "${REAPER_IMAGE}"
echo "[build_push] ✓ dpp-reaper pushed: ${REAPER_IMAGE}"

# ── 완료 ─────────────────────────────────────────────────────────────
echo ""
echo "[build_push] ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "[build_push] ✓ All images built and pushed."
echo "  dpp-worker : ${WORKER_IMAGE}"
echo "  dpp-reaper : ${REAPER_IMAGE}"
echo ""
echo "Next step:"
echo "  cd $(dirname "${SCRIPT_DIR}/../../..")/$(basename "${PROJECT_ROOT}") && \\"
echo "  kubectl apply -k k8s/overlays/pilot/"
echo "[build_push] ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
