#!/usr/bin/env bash
# sync_pilot_values.sh
# SSOT 동기화 스크립트
#
# pilot.params.yaml 1곳만 수정 → 이 스크립트가 ingress/configmap 자동 업데이트
#
# 사용법:
#   cd dpp/k8s/overlays/pilot
#   chmod +x sync_pilot_values.sh
#   ./sync_pilot_values.sh
#
# 동기화 대상:
#   pilot.params.yaml  (SSOT — 직접 편집하는 유일한 파일)
#     → ingress-pilot.yaml         (host / cert-arn / security-groups)
#     → patch-configmap-pilot.yaml (CORS_ALLOWED_ORIGINS)
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
PARAMS="$ROOT/pilot.params.yaml"
INGRESS="$ROOT/ingress-pilot.yaml"
CONFIGMAP="$ROOT/patch-configmap-pilot.yaml"

echo "=== sync_pilot_values.sh ==="
echo "SSOT: $PARAMS"
echo ""

# ── 1. pilot.params.yaml 에서 4개 값 추출 ──────────────────────────────────
# python3 stdlib만 사용 (외부 패키지 불필요)
extract() {
  local key="$1"
  python3 - "$PARAMS" "$key" << 'PYEOF'
import sys, re
params_path, key = sys.argv[1], sys.argv[2]
with open(params_path, encoding='utf-8') as f:
    content = f.read()
# 따옴표/공백 대응: key: "value" 또는 key: value
m = re.search(r'^\s*' + re.escape(key) + r'\s*:\s*["\']?([^"\'#\r\n]+?)["\']?\s*$', content, re.MULTILINE)
if not m:
    print(f"ERROR: key '{key}' not found in {params_path}", file=sys.stderr)
    sys.exit(1)
print(m.group(1).strip())
PYEOF
}

PILOT_HOST=$(extract "PILOT_HOST")
PILOT_APP_HOST=$(extract "PILOT_APP_HOST")
PILOT_ACM_CERT_ARN=$(extract "PILOT_ACM_CERT_ARN")
PILOT_ALB_SG=$(extract "PILOT_ALB_SECURITY_GROUP_ID")

echo "Values extracted from SSOT:"
echo "  PILOT_HOST           = $PILOT_HOST"
echo "  PILOT_APP_HOST       = $PILOT_APP_HOST"
echo "  PILOT_ACM_CERT_ARN   = $PILOT_ACM_CERT_ARN"
echo "  PILOT_ALB_SG         = $PILOT_ALB_SG"
echo ""

# ── 2. ingress-pilot.yaml 치환 ─────────────────────────────────────────────
echo "Updating: $INGRESS"
python3 - "$INGRESS" "$PILOT_HOST" "$PILOT_ACM_CERT_ARN" "$PILOT_ALB_SG" << 'PYEOF'
import sys, re

path, host, cert_arn, sg_id = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
with open(path, encoding='utf-8') as f:
    content = f.read()

# host 라인 치환 (- host: REPLACE_ME_PILOT_HOST 또는 - host: api-pilot.xxx)
content = re.sub(
    r'^(\s*-\s*host:\s*)(.+)$',
    lambda m: m.group(1) + host,
    content, flags=re.MULTILINE
)
# cert-arn 어노테이션 치환
content = re.sub(
    r'^(\s*alb\.ingress\.kubernetes\.io/certificate-arn:\s*)(.+)$',
    lambda m: m.group(1) + cert_arn,
    content, flags=re.MULTILINE
)
# security-groups 어노테이션 치환
content = re.sub(
    r'^(\s*alb\.ingress\.kubernetes\.io/security-groups:\s*)(.+)$',
    lambda m: m.group(1) + sg_id,
    content, flags=re.MULTILINE
)

with open(path, 'w', encoding='utf-8') as f:
    f.write(content)
print("  OK: ingress-pilot.yaml updated")
PYEOF

# ── 3. patch-configmap-pilot.yaml 치환 ────────────────────────────────────
echo "Updating: $CONFIGMAP"
python3 - "$CONFIGMAP" "$PILOT_APP_HOST" << 'PYEOF'
import sys, re

path, app_host = sys.argv[1], sys.argv[2]
with open(path, encoding='utf-8') as f:
    content = f.read()

# CORS_ALLOWED_ORIGINS 치환 (https:// 접두사 유지)
content = re.sub(
    r'^(\s*CORS_ALLOWED_ORIGINS:\s*["\']?)https://[^"\'#\r\n]+(["\']?)$',
    lambda m: m.group(1) + 'https://' + app_host + m.group(2),
    content, flags=re.MULTILINE
)

with open(path, 'w', encoding='utf-8') as f:
    f.write(content)
print("  OK: patch-configmap-pilot.yaml updated")
PYEOF

# ── 4. 치환 후 REPLACE_ME_ 잔존 여부 확인 ─────────────────────────────────
echo ""
echo "Verifying no REPLACE_ME_ residuals..."
for f in "$INGRESS" "$CONFIGMAP"; do
  if grep -q "REPLACE_ME_" "$f"; then
    echo "FAIL: REPLACE_ME_ still found in $f"
    grep -n "REPLACE_ME_" "$f"
    exit 1
  fi
done
echo "  OK: no REPLACE_ME_ in ingress or configmap"

echo ""
echo "=== SYNC COMPLETE ==="
echo "Run ./pre_gate_check.sh to verify full overlay"
