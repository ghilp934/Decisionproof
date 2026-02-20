#!/usr/bin/env bash
# =============================================================================
# db_rollback_drill.sh — Phase 4.5 DB Rollback Drill (Alembic + Data Safety)
#
# 목적:
#   Staging DB에서 Alembic upgrade→downgrade→upgrade 리허설을 수행하고,
#   각 단계의 상태를 증빙팩으로 남긴다.
#
# Non-Negotiables:
#   1) 프로덕션 DB 접근 시 즉시 STOP (denylist 패턴).
#   2) DATABASE_URL 전체를 로그/증빙에 출력 금지 (host만 마스킹 노출).
#   3) 모든 타임스탬프는 UTC ISO-8601 Z 포맷.
#   4) 증빙은 PHASE_EVIDENCE_DIR/02_db_migrations/ 에 저장.
#
# 입력 환경변수:
#   DATABASE_URL_MIGRATIONS  — Alembic 전용 direct connection URL (필수)
#                              (또는 DATABASE_URL으로 대체)
#   DB_CHECKS_SQL_PATH       — rollback_drill_db_checks.sql 경로 (선택)
#   REVISION_TARGET          — 다운그레이드 목표 리비전 (선택; 기본: -1 스텝)
#   PROD_HOST_DENYLIST       — 쉼표 구분 프로덕션 호스트 패턴 (선택; 기본값 적용)
#   PHASE_EVIDENCE_DIR       — 증빙 루트 폴더 (선택; 기본: dpp/evidence/phase4_5_rollback_drill)
#
# 종료 코드:
#   0  성공
#   1  입력 오류 / STOP 조건
#   2  Alembic 실패
#   3  DB 체크 실패
# =============================================================================

set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# ─── 색상 ───────────────────────────────────────────────────────────────────
GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; BLUE='\033[0;34m'; NC='\033[0m'

# ─── 증빙 디렉토리 ──────────────────────────────────────────────────────────
PHASE_EVIDENCE_DIR="${PHASE_EVIDENCE_DIR:-$REPO_ROOT/evidence/phase4_5_rollback_drill}"
DB_MIG_DIR="$PHASE_EVIDENCE_DIR/02_db_migrations"
mkdir -p "$DB_MIG_DIR"

# ─── SQL 경로 ───────────────────────────────────────────────────────────────
DB_CHECKS_SQL_PATH="${DB_CHECKS_SQL_PATH:-$REPO_ROOT/ops/runbooks/sql/rollback_drill_db_checks.sql}"

# ─── 기본 프로덕션 호스트 denylist (패턴; grep -E) ──────────────────────────
# 환경변수로 오버라이드 가능 (쉼표 구분 → 파이프 변환)
DEFAULT_PROD_DENYLIST="\.pooler\.supabase\.com.*prod|decisionproof/prod|prod-db\.|prod\."
PROD_HOST_DENYLIST="${PROD_HOST_DENYLIST:-$DEFAULT_PROD_DENYLIST}"
# 쉼표 → | 변환 (grep -E용)
PROD_DENYLIST_PATTERN="$(echo "$PROD_HOST_DENYLIST" | tr ',' '|')"

# ─── 마스킹 함수 (host만, user/pass 제거) ───────────────────────────────────
mask_db_url() {
    # postgres://user:pass@host:port/db?... → postgres://***@host:port/db (sslmode만 유지)
    local raw="$1"
    # Python으로 파싱 — 값 출력 없이 host만 추출
    python3 -c "
from urllib.parse import urlparse, parse_qs
u = urlparse('''$raw''')
qs = parse_qs(u.query)
ssl = qs.get('sslmode', ['?'])[0]
h = (u.hostname or 'unknown')[:30]
p = u.port or 'default'
print(f'postgres://***@{h}:{p}/*** sslmode={ssl}')
" 2>/dev/null || echo "postgres://***@[parse_error]"
}

# ─── タイムスタンプ (UTC) ─────────────────────────────────────────────────────
ts_utc() { date -u +"%Y-%m-%dT%H:%M:%SZ"; }

# ─── 로그 ────────────────────────────────────────────────────────────────────
log()  { echo -e "${BLUE}[db_drill]${NC} $*"; }
ok()   { echo -e "${GREEN}  ✓${NC} $*"; }
warn() { echo -e "${YELLOW}  ⚠${NC} $*"; }
fail() { echo -e "${RED}  ✗${NC} $*"; }

# ─── トラップ ──────────────────────────────────────────────────────────────────
on_error() {
    local ec=$?
    fail "db_rollback_drill.sh failed (exit $ec) — check $DB_MIG_DIR/error.log"
    echo "exit_code=$ec timestamp=$(ts_utc)" >> "$DB_MIG_DIR/error.log" || true
}
trap on_error ERR

# =============================================================================
# 0. HEADER
# =============================================================================
echo ""
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${BLUE} Phase 4.5 — DB Rollback Drill (Alembic)${NC}"
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo "  started_at=$(ts_utc)"
echo "  evidence=$DB_MIG_DIR"
echo ""

# =============================================================================
# 1. 입력 검증
# =============================================================================
log "[1/7] 입력 검증..."

# DATABASE_URL_MIGRATIONS 우선, 없으면 DATABASE_URL 사용
DB_URL="${DATABASE_URL_MIGRATIONS:-${DATABASE_URL:-}}"
if [[ -z "$DB_URL" ]]; then
    fail "DATABASE_URL_MIGRATIONS (또는 DATABASE_URL) 이 설정되지 않았습니다."
    echo "  export DATABASE_URL_MIGRATIONS=\"postgres://...\" # Alembic direct connection URL"
    exit 1
fi

MASKED_URL="$(mask_db_url "$DB_URL")"
ok "DB URL 설정됨: $MASKED_URL"

# SQL 파일 존재 확인
if [[ ! -f "$DB_CHECKS_SQL_PATH" ]]; then
    fail "DB 체크 SQL 파일을 찾을 수 없습니다: $DB_CHECKS_SQL_PATH"
    exit 1
fi
ok "SQL 파일 확인됨: $DB_CHECKS_SQL_PATH"

# alembic 바이너리 확인
if ! command -v alembic &>/dev/null; then
    fail "alembic 명령을 찾을 수 없습니다. (pip install alembic 또는 가상환경 활성화 필요)"
    exit 1
fi
ok "alembic 바이너리 확인됨"

# psql 바이너리 확인
if ! command -v psql &>/dev/null; then
    fail "psql 명령을 찾을 수 없습니다."
    exit 1
fi
ok "psql 바이너리 확인됨"
echo ""

# =============================================================================
# 2. STOP RULE — Prod 호스트 denylist 검증
# =============================================================================
log "[2/7] Prod 호스트 denylist 검증..."

DB_HOST="$(python3 -c "from urllib.parse import urlparse; print(urlparse('''$DB_URL''').hostname or '')" 2>/dev/null || echo "")"

if echo "$DB_HOST" | grep -qE "$PROD_DENYLIST_PATTERN" 2>/dev/null; then
    fail "STOP: DB 호스트가 프로덕션 패턴과 일치합니다."
    fail "  host_prefix=${DB_HOST:0:20}..."
    fail "  denylist=$PROD_HOST_DENYLIST"
    fail "  Staging DB URL만 허용됩니다."
    exit 1
fi

# Allowlist 확인 (staging 패턴이 아니면 경고)
STAGING_ALLOWLIST_PATTERN="staging|test|dev|pilot|sandbox|drill|local|localhost|127\.0\.0"
if ! echo "$DB_HOST" | grep -qiE "$STAGING_ALLOWLIST_PATTERN" 2>/dev/null; then
    warn "DB 호스트가 staging allowlist 패턴과 일치하지 않습니다."
    warn "  host_prefix=${DB_HOST:0:20}..."
    warn "  계속 진행하려면 PROD_HOST_DENYLIST 를 확인하십시오."
    warn "  10초 후 계속... (Ctrl+C 로 중단)"
    sleep 10
fi

ok "Prod denylist 체크 통과: $MASKED_URL"
echo "prod_denylist_check=PASS db_host_prefix=${DB_HOST:0:20}" \
    | tee "$DB_MIG_DIR/00_denylist_check.txt"
echo ""

# =============================================================================
# 3. 드릴 마커 생성 (UUID)
# =============================================================================
log "[3/7] 드릴 마커 생성..."

DRILL_MARKER_ID="$(python3 -c "import uuid; print(uuid.uuid4())")"
MARKER_CREATED_AT_UTC="$(ts_utc)"

{
    echo "marker_id=$DRILL_MARKER_ID"
    echo "marker_created_at_utc=$MARKER_CREATED_AT_UTC"
    echo "masked_db_url=$MASKED_URL"
} | tee "$DB_MIG_DIR/01_drill_marker.txt"

ok "마커 생성됨: marker_id=$DRILL_MARKER_ID at=$MARKER_CREATED_AT_UTC"
echo ""

# ─── dpp_drill_markers 테이블 생성 (없으면) + 마커 삽입 ──────────────────────
psql "$DB_URL" --no-psqlrc -q <<SQL
BEGIN;
SET LOCAL statement_timeout = '10s';
SET LOCAL lock_timeout = '2s';

CREATE TABLE IF NOT EXISTS public.dpp_drill_markers (
    marker_id   UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    note        TEXT        NOT NULL
);

INSERT INTO public.dpp_drill_markers (marker_id, created_at, note)
VALUES (
    '$DRILL_MARKER_ID'::uuid,
    '$MARKER_CREATED_AT_UTC'::timestamptz,
    'phase4.5_rollback_drill'
);
COMMIT;
SQL

ok "드릴 마커 DB 삽입 완료"
echo ""

# =============================================================================
# 4. 사전 상태 캡처
# =============================================================================
log "[4/7] 사전 상태 캡처..."

# 4-A: Alembic current
ALEMBIC_INI_DIR="$REPO_ROOT"  # alembic.ini 위치
(cd "$ALEMBIC_INI_DIR" && \
    DATABASE_URL="$DB_URL" alembic current 2>&1) \
    | tee "$DB_MIG_DIR/02_pre_alembic_current.txt"

# Extract from tee'd file to avoid losing output when alembic writes to stderr
PRE_REVISION="$(grep -oE '[0-9a-f]{12}' "$DB_MIG_DIR/02_pre_alembic_current.txt" | head -1 || echo "unknown")"

# 4-B: Alembic heads
(cd "$ALEMBIC_INI_DIR" && \
    DATABASE_URL="$DB_URL" alembic heads 2>&1) \
    | tee "$DB_MIG_DIR/02_pre_alembic_heads.txt"

HEAD_REVISION="$(cd "$ALEMBIC_INI_DIR" && \
    DATABASE_URL="$DB_URL" alembic heads 2>/dev/null \
    | grep -oE '[0-9a-f]{12}' | head -1 || echo "unknown")"

# 4-C: DB 상태 SQL (사전)
psql "$DB_URL" --no-psqlrc \
    -v "MARKER_ID=$DRILL_MARKER_ID" \
    -f "$DB_CHECKS_SQL_PATH" \
    > "$DB_MIG_DIR/03_pre_db_checks.txt" 2>&1 || {
    fail "사전 DB 체크 SQL 실패 — $DB_MIG_DIR/03_pre_db_checks.txt 참조"
    exit 3
}

{
    echo "pre_revision=$PRE_REVISION"
    echo "head_revision=$HEAD_REVISION"
    echo "pre_capture_at=$(ts_utc)"
} | tee "$DB_MIG_DIR/03_pre_state_summary.txt"

ok "사전 상태 캡처 완료: pre_revision=$PRE_REVISION head=$HEAD_REVISION"
echo ""

# =============================================================================
# 5. Alembic upgrade → downgrade → upgrade 리허설
# =============================================================================
log "[5/7] Alembic 마이그레이션 리허설..."

DRILL_START_SEC=$(date +%s)

# 5-A: head가 아니거나 미초기화 상태(unknown)면 upgrade head 먼저
# unknown = fresh DB (no alembic_version table) — must upgrade before downgrade
if [[ "$PRE_REVISION" != "$HEAD_REVISION" ]]; then
    warn "DB가 head가 아니거나 미초기화 상태입니다 (pre=$PRE_REVISION → head=$HEAD_REVISION). upgrade head 실행."
    (cd "$ALEMBIC_INI_DIR" && \
        DATABASE_URL="$DB_URL" alembic upgrade head 2>&1) \
        | tee "$DB_MIG_DIR/04a_upgrade_head.txt"
    ok "upgrade head 완료"
fi

# 5-B: downgrade -1 (또는 REVISION_TARGET)
REVISION_TARGET="${REVISION_TARGET:--1}"
warn "downgrade $REVISION_TARGET 실행 중..."
(cd "$ALEMBIC_INI_DIR" && \
    DATABASE_URL="$DB_URL" alembic downgrade "$REVISION_TARGET" 2>&1) \
    | tee "$DB_MIG_DIR/04b_downgrade.txt"

DOWNGRADE_REVISION="$(cd "$ALEMBIC_INI_DIR" && \
    DATABASE_URL="$DB_URL" alembic current 2>/dev/null \
    | grep -oE '[0-9a-f]{12}' | head -1 || echo "unknown")"

ok "downgrade 완료: post_downgrade=$DOWNGRADE_REVISION"

# 5-C: 중간 상태 DB 체크
psql "$DB_URL" --no-psqlrc \
    -v "MARKER_ID=$DRILL_MARKER_ID" \
    -f "$DB_CHECKS_SQL_PATH" \
    > "$DB_MIG_DIR/04c_mid_db_checks.txt" 2>&1 || {
    fail "중간 DB 체크 SQL 실패 (downgrade 후)"
    exit 3
}

# 5-D: upgrade head 재실행
log "upgrade head 재실행..."
(cd "$ALEMBIC_INI_DIR" && \
    DATABASE_URL="$DB_URL" alembic upgrade head 2>&1) \
    | tee "$DB_MIG_DIR/04d_upgrade_head_restore.txt"

POST_REVISION="$(cd "$ALEMBIC_INI_DIR" && \
    DATABASE_URL="$DB_URL" alembic current 2>/dev/null \
    | grep -oE '[0-9a-f]{12}' | head -1 || echo "unknown")"

ok "upgrade head 복원 완료: post_revision=$POST_REVISION"

DRILL_END_SEC=$(date +%s)
DRILL_DURATION_SEC=$((DRILL_END_SEC - DRILL_START_SEC))
echo ""

# =============================================================================
# 6. 사후 상태 캡처 + 검증
# =============================================================================
log "[6/7] 사후 상태 캡처..."

# 6-A: Alembic current (최종)
(cd "$ALEMBIC_INI_DIR" && \
    DATABASE_URL="$DB_URL" alembic current 2>&1) \
    | tee "$DB_MIG_DIR/05_post_alembic_current.txt"

# 6-B: DB 상태 SQL (사후)
psql "$DB_URL" --no-psqlrc \
    -v "MARKER_ID=$DRILL_MARKER_ID" \
    -f "$DB_CHECKS_SQL_PATH" \
    > "$DB_MIG_DIR/05_post_db_checks.txt" 2>&1 || {
    fail "사후 DB 체크 SQL 실패"
    exit 3
}

# 6-C: 마커 존재 확인 (카운트만)
MARKER_COUNT="$(psql "$DB_URL" --no-psqlrc -t -c \
    "SELECT count(*) FROM public.dpp_drill_markers WHERE marker_id = '$DRILL_MARKER_ID'::uuid;" \
    2>/dev/null | tr -d ' ' || echo "0")"

# 6-D: 리비전 일치 확인
DRILL_OK=true
if [[ "$POST_REVISION" != "$HEAD_REVISION" ]]; then
    warn "최종 리비전($POST_REVISION)이 head($HEAD_REVISION)와 불일치"
    DRILL_OK=false
fi

{
    echo "post_revision=$POST_REVISION"
    echo "head_revision=$HEAD_REVISION"
    echo "downgrade_revision=$DOWNGRADE_REVISION"
    echo "drill_ok=$DRILL_OK"
    echo "marker_count=$MARKER_COUNT"
    echo "duration_sec=$DRILL_DURATION_SEC"
    echo "post_capture_at=$(ts_utc)"
} | tee "$DB_MIG_DIR/05_post_state_summary.txt"

ok "사후 상태 캡처 완료"
echo ""

# =============================================================================
# 7. manifest.json 출력 (db_migrations 섹션)
# =============================================================================
log "[7/7] manifest 데이터 출력..."

DB_MANIFEST_JSON="$(cat <<EOF
{
  "ok": $DRILL_OK,
  "pre_revision": "$PRE_REVISION",
  "post_revision": "$POST_REVISION",
  "downgrade_revision": "$DOWNGRADE_REVISION",
  "head_revision": "$HEAD_REVISION",
  "marker_id": "$DRILL_MARKER_ID",
  "marker_count_post": $MARKER_COUNT,
  "duration_sec": $DRILL_DURATION_SEC,
  "started_at_utc": "$MARKER_CREATED_AT_UTC",
  "completed_at_utc": "$(ts_utc)",
  "masked_db_url": "$MASKED_URL",
  "evidence_dir": "$DB_MIG_DIR"
}
EOF
)"

echo "$DB_MANIFEST_JSON" | tee "$DB_MIG_DIR/manifest_db_migrations.json"

# =============================================================================
# 완료
# =============================================================================
echo ""
if [[ "$DRILL_OK" == "true" ]]; then
    echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${GREEN}✅ DB Rollback Drill COMPLETE${NC}"
    echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
else
    echo -e "${RED}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${RED}⚠ DB Rollback Drill COMPLETED WITH WARNINGS${NC}"
    echo -e "${RED}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
fi
echo "  pre_revision    : $PRE_REVISION"
echo "  post_revision   : $POST_REVISION (= head: $HEAD_REVISION)"
echo "  downgrade_step  : $DOWNGRADE_REVISION"
echo "  marker_id       : $DRILL_MARKER_ID"
echo "  marker_count    : $MARKER_COUNT (post-drill)"
echo "  duration_sec    : $DRILL_DURATION_SEC"
echo "  evidence        : $DB_MIG_DIR"
echo ""
