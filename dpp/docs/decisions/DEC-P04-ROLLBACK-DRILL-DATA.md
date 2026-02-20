# DEC-P04-ROLLBACK-DRILL-DATA: Rollback Drill Data Safety — Spec Lock

**Decision ID**: DEC-P04-ROLLBACK-DRILL-DATA
**Phase**: Phase 4.5
**Status**: LOCKED
**Date**: 2026-02-20
**Author**: DPP DevOps

---

## 결정 (Decision)

Phase 4.5 DB Rollback Drill은 "Restore-to-New-Target" 정책과 Evidence-First 원칙을 기반으로 수행한다.
Staging DB에서만 Alembic 마이그레이션 리허설을 실행하며, DB 복구 드릴은 항상 신규 타겟으로만 복원한다.

---

## 잠금 항목 1: Restore-to-New-Target 정책

| 항목 | 값 |
|------|-----|
| 원칙 | DB 복구 드릴은 항상 **새 프로젝트/새 대상**으로 복원 |
| 소스 DB 덮어쓰기 | **절대 금지** — 새 Supabase 프로젝트 또는 새 DB 인스턴스만 허용 |
| 복원 타겟 | `target_project_ref` + `target_host_prefix` 만 증빙에 기록 (URL/비밀번호 출력 금지) |
| 복원 시점 | `as_of_utc` — 드릴 마커 생성 시점(`marker_created_at_utc`) 이전 시점 선택 |

---

## 잠금 항목 2: 증빙 폴더 구조 + 필수 아티팩트

```
dpp/evidence/phase4_5_rollback_drill/
├── 00_meta/
│   └── meta.txt              # git SHA, 타임스탬프(UTC), 운영자, kubectl context
├── 01_k8s_rollback/          # K8s rollback 증빙 (rollback_drill.sh 출력)
├── 02_db_migrations/
│   ├── 00_denylist_check.txt # Prod 호스트 denylist 결과
│   ├── 01_drill_marker.txt   # marker_id, marker_created_at_utc (값 노출 없음)
│   ├── 02_pre_alembic_current.txt
│   ├── 02_pre_alembic_heads.txt
│   ├── 03_pre_db_checks.txt  # rollback_drill_db_checks.sql 사전 실행 결과
│   ├── 03_pre_state_summary.txt
│   ├── 04a_upgrade_head.txt  # (조건부)
│   ├── 04b_downgrade.txt
│   ├── 04c_mid_db_checks.txt
│   ├── 04d_upgrade_head_restore.txt
│   ├── 05_post_alembic_current.txt
│   ├── 05_post_db_checks.txt
│   ├── 05_post_state_summary.txt
│   └── manifest_db_migrations.json
├── 03_db_recovery_pitr/
│   ├── 00_restore_decision.txt  # as_of_utc, target_project_ref, method
│   ├── 01_db_checks_restored.txt # SQL 결과 (marker count=0 확인)
│   └── 02_post_restore_summary.txt
├── 04_app_smoke/
└── template/
    └── manifest.template.json
```

**필수 아티팩트** (드릴 성공 기준):
- `02_db_migrations/manifest_db_migrations.json` — `ok: true`
- `02_db_migrations/03_pre_db_checks.txt` + `05_post_db_checks.txt` 존재
- `02_db_migrations/01_drill_marker.txt` — `marker_id` 기록

---

## 잠금 항목 3: DB Drill 성공 기준

| 항목 | 기준 |
|------|------|
| Alembic 리비전 복원 | `post_revision == head_revision` |
| 마커 카운트 | `marker_count_post >= 1` (마커가 드릴 후에도 존재) |
| downgrade 실행 | `downgrade_revision != head_revision` (실제 다운그레이드 발생) |
| 복구 드릴 (PITR) | `marker_count_in_restored == 0` (마커 생성 이전 시점 복원) |

---

## 잠금 항목 4: No PII in Evidence 규칙

- ❌ 증빙 파일에 이메일, 전화번호, 이름 등 실제 값 출력 금지
- ❌ DATABASE_URL 전체 문자열 출력 금지 (마스킹된 host만 허용)
- ❌ dpp_drill_markers.note 컬럼에 PII 저장 금지 (`phase4.5_rollback_drill` 리터럴만 허용)
- ✅ 카운트, 리비전 해시, 마커 UUID, 마스킹된 호스트만 출력 허용
- ✅ 복구 드릴 증빙: `target_project_ref`, `target_host_prefix` 만 기록

---

## 잠금 항목 5: 타임스탬프 규율

| 규칙 | 형식 |
|------|------|
| 모든 타임스탬프 | UTC ISO-8601 Z 포맷: `YYYY-MM-DDTHH:MM:SSZ` |
| 금지 형식 | 로컬타임, KST, 타임존 없는 형식 |
| `as_of_utc` 선택 기준 | `marker_created_at_utc` 보다 이전 시점 |

---

## STOP RULE (잠금)

| 조건 | 행동 |
|------|------|
| 환경이 staging이 아님 | 즉시 중단 |
| DATABASE_URL이 prod 패턴 | 즉시 중단 (denylist) |
| PITR 미활성 + 일별 백업 없음 | 미충족 전제조건으로 기록 + 중단 |
| 소스 DB 덮어쓰기 시도 | 즉시 중단 |

---

## 관련 파일

| 역할 | 파일 |
|------|------|
| Alembic 드릴 스크립트 | `dpp/tools/db_rollback_drill.sh` |
| K8s 드릴 스크립트 | `dpp/tools/rollback_drill.sh` |
| DB 상태 검증 SQL | `dpp/ops/runbooks/sql/rollback_drill_db_checks.sql` |
| Rollback 런북 | `dpp/ops/runbooks/rollback_drill.md` |
| 복구 드릴 가이드 | `dpp/ops/runbooks/db_recovery_drill_restore_to_new_target.md` |
| 증빙 템플릿 | `dpp/evidence/phase4_5_rollback_drill/template/manifest.template.json` |
