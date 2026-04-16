#!/usr/bin/env bash
# create_pilot_secret.sh
# pilot_secret_values.env → AWS Secrets Manager에 업로드
#
# 실행: bash ./k8s/overlays/pilot/create_pilot_secret.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="${SCRIPT_DIR}/pilot_secret_values.env"
SECRET_NAME="decisionproof/pilot/dpp-secrets"
REGION="ap-northeast-2"
PROFILE="dpp-admin"

# Windows AWS CLI용 경로 변환
win() { cygpath -w "$1" 2>/dev/null || echo "$1"; }

if [[ ! -f "${ENV_FILE}" ]]; then
  echo "FAIL: ${ENV_FILE} not found"
  exit 1
fi

# .env 파일 로드
set -a; source "${ENV_FILE}"; set +a

# FILL_IN 미치환 검사
UNFILLED=$(grep -E "^[A-Z_]+=.FILL_IN" "${ENV_FILE}" | awk -F= '{print $1}' | tr '\n' ' ' || true)
if [[ -n "${UNFILLED}" ]]; then
  echo "FAIL: 아직 FILL_IN 상태인 키: ${UNFILLED}"
  echo "  pilot_secret_values.env 를 실제 값으로 채운 뒤 재실행하세요."
  exit 1
fi

echo "=== Secrets Manager 시크릿 생성 ==="
echo "  Secret: ${SECRET_NAME}"

# 임시 JSON 파일을 스크립트 디렉토리에 생성 (Windows AWS CLI 접근 가능)
SECRET_JSON="${SCRIPT_DIR}/_pilot_secret_tmp.json"
cleanup() { rm -f "${SECRET_JSON}"; }
trap cleanup EXIT

# Phase 4: Toss is dormant for v1.0 pilot. TOSS_* vars are commented out in
# pilot_secret_values.env and removed from SecretProviderClass.
# They are NOT sent to Secrets Manager here.
# To reactivate Toss: uncomment TOSS_* in pilot_secret_values.env and add back below.
python3 - \
  "${DATABASE_URL}" "${REDIS_URL}" "${REDIS_PASSWORD}" \
  "${SENTRY_DSN}" "${SUPABASE_URL}" \
  "${SB_PUBLISHABLE_KEY}" "${SB_SECRET_KEY}" \
  "${PAYPAL_CLIENT_ID}" "${PAYPAL_CLIENT_SECRET}" "${PAYPAL_WEBHOOK_ID}" \
  "${KS_AUDIT_FINGERPRINT_PEPPER_B64}" \
  "${SECRET_JSON}" << 'PYEOF'
import sys, json
keys = [
  "database-url", "redis-url", "redis-password",
  "sentry-dsn", "supabase-url", "sb-publishable-key", "sb-secret-key",
  "paypal_client_id", "paypal_client_secret", "paypal_webhook_id",
  "ks_audit_fingerprint_pepper_b64"
]
vals = sys.argv[1:-1]
out_path = sys.argv[-1]
with open(out_path, "w", encoding="utf-8") as f:
    json.dump(dict(zip(keys, vals)), f, ensure_ascii=False, indent=2)
print(f"  JSON 작성 완료: {out_path}")
PYEOF

WIN_SECRET_JSON="$(win "${SECRET_JSON}")"

# 기존 시크릿이 있으면 put-secret-value, 없으면 create-secret
if aws secretsmanager describe-secret \
     --secret-id "${SECRET_NAME}" \
     --region "${REGION}" --profile "${PROFILE}" \
     --output text --query "Name" 2>/dev/null | grep -q "${SECRET_NAME}"; then
  echo "  기존 시크릿 업데이트..."
  aws secretsmanager put-secret-value \
    --secret-id "${SECRET_NAME}" \
    --secret-string "file://${WIN_SECRET_JSON}" \
    --region "${REGION}" --profile "${PROFILE}" \
    --output text --query "VersionId"
else
  echo "  신규 시크릿 생성..."
  aws secretsmanager create-secret \
    --name "${SECRET_NAME}" \
    --description "DPP Pilot application secrets" \
    --secret-string "file://${WIN_SECRET_JSON}" \
    --region "${REGION}" --profile "${PROFILE}" \
    --query "ARN" --output text
fi

echo ""
echo "=== 시크릿 생성 완료 ==="
aws secretsmanager describe-secret \
  --secret-id "${SECRET_NAME}" \
  --region "${REGION}" --profile "${PROFILE}" \
  --query "[Name, ARN]" --output table
