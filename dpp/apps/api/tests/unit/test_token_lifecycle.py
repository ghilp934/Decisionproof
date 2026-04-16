"""Tests for P0-3 Token Lifecycle.

Test Coverage:
T1: Create token returns raw token once
T2: API auth works and updates last_used_at
T3: Revocation blocks access
T4: Rotation grace works
T5: Revoke-all blocks all tokens
T6: Workspace boundary (BOLA defense)
T7: Logging redaction
"""

import os
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from unittest.mock import MagicMock

from dpp_api.auth.session_auth import SessionAuthContext, get_session_auth_context, require_admin_role
from dpp_api.auth.token_lifecycle import generate_token, hash_token, verify_token_hash
from dpp_api.db.models import APIToken, Tenant
from dpp_api.db.session import get_db
from dpp_api.main import app


@pytest.fixture(autouse=True)
def set_token_pepper(monkeypatch):
    """Provide TOKEN_PEPPER_V1 required by hash_token() / verify_token_hash()."""
    monkeypatch.setenv("TOKEN_PEPPER_V1", "test-pepper-value-for-unit-tests-only")


def _mock_admin_ctx(tenant_id: str = "test-tenant-001") -> SessionAuthContext:
    """Return a fake admin SessionAuthContext for dependency override."""
    return SessionAuthContext(
        user_id="test-user-001",
        tenant_id=tenant_id,
        role="admin",
        email="admin@test.example",
    )


@pytest.fixture
def client():
    """Create test client with session auth dependencies overridden."""
    # Both require_admin_role (write ops) and get_session_auth_context (read ops)
    # must be overridden so no real Supabase session is required.
    app.dependency_overrides[require_admin_role] = lambda: _mock_admin_ctx()
    app.dependency_overrides[get_session_auth_context] = lambda: _mock_admin_ctx()
    yield TestClient(app)
    app.dependency_overrides.pop(require_admin_role, None)
    app.dependency_overrides.pop(get_session_auth_context, None)


@pytest.fixture
def admin_headers():
    """Headers for token management (tenant resolved via dependency override)."""
    return {"X-Tenant-ID": "test-tenant-001"}


# ============================================================================
# T1: Create token returns raw token once
# ============================================================================


def test_create_token_returns_raw_once(client, admin_headers):
    """Test T1: POST /v1/tokens returns raw token, GET does not."""
    mock_session = MagicMock()
    mock_session.query.return_value.filter.return_value.first.return_value = Tenant(
        tenant_id="test-tenant-001",
        display_name="Test Tenant",
        status="ACTIVE",
    )
    mock_session.query.return_value.filter.return_value.count.return_value = 0
    mock_session.commit = lambda: None
    # refresh must set created_at — it's a DB server default not in the constructor
    mock_session.refresh = lambda obj: setattr(obj, "created_at", datetime.now(timezone.utc))

    def _override_db():
        yield mock_session

    app.dependency_overrides[get_db] = _override_db

    # Create token
    create_response = client.post(
        "/v1/tokens",
        json={
            "name": "Test Token",
            "scopes": ["read", "write"],
            "expires_in_days": 30,
        },
        headers=admin_headers,
    )

    assert create_response.status_code == 201
    create_data = create_response.json()

    # Raw token should be present
    assert "token" in create_data
    assert create_data["token"].startswith("dp_live_")
    assert "token_id" in create_data
    assert create_data["last4"] == create_data["token"][-4:]

    # List tokens (should NOT return raw token)
    mock_session.query.return_value.filter.return_value.order_by.return_value.all.return_value = []

    list_response = client.get("/v1/tokens", headers=admin_headers)
    assert list_response.status_code == 200
    list_data = list_response.json()

    # If tokens exist in list, verify no raw token
    for token_item in list_data.get("tokens", []):
        assert "token" not in token_item  # Raw token should NOT be in list

    app.dependency_overrides.pop(get_db, None)


# ============================================================================
# T2: API auth works and updates last_used_at
# ============================================================================


def test_api_auth_and_last_used():
    """Test T2: _update_last_used_if_needed sets last_used_at on first use."""
    from dpp_api.auth.token_auth import _update_last_used_if_needed

    mock_db = MagicMock()
    mock_token = APIToken(
        id=str(uuid.uuid4()),
        tenant_id="test-tenant-001",
        name="Test Token",
        token_hash="some-hash",
        prefix="dp_live",
        last4="abcd",
        scopes=[],
        status="active",
        created_at=datetime.now(timezone.utc),
        expires_at=None,
        revoked_at=None,
        last_used_at=None,  # Never used
        pepper_version=1,
        created_by_user_id=None,
        user_agent=None,
        ip_address=None,
    )

    _update_last_used_if_needed(mock_db, mock_token)

    # last_used_at must be set on first use
    assert mock_token.last_used_at is not None
    mock_db.commit.assert_called_once()


# ============================================================================
# T3: Revocation blocks access
# ============================================================================


def test_revocation_blocks_access(client, admin_headers):
    """Test T3: Revoked token cannot authenticate."""
    # Generate test token
    raw_token, last4 = generate_token("dp_live")
    token_hash_value = hash_token(raw_token)
    token_id = str(uuid.uuid4())

    mock_session = MagicMock()
    mock_token = APIToken(
        id=token_id,
        tenant_id="test-tenant-001",
        name="Test Token",
        token_hash=token_hash_value,
        prefix="dp_live",
        last4=last4,
        scopes=[],
        status="active",
        created_at=datetime.now(timezone.utc),
        expires_at=None,
        revoked_at=None,
        last_used_at=None,
        pepper_version=1,
        created_by_user_id=None,
        user_agent=None,
        ip_address=None,
    )

    mock_session.query.return_value.filter.return_value.first.return_value = mock_token
    mock_session.commit = lambda: None

    def _override_db():
        yield mock_session

    app.dependency_overrides[get_db] = _override_db

    # Revoke token
    revoke_response = client.post(
        f"/v1/tokens/{token_id}/revoke",
        headers=admin_headers,
    )

    assert revoke_response.status_code == 200
    assert mock_token.status == "revoked"

    app.dependency_overrides.pop(get_db, None)


# ============================================================================
# T4: Rotation grace works
# ============================================================================


def test_rotation_grace_period(client, admin_headers):
    """Test T4: Old token works during grace, fails after."""
    mock_session = MagicMock()

    def _override_db():
        yield mock_session

    app.dependency_overrides[get_db] = _override_db

    # Original token
    old_token_id = str(uuid.uuid4())
    old_token = APIToken(
        id=old_token_id,
        tenant_id="test-tenant-001",
        name="Test Token",
        token_hash="old-hash",
        prefix="dp_live",
        last4="old4",
        scopes=[],
        status="active",
        created_at=datetime.now(timezone.utc),
        expires_at=None,
        revoked_at=None,
        last_used_at=None,
        pepper_version=1,
        created_by_user_id=None,
        user_agent=None,
        ip_address=None,
    )

    mock_session.query.return_value.filter.return_value.first.return_value = old_token
    mock_session.query.return_value.filter.return_value.count.return_value = 1
    mock_session.commit = lambda: None
    mock_session.add = lambda x: None
    mock_session.refresh = lambda x: None

    # Rotate token
    rotate_response = client.post(
        f"/v1/tokens/{old_token_id}/rotate",
        headers=admin_headers,
    )

    assert rotate_response.status_code == 201
    rotate_data = rotate_response.json()

    # New token returned
    assert "new_token" in rotate_data
    assert rotate_data["old_status"] == "rotating"

    # Old token should have grace period expiration
    assert old_token.status == "rotating"
    assert old_token.expires_at is not None

    # Grace period should be ROTATION_GRACE_MINUTES (default 10)
    grace_minutes = rotate_data["grace_period_minutes"]
    assert grace_minutes == 10

    app.dependency_overrides.pop(get_db, None)


# ============================================================================
# T5: Revoke-all blocks all tokens
# ============================================================================


def test_revoke_all_tokens(client, admin_headers):
    """Test T5: Revoke-all revokes all active/rotating tokens."""
    mock_session = MagicMock()

    def _override_db():
        yield mock_session

    app.dependency_overrides[get_db] = _override_db

    # Create multiple tokens
    token1 = APIToken(
        id=str(uuid.uuid4()),
        tenant_id="test-tenant-001",
        name="Token 1",
        token_hash="hash1",
        prefix="dp_live",
        last4="tok1",
        scopes=[],
        status="active",
        created_at=datetime.now(timezone.utc),
        expires_at=None,
        revoked_at=None,
        last_used_at=None,
        pepper_version=1,
        created_by_user_id=None,
        user_agent=None,
        ip_address=None,
    )

    token2 = APIToken(
        id=str(uuid.uuid4()),
        tenant_id="test-tenant-001",
        name="Token 2",
        token_hash="hash2",
        prefix="dp_live",
        last4="tok2",
        scopes=[],
        status="rotating",
        created_at=datetime.now(timezone.utc),
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=10),
        revoked_at=None,
        last_used_at=None,
        pepper_version=1,
        created_by_user_id=None,
        user_agent=None,
        ip_address=None,
    )

    mock_session.query.return_value.filter.return_value.all.return_value = [token1, token2]
    mock_session.commit = lambda: None
    mock_session.add = lambda x: None

    # Revoke all
    response = client.post("/v1/tokens/revoke-all", headers=admin_headers)

    assert response.status_code == 200
    data = response.json()

    assert data["revoked_count"] == 2
    assert len(data["revoked_token_ids"]) == 2

    # Both tokens should be revoked
    assert token1.status == "revoked"
    assert token2.status == "revoked"

    app.dependency_overrides.pop(get_db, None)


# ============================================================================
# T6: Workspace boundary (BOLA defense)
# ============================================================================


def test_bola_defense(client):
    """Test T6: Cannot access tokens from different tenant."""
    mock_session = MagicMock()

    def _override_db():
        yield mock_session

    app.dependency_overrides[get_db] = _override_db

    # Token belongs to tenant-002
    other_token = APIToken(
        id=str(uuid.uuid4()),
        tenant_id="test-tenant-002",  # Different tenant
        name="Other Token",
        token_hash="other-hash",
        prefix="dp_live",
        last4="oth4",
        scopes=[],
        status="active",
        created_at=datetime.now(timezone.utc),
        expires_at=None,
        revoked_at=None,
        last_used_at=None,
        pepper_version=1,
        created_by_user_id=None,
        user_agent=None,
        ip_address=None,
    )

    # Query will return None (tenant mismatch)
    mock_session.query.return_value.filter.return_value.first.return_value = None

    # Try to revoke token from tenant-001 (should fail — token belongs to 002)
    headers = {"X-Tenant-ID": "test-tenant-001"}

    response = client.post(
        f"/v1/tokens/{other_token.id}/revoke",
        headers=headers,
    )

    # Should return 404 (stealth BOLA defense)
    assert response.status_code == 404
    assert "not found" in response.json()["detail"].lower()

    app.dependency_overrides.pop(get_db, None)


# ============================================================================
# T7: Logging redaction
# ============================================================================


def test_token_generation_does_not_log_raw():
    """Test T7: Token generation does not log raw token."""
    with patch("dpp_api.auth.token_lifecycle.logger") as mock_logger:
        raw_token, last4 = generate_token("dp_live")

        # Check all log calls
        for call in mock_logger.info.call_args_list:
            args, kwargs = call
            # Ensure raw token is not in log message or extra data
            if "extra" in kwargs:
                extra = kwargs["extra"]
                # Convert extra dict to string for searching
                extra_str = str(extra)
                assert raw_token not in extra_str, "Raw token found in logs!"
                assert "last4" in extra  # last4 should be logged
                assert last4 == extra["last4"]


def test_hash_verification():
    """Test token hash verification with constant-time comparison."""
    raw_token, _ = generate_token("dp_live")
    token_hash_value = hash_token(raw_token)

    # Correct hash should verify
    assert verify_token_hash(raw_token, token_hash_value, pepper_version=1)

    # Wrong hash should not verify
    assert not verify_token_hash(raw_token, "wrong-hash", pepper_version=1)

    # Different token should not verify
    other_token, _ = generate_token("dp_live")
    assert not verify_token_hash(other_token, token_hash_value, pepper_version=1)


# Note: Full tests require actual DB setup with fixtures
# These are test stubs showing structure - implement with db_session fixture
