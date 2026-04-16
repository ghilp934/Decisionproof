#!/usr/bin/env bash
# Supabase Region Migration - Master Execution Script
#
# Purpose: 안전하게 Supabase 프로젝트 리전 마이그레이션 실행
#
# 사용법:
#   1. 환경변수 파일 준비: migration_env.sh
#   2. 실행: bash execute_supabase_migration.sh
#
# 필수 환경변수 (migration_env.sh 파일에 정의):
#   - OLD_PROJECT_REF, OLD_DB_URL, OLD_SERVICE_ROLE_KEY
#   - NEW_PROJECT_REF, NEW_DB_URL, NEW_SERVICE_ROLE_KEY
#   - JWT_STRATEGY, HAS_STORAGE_OBJECTS, etc.

set -euo pipefail

# ========================================
# Color Output
# ========================================
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

function log_info() {
  echo -e "${BLUE}[INFO]${NC} $1"
}

function log_success() {
  echo -e "${GREEN}[SUCCESS]${NC} $1"
}

function log_warning() {
  echo -e "${YELLOW}[WARNING]${NC} $1"
}

function log_error() {
  echo -e "${RED}[ERROR]${NC} $1"
}

# ========================================
# Step 0: Load Environment
# ========================================
log_info "Loading migration environment variables..."

ENV_FILE="${1:-migration_env.sh}"

if [ ! -f "$ENV_FILE" ]; then
  log_error "Environment file not found: $ENV_FILE"
  log_info "Create migration_env.sh with required variables:"
  cat <<EOF
# === migration_env.sh 예시 ===
export OLD_PROJECT_REF="xxxxxxxxxxxxxxxxxxxxx"
export OLD_DB_URL="postgresql://postgres.[OLD_REF]:[PASSWORD]@aws-0-ap-southeast-2.pooler.supabase.com:6543/postgres?sslmode=require"
export OLD_PROJECT_URL="https://[OLD_REF].supabase.co"
export OLD_SERVICE_ROLE_KEY="[SECRET]"

export NEW_PROJECT_REF="yyyyyyyyyyyyyyyyyyyyyyy"
export NEW_DB_URL="postgresql://postgres.[NEW_REF]:[PASSWORD]@aws-0-ap-northeast-2.pooler.supabase.com:6543/postgres?sslmode=require"
export NEW_PROJECT_URL="https://[NEW_REF].supabase.co"
export NEW_SERVICE_ROLE_KEY="[SECRET]"

export JWT_STRATEGY="FORCE_RELOGIN"  # or KEEP_SESSIONS
export HAS_CUSTOM_AUTH_OR_STORAGE_CHANGES="no"
export MIGRATION_HISTORY_NEEDED="yes"
export HAS_EDGE_FUNCTIONS="no"
export HAS_STORAGE_OBJECTS="yes"

export MIGRATION_BACKUP_DIR="./supabase_migration_backups/\$(date +%Y%m%d_%H%M%S)"
EOF
  exit 1
fi

# shellcheck source=/dev/null
source "$ENV_FILE"

# Validate required variables
REQUIRED_VARS=(
  "OLD_PROJECT_REF" "OLD_DB_URL" "OLD_SERVICE_ROLE_KEY"
  "NEW_PROJECT_REF" "NEW_DB_URL" "NEW_SERVICE_ROLE_KEY"
  "JWT_STRATEGY"
)

for var in "${REQUIRED_VARS[@]}"; do
  if [ -z "${!var:-}" ]; then
    log_error "Required environment variable not set: $var"
    exit 1
  fi
done

log_success "Environment loaded successfully"

# ========================================
# Step 1: STOP RULES Validation
# ========================================
log_info "Phase 0: Pre-flight validation (STOP RULES)"

# STOP RULE 1: NEW 프로젝트 리전 확인
log_info "  Checking NEW project region (must be ap-northeast-2)..."
if [[ "$NEW_DB_URL" != *"ap-northeast-2"* ]]; then
  log_error "STOP RULE VIOLATED: NEW project region is NOT ap-northeast-2"
  log_error "NEW_DB_URL: $NEW_DB_URL"
  exit 1
fi
log_success "  ✅ NEW project region: ap-northeast-2 (Seoul)"

# STOP RULE 2: OLD/NEW URL 바뀌지 않았는지 확인
log_info "  Checking OLD/NEW URLs are not swapped..."
if [[ "$OLD_DB_URL" == *"ap-northeast-2"* ]] || [[ "$NEW_DB_URL" == *"ap-southeast-2"* ]]; then
  log_error "STOP RULE VIOLATED: OLD/NEW URLs appear to be swapped"
  exit 1
fi
log_success "  ✅ OLD/NEW URLs are correct"

# STOP RULE 3: JWT Strategy 결정됨
log_info "  Checking JWT strategy..."
if [[ "$JWT_STRATEGY" != "FORCE_RELOGIN" ]] && [[ "$JWT_STRATEGY" != "KEEP_SESSIONS" ]]; then
  log_error "STOP RULE VIOLATED: Invalid JWT_STRATEGY: $JWT_STRATEGY"
  log_error "Must be one of: FORCE_RELOGIN, KEEP_SESSIONS"
  exit 1
fi
log_success "  ✅ JWT Strategy: $JWT_STRATEGY"

# ========================================
# User Confirmation
# ========================================
log_warning "==============================================="
log_warning "SUPABASE REGION MIGRATION SUMMARY"
log_warning "==============================================="
log_warning "OLD Project: $OLD_PROJECT_REF (ap-southeast-2)"
log_warning "NEW Project: $NEW_PROJECT_REF (ap-northeast-2)"
log_warning "JWT Strategy: $JWT_STRATEGY"
log_warning "Backup Dir: ${MIGRATION_BACKUP_DIR:-./supabase_migration_backups}"
log_warning "==============================================="
log_warning ""
log_warning "⚠️  This operation will:"
log_warning "  1. Dump all data from OLD project"
log_warning "  2. Restore data to NEW project (OVERWRITES existing data)"
log_warning "  3. Migrate Storage objects"
log_warning "  4. Require manual cutover for app environment variables"
log_warning ""
log_warning "⚠️  BEFORE proceeding, ensure:"
log_warning "  - App is in MAINTENANCE MODE (write operations blocked)"
log_warning "  - All background workers/cron jobs are STOPPED"
log_warning "  - You have reviewed ops/runbooks/supabase_region_migration.md"
log_warning ""

read -p "Type 'PROCEED' to continue: " -r
if [[ "$REPLY" != "PROCEED" ]]; then
  log_error "Migration aborted by user"
  exit 1
fi

# ========================================
# Phase 1: DB Backup (OLD)
# ========================================
log_info "Phase 1: Backing up OLD project database..."

mkdir -p "${MIGRATION_BACKUP_DIR}"

log_info "  Dumping roles..."
supabase db dump \
  --db-url "$OLD_DB_URL" \
  -f "${MIGRATION_BACKUP_DIR}/roles.sql" \
  --role-only

log_info "  Dumping schema..."
supabase db dump \
  --db-url "$OLD_DB_URL" \
  -f "${MIGRATION_BACKUP_DIR}/schema.sql"

log_info "  Dumping data..."
supabase db dump \
  --db-url "$OLD_DB_URL" \
  -f "${MIGRATION_BACKUP_DIR}/data.sql" \
  --use-copy \
  --data-only

if [ "${MIGRATION_HISTORY_NEEDED:-no}" = "yes" ]; then
  log_info "  Dumping migration history..."
  supabase db dump \
    --db-url "$OLD_DB_URL" \
    -f "${MIGRATION_BACKUP_DIR}/history_schema.sql" \
    --schema supabase_migrations

  supabase db dump \
    --db-url "$OLD_DB_URL" \
    -f "${MIGRATION_BACKUP_DIR}/history_data.sql" \
    --use-copy \
    --data-only \
    --schema supabase_migrations
fi

# Validate dumps
log_info "  Validating backup files..."
for file in roles.sql schema.sql data.sql; do
  filepath="${MIGRATION_BACKUP_DIR}/$file"
  if [ ! -f "$filepath" ] || [ ! -s "$filepath" ]; then
    log_error "Backup file is missing or empty: $file"
    exit 1
  fi
  size=$(stat -f%z "$filepath" 2>/dev/null || stat -c%s "$filepath")
  log_success "    ✅ $file: ${size} bytes"
done

# Generate checksums
cd "${MIGRATION_BACKUP_DIR}"
sha256sum *.sql > checksums.txt
cd - > /dev/null

log_success "Phase 1 Complete: Backup saved to ${MIGRATION_BACKUP_DIR}"

# ========================================
# Phase 2: DB Restore (NEW)
# ========================================
log_info "Phase 2: Restoring database to NEW project..."

log_warning "⚠️  This will OVERWRITE all data in NEW project!"
read -p "Type 'RESTORE' to continue: " -r
if [[ "$REPLY" != "RESTORE" ]]; then
  log_error "Restore aborted by user"
  exit 1
fi

cd "${MIGRATION_BACKUP_DIR}"

log_info "  Restoring roles, schema, and data..."
psql \
  --single-transaction \
  --variable ON_ERROR_STOP=1 \
  --dbname "$NEW_DB_URL" \
  <<EOF
-- Roles
\i roles.sql

-- Schema
\i schema.sql

-- Data (with RLS bypassed for speed)
SET session_replication_role = replica;
\i data.sql
SET session_replication_role = DEFAULT;
EOF

log_success "  ✅ Database restored"

if [ "${MIGRATION_HISTORY_NEEDED:-no}" = "yes" ]; then
  log_info "  Restoring migration history..."
  psql \
    --single-transaction \
    --variable ON_ERROR_STOP=1 \
    --dbname "$NEW_DB_URL" \
    <<EOF
\i history_schema.sql
\i history_data.sql
EOF
  log_success "  ✅ Migration history restored"
fi

cd - > /dev/null

# Verify restore
log_info "  Verifying restored data..."
psql "$NEW_DB_URL" -c "SELECT 'tenants' AS table_name, COUNT(*) FROM tenants
UNION ALL SELECT 'runs', COUNT(*) FROM runs
UNION ALL SELECT 'auth.users', COUNT(*) FROM auth.users;"

log_success "Phase 2 Complete: Database restored to NEW project"

# ========================================
# Phase 3: Storage Objects Migration
# ========================================
if [ "${HAS_STORAGE_OBJECTS:-no}" = "yes" ]; then
  log_info "Phase 3: Migrating Storage objects..."

  SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  bash "${SCRIPT_DIR}/migrate_storage_objects.sh" \
    "$OLD_PROJECT_URL" \
    "$OLD_SERVICE_ROLE_KEY" \
    "$NEW_PROJECT_URL" \
    "$NEW_SERVICE_ROLE_KEY"

  log_success "Phase 3 Complete: Storage objects migrated"
else
  log_info "Phase 3: Skipping Storage migration (HAS_STORAGE_OBJECTS=no)"
fi

# ========================================
# Final Summary
# ========================================
log_success "==============================================="
log_success "SUPABASE MIGRATION COMPLETED SUCCESSFULLY"
log_success "==============================================="
log_success ""
log_success "✅ Database backup: ${MIGRATION_BACKUP_DIR}"
log_success "✅ Database restored to NEW project"
if [ "${HAS_STORAGE_OBJECTS:-no}" = "yes" ]; then
  log_success "✅ Storage objects migrated"
fi
log_success ""
log_warning "⚠️  NEXT STEPS (MANUAL):"
log_warning "  1. Review Auth Providers in NEW project Supabase Dashboard"
log_warning "  2. Verify Database Webhooks/Realtime/Extensions settings"
log_warning "  3. Update DPP Kubernetes Secrets with NEW_DB_URL"
log_warning "  4. Restart API/Worker deployments"
log_warning "  5. Run health checks and smoke tests"
log_warning "  6. Monitor for 24 hours before OLD project deletion"
log_warning ""
log_warning "📖 Detailed guide: ops/runbooks/supabase_region_migration.md"
log_warning "==============================================="
