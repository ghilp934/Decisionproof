-- =============================================================================
-- staging_data_isolation_checks.sql
-- Phase 4.3: Staging 데이터 격리 검증 (PII 혼입 방지)
--
-- 목적:  Staging DB에 Prod 고객 데이터/PII가 혼입되지 않았음을 검증.
-- 출력:  카운트, 메타데이터, scan_scope_hash만 출력 (실제 값 출력 절대 금지).
-- 실행:  Staging DB에서만 실행할 것 (Preflight에서 컨텍스트/NS 확인 후 진행).
--
-- scan_scope_hash:
--   md5(schema.table.column) — 스코프를 값 노출 없이 식별하는 해시.
--   PASS/FAIL 판정, 증빙 보관, 조치 추적에 사용.
--
-- Non-Negotiables:
--   - SELECT로 실제 이메일/전화/이름 등 값 샘플 출력 절대 금지.
--   - violating_count > 0이면 즉시 FAIL (데이터 클리어/재시드/익명화 후 재실행).
--   - auth.users total_count > 100이면 수동 확인 필요 STOP.
-- =============================================================================

BEGIN;

-- ─────────────────────────────────────────────────────────────────────────────
-- 0. 안전장치 타임아웃 (이 트랜잭션 전체에 적용)
-- ─────────────────────────────────────────────────────────────────────────────
SET LOCAL statement_timeout                 = '10s';
SET LOCAL lock_timeout                      = '2s';
SET LOCAL idle_in_transaction_session_timeout = '15s';

-- ─────────────────────────────────────────────────────────────────────────────
-- Init: 임시 결과 테이블 (세션 스코프, 값 없이 카운트/메타만 저장)
-- ─────────────────────────────────────────────────────────────────────────────
DROP TABLE IF EXISTS _pii_check_results;
CREATE TEMP TABLE _pii_check_results (
    check_name      TEXT    NOT NULL,
    schema_name     TEXT    NOT NULL,
    table_name      TEXT    NOT NULL,
    column_name     TEXT    NOT NULL,
    violating_count BIGINT  NOT NULL DEFAULT 0,
    total_count     BIGINT  NOT NULL DEFAULT 0,
    note            TEXT    NOT NULL
);

-- ─────────────────────────────────────────────────────────────────────────────
-- Main: 체크 로직 (DO 블록 — 값 출력 없이 카운트만 수집)
-- ─────────────────────────────────────────────────────────────────────────────
DO $body$
DECLARE
    -- ──────────────────────────────────────────────────────────────────────
    -- Allowlist 설정 (허용 더미/익명화 값 패턴)
    -- 필요 시 이 값만 수정하여 실행할 것.
    -- ──────────────────────────────────────────────────────────────────────
    c_allowed_email_domains  TEXT[] := ARRAY[
        'example.com', 'example.net', 'example.org',
        'invalid', 'test.local'
    ];
    c_allowed_phone_patterns TEXT[] := ARRAY[
        '000%', '555%', 'TEST%', 'DUMMY%', 'SAMPLE%', '+1000%'
    ];
    c_allowed_name_tokens    TEXT[] := ARRAY[
        'TEST', 'DUMMY', 'SAMPLE', 'QA', 'STAGING'
    ];

    -- ──────────────────────────────────────────────────────────────────────
    -- 시스템 스키마 제외 목록
    -- auth는 고정 체크(섹션1)에서 별도 처리하므로 동적 탐색(섹션2)에서 제외.
    -- ──────────────────────────────────────────────────────────────────────
    c_excluded_schemas TEXT[] := ARRAY[
        'pg_catalog', 'information_schema', 'pg_toast',
        'extensions', 'supabase_migrations', 'realtime',
        'storage', 'vault', 'pgbouncer', 'pgsodium',
        'supabase_functions', '_realtime',
        'graphql', 'graphql_public',
        'auth'   -- auth.users는 섹션1 고정 체크에서 별도 처리
    ];

    -- 동적 탐색 후보 컬럼 상한
    c_max_cols   INT  := 200;

    -- name 토큰 regex 패턴 (동적 빌드)
    c_name_pat   TEXT;

    -- 작업 변수
    v_total      BIGINT;
    v_viol       BIGINT;
    v_scope      TEXT;
    v_hash       TEXT;
    v_exists     BOOLEAN;
    col_rec      RECORD;

BEGIN
    -- name 허용 패턴을 regex alternation으로 변환 (예: TEST|DUMMY|SAMPLE|QA|STAGING)
    c_name_pat := array_to_string(c_allowed_name_tokens, '|');

    -- ──────────────────────────────────────────────────────────────────────
    -- 섹션 1: auth.users 고정 체크 (Supabase Auth 구조 반영)
    -- ──────────────────────────────────────────────────────────────────────

    -- auth.users 테이블 존재 확인
    SELECT EXISTS (
        SELECT 1 FROM information_schema.tables
        WHERE table_schema = 'auth' AND table_name = 'users'
    ) INTO v_exists;

    IF v_exists THEN

        -- (A) total_count 집계
        EXECUTE 'SELECT count(*) FROM auth.users' INTO v_total;

        -- 메타 행: total count 기록 (violating=0 고정, note에 count 포함)
        INSERT INTO _pii_check_results VALUES (
            'AUTH_USERS_TOTAL', 'auth', 'users', '*',
            0, v_total,
            format(
                'scan_scope_hash=%s; rule=AUTH_USERS_TOTAL; total_users_count=%s',
                md5('auth.users.*'), v_total
            )
        );

        -- (B) email 컬럼 — allowlist 도메인 위반 카운트
        SELECT EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_schema = 'auth' AND table_name = 'users'
              AND column_name = 'email'
        ) INTO v_exists;

        IF v_exists THEN
            v_scope := 'auth.users.email';
            v_hash  := md5(v_scope);
            EXECUTE
                'SELECT count(*) FROM auth.users
                 WHERE email IS NOT NULL
                   AND position(''@'' IN email) > 0
                   AND lower(split_part(email, ''@'', 2)) <> ALL($1)'
            USING c_allowed_email_domains INTO v_viol;

            INSERT INTO _pii_check_results VALUES (
                'AUTH_USERS_EMAIL_ALLOWLIST', 'auth', 'users', 'email',
                v_viol, v_total,
                format('scan_scope_hash=%s; rule=AUTH_USERS_EMAIL_ALLOWLIST', v_hash)
            );
        END IF;

        -- (C) phone 컬럼 — allowlist 패턴 위반 카운트
        SELECT EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_schema = 'auth' AND table_name = 'users'
              AND column_name = 'phone'
        ) INTO v_exists;

        IF v_exists THEN
            v_scope := 'auth.users.phone';
            v_hash  := md5(v_scope);
            EXECUTE
                'SELECT count(*) FROM auth.users
                 WHERE phone IS NOT NULL
                   AND NOT (phone ILIKE ANY($1))'
            USING c_allowed_phone_patterns INTO v_viol;

            INSERT INTO _pii_check_results VALUES (
                'AUTH_USERS_PHONE_ALLOWLIST', 'auth', 'users', 'phone',
                v_viol, v_total,
                format('scan_scope_hash=%s; rule=AUTH_USERS_PHONE_ALLOWLIST', v_hash)
            );
        END IF;

        -- (D) raw_user_meta_data (jsonb) — email/phone 키 위반 카운트
        SELECT EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_schema = 'auth' AND table_name = 'users'
              AND column_name = 'raw_user_meta_data'
        ) INTO v_exists;

        IF v_exists THEN
            -- D-1: meta->>'email'
            v_scope := 'auth.users.raw_user_meta_data->email';
            v_hash  := md5(v_scope);
            EXECUTE
                'SELECT count(*) FROM auth.users
                 WHERE raw_user_meta_data->>''email'' IS NOT NULL
                   AND position(''@'' IN (raw_user_meta_data->>''email'')) > 0
                   AND lower(split_part(
                         raw_user_meta_data->>''email'', ''@'', 2
                       )) <> ALL($1)'
            USING c_allowed_email_domains INTO v_viol;

            INSERT INTO _pii_check_results VALUES (
                'AUTH_USERS_META_EMAIL_ALLOWLIST', 'auth', 'users',
                'raw_user_meta_data->email',
                v_viol, v_total,
                format('scan_scope_hash=%s; rule=AUTH_USERS_META_EMAIL_ALLOWLIST', v_hash)
            );

            -- D-2: meta->>'phone'
            v_scope := 'auth.users.raw_user_meta_data->phone';
            v_hash  := md5(v_scope);
            EXECUTE
                'SELECT count(*) FROM auth.users
                 WHERE raw_user_meta_data->>''phone'' IS NOT NULL
                   AND NOT ((raw_user_meta_data->>''phone'') ILIKE ANY($1))'
            USING c_allowed_phone_patterns INTO v_viol;

            INSERT INTO _pii_check_results VALUES (
                'AUTH_USERS_META_PHONE_ALLOWLIST', 'auth', 'users',
                'raw_user_meta_data->phone',
                v_viol, v_total,
                format('scan_scope_hash=%s; rule=AUTH_USERS_META_PHONE_ALLOWLIST', v_hash)
            );
        END IF;

    END IF; -- auth.users

    -- ──────────────────────────────────────────────────────────────────────
    -- 섹션 2: 동적 탐색 — 비시스템 스키마 전체 (information_schema 기반)
    --
    -- 후보 선정 기준:
    --   - c_excluded_schemas 제외
    --   - 문자열 계열 컬럼 (text, varchar, char, citext)
    --   - 컬럼명이 email/phone/name 계열 패턴에 매칭
    --   - ORDER BY schema, table, column (고정) + LIMIT c_max_cols
    -- ──────────────────────────────────────────────────────────────────────
    FOR col_rec IN (
        SELECT
            c.table_schema,
            c.table_name,
            c.column_name,
            CASE
                WHEN c.column_name ~* '(^|_)(email|e_mail|mail)($|_)'
                     THEN 'EMAIL'
                WHEN c.column_name ~* '(^|_)(phone|mobile|tel)($|_)'
                     THEN 'PHONE'
                WHEN c.column_name ~* '(^|_)(name|full_name|first_name|last_name)($|_)'
                     THEN 'NAME'
                ELSE NULL
            END AS col_type
        FROM information_schema.columns c
        WHERE
            c.table_schema <> ALL(c_excluded_schemas)
            AND (
                c.data_type IN ('text', 'character varying', 'character')
                OR (c.data_type = 'USER-DEFINED' AND c.udt_name = 'citext')
            )
            AND (
                c.column_name ~* '(^|_)(email|e_mail|mail)($|_)'
                OR c.column_name ~* '(^|_)(phone|mobile|tel)($|_)'
                OR c.column_name ~* '(^|_)(name|full_name|first_name|last_name)($|_)'
            )
        ORDER BY c.table_schema, c.table_name, c.column_name
        LIMIT c_max_cols
    )
    LOOP
        -- col_type이 NULL이면 매칭 실패 — skip
        CONTINUE WHEN col_rec.col_type IS NULL;

        v_scope := col_rec.table_schema || '.' || col_rec.table_name || '.' || col_rec.column_name;
        v_hash  := md5(v_scope);

        -- total (non-null 행수)
        EXECUTE format(
            'SELECT count(*) FROM %I.%I WHERE %I IS NOT NULL',
            col_rec.table_schema, col_rec.table_name, col_rec.column_name
        ) INTO v_total;

        IF col_rec.col_type = 'EMAIL' THEN
            -- 이메일: '@' 포함 + allowlist 도메인 외 → 위반
            EXECUTE format(
                'SELECT count(*) FROM %I.%I
                 WHERE %I IS NOT NULL
                   AND position(''@'' IN %I::text) > 0
                   AND lower(split_part(%I::text, ''@'', 2)) <> ALL($1)',
                col_rec.table_schema, col_rec.table_name,
                col_rec.column_name, col_rec.column_name, col_rec.column_name
            ) USING c_allowed_email_domains INTO v_viol;

            INSERT INTO _pii_check_results VALUES (
                'DYN_EMAIL_ALLOWLIST',
                col_rec.table_schema, col_rec.table_name, col_rec.column_name,
                v_viol, v_total,
                format('scan_scope_hash=%s; rule=DYN_EMAIL_ALLOWLIST', v_hash)
            );

        ELSIF col_rec.col_type = 'PHONE' THEN
            -- 전화: allowlist 패턴 미매칭 → 위반
            EXECUTE format(
                'SELECT count(*) FROM %I.%I
                 WHERE %I IS NOT NULL
                   AND NOT (%I::text ILIKE ANY($1))',
                col_rec.table_schema, col_rec.table_name,
                col_rec.column_name, col_rec.column_name
            ) USING c_allowed_phone_patterns INTO v_viol;

            INSERT INTO _pii_check_results VALUES (
                'DYN_PHONE_ALLOWLIST',
                col_rec.table_schema, col_rec.table_name, col_rec.column_name,
                v_viol, v_total,
                format('scan_scope_hash=%s; rule=DYN_PHONE_ALLOWLIST', v_hash)
            );

        ELSIF col_rec.col_type = 'NAME' THEN
            -- 이름: 비어있지 않고 허용 토큰 미포함 → 위반
            EXECUTE format(
                'SELECT count(*) FROM %I.%I
                 WHERE %I IS NOT NULL
                   AND %I != ''''
                   AND NOT (upper(%I::text) ~ $1)',
                col_rec.table_schema, col_rec.table_name,
                col_rec.column_name, col_rec.column_name, col_rec.column_name
            ) USING c_name_pat INTO v_viol;

            INSERT INTO _pii_check_results VALUES (
                'DYN_NAME_ALLOWLIST',
                col_rec.table_schema, col_rec.table_name, col_rec.column_name,
                v_viol, v_total,
                format('scan_scope_hash=%s; rule=DYN_NAME_ALLOWLIST', v_hash)
            );

        END IF;

    END LOOP; -- 동적 탐색 루프

END;
$body$;

-- =============================================================================
-- 최종 출력 (값 없이 — 카운트, 테이블/컬럼명, scan_scope_hash만)
--
-- PASS/FAIL 판정 기준 (실행자가 확인):
--   FAIL:  violating_count > 0 인 행이 1개 이상 (데이터 클리어/재시드/익명화 필요)
--   STOP:  AUTH_USERS_TOTAL 의 total_count > 100 (원인 파악 전 진행 금지)
--   PASS:  모든 violating_count = 0
-- =============================================================================
SELECT
    check_name,
    schema_name,
    table_name,
    column_name,
    violating_count,
    total_count,
    note
FROM _pii_check_results
ORDER BY violating_count DESC, check_name ASC;

-- 임시 테이블 정리
DROP TABLE IF EXISTS _pii_check_results;

COMMIT;
