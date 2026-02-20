# =============================================================================
# db_rollback_drill.ps1 - Phase 4.5 DB Rollback Drill (Windows PowerShell)
# db_rollback_drill.sh 의 PowerShell 동등 스크립트 (psycopg2 사용, psql 불필요)
#
# 입력 환경변수:
#   DATABASE_URL_MIGRATIONS  - Alembic 전용 direct connection URL (필수)
#   PHASE_EVIDENCE_DIR       - 증빙 루트 폴더 (선택)
#   REVISION_TARGET          - 다운그레이드 목표 (선택; 기본: -1)
# =============================================================================

$ErrorActionPreference = "Stop"

# --- 경로 설정 ---------------------------------------------------------------
$SCRIPT_DIR   = Split-Path -Parent $MyInvocation.MyCommand.Path
$DPP_ROOT     = Split-Path -Parent $SCRIPT_DIR
$VENV_SCRIPTS = Join-Path $DPP_ROOT ".venv\Scripts"
$ALEMBIC_EXE  = Join-Path $VENV_SCRIPTS "alembic.exe"
$PYTHON_EXE   = Join-Path $VENV_SCRIPTS "python.exe"

# --- 증빙 디렉토리 -----------------------------------------------------------
if ($env:PHASE_EVIDENCE_DIR) {
    $PHASE_EVIDENCE_DIR = $env:PHASE_EVIDENCE_DIR
} else {
    $PHASE_EVIDENCE_DIR = Join-Path $DPP_ROOT "evidence\phase4_5_rollback_drill"
}
$DB_MIG_DIR = Join-Path $PHASE_EVIDENCE_DIR "02_db_migrations"
[System.IO.Directory]::CreateDirectory($DB_MIG_DIR) | Out-Null

$DB_CHECKS_SQL = Join-Path $DPP_ROOT "ops\runbooks\sql\rollback_drill_db_checks.sql"

# --- 타임스탬프 (UTC) --------------------------------------------------------
function Get-TsUtc {
    return (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
}

# --- Python 실행 헬퍼 (DB_URL은 환경변수로 전달) -----------------------------
# Python 코드는 별도 임시 파일로 작성하여 here-string 파싱 문제 회피
$PY_TEMP = Join-Path $env:TEMP "dpp_drill_helper.py"

function Invoke-PyHelper {
    param([string]$Action, [string]$Extra = "")
    $env:_DPP_DB_URL  = $script:DB_URL
    $env:_DPP_MARKER  = $script:MARKER_ID
    $env:_DPP_SQL     = $script:DB_CHECKS_SQL
    $env:_DPP_ACTION  = $Action
    $env:_DPP_EXTRA   = $Extra
    $result = & $PYTHON_EXE $PY_TEMP 2>&1
    Remove-Item Env:\_DPP_DB_URL  -ErrorAction SilentlyContinue
    Remove-Item Env:\_DPP_MARKER  -ErrorAction SilentlyContinue
    Remove-Item Env:\_DPP_SQL     -ErrorAction SilentlyContinue
    Remove-Item Env:\_DPP_ACTION  -ErrorAction SilentlyContinue
    Remove-Item Env:\_DPP_EXTRA   -ErrorAction SilentlyContinue
    return $result
}

# --- Python 헬퍼 스크립트 작성 -----------------------------------------------
$pyCode = @'
import os, sys, uuid, re
from urllib.parse import urlparse, parse_qs

db_url  = os.environ.get("_DPP_DB_URL", "")
marker  = os.environ.get("_DPP_MARKER", "NO_MARKER")
sql_f   = os.environ.get("_DPP_SQL", "")
action  = os.environ.get("_DPP_ACTION", "")
extra   = os.environ.get("_DPP_EXTRA", "")

def mask_url(url):
    try:
        u = urlparse(url)
        qs = parse_qs(u.query)
        ssl = qs.get("sslmode", ["?"])[0]
        h = (u.hostname or "unknown")[:30]
        p = u.port or "default"
        return f"postgres://***@{h}:{p}/*** sslmode={ssl}"
    except Exception:
        return "postgres://***@[parse_error]"

def get_host(url):
    try:
        return urlparse(url).hostname or ""
    except Exception:
        return ""

def run_db_checks():
    import psycopg2
    sql = open(sql_f, encoding="utf-8").read()
    sql = sql.replace(":'MARKER_ID'", f"'{marker}'")
    conn = psycopg2.connect(db_url)
    conn.autocommit = True
    cur = conn.cursor()
    skip = {"begin", "commit"}
    stmts = [s.strip() for s in sql.split(";") if s.strip()]
    out = []
    for stmt in stmts:
        words = stmt.split()
        first = words[0].lower() if words else ""
        if first in skip:
            continue
        if first == "set" and "local" in stmt.lower():
            try:
                cur.execute(stmt)
            except Exception:
                pass
            continue
        try:
            cur.execute(stmt)
            if cur.description:
                cols = [d[0] for d in cur.description]
                out.append(" | ".join(cols))
                out.append("-" * 60)
                for row in cur.fetchall():
                    out.append(" | ".join(str(v) if v is not None else "NULL" for v in row))
                out.append("")
        except Exception as e:
            out.append(f"ERROR: {e}")
    conn.close()
    print("\n".join(out))

def insert_marker():
    import psycopg2
    conn = psycopg2.connect(db_url)
    conn.autocommit = False
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS public.dpp_drill_markers (
            marker_id   UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
            created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            note        TEXT        NOT NULL
        )
    """)
    cur.execute(
        "INSERT INTO public.dpp_drill_markers (marker_id, created_at, note) VALUES (%s::uuid, now(), %s)",
        (marker, "phase4.5_rollback_drill")
    )
    conn.commit()
    conn.close()
    print("marker_inserted")

def get_marker_count():
    import psycopg2
    conn = psycopg2.connect(db_url)
    conn.autocommit = True
    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT count(*) FROM public.dpp_drill_markers WHERE marker_id = %s::uuid",
            (marker,)
        )
        print(cur.fetchone()[0])
    except Exception:
        print(0)
    conn.close()

if action == "mask_url":
    print(mask_url(db_url))
elif action == "get_host":
    print(get_host(db_url))
elif action == "gen_uuid":
    print(str(uuid.uuid4()))
elif action == "insert_marker":
    insert_marker()
elif action == "run_checks":
    run_db_checks()
elif action == "get_marker_count":
    get_marker_count()
else:
    print(f"unknown action: {action}", file=sys.stderr)
    sys.exit(1)
'@

$pyCode | Set-Content -Encoding UTF8 $PY_TEMP

# --- alembic 실행 ------------------------------------------------------------
function Invoke-Alembic {
    param([string[]]$AlembicArgs)
    $env:DATABASE_URL = $script:DB_URL
    Push-Location $DPP_ROOT
    try {
        $result = & $ALEMBIC_EXE @AlembicArgs 2>&1
        return $result
    } finally {
        Pop-Location
        Remove-Item Env:\DATABASE_URL -ErrorAction SilentlyContinue
    }
}

function Get-AlembicRevision {
    $raw = Invoke-Alembic -AlembicArgs @("current")
    $m = [regex]::Match(($raw -join ""), '[0-9a-f]{12}')
    if ($m.Success) { return $m.Value } else { return "unknown" }
}

function Get-AlembicHead {
    $raw = Invoke-Alembic -AlembicArgs @("heads")
    $m = [regex]::Match(($raw -join ""), '[0-9a-f]{12}')
    if ($m.Success) { return $m.Value } else { return "unknown" }
}

# =============================================================================
# 메인 실행
# =============================================================================
Write-Host ""
Write-Host "======================================================="
Write-Host " Phase 4.5 - DB Rollback Drill [PowerShell]"
Write-Host "======================================================="
Write-Host "  started_at=$(Get-TsUtc)"
Write-Host "  evidence=$DB_MIG_DIR"
Write-Host ""

# --- [1/7] 입력 검증 ---------------------------------------------------------
Write-Host "[db_drill] [1/7] 입력 검증..."

if ($env:DATABASE_URL_MIGRATIONS) {
    $DB_URL = $env:DATABASE_URL_MIGRATIONS
} elseif ($env:DATABASE_URL) {
    $DB_URL = $env:DATABASE_URL
} else {
    $DB_URL = ""
}

if (-not $DB_URL) {
    Write-Error "DATABASE_URL_MIGRATIONS 가 설정되지 않았습니다.`n  PowerShell: `$env:DATABASE_URL_MIGRATIONS = 'postgres://...'"
    exit 1
}

$MASKED_URL = (Invoke-PyHelper -Action "mask_url") -join ""
Write-Host "  OK DB URL: $MASKED_URL"

if (-not (Test-Path $DB_CHECKS_SQL)) {
    Write-Error "SQL 파일 없음: $DB_CHECKS_SQL"
    exit 1
}
Write-Host "  OK SQL 파일 확인"

if (-not (Test-Path $ALEMBIC_EXE)) {
    Write-Error "alembic.exe 없음: $ALEMBIC_EXE"
    exit 1
}
Write-Host "  OK alembic 확인"

if (-not (Test-Path $PYTHON_EXE)) {
    Write-Error "python.exe 없음: $PYTHON_EXE"
    exit 1
}
Write-Host "  OK python 확인"
Write-Host ""

# --- [2/7] Prod denylist 검증 ------------------------------------------------
Write-Host "[db_drill] [2/7] Prod denylist 검증..."

$DB_HOST = (Invoke-PyHelper -Action "get_host") -join ""
$PROD_PATTERNS = @("pooler\.supabase\.com.*prod", "decisionproof\.prod", "prod-db\.", "^prod\.")
$isProd = $false
foreach ($pat in $PROD_PATTERNS) {
    if ($DB_HOST -match $pat) {
        $isProd = $true
        break
    }
}

if ($isProd) {
    Write-Error "STOP: DB 호스트가 프로덕션 패턴과 일치합니다. Staging DB URL만 허용."
    exit 1
}

$STAGING_PATTERNS = @("staging", "test", "dev", "pilot", "sandbox", "drill", "local", "localhost", "127\.0\.0")
$isStaging = $false
foreach ($pat in $STAGING_PATTERNS) {
    if ($DB_HOST -match $pat) {
        $isStaging = $true
        break
    }
}

if (-not $isStaging) {
    $hostLen = [Math]::Min(20, $DB_HOST.Length)
    Write-Warning "DB 호스트가 staging 패턴과 불일치: $($DB_HOST.Substring(0, $hostLen))..."
    Write-Warning "10초 후 계속... (Ctrl+C 로 중단)"
    Start-Sleep -Seconds 10
}

Write-Host "  OK Prod denylist 통과"
$hostLen = [Math]::Min(20, $DB_HOST.Length)
"prod_denylist_check=PASS db_host_prefix=$($DB_HOST.Substring(0, $hostLen))" |
    Set-Content -Encoding UTF8 (Join-Path $DB_MIG_DIR "00_denylist_check.txt")
Write-Host ""

# --- [3/7] 드릴 마커 생성 ---------------------------------------------------
Write-Host "[db_drill] [3/7] 드릴 마커 생성..."

$MARKER_ID = (& $PYTHON_EXE -c "import uuid; print(str(uuid.uuid4()))") -join ""
$MARKER_CREATED_AT_UTC = Get-TsUtc

"marker_id=$MARKER_ID`nmarker_created_at_utc=$MARKER_CREATED_AT_UTC`nmasked_db_url=$MASKED_URL" |
    Set-Content -Encoding UTF8 (Join-Path $DB_MIG_DIR "01_drill_marker.txt")

Write-Host "  OK 마커 생성: $MARKER_ID"

$insertResult = Invoke-PyHelper -Action "insert_marker"
if ($LASTEXITCODE -ne 0) {
    Write-Error "마커 DB 삽입 실패: $insertResult"
    exit 1
}
Write-Host "  OK 마커 DB 삽입 완료"
Write-Host ""

# --- [4/7] 사전 상태 캡처 ---------------------------------------------------
Write-Host "[db_drill] [4/7] 사전 상태 캡처..."

$preCurrentOut = Invoke-Alembic -AlembicArgs @("current")
$preCurrentOut | Set-Content -Encoding UTF8 (Join-Path $DB_MIG_DIR "02_pre_alembic_current.txt")

$preHeadsOut = Invoke-Alembic -AlembicArgs @("heads")
$preHeadsOut | Set-Content -Encoding UTF8 (Join-Path $DB_MIG_DIR "02_pre_alembic_heads.txt")

$PRE_REVISION  = Get-AlembicRevision
$HEAD_REVISION = Get-AlembicHead

$preDbChecks = Invoke-PyHelper -Action "run_checks"
$preDbChecks | Set-Content -Encoding UTF8 (Join-Path $DB_MIG_DIR "03_pre_db_checks.txt")

"pre_revision=$PRE_REVISION`nhead_revision=$HEAD_REVISION`npre_capture_at=$(Get-TsUtc)" |
    Set-Content -Encoding UTF8 (Join-Path $DB_MIG_DIR "03_pre_state_summary.txt")

Write-Host "  OK pre_revision=$PRE_REVISION  head=$HEAD_REVISION"
Write-Host ""

# --- [5/7] Alembic upgrade->downgrade->upgrade -------------------------------
Write-Host "[db_drill] [5/7] Alembic 마이그레이션 리허설..."

$DRILL_START = Get-Date

if ($PRE_REVISION -ne $HEAD_REVISION -and $PRE_REVISION -ne "unknown") {
    Write-Warning "현재 리비전($PRE_REVISION)이 head($HEAD_REVISION)가 아님 - upgrade head 먼저 실행"
    $upgradeOut = Invoke-Alembic -AlembicArgs @("upgrade", "head")
    $upgradeOut | Set-Content -Encoding UTF8 (Join-Path $DB_MIG_DIR "04a_upgrade_head.txt")
    Write-Host "  OK upgrade head 완료"
}

if ($env:REVISION_TARGET) {
    $REVISION_TARGET = $env:REVISION_TARGET
} else {
    $REVISION_TARGET = "-1"
}

Write-Warning "downgrade $REVISION_TARGET 실행 중..."
$downgradeOut = Invoke-Alembic -AlembicArgs @("downgrade", $REVISION_TARGET)
$downgradeOut | Set-Content -Encoding UTF8 (Join-Path $DB_MIG_DIR "04b_downgrade.txt")
$DOWNGRADE_REVISION = Get-AlembicRevision
Write-Host "  OK downgrade 완료: $DOWNGRADE_REVISION"

$midDbChecks = Invoke-PyHelper -Action "run_checks"
$midDbChecks | Set-Content -Encoding UTF8 (Join-Path $DB_MIG_DIR "04c_mid_db_checks.txt")

Write-Host "  upgrade head 복원 중..."
$upgradeRestoreOut = Invoke-Alembic -AlembicArgs @("upgrade", "head")
$upgradeRestoreOut | Set-Content -Encoding UTF8 (Join-Path $DB_MIG_DIR "04d_upgrade_head_restore.txt")
$POST_REVISION = Get-AlembicRevision
Write-Host "  OK 복원 완료: $POST_REVISION"

$DRILL_DURATION_SEC = [int]((Get-Date) - $DRILL_START).TotalSeconds
Write-Host ""

# --- [6/7] 사후 상태 캡처 ---------------------------------------------------
Write-Host "[db_drill] [6/7] 사후 상태 캡처..."

$postCurrentOut = Invoke-Alembic -AlembicArgs @("current")
$postCurrentOut | Set-Content -Encoding UTF8 (Join-Path $DB_MIG_DIR "05_post_alembic_current.txt")

$postDbChecks = Invoke-PyHelper -Action "run_checks"
$postDbChecks | Set-Content -Encoding UTF8 (Join-Path $DB_MIG_DIR "05_post_db_checks.txt")

$MARKER_COUNT = (Invoke-PyHelper -Action "get_marker_count") -join ""
if (-not $MARKER_COUNT) { $MARKER_COUNT = "0" }

$DRILL_OK = ($POST_REVISION -eq $HEAD_REVISION)
$DRILL_OK_STR = $DRILL_OK.ToString().ToLower()

"post_revision=$POST_REVISION`nhead_revision=$HEAD_REVISION`ndowngrade_revision=$DOWNGRADE_REVISION`ndrill_ok=$DRILL_OK_STR`nmarker_count=$MARKER_COUNT`nduration_sec=$DRILL_DURATION_SEC`npost_capture_at=$(Get-TsUtc)" |
    Set-Content -Encoding UTF8 (Join-Path $DB_MIG_DIR "05_post_state_summary.txt")

Write-Host "  OK post=$POST_REVISION  marker_count=$MARKER_COUNT"
Write-Host ""

# --- [7/7] manifest_db_migrations.json 생성 ----------------------------------
Write-Host "[db_drill] [7/7] manifest 생성..."

$COMPLETED_AT = Get-TsUtc
$DB_MIG_DIR_FWD = $DB_MIG_DIR -replace '\\', '/'

$manifest = "{
  `"ok`": $DRILL_OK_STR,
  `"pre_revision`": `"$PRE_REVISION`",
  `"post_revision`": `"$POST_REVISION`",
  `"downgrade_revision`": `"$DOWNGRADE_REVISION`",
  `"head_revision`": `"$HEAD_REVISION`",
  `"marker_id`": `"$MARKER_ID`",
  `"marker_count_post`": $MARKER_COUNT,
  `"duration_sec`": $DRILL_DURATION_SEC,
  `"started_at_utc`": `"$MARKER_CREATED_AT_UTC`",
  `"completed_at_utc`": `"$COMPLETED_AT`",
  `"masked_db_url`": `"$MASKED_URL`",
  `"evidence_dir`": `"$DB_MIG_DIR_FWD`"
}"

$manifest | Set-Content -Encoding UTF8 (Join-Path $DB_MIG_DIR "manifest_db_migrations.json")
Write-Host $manifest

# --- 완료 -------------------------------------------------------------------
Write-Host ""
if ($DRILL_OK) {
    Write-Host "======================================================="
    Write-Host "  DB Rollback Drill COMPLETE"
    Write-Host "======================================================="
} else {
    Write-Host "======================================================="
    Write-Warning "DB Rollback Drill COMPLETED WITH WARNINGS"
    Write-Host "======================================================="
}
Write-Host "  pre_revision   : $PRE_REVISION"
Write-Host "  post_revision  : $POST_REVISION  (head: $HEAD_REVISION)"
Write-Host "  downgrade_step : $DOWNGRADE_REVISION"
Write-Host "  marker_id      : $MARKER_ID"
Write-Host "  marker_count   : $MARKER_COUNT (post-drill)"
Write-Host "  duration_sec   : $DRILL_DURATION_SEC"
Write-Host "  evidence       : $DB_MIG_DIR"
Write-Host ""

# 임시 파일 정리
Remove-Item $PY_TEMP -ErrorAction SilentlyContinue
