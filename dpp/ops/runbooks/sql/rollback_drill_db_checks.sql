-- =============================================================================
-- rollback_drill_db_checks.sql
-- Phase 4.5: DB Rollback Drill — 사전/사후 상태 검증 쿼리
--
-- 목적:  Alembic 마이그레이션 드릴 전후로 DB 상태를 카운트/버전만으로 기록.
-- 출력:  수치, 불리언, 버전 문자열만. 실제 이메일/전화/이름 등 값 출력 절대 금지.
-- 안전:  statement_timeout 10s, lock_timeout 2s 적용.
-- 실행:  Staging DB에서만 사용. Prod에서는 STOP.
--
-- 호출 컨텍스트:
--   - db_rollback_drill.sh가 MARKER_ID를 환경변수로 전달.
--   - psql :'MARKER_ID' 형태로 변수 바인딩.
--   - 미전달 시 marker 체크는 'NO_MARKER' 고정값으로 처리.
-- =============================================================================

BEGIN;

SET LOCAL statement_timeout                   = '10s';
SET LOCAL lock_timeout                        = '2s';
SET LOCAL idle_in_transaction_session_timeout = '15s';

-- ─────────────────────────────────────────────────────────────────────────────
-- 1. Alembic 현재 리비전
-- ─────────────────────────────────────────────────────────────────────────────
SELECT
    'alembic_revision'   AS check_name,
    CASE
        WHEN EXISTS (
            SELECT 1 FROM information_schema.tables
            WHERE table_schema = 'public' AND table_name = 'alembic_version'
        )
        THEN (SELECT version_num FROM public.alembic_version LIMIT 1)
        ELSE 'TABLE_NOT_FOUND'
    END AS value,
    NULL::bigint AS count_value,
    'current alembic revision (or TABLE_NOT_FOUND)' AS note;

-- ─────────────────────────────────────────────────────────────────────────────
-- 2. 핵심 테이블 존재 여부 (존재: 1, 부재: 0)
-- ─────────────────────────────────────────────────────────────────────────────
SELECT
    'table_exists_' || t.tbl AS check_name,
    CASE WHEN EXISTS (
        SELECT 1 FROM information_schema.tables
        WHERE table_schema = 'public' AND table_name = t.tbl
    ) THEN 'true' ELSE 'false' END AS value,
    NULL::bigint AS count_value,
    'table existence check' AS note
FROM (
    VALUES
        ('tenants'),
        ('runs'),
        ('api_keys'),
        ('tenant_plans'),
        ('tenant_usage_daily')
) AS t(tbl)
ORDER BY t.tbl;

-- ─────────────────────────────────────────────────────────────────────────────
-- 3. 핵심 테이블 row count (카운트만, 값 출력 없음)
-- ─────────────────────────────────────────────────────────────────────────────
SELECT 'row_count_tenants'            AS check_name, NULL AS value,
       (SELECT count(*) FROM public.tenants             WHERE EXISTS(SELECT 1 FROM information_schema.tables WHERE table_schema='public' AND table_name='tenants'            )) AS count_value, 'row count' AS note
UNION ALL
SELECT 'row_count_runs',              NULL,
       (SELECT count(*) FROM public.runs                WHERE EXISTS(SELECT 1 FROM information_schema.tables WHERE table_schema='public' AND table_name='runs'               )), 'row count'
UNION ALL
SELECT 'row_count_api_keys',          NULL,
       (SELECT count(*) FROM public.api_keys            WHERE EXISTS(SELECT 1 FROM information_schema.tables WHERE table_schema='public' AND table_name='api_keys'           )), 'row count'
UNION ALL
SELECT 'row_count_tenant_plans',      NULL,
       (SELECT count(*) FROM public.tenant_plans        WHERE EXISTS(SELECT 1 FROM information_schema.tables WHERE table_schema='public' AND table_name='tenant_plans'       )), 'row count'
UNION ALL
SELECT 'row_count_tenant_usage_daily',NULL,
       (SELECT count(*) FROM public.tenant_usage_daily  WHERE EXISTS(SELECT 1 FROM information_schema.tables WHERE table_schema='public' AND table_name='tenant_usage_daily' )), 'row count'
ORDER BY check_name;

-- ─────────────────────────────────────────────────────────────────────────────
-- 4. Drill Marker 테이블 (dpp_drill_markers) 존재 여부 및 특정 marker_id 카운트
--
-- 드릴 마커: 드릴 중 삽입하는 무해한 식별자 레코드.
-- note 컬럼에 PII 저장 금지 (rollback_drill 문자열만 허용).
-- ─────────────────────────────────────────────────────────────────────────────

-- 4-A: 테이블 존재 확인
SELECT
    'drill_marker_table_exists' AS check_name,
    CASE WHEN EXISTS (
        SELECT 1 FROM information_schema.tables
        WHERE table_schema = 'public' AND table_name = 'dpp_drill_markers'
    ) THEN 'true' ELSE 'false' END AS value,
    NULL::bigint AS count_value,
    'dpp_drill_markers table existence' AS note;

-- 4-B: 특정 marker_id 존재 카운트 (값 출력 없이 count만)
-- MARKER_ID 미설정 시 :'MARKER_ID' 는 psql 변수; 쉘에서 -v MARKER_ID=<uuid> 전달.
-- 변수 미설정 가드: NO_MARKER 리터럴이면 0 반환.
SELECT
    'drill_marker_count' AS check_name,
    NULL AS value,
    CASE
        WHEN NOT EXISTS (
            SELECT 1 FROM information_schema.tables
            WHERE table_schema = 'public' AND table_name = 'dpp_drill_markers'
        ) THEN 0
        WHEN :'MARKER_ID' = 'NO_MARKER' THEN -1
        ELSE (
            SELECT count(*)
            FROM public.dpp_drill_markers
            WHERE marker_id = (:'MARKER_ID')::uuid
        )
    END AS count_value,
    'count of rows matching marker_id (0=not found; -1=no marker_id provided)' AS note;

-- ─────────────────────────────────────────────────────────────────────────────
-- 5. DB 타임스탬프 / 서버 시간 (UTC)
-- ─────────────────────────────────────────────────────────────────────────────
SELECT
    'db_server_time_utc' AS check_name,
    to_char(now() AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"Z"') AS value,
    NULL::bigint AS count_value,
    'server timestamp in UTC ISO-8601' AS note;

COMMIT;
