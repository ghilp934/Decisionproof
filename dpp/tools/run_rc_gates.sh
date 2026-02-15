#!/usr/bin/env bash
# Decisionproof RC Gates One-shot Execution Script (Mac/Linux)
# Purpose: Run all RC gates (RC-1 to RC-9) and auto-dump logs on failure

set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
TIMESTAMP="$(date +%Y%m%d_%H%M%S)"
EVIDENCE_DIR="$REPO_ROOT/evidence/01_ci/$TIMESTAMP"
DUMP_DIR="$EVIDENCE_DIR/dump_logs"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Create evidence directory
mkdir -p "$EVIDENCE_DIR"
mkdir -p "$DUMP_DIR"

# RC test files in execution order
RC_TESTS=(
  "apps/api/tests/test_rc1_contract.py"
  "apps/api/tests/test_rc2_error_format.py"
  "apps/api/tests/test_rc3_rate_limit_headers.py"
  "apps/api/tests/test_rc4_billing_invariants.py"
  "apps/worker/tests/test_rc4_finalize_invariants.py"
  "apps/api/tests/test_rc5_gate.py"
  "apps/api/tests/test_rc6_observability.py"
  "apps/api/tests/test_rc7_otel_contract.py"
  "apps/api/tests/test_rc8_release_packet_gate.py"
  "apps/api/tests/test_rc9_ops_pack_gate.py"
)

# Auto-dump logs on failure
DumpLogs() {
  local exit_code=$?

  # CRITICAL: Display pytest output FIRST if files exist (before any other output)
  if [[ -f "$EVIDENCE_DIR/rc_run_stdout.log" ]]; then
    echo -e "${YELLOW}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${YELLOW}PYTEST STDOUT:${NC}"
    echo -e "${YELLOW}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    cat "$EVIDENCE_DIR/rc_run_stdout.log" || echo "Failed to cat stdout"
  else
    echo -e "${RED}WARNING: rc_run_stdout.log not found at $EVIDENCE_DIR/${NC}"
  fi

  if [[ -f "$EVIDENCE_DIR/rc_run_stderr.log" && -s "$EVIDENCE_DIR/rc_run_stderr.log" ]]; then
    echo -e "${YELLOW}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${YELLOW}PYTEST STDERR:${NC}"
    echo -e "${YELLOW}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    cat "$EVIDENCE_DIR/rc_run_stderr.log" || echo "Failed to cat stderr"
  fi

  echo -e "${YELLOW}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
  echo -e "${RED}[FAILURE DETECTED] Exit code: $exit_code${NC}"
  echo -e "${YELLOW}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
  echo "Auto-dumping logs to: $DUMP_DIR"

  # Docker compose status
  if command -v docker &> /dev/null && docker compose version &> /dev/null; then
    echo "Collecting docker compose ps..."
    docker compose -f "$REPO_ROOT/infra/docker-compose.yml" ps -a > "$DUMP_DIR/docker_compose_ps.txt" 2>&1 || true

    # Service logs
    echo "Collecting docker compose logs (last 500 lines)..."
    docker compose -f "$REPO_ROOT/infra/docker-compose.yml" logs --tail=500 --timestamps postgres > "$DUMP_DIR/docker_postgres.log" 2>&1 || true
    docker compose -f "$REPO_ROOT/infra/docker-compose.yml" logs --tail=500 --timestamps redis > "$DUMP_DIR/docker_redis.log" 2>&1 || true
    docker compose -f "$REPO_ROOT/infra/docker-compose.yml" logs --tail=500 --timestamps localstack > "$DUMP_DIR/docker_localstack.log" 2>&1 || true

    # Container status
    echo "Collecting docker ps -a..."
    docker ps -a > "$DUMP_DIR/docker_ps_all.txt" 2>&1 || true
  fi

  # Last failed command
  echo "Last command: ${BASH_COMMAND:-N/A}" > "$DUMP_DIR/last_command.txt"

  echo -e "${YELLOW}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
  echo -e "${RED}Logs dumped to: $DUMP_DIR${NC}"
  echo -e "${YELLOW}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"

  exit "$exit_code"
}

# Register error trap
trap DumpLogs ERR

echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${GREEN}Decisionproof RC Gates One-shot Execution${NC}"
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo "Timestamp: $TIMESTAMP"
echo "Evidence directory: $EVIDENCE_DIR"
echo ""

# Step 1: Environment snapshot
echo -e "${YELLOW}[1/6] Collecting environment snapshot...${NC}"
{
  echo "=== Python Version ==="
  python3 --version 2>&1 || python --version 2>&1 || echo "Python not found"
  echo ""
  echo "=== Pytest Version ==="
  pytest --version 2>&1 || echo "Pytest not found"
  echo ""
  echo "=== Docker Version ==="
  docker --version 2>&1 || echo "Docker not found"
  echo ""
  echo "=== Docker Compose Version ==="
  docker compose version 2>&1 || echo "Docker Compose not found"
  echo ""
  echo "=== Docker Compose Services Status ==="
  docker compose -f "$REPO_ROOT/infra/docker-compose.yml" ps 2>&1 || echo "Services not running"
} > "$EVIDENCE_DIR/rc_run_env.txt"

# Step 2: Start dependencies
echo -e "${YELLOW}[2/6] Starting docker dependencies (postgres/redis/localstack)...${NC}"
cd "$REPO_ROOT/infra"
docker compose up -d
echo "Waiting for services to be healthy (max 60s)..."
timeout 60s bash -c 'until docker compose ps | grep -q "(healthy)"; do sleep 2; done' || {
  echo -e "${RED}Warning: Services may not be fully healthy${NC}"
}
cd "$REPO_ROOT"

# Step 3: Build docker images (RC-5 requirement)
echo -e "${YELLOW}[3/6] Building docker images for RC-5 gate...${NC}"
docker build -f Dockerfile.api -t dpp-api:rc-test .
docker build -f Dockerfile.worker -t dpp-worker:rc-test .
docker build -f Dockerfile.reaper -t dpp-reaper:rc-test .

# Step 4: Set environment variables
echo -e "${YELLOW}[4/6] Setting environment variables...${NC}"
export DATABASE_URL="postgresql://dpp_user:dpp_pass@localhost:5432/dpp"
export REDIS_URL="redis://localhost:6379/0"
export AWS_ENDPOINT_URL="http://localhost:4566"
export AWS_ACCESS_KEY_ID="test"
export AWS_SECRET_ACCESS_KEY="test"
export AWS_DEFAULT_REGION="us-east-1"

# Ops Hardening v2: Service-specific endpoints and required env vars
export S3_ENDPOINT_URL="http://localhost:4566"
export SQS_ENDPOINT_URL="http://localhost:4566"
export S3_RESULT_BUCKET="dpp-results-test"
export SQS_QUEUE_URL="http://localhost:4566/000000000000/dpp-runs"

# Step 5: Construct pytest command
echo -e "${YELLOW}[5/6] Constructing pytest command...${NC}"
PYTEST_CMD="pytest -q -o addopts= --maxfail=1"
for test_file in "${RC_TESTS[@]}"; do
  PYTEST_CMD="$PYTEST_CMD $test_file"
done

echo "$PYTEST_CMD" > "$EVIDENCE_DIR/rc_run_cmd.txt"
echo "Command: $PYTEST_CMD"
echo ""

# Step 6: Execute RC gates
echo -e "${YELLOW}[6/6] Executing RC gates...${NC}"
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"

# Run pytest and capture output
set +e  # Temporarily disable exit on error to capture exit code
$PYTEST_CMD > "$EVIDENCE_DIR/rc_run_stdout.log" 2> "$EVIDENCE_DIR/rc_run_stderr.log"
PYTEST_EXIT_CODE=$?

# Copy stdout/stderr to console (keep set +e to prevent ERR trap on cat failure)
if [[ -f "$EVIDENCE_DIR/rc_run_stdout.log" ]]; then
  cat "$EVIDENCE_DIR/rc_run_stdout.log"
else
  echo -e "${RED}ERROR: rc_run_stdout.log not found!${NC}"
fi

if [[ -s "$EVIDENCE_DIR/rc_run_stderr.log" ]]; then
  echo -e "${YELLOW}--- STDERR ---${NC}"
  cat "$EVIDENCE_DIR/rc_run_stderr.log"
fi

set -e  # Re-enable exit on error after output handling

# Check result
if [[ $PYTEST_EXIT_CODE -eq 0 ]]; then
  echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
  echo -e "${GREEN}✅ ALL RC GATES PASSED${NC}"
  echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
  echo "Evidence saved to: $EVIDENCE_DIR"
  exit 0
else
  echo -e "${RED}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
  echo -e "${RED}❌ RC GATES FAILED (exit code: $PYTEST_EXIT_CODE)${NC}"
  echo -e "${RED}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
  # Trigger DumpLogs via ERR trap
  exit $PYTEST_EXIT_CODE
fi
