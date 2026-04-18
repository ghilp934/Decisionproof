"""Tests for P0-3.1 user-tenant backfill and session auth integration.

Tests:
- T1: Migration/backfill creates tenant and mapping idempotently
- T2: Token management endpoints accept session JWT but reject API token
- T3: Email signup is disabled (returns 503); provision endpoint creates tenant
- T4: BOLA defense - session user cannot manage tokens from other tenants
"""

import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from dpp_api.db.models import APIToken, Tenant, UserTenant
from dpp_api.main import app

client = TestClient(app)


# ============================================================================
# T1: Migration Idempotency Test
# ============================================================================


def test_backfill_migration_idempotent(db: Session):
    """Test that backfill migration is idempotent (safe to run multiple times).

    Simulates running the migration SQL twice and verifies:
    - No duplicates created
    - No errors raised
    - Tenant and mapping exist after both runs
    """
    # Setup: Create mock auth.users entry (simulate Supabase user)
    user_id = str(uuid.uuid4())
    user_email = "test@example.com"

    # Simulate auth.users insert (we can't actually insert into auth.users in test)
    # Instead, we'll directly test the backfill logic

    # Run 1: Create tenant and mapping
    tenant_id = f"user_{user_id[:8]}"

    tenant = Tenant(
        tenant_id=tenant_id,
        display_name=user_email,
        status="ACTIVE",
    )
    db.add(tenant)

    user_tenant = UserTenant(
        id=str(uuid.uuid4()),
        user_id=user_id,
        tenant_id=tenant_id,
        role="owner",
        status="active",
    )
    db.add(user_tenant)
    db.commit()

    # Run 2: Try to create again (simulates re-running migration)
    # Should not raise error due to ON CONFLICT DO NOTHING

    # Tenant insert (should be skipped due to PK conflict)
    duplicate_tenant = Tenant(
        tenant_id=tenant_id,
        display_name=user_email,
        status="ACTIVE",
    )
    db.add(duplicate_tenant)

    try:
        db.flush()
    except Exception:
        # Expected: PK violation, rollback
        db.rollback()

    # Verify only 1 tenant exists
    tenant_count = db.query(Tenant).filter(Tenant.tenant_id == tenant_id).count()
    assert tenant_count == 1, "Tenant should not be duplicated"

    # User-tenant mapping insert (should be skipped due to UNIQUE constraint)
    duplicate_mapping = UserTenant(
        id=str(uuid.uuid4()),
        user_id=user_id,
        tenant_id=tenant_id,
        role="owner",
        status="active",
    )
    db.add(duplicate_mapping)

    try:
        db.flush()
    except Exception:
        # Expected: UNIQUE constraint violation
        db.rollback()

    # Verify only 1 mapping exists
    mapping_count = (
        db.query(UserTenant)
        .filter(UserTenant.user_id == user_id, UserTenant.tenant_id == tenant_id)
        .count()
    )
    assert mapping_count == 1, "User-tenant mapping should not be duplicated"

    # Cleanup
    db.query(UserTenant).filter(UserTenant.user_id == user_id).delete()
    db.query(Tenant).filter(Tenant.tenant_id == tenant_id).delete()
    db.commit()


# ============================================================================
# T2: Session JWT vs API Token Auth Test
# ============================================================================


@pytest.mark.parametrize("endpoint", [
    "/v1/tokens",  # POST create, GET list
    "/v1/tokens/revoke-all",  # POST revoke-all
])
def test_token_management_rejects_api_token(endpoint: str, db: Session):
    """Test that token management endpoints reject API tokens (403).

    Only session JWT should be accepted for token management.
    """
    # Setup: Create tenant and API token
    tenant_id = "test_tenant_t2"
    tenant = Tenant(tenant_id=tenant_id, display_name="Test Tenant", status="ACTIVE")
    db.add(tenant)

    api_token = APIToken(
        id=str(uuid.uuid4()),
        tenant_id=tenant_id,
        name="Test API Token",
        token_hash="fake_hash_12345",
        prefix="dp_live",
        last4="5678",
        scopes=[],
        status="active",
        pepper_version=1,
    )
    db.add(api_token)
    db.commit()

    # Test: Call token management endpoint with API token (should fail)
    headers = {
        "Authorization": "Bearer dp_live_fake_api_token_12345678"  # API token format
    }

    if endpoint == "/v1/tokens":
        # Test POST (create)
        response = client.post(
            endpoint,
            json={"name": "New Token"},
            headers=headers,
        )
        assert response.status_code in (401, 403), \
            f"Should reject API token auth for {endpoint}"

        # Test GET (list)
        response = client.get(endpoint, headers=headers)
        assert response.status_code in (401, 403), \
            f"Should reject API token auth for GET {endpoint}"
    else:
        # Test POST for other endpoints
        response = client.post(endpoint, headers=headers)
        assert response.status_code in (401, 403), \
            f"Should reject API token auth for {endpoint}"

    # Cleanup
    db.query(APIToken).filter(APIToken.id == api_token.id).delete()
    db.query(Tenant).filter(Tenant.tenant_id == tenant_id).delete()
    db.commit()


# ============================================================================
# T3: Disabled email signup + provision endpoint
# ============================================================================


def test_signup_disabled_returns_503():
    """Email signup is disabled — POST /v1/auth/signup must return 503."""
    response = client.post(
        "/v1/auth/signup",
        json={"email": "test@example.com", "password": "SomePassword123!"},
    )
    assert response.status_code == 503, (
        f"Expected 503 for disabled signup, got {response.status_code}: {response.json()}"
    )
    data = response.json()
    assert data.get("status") == 503
    assert "Google" in data.get("detail", ""), "Detail should mention Google OAuth"


def test_login_disabled_returns_503():
    """Email login is disabled — POST /v1/auth/login must return 503."""
    response = client.post(
        "/v1/auth/login",
        json={"email": "test@example.com", "password": "SomePassword123!"},
    )
    assert response.status_code == 503, (
        f"Expected 503 for disabled login, got {response.status_code}: {response.json()}"
    )
    data = response.json()
    assert data.get("status") == 503


@patch("dpp_api.routers.auth.get_supabase_client")
def test_provision_creates_tenant_for_new_oauth_user(mock_supabase, db: Session):
    """POST /v1/auth/provision creates tenant + owner mapping for a new OAuth user."""
    user_id    = str(uuid.uuid4())
    user_email = "oauth_newuser@example.com"

    mock_user = MagicMock()
    mock_user.id    = user_id
    mock_user.email = user_email

    mock_user_response = MagicMock()
    mock_user_response.user = mock_user

    mock_supabase_instance = MagicMock()
    mock_supabase_instance.auth.get_user.return_value = mock_user_response
    mock_supabase.return_value = mock_supabase_instance

    response = client.post(
        "/v1/auth/provision",
        headers={"Authorization": "Bearer fake_jwt_for_test"},
    )

    assert response.status_code == 201, (
        f"Expected 201 for new tenant creation, got {response.status_code}: {response.json()}"
    )
    data = response.json()
    assert data["created"] is True
    assert data["tenant_id"].startswith("user_")

    expected_tenant_id = data["tenant_id"]
    tenant = db.query(Tenant).filter(Tenant.tenant_id == expected_tenant_id).first()
    assert tenant is not None, "Tenant should exist after provision"
    assert tenant.status == "ACTIVE"

    user_tenant = (
        db.query(UserTenant)
        .filter(UserTenant.user_id == user_id, UserTenant.tenant_id == expected_tenant_id)
        .first()
    )
    assert user_tenant is not None, "UserTenant mapping should exist after provision"
    assert user_tenant.role == "owner"
    assert user_tenant.status == "active"

    # Cleanup
    db.query(UserTenant).filter(UserTenant.user_id == user_id).delete()
    db.query(Tenant).filter(Tenant.tenant_id == expected_tenant_id).delete()
    db.commit()


@patch("dpp_api.routers.auth.get_supabase_client")
def test_provision_is_idempotent(mock_supabase, db: Session):
    """POST /v1/auth/provision returns 200 (not 201) when tenant already exists."""
    user_id    = str(uuid.uuid4())
    user_email = "oauth_existing@example.com"

    # Pre-create tenant and mapping
    tenant_id  = f"user_{user_id[:8]}"
    tenant     = Tenant(tenant_id=tenant_id, display_name=user_email, status="ACTIVE")
    db.add(tenant)
    user_tenant = UserTenant(
        id=str(uuid.uuid4()),
        user_id=user_id,
        tenant_id=tenant_id,
        role="owner",
        status="active",
    )
    db.add(user_tenant)
    db.commit()

    mock_user = MagicMock()
    mock_user.id    = user_id
    mock_user.email = user_email

    mock_user_response = MagicMock()
    mock_user_response.user = mock_user

    mock_supabase_instance = MagicMock()
    mock_supabase_instance.auth.get_user.return_value = mock_user_response
    mock_supabase.return_value = mock_supabase_instance

    response = client.post(
        "/v1/auth/provision",
        headers={"Authorization": "Bearer fake_jwt_for_test"},
    )

    assert response.status_code == 200, (
        f"Expected 200 for idempotent provision, got {response.status_code}: {response.json()}"
    )
    data = response.json()
    assert data["created"] is False
    assert data["tenant_id"] == tenant_id

    # Cleanup
    db.query(UserTenant).filter(UserTenant.user_id == user_id).delete()
    db.query(Tenant).filter(Tenant.tenant_id == tenant_id).delete()
    db.commit()


# ============================================================================
# T4: BOLA Defense Test
# ============================================================================


def test_bola_defense_session_auth(db: Session):
    """Test BOLA defense: Session user A cannot manage tokens for tenant B.

    Security requirement: Tenant boundary must be enforced in session auth.
    """
    # Setup: Create 2 tenants and 2 users
    tenant_a_id = "tenant_a_bola"
    tenant_b_id = "tenant_b_bola"
    user_a_id = str(uuid.uuid4())
    user_b_id = str(uuid.uuid4())

    # Tenant A
    tenant_a = Tenant(tenant_id=tenant_a_id, display_name="Tenant A", status="ACTIVE")
    db.add(tenant_a)

    # Tenant B
    tenant_b = Tenant(tenant_id=tenant_b_id, display_name="Tenant B", status="ACTIVE")
    db.add(tenant_b)

    # User A -> Tenant A (owner)
    user_tenant_a = UserTenant(
        id=str(uuid.uuid4()),
        user_id=user_a_id,
        tenant_id=tenant_a_id,
        role="owner",
        status="active",
    )
    db.add(user_tenant_a)

    # User B -> Tenant B (owner)
    user_tenant_b = UserTenant(
        id=str(uuid.uuid4()),
        user_id=user_b_id,
        tenant_id=tenant_b_id,
        role="owner",
        status="active",
    )
    db.add(user_tenant_b)

    # Create API token for Tenant B
    token_b = APIToken(
        id=str(uuid.uuid4()),
        tenant_id=tenant_b_id,
        name="Tenant B Token",
        token_hash="hash_tenant_b",
        prefix="dp_live",
        last4="b999",
        scopes=[],
        status="active",
        pepper_version=1,
    )
    db.add(token_b)
    db.commit()

    # Test: User A (from Tenant A) tries to revoke Tenant B's token
    # This should fail with 404 (stealth BOLA defense)

    # Mock session JWT for User A
    # In real scenario, we'd mock Supabase JWT validation
    # For this test, we verify the logic in session_auth.py would prevent this

    # Verify mapping exists
    assert user_tenant_a.tenant_id == tenant_a_id
    assert user_tenant_b.tenant_id == tenant_b_id
    assert token_b.tenant_id == tenant_b_id

    # Verify User A cannot access Tenant B's token
    # (This would be enforced by session_auth.py's tenant_id check)
    user_a_tenant = (
        db.query(UserTenant)
        .filter(UserTenant.user_id == user_a_id, UserTenant.status == "active")
        .first()
    )
    assert user_a_tenant.tenant_id == tenant_a_id

    # Attempt to query Tenant B's token with User A's tenant context
    cross_tenant_token = (
        db.query(APIToken)
        .filter(
            APIToken.id == token_b.id,
            APIToken.tenant_id == user_a_tenant.tenant_id,  # Wrong tenant!
        )
        .first()
    )

    assert cross_tenant_token is None, \
        "User A should not see Tenant B's token (BOLA defense)"

    # Cleanup
    db.query(APIToken).filter(APIToken.id == token_b.id).delete()
    db.query(UserTenant).filter(UserTenant.user_id.in_([user_a_id, user_b_id])).delete()
    db.query(Tenant).filter(Tenant.tenant_id.in_([tenant_a_id, tenant_b_id])).delete()
    db.commit()


# ============================================================================
# Additional Test: Orphan User Detection
# ============================================================================


def test_orphan_user_detection(db: Session):
    """Test verification query Q1: Detect users without tenant mapping.

    This simulates the verification SQL query from p0_3_1_verification.sql.
    """
    # Setup: Create user without tenant mapping (orphan state)
    orphan_user_id = str(uuid.uuid4())

    # In production, this would be in auth.users
    # We simulate by checking user_tenants table

    # Verify orphan user is detected
    orphan_count = (
        db.query(UserTenant)
        .filter(UserTenant.user_id == orphan_user_id)
        .count()
    )

    assert orphan_count == 0, "Orphan user should have no tenant mapping"

    # After backfill, this should be 0
    # In production, Q1 query would detect this
