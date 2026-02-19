#!/usr/bin/env bash
# psql_verify_full.sh — verify-full SSL connectivity (Direct + Session Pooler)
#
# SSOT: set all env vars before running. No hardcoded values.
#
# Required env vars:
#   PGHOST         — Direct host (e.g. db.<ref>.supabase.co)
#   PGHOST_POOLER  — Pooler host (e.g. aws-1-ap-northeast-2.pooler.supabase.com)
#   PGPORT         — Direct port (default: 5432)
#   PGPORT_POOLER  — Pooler port (default: 5432)
#   PGDATABASE     — DB name (default: postgres)
#   PGUSER         — Direct user (e.g. postgres)
#   PGUSER_POOLER  — Pooler user (e.g. postgres.<ref>)
#   PGPASSWORD     — Password (shared by direct and pooler)
#   PGSSLMODE      — SSL mode (must be: verify-full)
#   PGSSLROOTCERT  — Absolute path to CA cert (e.g. /path/to/prod-ca-2021.crt)

set -euo pipefail

# Validate required vars
: "${PGHOST:?PGHOST is required}"
: "${PGHOST_POOLER:?PGHOST_POOLER is required}"
: "${PGUSER:?PGUSER is required}"
: "${PGUSER_POOLER:?PGUSER_POOLER is required}"
: "${PGPASSWORD:?PGPASSWORD is required}"
: "${PGSSLMODE:?PGSSLMODE is required}"
: "${PGSSLROOTCERT:?PGSSLROOTCERT is required}"

# Defaults
PGPORT="${PGPORT:-5432}"
PGPORT_POOLER="${PGPORT_POOLER:-5432}"
PGDATABASE="${PGDATABASE:-postgres}"

# Enforce verify-full
if [[ "$PGSSLMODE" != "verify-full" ]]; then
  echo "ERROR: PGSSLMODE must be 'verify-full', got: $PGSSLMODE"
  echo "       sslmode=require does NOT verify the server certificate."
  exit 1
fi

# Check CA cert file exists
if [[ ! -f "$PGSSLROOTCERT" ]]; then
  echo "ERROR: PGSSLROOTCERT file not found: $PGSSLROOTCERT"
  exit 1
fi

run_check() {
  local label="$1"
  local host="$2"
  local port="$3"
  local user="$4"

  printf "%-10s " "[$label]"
  result=$(PGPASSWORD="$PGPASSWORD" psql \
    "host=$host port=$port dbname=$PGDATABASE user=$user sslmode=$PGSSLMODE sslrootcert=$PGSSLROOTCERT" \
    -tAc "SELECT NOW();" 2>&1) && {
      echo "PASS — now()=$result"
    } || {
      echo "FAIL — $result"
      return 1
    }
}

echo "=== psql_verify_full.sh ==="
echo "PGSSLMODE     : $PGSSLMODE"
echo "PGSSLROOTCERT : $PGSSLROOTCERT"
echo ""

FAIL=0
run_check "Direct"  "$PGHOST"        "$PGPORT"        "$PGUSER"        || FAIL=1
run_check "Pooler"  "$PGHOST_POOLER" "$PGPORT_POOLER" "$PGUSER_POOLER" || FAIL=1

echo ""
if [[ "$FAIL" -eq 0 ]]; then
  echo "=== All checks PASSED ==="
  exit 0
else
  echo "=== FAILED — see errors above ==="
  exit 1
fi
