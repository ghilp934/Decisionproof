#!/usr/bin/env bash
# create_supabase_ca_configmap.sh
# Supabase CA 인증서를 dpp-pilot 네임스페이스의 ConfigMap으로 등록
#
# 사용법:
#   chmod +x create_supabase_ca_configmap.sh
#   ./create_supabase_ca_configmap.sh /path/to/supabase-ca.crt
#
# 멱등성: kubectl apply -f - 방식으로 이미 존재해도 안전하게 덮어씀
# 참고: ops/runbooks/db_ssl_verify_full.md
set -euo pipefail

CERT_FILE="${1:-}"
NAMESPACE="${NAMESPACE:-dpp-pilot}"

if [ -z "$CERT_FILE" ]; then
  echo "FAIL: Certificate file path required."
  echo "Usage: $0 /path/to/supabase-ca.crt"
  exit 1
fi

if [ ! -f "$CERT_FILE" ]; then
  echo "FAIL: Certificate file not found: $CERT_FILE"
  exit 1
fi

echo "Creating ConfigMap dpp-supabase-ca in namespace $NAMESPACE..."
echo "  Source: $CERT_FILE"

kubectl create configmap dpp-supabase-ca \
  -n "$NAMESPACE" \
  --from-file=supabase-ca.crt="$CERT_FILE" \
  --dry-run=client -o yaml | kubectl apply -f -

echo ""
echo "OK: ConfigMap dpp-supabase-ca applied."
echo "Verify: kubectl get configmap dpp-supabase-ca -n $NAMESPACE"
