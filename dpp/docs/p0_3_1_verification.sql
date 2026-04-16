-- P0-3.1 Verification Queries
-- Purpose: Verify user-tenant mapping integrity after backfill
-- Run these queries against production/staging DB to ensure correctness

-- ============================================================================
-- Q1: Count users missing tenant mapping (should be 0 or explained)
-- ============================================================================
-- Expected: 0 (all active users should have mapping)
-- If > 0: Review user list and manually assign tenants

SELECT COUNT(*) AS orphan_user_count
FROM auth.users u
LEFT JOIN user_tenants ut ON ut.user_id = u.id
WHERE u.deleted_at IS NULL  -- Only active users
  AND u.banned_until IS NULL  -- Exclude banned users
  AND ut.user_id IS NULL;  -- No mapping found

-- Detail view (if count > 0):
SELECT
    u.id AS user_id,
    u.email,
    u.created_at AS user_created_at,
    'NO_TENANT_MAPPING' AS issue
FROM auth.users u
LEFT JOIN user_tenants ut ON ut.user_id = u.id
WHERE u.deleted_at IS NULL
  AND u.banned_until IS NULL
  AND ut.user_id IS NULL
ORDER BY u.created_at DESC;


-- ============================================================================
-- Q2: Membership duplicates (must be 0)
-- ============================================================================
-- Expected: 0 (UNIQUE constraint should prevent duplicates)
-- If > 0: CRITICAL - Data integrity violation, investigate immediately

SELECT
    user_id,
    tenant_id,
    COUNT(*) AS duplicate_count
FROM user_tenants
GROUP BY user_id, tenant_id
HAVING COUNT(*) > 1
ORDER BY duplicate_count DESC;

-- Detail view (if duplicates found):
SELECT *
FROM user_tenants
WHERE (user_id, tenant_id) IN (
    SELECT user_id, tenant_id
    FROM user_tenants
    GROUP BY user_id, tenant_id
    HAVING COUNT(*) > 1
)
ORDER BY user_id, tenant_id, created_at;


-- ============================================================================
-- Q3: Orphaned membership rows (tenant does not exist)
-- ============================================================================
-- Expected: 0 (FK constraint should prevent this)
-- If > 0: Data corruption, delete orphaned rows

SELECT COUNT(*) AS orphaned_mapping_count
FROM user_tenants ut
LEFT JOIN tenants t ON t.tenant_id = ut.tenant_id
WHERE t.tenant_id IS NULL;

-- Detail view (if count > 0):
SELECT
    ut.*,
    'TENANT_NOT_FOUND' AS issue
FROM user_tenants ut
LEFT JOIN tenants t ON t.tenant_id = ut.tenant_id
WHERE t.tenant_id IS NULL
ORDER BY ut.created_at DESC;


-- ============================================================================
-- Q4: Users mapped to non-existent tenant or NULL (must be 0)
-- ============================================================================
-- Expected: 0 (NOT NULL constraint should prevent this)
-- If > 0: CRITICAL - Data integrity violation

SELECT COUNT(*) AS null_value_count
FROM user_tenants
WHERE user_id IS NULL OR tenant_id IS NULL;

-- Detail view:
SELECT *
FROM user_tenants
WHERE user_id IS NULL OR tenant_id IS NULL;


-- ============================================================================
-- Q5: For Pilot single-tenant expectation, list users with >1 tenant
-- ============================================================================
-- Expected: 0 for Pilot (each user should have exactly 1 personal tenant)
-- If > 0: Review - user may have been invited to multiple tenants (valid scenario)

SELECT
    user_id,
    COUNT(*) AS tenant_count,
    ARRAY_AGG(tenant_id ORDER BY created_at) AS tenant_ids,
    ARRAY_AGG(role ORDER BY created_at) AS roles
FROM user_tenants
WHERE status = 'active'
GROUP BY user_id
HAVING COUNT(*) > 1
ORDER BY tenant_count DESC
LIMIT 50;

-- Detail view for multi-tenant users:
SELECT
    u.email,
    ut.tenant_id,
    t.display_name AS tenant_name,
    ut.role,
    ut.status,
    ut.created_at AS mapping_created_at
FROM user_tenants ut
JOIN auth.users u ON u.id = ut.user_id
JOIN tenants t ON t.tenant_id = ut.tenant_id
WHERE ut.user_id IN (
    SELECT user_id
    FROM user_tenants
    WHERE status = 'active'
    GROUP BY user_id
    HAVING COUNT(*) > 1
)
ORDER BY u.email, ut.created_at;


-- ============================================================================
-- Q6: Tenants without any owner (system/test tenants)
-- ============================================================================
-- Expected: May be > 0 for existing non-personal tenants
-- Action: Review and manually assign owners if needed

SELECT
    t.tenant_id,
    t.display_name,
    t.status,
    t.created_at,
    COUNT(ut.id) AS member_count,
    COUNT(ut.id) FILTER (WHERE ut.role = 'owner') AS owner_count
FROM tenants t
LEFT JOIN user_tenants ut ON ut.tenant_id = t.tenant_id AND ut.status = 'active'
GROUP BY t.tenant_id, t.display_name, t.status, t.created_at
HAVING COUNT(ut.id) FILTER (WHERE ut.role = 'owner') = 0  -- No owner
ORDER BY t.created_at DESC;


-- ============================================================================
-- Q7: Summary Statistics (Overall Health Check)
-- ============================================================================

SELECT
    'Active Users (auth.users)' AS metric,
    COUNT(*) AS count
FROM auth.users
WHERE deleted_at IS NULL
  AND banned_until IS NULL

UNION ALL

SELECT
    'Total Tenants',
    COUNT(*)
FROM tenants
WHERE status = 'ACTIVE'

UNION ALL

SELECT
    'Personal Tenants (user_*)',
    COUNT(*)
FROM tenants
WHERE tenant_id LIKE 'user_%'
  AND status = 'ACTIVE'

UNION ALL

SELECT
    'Non-Personal Tenants (system/test)',
    COUNT(*)
FROM tenants
WHERE tenant_id NOT LIKE 'user_%'
  AND status = 'ACTIVE'

UNION ALL

SELECT
    'Total User-Tenant Mappings',
    COUNT(*)
FROM user_tenants
WHERE status = 'active'

UNION ALL

SELECT
    'Owner Mappings',
    COUNT(*)
FROM user_tenants
WHERE role = 'owner'
  AND status = 'active'

UNION ALL

SELECT
    'Admin Mappings',
    COUNT(*)
FROM user_tenants
WHERE role = 'admin'
  AND status = 'active'

UNION ALL

SELECT
    'Member Mappings',
    COUNT(*)
FROM user_tenants
WHERE role = 'member'
  AND status = 'active';


-- ============================================================================
-- Q8: BOLA Test - Verify tenant isolation (Security Check)
-- ============================================================================
-- Expected: Each user should only see/access their own tenant's data
-- This is a smoke test - full BOLA testing should be in automated tests

-- Sample: Check if user A can access tenant B's tokens (should be prevented by app logic)
-- Run this as a manual test with real user_id/tenant_id values

-- Example:
-- SELECT *
-- FROM api_tokens
-- WHERE tenant_id = '<tenant_B>'
--   AND created_by_user_id = '<user_A>';  -- Should return 0 rows

-- NOTE: This query is for manual testing only. Replace placeholders with actual values.


-- ============================================================================
-- PASS/FAIL Criteria (Run all queries above)
-- ============================================================================
-- ✅ PASS if:
--   Q1: orphan_user_count = 0
--   Q2: duplicate_count rows = 0
--   Q3: orphaned_mapping_count = 0
--   Q4: null_value_count = 0
--   Q5: (acceptable if > 0 for invited users, but should be 0 for Pilot)
--   Q6: (acceptable if > 0 for system tenants, review and assign owners)
--   Q7: Active Users = Personal Tenants = Owner Mappings (for single-tenant model)
--
-- ❌ FAIL if:
--   Q1 > 0 (unexplained orphan users)
--   Q2 > 0 (duplicate mappings)
--   Q3 > 0 (orphaned mappings)
--   Q4 > 0 (NULL values)
--
-- ⚠️  REVIEW if:
--   Q5 > 0 (multi-tenant users - may be valid)
--   Q6 > 0 (tenants without owner - manual assignment needed)
