#!/usr/bin/env bash
# sync_pilot_values.sh
# SSOT: pilot.params.yaml → ingress-pilot.yaml의 BEGIN/END:SYNC_HOST_RULES 구간을 완전 치환
#
# 사용법:
#   chmod +x sync_pilot_values.sh
#   ./sync_pilot_values.sh
#
# Fail-Fast 규칙:
#   1) PILOT_HOST 미정의 → FAIL
#   2) BEGIN/END 마커 미존재 → FAIL (조용히 성공 금지)
#   3) 치환 후 REPLACE_ME_ 잔존 → FAIL
#   4) 최종 host rule 개수 < 1 → FAIL
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PARAMS_FILE="${SCRIPT_DIR}/pilot.params.yaml"
INGRESS_FILE="${SCRIPT_DIR}/ingress-pilot.yaml"

# ─── 0) 파일 존재 확인 ────────────────────────────────────────────────────────
if [[ ! -f "${PARAMS_FILE}" ]]; then
  echo "❌ FAIL: params file not found: ${PARAMS_FILE}"
  exit 1
fi
if [[ ! -f "${INGRESS_FILE}" ]]; then
  echo "❌ FAIL: ingress file not found: ${INGRESS_FILE}"
  exit 1
fi

# ─── 1) PILOT_HOST / PILOT_HOST_ALIASES 읽기 ─────────────────────────────────
PILOT_HOST="$(grep -E '^\s+PILOT_HOST:' "${PARAMS_FILE}" | head -1 | sed 's/.*PILOT_HOST:[[:space:]]*//' | tr -d '"' | xargs)"
PILOT_HOST_ALIASES="$(grep -E '^\s+PILOT_HOST_ALIASES:' "${PARAMS_FILE}" | head -1 | sed 's/.*PILOT_HOST_ALIASES:[[:space:]]*//' | tr -d '"' | xargs || true)"

if [[ -z "${PILOT_HOST}" ]]; then
  echo "❌ FAIL: PILOT_HOST is not defined in ${PARAMS_FILE}"
  exit 1
fi

# hosts 배열 구성: PILOT_HOST + PILOT_HOST_ALIASES (쉼표 구분)
declare -a HOSTS
HOSTS=("${PILOT_HOST}")
if [[ -n "${PILOT_HOST_ALIASES}" ]]; then
  IFS=',' read -ra ALIAS_ARRAY <<< "${PILOT_HOST_ALIASES}"
  for alias in "${ALIAS_ARRAY[@]}"; do
    trimmed="$(echo "${alias}" | xargs)"
    if [[ -n "${trimmed}" ]]; then
      HOSTS+=("${trimmed}")
    fi
  done
fi

echo "ℹ️  Hosts to sync (${#HOSTS[@]}): ${HOSTS[*]}"

# ─── 2) 마커 존재 확인 ────────────────────────────────────────────────────────
if ! grep -q "# BEGIN:SYNC_HOST_RULES" "${INGRESS_FILE}"; then
  echo "❌ FAIL: BEGIN:SYNC_HOST_RULES marker not found in ${INGRESS_FILE}"
  echo "   ingress-pilot.yaml에 마커를 추가한 뒤 재실행하세요."
  exit 1
fi
if ! grep -q "# END:SYNC_HOST_RULES" "${INGRESS_FILE}"; then
  echo "❌ FAIL: END:SYNC_HOST_RULES marker not found in ${INGRESS_FILE}"
  exit 1
fi

# ─── 3) 새 host rules 블록 생성 ──────────────────────────────────────────────
NEW_RULES=""
for host in "${HOSTS[@]}"; do
  NEW_RULES+="  - host: ${host}\n"
  NEW_RULES+="    http:\n"
  NEW_RULES+="      paths:\n"
  NEW_RULES+="      - path: /\n"
  NEW_RULES+="        pathType: Prefix\n"
  NEW_RULES+="        backend:\n"
  NEW_RULES+="          service:\n"
  NEW_RULES+="            name: dpp-api\n"
  NEW_RULES+="            port:\n"
  NEW_RULES+="              number: 80\n"
done

# ─── 4) 마커 사이를 완전 치환 ─────────────────────────────────────────────────
TMPFILE="$(mktemp)"
trap 'rm -f "${TMPFILE}"' EXIT

# BEGIN 마커 줄까지 유지 → 새 rules 삽입 → END 마커부터 재개
awk -v new_rules="${NEW_RULES}" '
  /# BEGIN:SYNC_HOST_RULES/ {
    print
    printf "%s", new_rules
    skip=1
    next
  }
  /# END:SYNC_HOST_RULES/ {
    skip=0
  }
  !skip { print }
' "${INGRESS_FILE}" > "${TMPFILE}"

# ─── 5) 검증 ─────────────────────────────────────────────────────────────────
# 5-1) REPLACE_ME_ 잔존 검사
if grep -q "REPLACE_ME_" "${TMPFILE}"; then
  echo "❌ FAIL: REPLACE_ME_ tokens remain after sync"
  grep "REPLACE_ME_" "${TMPFILE}"
  exit 1
fi

# 5-2) host rule 개수 검사
RULE_COUNT="$(grep -c "^  - host:" "${TMPFILE}" || true)"
if [[ "${RULE_COUNT}" -lt 1 ]]; then
  echo "❌ FAIL: host rule count is ${RULE_COUNT} (expected >= 1)"
  exit 1
fi

echo "✅ host rules after sync: ${RULE_COUNT}"

# 5-3) 기대 host 개수와 일치하는지 확인
EXPECTED_COUNT="${#HOSTS[@]}"
if [[ "${RULE_COUNT}" -ne "${EXPECTED_COUNT}" ]]; then
  echo "⚠️  WARNING: expected ${EXPECTED_COUNT} host rule(s), found ${RULE_COUNT}"
fi

# ─── 6) 파일 교체 ─────────────────────────────────────────────────────────────
cp "${TMPFILE}" "${INGRESS_FILE}"
echo "✅ sync complete: ${INGRESS_FILE}"
echo "   Synced hosts:"
for host in "${HOSTS[@]}"; do
  echo "   - ${host}"
done
