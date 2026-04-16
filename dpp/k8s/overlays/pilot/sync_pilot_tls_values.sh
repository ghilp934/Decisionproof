#!/usr/bin/env bash
# sync_pilot_tls_values.sh
# pilot_tls.env → ingress-pilot.yaml의 TLS 어노테이션(cert-arn, security-groups)을 동기화
#
# 사용법:
#   cd k8s/overlays/pilot && ./sync_pilot_tls_values.sh
#
# 전제조건:
#   - pilot_tls.env 에 PILOT_ACM_CERT_ARN, PILOT_ALB_SG 값이 채워져 있어야 함
#   - Python 3 설치됨 (표준라이브러리 re만 사용, 외부 패키지 불필요)
#
# 동작:
#   1. pilot_tls.env 파싱 → PILOT_ACM_CERT_ARN, PILOT_ALB_SG 추출
#   2. 값에 REPLACE_ME가 남아있으면 즉시 FAIL
#   3. ingress-pilot.yaml의 플레이스홀더 치환
#   4. 치환 후 REPLACE_ME 잔존 시 FAIL

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="${SCRIPT_DIR}/pilot_tls.env"
INGRESS_FILE="${SCRIPT_DIR}/ingress-pilot.yaml"

echo "[sync_pilot_tls] ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "[sync_pilot_tls] ENV    : ${ENV_FILE}"
echo "[sync_pilot_tls] Ingress: ${INGRESS_FILE}"
echo "[sync_pilot_tls] ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

if [[ ! -f "${ENV_FILE}" ]]; then
  echo "[FAIL] pilot_tls.env not found: ${ENV_FILE}" >&2
  exit 1
fi

if [[ ! -f "${INGRESS_FILE}" ]]; then
  echo "[FAIL] ingress-pilot.yaml not found: ${INGRESS_FILE}" >&2
  exit 1
fi

python3 - "${ENV_FILE}" "${INGRESS_FILE}" <<'PYEOF'
import re
import sys

env_path, ingress_path = sys.argv[1], sys.argv[2]

# ── pilot_tls.env 파싱 (따옴표, 공백 허용) ─────────────────────────
env = {}
with open(env_path, encoding='utf-8') as f:
    for raw in f:
        line = raw.strip()
        if not line or line.startswith('#'):
            continue
        key, _, val = line.partition('=')
        env[key.strip()] = val.strip().strip('"\'')

# ── 필수 키 검증 ────────────────────────────────────────────────────
required_keys = ('PILOT_ACM_CERT_ARN', 'PILOT_ALB_SG')
for k in required_keys:
    if k not in env or not env[k]:
        print(f"[FAIL] {k} is not set or empty in pilot_tls.env", file=sys.stderr)
        sys.exit(1)
    if 'REPLACE_ME' in env[k]:
        print(f"[FAIL] {k} still contains REPLACE_ME: {env[k]}", file=sys.stderr)
        sys.exit(1)

cert_arn = env['PILOT_ACM_CERT_ARN']
alb_sg   = env['PILOT_ALB_SG']

# ── ingress-pilot.yaml 읽기 ─────────────────────────────────────────
with open(ingress_path, encoding='utf-8') as f:
    content = f.read()

# ── 플레이스홀더 치환 ───────────────────────────────────────────────
content, n_cert = re.subn(
    r'(alb\.ingress\.kubernetes\.io/certificate-arn:\s*)REPLACE_ME_ACM_ARN',
    lambda m: m.group(1) + cert_arn,
    content
)
content, n_sg = re.subn(
    r'(alb\.ingress\.kubernetes\.io/security-groups:\s*)sg-REPLACE_ME',
    lambda m: m.group(1) + alb_sg,
    content
)

if n_cert == 0:
    print("[WARN] certificate-arn placeholder (REPLACE_ME_ACM_ARN) not found — "
          "already substituted or placeholder mismatch.", file=sys.stderr)
if n_sg == 0:
    print("[WARN] security-groups placeholder (sg-REPLACE_ME) not found — "
          "already substituted or placeholder mismatch.", file=sys.stderr)

# ── REPLACE_ME 잔존 검사 ────────────────────────────────────────────
remaining = [ln.strip() for ln in content.splitlines() if 'REPLACE_ME' in ln]
if remaining:
    print("[FAIL] REPLACE_ME still present after substitution:", file=sys.stderr)
    for r in remaining:
        print(f"  {r}", file=sys.stderr)
    sys.exit(1)

# ── 파일 쓰기 ───────────────────────────────────────────────────────
with open(ingress_path, 'w', encoding='utf-8') as f:
    f.write(content)

print(f"[OK] certificate-arn  → {cert_arn}")
print(f"[OK] security-groups  → {alb_sg}")
print("[OK] ingress-pilot.yaml updated. No REPLACE_ME remaining.")
PYEOF

echo "[sync_pilot_tls] ✓ Sync complete."
