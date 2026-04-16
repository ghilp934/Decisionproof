# DB Recovery Drill — Restore to New Target

Doc ID: DEC-P04-ROLLBACK-DRILL-DATA (Recovery Section)
Phase: 4.5
Last Updated: 2026-02-20

---

## 목적

DB 복구 드릴을 통해 "실제 장애 시 백업/PITR으로 데이터를 복원할 수 있는지"를 검증한다.

**핵심 정책 (Non-Negotiables)**:
- 복원은 항상 **새 프로젝트(새 대상)**로만 수행. 소스 DB 덮어쓰기 금지.
- Prod 데이터 사용 금지. Staging 환경 + Staging 백업만 사용.
- 증빙에 DATABASE_URL 전체 또는 PII 출력 금지.
- 모든 타임스탬프 UTC ISO-8601 Z 포맷.

---

## 전제 조건

이 드릴을 실행하기 전 `db_rollback_drill.sh`로 드릴 마커를 먼저 생성해야 합니다.

```bash
# db_rollback_drill.sh 실행 결과에서 확인
MARKER_ID="<db_rollback_drill.sh의 marker_id 출력값>"
MARKER_CREATED_AT_UTC="<marker_created_at_utc 출력값>"
```

**복원 시점 선택 규칙**:
- `as_of_utc` < `MARKER_CREATED_AT_UTC` (마커 생성 이전 시점)
- 복원 후 `dpp_drill_markers` 에서 해당 `marker_id` count = 0이어야 함

---

## 입력

```bash
# 필수
export AS_OF_UTC="YYYY-MM-DDTHH:MM:SSZ"        # 복원 시점 (마커 이전)
export MARKER_ID="<uuid>"                        # db_rollback_drill.sh 출력값
export TARGET_PROJECT_REF="<new_project_ref>"    # 신규 Supabase 프로젝트 ref (소스 금지)
export RESTORED_DB_URL="<new_target_db_url>"     # 복원 완료 후 신규 프로젝트 DB URL

# 증빙 디렉토리
EVIDENCE_DIR="${PHASE_EVIDENCE_DIR:-dpp/evidence/phase4_5_rollback_drill}/03_db_recovery_pitr"
mkdir -p "$EVIDENCE_DIR"
```

---

## 절차

### Step 0: 복원 결정 기록 (as_of_utc, target_project_ref)

```bash
{
  echo "as_of_utc=$AS_OF_UTC"
  echo "marker_created_at_utc=$MARKER_CREATED_AT_UTC"
  echo "marker_id=$MARKER_ID"
  echo "target_project_ref=$TARGET_PROJECT_REF"
  echo "target_host_prefix=$(python3 -c "
from urllib.parse import urlparse
h = urlparse('''$RESTORED_DB_URL''').hostname or ''
print(h[:20] + '***')
" 2>/dev/null || echo '[masked]')"
  echo "decision_recorded_at=$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
} | tee "$EVIDENCE_DIR/00_restore_decision.txt"
```

**증빙**: `03_db_recovery_pitr/00_restore_decision.txt`

---

### Step 1: 백업/PITR 복원 (UI 주도 — 방법 선택)

> ⚠️ 아래 두 방법 중 하나를 사용. 복원은 **신규 프로젝트**로만.

#### 방법 A: PITR (활성화된 경우)

> 전제: Supabase 프로젝트에 PITR이 활성화되어 있어야 함.
> PITR 활성화 여부: Supabase Dashboard → Settings → Database → Backups 에서 확인.

1. Supabase Dashboard → **새 프로젝트 생성** (또는 기존 빈 프로젝트 선택)
2. 소스 프로젝트 → Settings → Database → Backups → Point in Time
3. 복원 시점: `$AS_OF_UTC` 입력
4. 대상: **새 프로젝트** (`$TARGET_PROJECT_REF`)
5. Restore 실행 → 완료 대기

#### 방법 B: 일별 스냅샷 복원 (PITR 미활성 시)

> 전제: 일별 백업 스냅샷이 존재해야 함.
> 일별 백업 확인: Supabase Dashboard → Settings → Database → Backups (Daily backups 탭).

1. Supabase Dashboard → **새 프로젝트 생성**
2. 소스 프로젝트 → Settings → Database → Backups → Daily backups
3. `$AS_OF_UTC` 이전 가장 최근 스냅샷 선택
4. **Restore to new project** 클릭 (소스 프로젝트 덮어쓰기 금지)
5. 복원 완료 대기

#### 방법 C: pg_dump 백업 파일 사용 (ops/scripts/db_backup_pgdump.sh 결과물)

```bash
# 백업 파일 복원 (신규 대상 DB)
./ops/scripts/db_restore_verify.sh \
  --file backups/<staging_backup_file>.sql.gz \
  --target-url "$RESTORED_DB_URL"
```

> ⚠️ `RESTORED_DB_URL`은 신규 타겟 DB URL. 소스 DB URL 절대 사용 금지.

---

### Step 2: 복원 DB에서 상태 검증 SQL 실행

```bash
# 마커 카운트 확인 (count=0이어야 함)
psql "$RESTORED_DB_URL" --no-psqlrc \
  -v "MARKER_ID=$MARKER_ID" \
  -f dpp/ops/runbooks/sql/rollback_drill_db_checks.sql \
  > "$EVIDENCE_DIR/01_db_checks_restored.txt" 2>&1

cat "$EVIDENCE_DIR/01_db_checks_restored.txt"
```

**기대값**:
- `drill_marker_count` 행의 `count_value = 0` (마커 생성 이전 복원이므로)
- `alembic_revision` 값이 복원 시점 리비전과 일치
- 핵심 테이블(`tenants`, `runs` 등) 존재 여부 확인

---

### Step 3: 결과 요약 기록

```bash
MARKER_COUNT_RESTORED=$(psql "$RESTORED_DB_URL" --no-psqlrc -t -c \
  "SELECT CASE WHEN EXISTS(SELECT 1 FROM information_schema.tables WHERE table_schema='public' AND table_name='dpp_drill_markers')
          THEN (SELECT count(*) FROM public.dpp_drill_markers WHERE marker_id='$MARKER_ID'::uuid)
          ELSE 0 END;" \
  2>/dev/null | tr -d ' ' || echo "unknown")

RECOVERY_OK="false"
[[ "$MARKER_COUNT_RESTORED" == "0" ]] && RECOVERY_OK="true"

{
  echo "as_of_utc=$AS_OF_UTC"
  echo "target_project_ref=$TARGET_PROJECT_REF"
  echo "marker_id=$MARKER_ID"
  echo "marker_count_in_restored=$MARKER_COUNT_RESTORED"
  echo "recovery_ok=$RECOVERY_OK"
  echo "completed_at_utc=$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
} | tee "$EVIDENCE_DIR/02_post_restore_summary.txt"

if [[ "$RECOVERY_OK" != "true" ]]; then
  echo "FAIL: marker_count_in_restored=$MARKER_COUNT_RESTORED (expected=0)"
  echo "  → 복원 시점이 마커 생성 이후일 수 있음. as_of_utc 를 더 이른 시점으로 재선택 후 재시도."
else
  echo "PASS: DB Recovery Drill — marker_count=0 확인됨"
fi
```

**증빙**: `03_db_recovery_pitr/02_post_restore_summary.txt`

---

## STOP RULES

| 조건 | 행동 |
|------|------|
| `RESTORED_DB_URL == 소스 DB URL` | 즉시 중단 — 소스 덮어쓰기 금지 |
| PITR 미활성 + 일별 백업 없음 | "미충족 전제조건"으로 기록 후 중단 |
| `as_of_utc >= marker_created_at_utc` | 시점 재선택 (마커 이전이어야 함) |
| 복원 후 marker_count != 0 | FAIL 기록 — 복원 시점/방법 재검토 |

---

## 증빙 체크리스트

```
evidence/phase4_5_rollback_drill/03_db_recovery_pitr/
├── 00_restore_decision.txt      # as_of_utc, target_project_ref, marker_id (값 없음)
├── 01_db_checks_restored.txt    # SQL 결과 (count만, PII 없음)
└── 02_post_restore_summary.txt  # PASS/FAIL + marker_count_in_restored
```

- [ ] `00_restore_decision.txt` — as_of_utc, target_project_ref 기록됨
- [ ] `01_db_checks_restored.txt` — `drill_marker_count.count_value = 0`
- [ ] `02_post_restore_summary.txt` — `recovery_ok=true`
- [ ] 어떤 파일에도 DATABASE_URL 전체, 이메일, 전화번호 출력 없음

---

**End of Runbook**
