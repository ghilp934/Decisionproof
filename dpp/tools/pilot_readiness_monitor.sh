#!/usr/bin/env bash
# Pilot Readiness Monitor
# Purpose: Continuously detect readiness regression during Paid Pilot

set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

# Configuration (env vars with defaults)
PILOT_BASE_URL="${PILOT_BASE_URL:-}"
PILOT_TOKEN="${PILOT_TOKEN:-}"
PASS_THRESHOLD="${PASS_THRESHOLD:-0.8}"
RETRIES="${RETRIES:-3}"
RETRY_SLEEP_SEC="${RETRY_SLEEP_SEC:-2}"
EXPECT_V1_RUNS="${EXPECT_V1_RUNS:-401_or_200}"
MODE="${MODE:-ci}"
EVENT_NAME="${EVENT_NAME:-${GITHUB_EVENT_NAME:-local}}"
CHECK_METRICS="${CHECK_METRICS:-0}"

# Validate required inputs
if [ -z "$PILOT_BASE_URL" ]; then
  echo -e "${RED}ERROR: PILOT_BASE_URL is required${NC}" >&2
  echo "Usage: PILOT_BASE_URL=https://... $0" >&2
  exit 2
fi

# Check required tools
if ! command -v curl &> /dev/null; then
  echo -e "${RED}ERROR: curl is required${NC}" >&2
  exit 2
fi

if ! command -v jq &> /dev/null; then
  echo -e "${YELLOW}WARNING: jq not found, JSON output will be minimal${NC}" >&2
fi

# Evidence directory
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
EVIDENCE_DIR="$REPO_ROOT/evidence/pilot_monitor/$TIMESTAMP"

echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${GREEN}Pilot Readiness Monitor${NC}"
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo "Base URL: $PILOT_BASE_URL"
echo "Event: $EVENT_NAME"
echo "Threshold: ${PASS_THRESHOLD} (${PASS_THRESHOLD%.*}0%)"
echo "Evidence: $EVIDENCE_DIR"
echo ""

# Create evidence structure
mkdir -p "$EVIDENCE_DIR/preflight"
mkdir -p "$EVIDENCE_DIR/meta"
mkdir -p "$EVIDENCE_DIR/smoke"
mkdir -p "$EVIDENCE_DIR/dump_logs"

# Trap for diagnostics on error
dump_diagnostics() {
  local exit_code=$?
  local failed_cmd="${BASH_COMMAND}"

  cat > "$EVIDENCE_DIR/dump_logs/diagnostics.txt" <<EOF
Pilot Readiness Monitor - Error Diagnostics
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Exit Code: $exit_code
Failed Command: $failed_cmd
Timestamp: $(date -u +"%Y-%m-%dT%H:%M:%SZ")

Network Debug:
$(curl -I -m 5 "$PILOT_BASE_URL" 2>&1 || echo "Failed to reach $PILOT_BASE_URL")

DNS Info:
$(nslookup "$(echo "$PILOT_BASE_URL" | sed 's|https\?://||' | cut -d/ -f1)" 2>&1 || echo "DNS lookup failed")

Environment:
- MODE: $MODE
- EVENT_NAME: $EVENT_NAME
- RETRIES: $RETRIES
- THRESHOLD: $PASS_THRESHOLD

EOF
}

trap dump_diagnostics ERR

# Preflight: record versions
cat > "$EVIDENCE_DIR/preflight/versions.txt" <<EOF
Git SHA: $(git -C "$REPO_ROOT" rev-parse HEAD 2>/dev/null || echo "unknown")
OS: $(uname -s) $(uname -r)
curl: $(curl --version 2>&1 | head -1)
jq: $(jq --version 2>&1 || echo "not installed")
EOF

# Meta: record event
cat > "$EVIDENCE_DIR/meta/event.txt" <<EOF
event: $EVENT_NAME
mode: $MODE
github_sha: ${GITHUB_SHA:-}
github_ref: ${GITHUB_REF:-}
github_workflow: ${GITHUB_WORKFLOW:-}
github_run_id: ${GITHUB_RUN_ID:-}
EOF

# Prepare headers
HEADERS=(-H "User-Agent: Decisionproof-Pilot-Monitor/1.0")
if [ -n "$PILOT_TOKEN" ]; then
  HEADERS+=(-H "Authorization: Bearer $PILOT_TOKEN")
fi

# Check function
run_check() {
  local name="$1"
  local method="$2"
  local path="$3"
  local expected_status="$4"

  local url="${PILOT_BASE_URL}${path}"
  local attempts=0
  local actual_status=0
  local ok=false
  local error=""
  local start_ms=$(date +%s%3N)

  for ((attempts=1; attempts<=RETRIES; attempts++)); do
    actual_status=$(curl -X "$method" -s -o /dev/null -w "%{http_code}" "${HEADERS[@]}" "$url" 2>&1 || echo "000")

    # Check if status matches expected
    if [[ "$expected_status" == "401_or_200" ]]; then
      if [[ "$actual_status" == "401" ]] || [[ "$actual_status" == "200" ]]; then
        ok=true
        break
      fi
    else
      if [[ "$actual_status" == "$expected_status" ]]; then
        ok=true
        break
      fi
    fi

    if [ $attempts -lt $RETRIES ]; then
      sleep "$RETRY_SLEEP_SEC"
    fi
  done

  local end_ms=$(date +%s%3N)
  local latency_ms=$((end_ms - start_ms))

  if [ "$ok" = false ]; then
    error="Expected $expected_status, got $actual_status after $attempts attempts"
  fi

  # Output JSON line
  cat <<EOF
{
  "name": "$name",
  "method": "$method",
  "url": "$url",
  "expected": "$expected_status",
  "actual_status": "$actual_status",
  "ok": $ok,
  "latency_ms": $latency_ms,
  "attempts_used": $attempts,
  "error": "${error}"
}
EOF
}

# Run all checks
echo -e "${YELLOW}Running health checks...${NC}"

RESULTS=()

# 1) /health
RESULTS+=("$(run_check "health" "GET" "/health" "200")")

# 2) /readyz
RESULTS+=("$(run_check "readyz" "GET" "/readyz" "200")")

# 3) /.well-known/openapi.json
RESULTS+=("$(run_check "openapi" "GET" "/.well-known/openapi.json" "200")")

# 4) /llms.txt
RESULTS+=("$(run_check "llms-txt" "GET" "/llms.txt" "200")")

# 5) /api-docs
RESULTS+=("$(run_check "api-docs" "GET" "/api-docs" "200")")

# 6) /redoc
RESULTS+=("$(run_check "redoc" "GET" "/redoc" "200")")

# 7) /pricing/ssot.json
RESULTS+=("$(run_check "pricing-ssot" "GET" "/pricing/ssot.json" "200")")

# 8) /docs/quickstart.md
RESULTS+=("$(run_check "quickstart" "GET" "/docs/quickstart.md" "200")")

# 9) /v1/runs
RESULTS+=("$(run_check "v1-runs" "GET" "/v1/runs" "$EXPECT_V1_RUNS")")

# 10) /metrics (optional)
if [ "$CHECK_METRICS" = "1" ]; then
  RESULTS+=("$(run_check "metrics" "GET" "/metrics" "200")")
fi

# Write results.json
echo "[" > "$EVIDENCE_DIR/smoke/results.json"
for i in "${!RESULTS[@]}"; do
  echo "${RESULTS[$i]}" >> "$EVIDENCE_DIR/smoke/results.json"
  if [ $i -lt $((${#RESULTS[@]} - 1)) ]; then
    echo "," >> "$EVIDENCE_DIR/smoke/results.json"
  fi
done
echo "]" >> "$EVIDENCE_DIR/smoke/results.json"

# Calculate summary
CHECKS_TOTAL=${#RESULTS[@]}
CHECKS_PASSED=0
FAILED_CHECKS=()

for result in "${RESULTS[@]}"; do
  ok=$(echo "$result" | grep -o '"ok": [^,]*' | cut -d' ' -f2)
  if [ "$ok" = "true" ]; then
    CHECKS_PASSED=$((CHECKS_PASSED + 1))
  else
    check_name=$(echo "$result" | grep -o '"name": "[^"]*"' | cut -d'"' -f4)
    FAILED_CHECKS+=("$check_name")
  fi
done

PASS_RATIO=$(echo "scale=2; $CHECKS_PASSED / $CHECKS_TOTAL" | bc)

# Determine overall result
OVERALL_OK=false
if (( $(echo "$PASS_RATIO >= $PASS_THRESHOLD" | bc -l) )); then
  OVERALL_OK=true
fi

# Write summary.txt
cat > "$EVIDENCE_DIR/smoke/summary.txt" <<EOF
Pilot Readiness Monitor - Summary
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Status: $([ "$OVERALL_OK" = true ] && echo "PASS" || echo "FAIL")
Checks Passed: $CHECKS_PASSED / $CHECKS_TOTAL
Pass Ratio: $PASS_RATIO
Threshold: $PASS_THRESHOLD

Base URL: $PILOT_BASE_URL
Event: $EVENT_NAME
Timestamp: $(date -u +"%Y-%m-%dT%H:%M:%SZ")

EOF

if [ ${#FAILED_CHECKS[@]} -gt 0 ]; then
  echo "Failed Checks:" >> "$EVIDENCE_DIR/smoke/summary.txt"
  for check in "${FAILED_CHECKS[@]}"; do
    echo "  - $check" >> "$EVIDENCE_DIR/smoke/summary.txt"
  done
fi

# Write manifest.json
COMMIT_SHA=$(git -C "$REPO_ROOT" rev-parse HEAD 2>/dev/null || echo "unknown")
DATE_UTC=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
DATE_KST=$(TZ=Asia/Seoul date +"%Y-%m-%d %H:%M:%S %Z")

cat > "$EVIDENCE_DIR/manifest.json" <<EOF
{
  "ok": $OVERALL_OK,
  "checks_passed": $CHECKS_PASSED,
  "checks_total": $CHECKS_TOTAL,
  "pass_ratio": $PASS_RATIO,
  "threshold": $PASS_THRESHOLD,
  "base_url": "$PILOT_BASE_URL",
  "event": "$EVENT_NAME",
  "mode": "$MODE",
  "commit_sha": "$COMMIT_SHA",
  "date_utc": "$DATE_UTC",
  "date_kst": "$DATE_KST",
  "evidence_dir": "$EVIDENCE_DIR"
}
EOF

# Display results
echo ""
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
if [ "$OVERALL_OK" = true ]; then
  echo -e "${GREEN}✅ PASS${NC}"
else
  echo -e "${RED}❌ FAIL${NC}"
fi
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""
echo "Checks: $CHECKS_PASSED / $CHECKS_TOTAL passed"
echo "Pass Ratio: $PASS_RATIO (threshold: $PASS_THRESHOLD)"

if [ ${#FAILED_CHECKS[@]} -gt 0 ]; then
  echo ""
  echo -e "${RED}Failed checks:${NC}"
  for check in "${FAILED_CHECKS[@]}"; do
    echo "  - $check"
  done
fi

echo ""
echo "Evidence: $EVIDENCE_DIR"
echo ""

# Exit with appropriate code
if [ "$OVERALL_OK" = true ]; then
  exit 0
else
  exit 1
fi
