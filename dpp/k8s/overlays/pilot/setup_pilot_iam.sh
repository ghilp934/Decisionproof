#!/usr/bin/env bash
# setup_pilot_iam.sh
# DPP Pilot IRSA 역할 3개 + 공유 권한 정책 1개 생성
#
# 실행: bash ./k8s/overlays/pilot/setup_pilot_iam.sh
set -euo pipefail

ACCOUNT_ID="783268398937"
REGION="ap-northeast-2"
OIDC_ID="21122D9EB7CB3688E31DBA87A12EF928"
NS="dpp-pilot"
PROFILE="dpp-admin"
POLICY_NAME="dpp-pilot-app-policy"

# Windows AWS CLI는 bash /tmp 를 읽지 못함 → 스크립트 디렉토리에 임시 파일 생성
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TMPD="${SCRIPT_DIR}/_iam_tmp"
mkdir -p "${TMPD}"
# 경로를 Windows 형식으로 변환 (AWS CLI file:// 파라미터용)
win() { cygpath -w "$1" 2>/dev/null || echo "$1"; }

cleanup() { rm -rf "${TMPD}"; }
trap cleanup EXIT

echo "=== [1/4] IAM Permission Policy 생성 ==="
POLICY_JSON="${TMPD}/dpp-pilot-app-policy.json"
cat > "${POLICY_JSON}" <<JSON
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "SecretsManager",
      "Effect": "Allow",
      "Action": [
        "secretsmanager:GetSecretValue",
        "secretsmanager:DescribeSecret"
      ],
      "Resource": "arn:aws:secretsmanager:${REGION}:${ACCOUNT_ID}:secret:decisionproof/pilot/dpp-secrets*"
    },
    {
      "Sid": "SQS",
      "Effect": "Allow",
      "Action": [
        "sqs:SendMessage",
        "sqs:ReceiveMessage",
        "sqs:DeleteMessage",
        "sqs:GetQueueAttributes",
        "sqs:ChangeMessageVisibility",
        "sqs:GetQueueUrl"
      ],
      "Resource": "arn:aws:sqs:${REGION}:${ACCOUNT_ID}:dpp-runs-pilot"
    },
    {
      "Sid": "S3",
      "Effect": "Allow",
      "Action": [
        "s3:GetObject",
        "s3:PutObject",
        "s3:DeleteObject",
        "s3:ListBucket"
      ],
      "Resource": [
        "arn:aws:s3:::dpp-results-pilot",
        "arn:aws:s3:::dpp-results-pilot/*"
      ]
    }
  ]
}
JSON

POLICY_ARN=$(aws iam create-policy \
  --policy-name "${POLICY_NAME}" \
  --policy-document "file://$(win "${POLICY_JSON}")" \
  --profile "${PROFILE}" \
  --query "Policy.Arn" --output text)
echo "  OK: ${POLICY_ARN}"

echo ""
echo "=== [2/4] Trust Policy 파일 생성 ==="
OIDC_ISSUER="oidc.eks.${REGION}.amazonaws.com/id/${OIDC_ID}"

cat > "${TMPD}/trust-dpp-api-role.json" <<JSON
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Principal": {
      "Federated": "arn:aws:iam::${ACCOUNT_ID}:oidc-provider/${OIDC_ISSUER}"
    },
    "Action": "sts:AssumeRoleWithWebIdentity",
    "Condition": {
      "StringEquals": {
        "${OIDC_ISSUER}:aud": "sts.amazonaws.com",
        "${OIDC_ISSUER}:sub": [
          "system:serviceaccount:${NS}:dpp-api",
          "system:serviceaccount:${NS}:dpp-migrator"
        ]
      }
    }
  }]
}
JSON

cat > "${TMPD}/trust-dpp-worker-role.json" <<JSON
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Principal": {
      "Federated": "arn:aws:iam::${ACCOUNT_ID}:oidc-provider/${OIDC_ISSUER}"
    },
    "Action": "sts:AssumeRoleWithWebIdentity",
    "Condition": {
      "StringEquals": {
        "${OIDC_ISSUER}:aud": "sts.amazonaws.com",
        "${OIDC_ISSUER}:sub": "system:serviceaccount:${NS}:dpp-worker"
      }
    }
  }]
}
JSON

cat > "${TMPD}/trust-dpp-reaper-role.json" <<JSON
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Principal": {
      "Federated": "arn:aws:iam::${ACCOUNT_ID}:oidc-provider/${OIDC_ISSUER}"
    },
    "Action": "sts:AssumeRoleWithWebIdentity",
    "Condition": {
      "StringEquals": {
        "${OIDC_ISSUER}:aud": "sts.amazonaws.com",
        "${OIDC_ISSUER}:sub": "system:serviceaccount:${NS}:dpp-reaper"
      }
    }
  }]
}
JSON
echo "  OK: 3개 trust policy JSON 생성"

echo ""
echo "=== [3/4] IAM 역할 3개 생성 + 정책 연결 ==="
for ROLE in dpp-api-role dpp-worker-role dpp-reaper-role; do
  TRUST_FILE="${TMPD}/trust-${ROLE}.json"
  echo "  Creating ${ROLE}..."
  aws iam create-role \
    --role-name "${ROLE}" \
    --assume-role-policy-document "file://$(win "${TRUST_FILE}")" \
    --profile "${PROFILE}" \
    --query "Role.Arn" --output text
  aws iam attach-role-policy \
    --role-name "${ROLE}" \
    --policy-arn "${POLICY_ARN}" \
    --profile "${PROFILE}"
  echo "    OK: created + policy attached"
done

echo ""
echo "=== [4/4] 결과 확인 ==="
aws iam list-roles --profile "${PROFILE}" \
  --query "Roles[?contains(RoleName, 'dpp-')].[RoleName, Arn]" \
  --output table

echo ""
echo "=== IAM 설정 완료 ==="
echo "다음 단계: pilot_secret_values.env 채운 뒤 create_pilot_secret.sh 실행"
