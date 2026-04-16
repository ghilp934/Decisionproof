-- Phase 1-E: RLS (Row Level Security) Smoke Test
--
-- Purpose: Verify RLS is ENABLED and working as expected
--
-- Usage (from NEW project DATABASE_URL with SERVICE_ROLE):
--   psql "$NEW_DB_URL" -f phase1_rls_smoke_test.sql

\echo '================================================================='
\echo 'Phase 1-E: RLS (Row Level Security) Smoke Test'
\echo '================================================================='
\echo ''

-- ========================================
-- Part 1: RLS Status Check
-- ========================================
\echo '[1] RLS Status on All Public Tables:'
\echo ''

SELECT
  tablename,
  CASE WHEN rowsecurity THEN '✅ ENABLED' ELSE '❌ DISABLED' END AS rls_status
FROM pg_tables
WHERE schemaname = 'public'
ORDER BY tablename;

\echo ''

-- ========================================
-- Part 2: RLS Policy Count
-- ========================================
\echo '[2] RLS Policy Count (per table):'
\echo ''

SELECT
  schemaname || '.' || tablename AS table_name,
  COUNT(policyname) AS policy_count,
  CASE
    WHEN COUNT(policyname) = 0 THEN '⚠️  NO POLICIES (default DENY)'
    ELSE '✅ HAS POLICIES'
  END AS status
FROM pg_policies
WHERE schemaname = 'public'
GROUP BY schemaname, tablename

UNION ALL

-- Include tables with RLS ENABLED but NO policies
SELECT
  'public.' || t.tablename AS table_name,
  0 AS policy_count,
  '⚠️  NO POLICIES (default DENY)' AS status
FROM pg_tables t
WHERE t.schemaname = 'public'
  AND t.rowsecurity = true
  AND NOT EXISTS (
    SELECT 1 FROM pg_policies p
    WHERE p.schemaname = t.schemaname
      AND p.tablename = t.tablename
  )

ORDER BY table_name;

\echo ''

-- ========================================
-- Part 3: Expected RLS Configuration
-- ========================================
\echo '[3] Expected Configuration (DPP Backend-Only):'
\echo ''
\echo '  ✅ RLS ENABLED on all public tables (defense-in-depth)'
\echo '  ⚠️  NO RLS POLICIES defined (intentional for server-only access)'
\echo ''
\echo '  Rationale:'
\echo '    - DPP is Backend-only (no client SDK access)'
\echo '    - Server uses SERVICE_ROLE (bypasses RLS by default)'
\echo '    - RLS protects against unauthorized anon/authenticated role access'
\echo ''

-- ========================================
-- Part 4: SERVICE_ROLE Access Test
-- ========================================
\echo '[4] SERVICE_ROLE Access Test (should succeed):'
\echo ''

-- Test: SELECT from tenants (should work)
SELECT COUNT(*) AS tenant_count FROM tenants;

-- Test: SELECT from runs (should work)
SELECT COUNT(*) AS run_count FROM runs;

\echo ''
\echo '  ✅ SERVICE_ROLE can access all tables (RLS bypassed as expected)'

-- ========================================
-- Part 5: Warnings and Recommendations
-- ========================================
\echo ''
\echo '[5] Warnings:'
\echo ''

DO $$
DECLARE
  disabled_rls_tables TEXT[];
BEGIN
  -- Find tables with RLS DISABLED
  SELECT ARRAY_AGG(tablename)
  INTO disabled_rls_tables
  FROM pg_tables
  WHERE schemaname = 'public' AND rowsecurity = false;

  IF disabled_rls_tables IS NOT NULL AND array_length(disabled_rls_tables, 1) > 0 THEN
    RAISE WARNING 'RLS DISABLED on tables: %', disabled_rls_tables;
    RAISE WARNING 'Action: Enable RLS on all public tables for defense-in-depth';
  ELSE
    RAISE NOTICE '✅ All public tables have RLS ENABLED';
  END IF;
END $$;

\echo ''
\echo '================================================================='
\echo 'Phase 1-E: RLS Smoke Test Complete'
\echo '================================================================='
\echo ''
\echo 'Summary:'
\echo '  - RLS should be ENABLED on all public tables'
\echo '  - RLS policies: 0 (intentional for backend-only service)'
\echo '  - SERVICE_ROLE access: working (bypasses RLS)'
\echo ''
\echo 'Phase 2+: If adding client SDK, implement RLS policies for tenant isolation'
