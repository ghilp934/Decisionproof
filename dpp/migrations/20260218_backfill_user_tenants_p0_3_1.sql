-- P0-3.1: Backfill user_tenants for existing users (Idempotent)
-- Created: 2026-02-18
-- Purpose: Create personal tenants for existing auth.users and map them as owners
-- Strategy: auth.users as authoritative source (no guessing)

-- ============================================================================
-- ASSUMPTIONS
-- ============================================================================
-- 1. Each auth.users record should have exactly 1 personal tenant (1:1 mapping)
-- 2. User is the "owner" of their personal tenant
-- 3. tenant_id format: "user_<first_8_chars_of_user_id>" (deterministic)
-- 4. Existing tenants table records are preserved (may be system/test tenants)

-- ============================================================================
-- SAFETY CHECKS
-- ============================================================================
-- Verify user_tenants table exists
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.tables
        WHERE table_name = 'user_tenants'
    ) THEN
        RAISE EXCEPTION 'user_tenants table does not exist. Run 20260218_create_user_tenants_mapping.sql first.';
    END IF;
END $$;

-- ============================================================================
-- STEP 1: Create personal tenants for existing users (if not exists)
-- ============================================================================

-- Insert personal tenant for each auth.users record
-- Idempotent: ON CONFLICT DO NOTHING
INSERT INTO tenants (tenant_id, display_name, status, created_at)
SELECT
    'user_' || substring(au.id::text, 1, 8) AS tenant_id,
    COALESCE(
        au.email,
        'User ' || substring(au.id::text, 1, 8)
    ) AS display_name,
    'ACTIVE' AS status,
    NOW() AS created_at
FROM auth.users au
WHERE au.deleted_at IS NULL  -- Exclude soft-deleted users
  AND au.banned_until IS NULL  -- Exclude banned users (if column exists)
  AND au.id IS NOT NULL  -- Sanity check
ON CONFLICT (tenant_id) DO NOTHING;  -- Idempotent: skip if tenant already exists

-- Log how many tenants were created
DO $$
DECLARE
    v_tenant_count INTEGER;
BEGIN
    SELECT COUNT(*) INTO v_tenant_count
    FROM tenants
    WHERE tenant_id LIKE 'user_%';

    RAISE NOTICE 'Personal tenants created/verified: % (tenant_id format: user_<uuid_prefix>)', v_tenant_count;
END $$;


-- ============================================================================
-- STEP 2: Create user-tenant mappings (owner role)
-- ============================================================================

-- Insert user_tenant mapping for each auth.users record
-- Idempotent: ON CONFLICT DO NOTHING
INSERT INTO user_tenants (id, user_id, tenant_id, role, status, created_at, updated_at)
SELECT
    gen_random_uuid() AS id,
    au.id AS user_id,
    'user_' || substring(au.id::text, 1, 8) AS tenant_id,
    'owner' AS role,
    'active' AS status,
    NOW() AS created_at,
    NOW() AS updated_at
FROM auth.users au
WHERE au.deleted_at IS NULL  -- Exclude soft-deleted users
  AND au.banned_until IS NULL  -- Exclude banned users (if column exists)
  AND au.id IS NOT NULL  -- Sanity check
ON CONFLICT (user_id, tenant_id) DO NOTHING;  -- Idempotent: skip if mapping already exists

-- Log how many mappings were created
DO $$
DECLARE
    v_mapping_count INTEGER;
BEGIN
    SELECT COUNT(*) INTO v_mapping_count
    FROM user_tenants
    WHERE role = 'owner';

    RAISE NOTICE 'User-tenant mappings created/verified: % (role=owner)', v_mapping_count;
END $$;


-- ============================================================================
-- STEP 3: Verification (inline checks)
-- ============================================================================

-- Check 1: No orphan users (users without tenant mapping)
DO $$
DECLARE
    v_orphan_count INTEGER;
BEGIN
    SELECT COUNT(*) INTO v_orphan_count
    FROM auth.users au
    LEFT JOIN user_tenants ut ON ut.user_id = au.id
    WHERE au.deleted_at IS NULL
      AND au.banned_until IS NULL
      AND ut.user_id IS NULL;

    IF v_orphan_count > 0 THEN
        RAISE WARNING 'Found % orphan users (no tenant mapping). Review required.', v_orphan_count;
    ELSE
        RAISE NOTICE 'Verification PASS: No orphan users (all active users have tenant mapping)';
    END IF;
END $$;

-- Check 2: No duplicate mappings
DO $$
DECLARE
    v_duplicate_count INTEGER;
BEGIN
    SELECT COUNT(*) INTO v_duplicate_count
    FROM (
        SELECT user_id, tenant_id, COUNT(*) AS dup_count
        FROM user_tenants
        GROUP BY user_id, tenant_id
        HAVING COUNT(*) > 1
    ) dups;

    IF v_duplicate_count > 0 THEN
        RAISE EXCEPTION 'CRITICAL: Found duplicate user_tenant mappings. Data integrity violation!';
    ELSE
        RAISE NOTICE 'Verification PASS: No duplicate user_tenant mappings';
    END IF;
END $$;

-- Check 3: All personal tenants have exactly 1 owner
DO $$
DECLARE
    v_orphan_tenant_count INTEGER;
BEGIN
    SELECT COUNT(*) INTO v_orphan_tenant_count
    FROM tenants t
    LEFT JOIN user_tenants ut ON ut.tenant_id = t.tenant_id AND ut.role = 'owner'
    WHERE t.tenant_id LIKE 'user_%'  -- Only check personal tenants
      AND ut.user_id IS NULL;

    IF v_orphan_tenant_count > 0 THEN
        RAISE WARNING 'Found % personal tenants without owner. Review required.', v_orphan_tenant_count;
    ELSE
        RAISE NOTICE 'Verification PASS: All personal tenants have owner';
    END IF;
END $$;


-- ============================================================================
-- STEP 4: Summary Report
-- ============================================================================

DO $$
DECLARE
    v_total_users INTEGER;
    v_total_tenants INTEGER;
    v_total_mappings INTEGER;
    v_orphan_tenants INTEGER;
BEGIN
    -- Count active users
    SELECT COUNT(*) INTO v_total_users
    FROM auth.users
    WHERE deleted_at IS NULL
      AND banned_until IS NULL;

    -- Count personal tenants
    SELECT COUNT(*) INTO v_total_tenants
    FROM tenants
    WHERE tenant_id LIKE 'user_%';

    -- Count user_tenant mappings
    SELECT COUNT(*) INTO v_total_mappings
    FROM user_tenants;

    -- Count non-personal tenants (existing system/test tenants)
    SELECT COUNT(*) INTO v_orphan_tenants
    FROM tenants
    WHERE tenant_id NOT LIKE 'user_%';

    RAISE NOTICE '========================================';
    RAISE NOTICE 'P0-3.1 Backfill Summary';
    RAISE NOTICE '========================================';
    RAISE NOTICE 'Active users (auth.users): %', v_total_users;
    RAISE NOTICE 'Personal tenants (user_*): %', v_total_tenants;
    RAISE NOTICE 'User-tenant mappings: %', v_total_mappings;
    RAISE NOTICE 'Non-personal tenants (system/test): %', v_orphan_tenants;
    RAISE NOTICE '========================================';

    IF v_total_users = v_total_tenants AND v_total_users = v_total_mappings THEN
        RAISE NOTICE 'STATUS: ✅ SUCCESS - All users have personal tenant + owner mapping';
    ELSE
        RAISE WARNING 'STATUS: ⚠️  REVIEW REQUIRED - Counts do not match';
        RAISE WARNING 'Expected: users=tenants=mappings, Got: users=%, tenants=%, mappings=%',
            v_total_users, v_total_tenants, v_total_mappings;
    END IF;

    IF v_orphan_tenants > 0 THEN
        RAISE NOTICE 'NOTE: % existing non-personal tenants preserved (no owner assigned)', v_orphan_tenants;
        RAISE NOTICE 'ACTION: Manually assign owners to these tenants if needed';
    END IF;
END $$;

-- Migration completed
RAISE NOTICE 'P0-3.1 Backfill Migration completed successfully';
