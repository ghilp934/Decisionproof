-- User-Tenant Mapping for Session Auth Integration
-- Created: 2026-02-18
-- Purpose: Map Supabase auth.users to application tenants

-- ============================================================================
-- 1. User-Tenant Mapping Table
-- ============================================================================

CREATE TABLE IF NOT EXISTS user_tenants (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    -- Supabase auth.users reference (UUID)
    user_id UUID NOT NULL,

    -- Application tenant reference
    tenant_id TEXT NOT NULL REFERENCES tenants(tenant_id),

    -- Role within tenant (for future RBAC)
    role TEXT NOT NULL DEFAULT 'member' CHECK (role IN ('owner', 'admin', 'member', 'viewer')),

    -- Status
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'inactive', 'suspended')),

    -- Timestamps
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    -- Constraints
    CONSTRAINT uq_user_tenants_user_tenant UNIQUE (user_id, tenant_id)
);

-- Indexes
CREATE INDEX idx_user_tenants_user_id ON user_tenants(user_id);
CREATE INDEX idx_user_tenants_tenant_id ON user_tenants(tenant_id);
CREATE INDEX idx_user_tenants_user_status ON user_tenants(user_id, status);

-- Updated_at trigger
CREATE OR REPLACE FUNCTION update_user_tenants_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trigger_user_tenants_updated_at
    BEFORE UPDATE ON user_tenants
    FOR EACH ROW
    EXECUTE FUNCTION update_user_tenants_updated_at();

-- Row Level Security (RLS)
ALTER TABLE user_tenants ENABLE ROW LEVEL SECURITY;

-- Policy: Users can see their own mappings
CREATE POLICY user_tenants_select_own ON user_tenants
    FOR SELECT
    USING (
        user_id = auth.uid()  -- Supabase auth context
        OR current_setting('app.role', TRUE) = 'admin'
    );

-- Policy: Only admins can insert/update/delete
CREATE POLICY user_tenants_modify_admin ON user_tenants
    FOR ALL
    USING (current_setting('app.role', TRUE) = 'admin');

COMMENT ON TABLE user_tenants IS 'Maps Supabase auth.users to application tenants with roles';
COMMENT ON COLUMN user_tenants.user_id IS 'Reference to auth.users (Supabase managed)';
COMMENT ON COLUMN user_tenants.role IS 'User role within tenant: owner | admin | member | viewer';


-- ============================================================================
-- 2. Helper Function: Get User Primary Tenant
-- ============================================================================

-- Get user's primary tenant (first active tenant, or owner tenant if exists)
CREATE OR REPLACE FUNCTION get_user_primary_tenant(p_user_id UUID)
RETURNS TEXT AS $$
DECLARE
    v_tenant_id TEXT;
BEGIN
    -- Try to get owner tenant first
    SELECT tenant_id INTO v_tenant_id
    FROM user_tenants
    WHERE user_id = p_user_id
      AND status = 'active'
      AND role = 'owner'
    LIMIT 1;

    -- If no owner tenant, get first active tenant
    IF v_tenant_id IS NULL THEN
        SELECT tenant_id INTO v_tenant_id
        FROM user_tenants
        WHERE user_id = p_user_id
          AND status = 'active'
        ORDER BY created_at ASC
        LIMIT 1;
    END IF;

    RETURN v_tenant_id;
END;
$$ LANGUAGE plpgsql STABLE;

COMMENT ON FUNCTION get_user_primary_tenant IS 'Get user primary tenant (owner tenant or first active)';


-- ============================================================================
-- 3. Helper Function: Check User Tenant Access
-- ============================================================================

CREATE OR REPLACE FUNCTION user_has_tenant_access(p_user_id UUID, p_tenant_id TEXT)
RETURNS BOOLEAN AS $$
BEGIN
    RETURN EXISTS (
        SELECT 1
        FROM user_tenants
        WHERE user_id = p_user_id
          AND tenant_id = p_tenant_id
          AND status = 'active'
    );
END;
$$ LANGUAGE plpgsql STABLE;

COMMENT ON FUNCTION user_has_tenant_access IS 'Check if user has active access to tenant';


-- ============================================================================
-- 4. Seed Data: Auto-create user_tenant mapping on signup (Trigger)
-- ============================================================================

-- Function to auto-create tenant and mapping when user signs up
CREATE OR REPLACE FUNCTION auto_create_user_tenant()
RETURNS TRIGGER AS $$
DECLARE
    v_tenant_id TEXT;
BEGIN
    -- Generate tenant_id from user email (or UUID)
    -- Format: email prefix or random
    v_tenant_id := COALESCE(
        split_part(NEW.email, '@', 1),
        'user_' || substring(NEW.id::text, 1, 8)
    );

    -- Ensure tenant_id is unique (append suffix if needed)
    WHILE EXISTS (SELECT 1 FROM tenants WHERE tenant_id = v_tenant_id) LOOP
        v_tenant_id := v_tenant_id || '_' || substring(md5(random()::text), 1, 4);
    END LOOP;

    -- Create tenant
    INSERT INTO tenants (tenant_id, display_name, status)
    VALUES (
        v_tenant_id,
        COALESCE(NEW.email, 'User ' || substring(NEW.id::text, 1, 8)),
        'ACTIVE'
    );

    -- Create user-tenant mapping as owner
    INSERT INTO user_tenants (user_id, tenant_id, role, status)
    VALUES (NEW.id, v_tenant_id, 'owner', 'active');

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Trigger on auth.users insert (Supabase managed table)
-- NOTE: This requires superuser access to auth schema
-- For production, run this separately with elevated privileges
-- Or handle tenant creation in application code during signup

-- CREATE TRIGGER trigger_auto_create_tenant
--     AFTER INSERT ON auth.users
--     FOR EACH ROW
--     EXECUTE FUNCTION auto_create_user_tenant();

-- ALTERNATIVE: Application-level tenant creation in signup endpoint


-- ============================================================================
-- 5. Migration Verification
-- ============================================================================

DO $$
BEGIN
    ASSERT (SELECT COUNT(*) FROM information_schema.tables
            WHERE table_name = 'user_tenants') = 1,
           'user_tenants table not created';

    RAISE NOTICE 'User-Tenant Mapping Migration completed successfully';
END $$;
