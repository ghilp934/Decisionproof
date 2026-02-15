#!/usr/bin/env bash
set -Eeuo pipefail

# ==============================================================================
# GitHub Branch Rules Verification
# ==============================================================================
# 목적: Ruleset 또는 Branch Protection 설정 검증
# 실행: bash tools/github/verify_branch_rules.sh
# ==============================================================================

die() { echo "FATAL: $*" >&2; exit 1; }
log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" >&2; }

trap 'die "Script failed at line $LINENO"' ERR

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$REPO_ROOT"

# Evidence 디렉토리
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
EVDIR="evidence/github_verify/$TIMESTAMP"
mkdir -p "$EVDIR"

log "Starting verification..."
log "Evidence Dir: $EVDIR"

# ------------------------------------------------------------------------------
# 1. GitHub CLI 확인
# ------------------------------------------------------------------------------
if ! command -v gh >/dev/null 2>&1; then
  die "gh CLI not found. Install: https://cli.github.com/"
fi

if ! gh auth status >/dev/null 2>&1; then
  die "gh not authenticated. Run: gh auth login"
fi

# Repository 정보
REPO_INFO=$(gh repo view --json nameWithOwner,defaultBranchRef 2>/dev/null || echo "")
OWNER_REPO=$(echo "$REPO_INFO" | grep -oP '"nameWithOwner":\s*"\K[^"]+' || echo "")
DEFAULT_BRANCH=$(echo "$REPO_INFO" | grep -oP '"name":\s*"\K[^"]+' | head -1 || echo "master")

log "Repository: $OWNER_REPO"
log "Default Branch: $DEFAULT_BRANCH"

echo "Repository: $OWNER_REPO" > "$EVDIR/repo_info.txt"
echo "Default Branch: $DEFAULT_BRANCH" >> "$EVDIR/repo_info.txt"

# ------------------------------------------------------------------------------
# 2. Ruleset 조회
# ------------------------------------------------------------------------------
log "Fetching rulesets..."

gh api "repos/$OWNER_REPO/rulesets" --jq '.' > "$EVDIR/rulesets.json" 2>"$EVDIR/api_error.log" || true

RULESET_EXISTS=$(cat "$EVDIR/rulesets.json" | grep -c '"name":\s*"decisionproof-main-rc-gate"' || echo "0")

if [ "$RULESET_EXISTS" -eq 0 ]; then
  log "❌ Ruleset 'decisionproof-main-rc-gate' not found"
  echo "FAIL: Ruleset not found" > "$EVDIR/result.txt"

  # Fallback: Check branch protection
  log "Checking branch protection (fallback)..."
  gh api "repos/$OWNER_REPO/branches/$DEFAULT_BRANCH/protection" --jq '.' > "$EVDIR/branch_protection.json" 2>"$EVDIR/bp_error.log" || true

  if [ -s "$EVDIR/branch_protection.json" ]; then
    log "⚠️  Branch protection found (fallback mode)"
  else
    log "❌ No branch protection found"
    die "No ruleset or branch protection configured for $DEFAULT_BRANCH"
  fi
fi

# ------------------------------------------------------------------------------
# 3. Ruleset 검증 - Get detailed ruleset info
# ------------------------------------------------------------------------------
RULESET_ID=$(cat "$EVDIR/rulesets.json" | jq -r '.[] | select(.name == "decisionproof-main-rc-gate") | .id' || echo "")

if [ -z "$RULESET_ID" ]; then
  die "Failed to find ruleset ID"
fi

log "Fetching ruleset details (ID=$RULESET_ID)..."
gh api "repos/$OWNER_REPO/rulesets/$RULESET_ID" --jq '.' > "$EVDIR/active_ruleset.json" 2>"$EVDIR/ruleset_detail_error.log" || die "Failed to fetch ruleset details"

RULESET_JSON=$(cat "$EVDIR/active_ruleset.json")

# 검증 항목
FAIL_COUNT=0
> "$EVDIR/verification.txt"

# A) Enforcement
ENFORCEMENT=$(echo "$RULESET_JSON" | jq -r '.enforcement // "unknown"')
echo "Enforcement: $ENFORCEMENT" >> "$EVDIR/verification.txt"
if [ "$ENFORCEMENT" != "active" ]; then
  echo "  ❌ FAIL: Enforcement is not 'active'" >> "$EVDIR/verification.txt"
  FAIL_COUNT=$((FAIL_COUNT + 1))
else
  echo "  ✅ PASS" >> "$EVDIR/verification.txt"
fi

# B) Target branch
TARGET_BRANCH=$(echo "$RULESET_JSON" | jq -r '.conditions.ref_name.include[0] // "unknown"' | sed 's|refs/heads/||')
echo "Target Branch: $TARGET_BRANCH" >> "$EVDIR/verification.txt"
if [ "$TARGET_BRANCH" != "$DEFAULT_BRANCH" ]; then
  echo "  ❌ FAIL: Target branch mismatch (expected: $DEFAULT_BRANCH)" >> "$EVDIR/verification.txt"
  FAIL_COUNT=$((FAIL_COUNT + 1))
else
  echo "  ✅ PASS" >> "$EVDIR/verification.txt"
fi

# C) Pull request rule
PR_REQUIRED=$(echo "$RULESET_JSON" | jq '[.rules[] | select(.type == "pull_request")] | length')
echo "Pull Request Rule: $PR_REQUIRED rule(s)" >> "$EVDIR/verification.txt"
if [ "$PR_REQUIRED" -eq 0 ]; then
  echo "  ❌ FAIL: Pull request rule not found" >> "$EVDIR/verification.txt"
  FAIL_COUNT=$((FAIL_COUNT + 1))
else
  APPROVALS=$(echo "$RULESET_JSON" | jq -r '[.rules[] | select(.type == "pull_request")][0].parameters.required_approving_review_count // 0')
  CONV_RES=$(echo "$RULESET_JSON" | jq -r '[.rules[] | select(.type == "pull_request")][0].parameters.required_review_thread_resolution // false')

  echo "  - Required approvals: $APPROVALS" >> "$EVDIR/verification.txt"
  echo "  - Conversation resolution: $CONV_RES" >> "$EVDIR/verification.txt"

  if [ "$APPROVALS" -lt 1 ]; then
    echo "  ❌ FAIL: Required approvals < 1" >> "$EVDIR/verification.txt"
    FAIL_COUNT=$((FAIL_COUNT + 1))
  fi

  if [ "$CONV_RES" != "true" ]; then
    echo "  ❌ FAIL: Conversation resolution not enabled" >> "$EVDIR/verification.txt"
    FAIL_COUNT=$((FAIL_COUNT + 1))
  fi

  if [ "$APPROVALS" -ge 1 ] && [ "$CONV_RES" = "true" ]; then
    echo "  ✅ PASS" >> "$EVDIR/verification.txt"
  fi
fi

# D) Required status checks
STATUS_CHECKS=$(echo "$RULESET_JSON" | jq '[.rules[] | select(.type == "required_status_checks")] | length')
echo "Required Status Checks: $STATUS_CHECKS rule(s)" >> "$EVDIR/verification.txt"
if [ "$STATUS_CHECKS" -eq 0 ]; then
  echo "  ❌ FAIL: Required status checks not found" >> "$EVDIR/verification.txt"
  FAIL_COUNT=$((FAIL_COUNT + 1))
else
  STRICT=$(echo "$RULESET_JSON" | jq -r '[.rules[] | select(.type == "required_status_checks")][0].parameters.strict_required_status_checks_policy // false')
  CHECKS=$(echo "$RULESET_JSON" | jq -r '[.rules[] | select(.type == "required_status_checks")][0].parameters.required_status_checks[].context' | tr '\n' ',' | sed 's/,$//')

  echo "  - Strict mode: $STRICT" >> "$EVDIR/verification.txt"
  echo "  - Checks: $CHECKS" >> "$EVDIR/verification.txt"

  if [ "$STRICT" != "true" ]; then
    echo "  ❌ FAIL: Strict mode not enabled" >> "$EVDIR/verification.txt"
    FAIL_COUNT=$((FAIL_COUNT + 1))
  fi

  # Check for required jobs
  if echo "$CHECKS" | grep -q "RC Gates (Linux)"; then
    echo "  ✅ RC Gates (Linux) found" >> "$EVDIR/verification.txt"
  else
    echo "  ❌ FAIL: RC Gates (Linux) not found" >> "$EVDIR/verification.txt"
    FAIL_COUNT=$((FAIL_COUNT + 1))
  fi

  if echo "$CHECKS" | grep -q "rehearse-customer"; then
    echo "  ✅ rehearse-customer found" >> "$EVDIR/verification.txt"
  else
    echo "  ❌ FAIL: rehearse-customer not found" >> "$EVDIR/verification.txt"
    FAIL_COUNT=$((FAIL_COUNT + 1))
  fi

  if [ "$STRICT" = "true" ] && echo "$CHECKS" | grep -q "RC Gates (Linux)" && echo "$CHECKS" | grep -q "rehearse-customer"; then
    echo "  ✅ PASS" >> "$EVDIR/verification.txt"
  fi
fi

# E) Block force pushes
FORCE_PUSH_BLOCK=$(echo "$RULESET_JSON" | jq '[.rules[] | select(.type == "non_fast_forward")] | length')
echo "Block Force Pushes: $FORCE_PUSH_BLOCK rule(s)" >> "$EVDIR/verification.txt"
if [ "$FORCE_PUSH_BLOCK" -eq 0 ]; then
  echo "  ❌ FAIL: Force push block not found" >> "$EVDIR/verification.txt"
  FAIL_COUNT=$((FAIL_COUNT + 1))
else
  echo "  ✅ PASS" >> "$EVDIR/verification.txt"
fi

# F) Block deletions
DELETION_BLOCK=$(echo "$RULESET_JSON" | jq '[.rules[] | select(.type == "deletion")] | length')
echo "Block Deletions: $DELETION_BLOCK rule(s)" >> "$EVDIR/verification.txt"
if [ "$DELETION_BLOCK" -eq 0 ]; then
  echo "  ❌ FAIL: Deletion block not found" >> "$EVDIR/verification.txt"
  FAIL_COUNT=$((FAIL_COUNT + 1))
else
  echo "  ✅ PASS" >> "$EVDIR/verification.txt"
fi

# ------------------------------------------------------------------------------
# 4. Summary
# ------------------------------------------------------------------------------
RESULT="PASS"
if [ "$FAIL_COUNT" -gt 0 ]; then
  RESULT="FAIL"
fi

cat > "$EVDIR/summary.txt" <<EOF
================================================================================
GitHub Branch Rules Verification - $RESULT
================================================================================

Repository: $OWNER_REPO
Default Branch: $DEFAULT_BRANCH
Ruleset Name: decisionproof-main-rc-gate

Verification Results:
$(cat "$EVDIR/verification.txt")

Total Failures: $FAIL_COUNT
Result: $RESULT

Evidence: $EVDIR

================================================================================
EOF

cat "$EVDIR/summary.txt"

if [ "$RESULT" = "FAIL" ]; then
  die "Verification failed with $FAIL_COUNT error(s)"
fi

log "✅ Verification passed"
log "Evidence: $EVDIR"
