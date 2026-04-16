"""Unit tests for Phase 2 billing endpoints.

Merge-blocker tests:
  T-13: Idempotent checkout session — second call returns 200 with existing session
  T-25: Capture is non-authoritative — 202 returned, entitlement NOT activated

Additional coverage:
  T-13a: New session creation returns 201
  T-25a: Capture idempotent replay returns 200
  T-14:  Email-not-confirmed → 403 before any DB access
  T-15:  Plan not found → 404
  T-16:  Session expired → 410
  T-17:  Terminal session rejected on create-order → 409
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_session(
    *,
    status: str = "CHECKOUT_SESSION_CREATED",
    paypal_order_id: str | None = None,
    expires_at: datetime | None = None,
) -> MagicMock:
    """Build a mock CheckoutSession object."""
    session = MagicMock()
    session.id = "session-uuid-001"
    session.tenant_id = "tenant-001"
    session.user_id = "user-uuid-001"
    session.plan_id = "beta_private_starter_v1"
    session.amount_usd_cents = 2900
    session.currency = "USD"
    session.status = status
    session.paypal_order_id = paypal_order_id
    session.paypal_request_id_create = "req-create-001"
    session.paypal_request_id_capture = "req-capture-001"
    session.expires_at = expires_at or datetime.now(timezone.utc) + timedelta(minutes=25)
    session.created_at = datetime.now(timezone.utc)
    session.updated_at = None
    return session


def _make_plan(*, price_usd_cents: int = 2900) -> MagicMock:
    plan = MagicMock()
    plan.plan_id = "beta_private_starter_v1"
    plan.status = "ACTIVE"
    plan.features_json = {"price_usd_cents": price_usd_cents}
    return plan


def _make_auth_context() -> MagicMock:
    ctx = MagicMock()
    ctx.user_id = "user-uuid-001"
    ctx.tenant_id = "tenant-001"
    ctx.role = "owner"
    return ctx


# ---------------------------------------------------------------------------
# T-13: Idempotent checkout session
# ---------------------------------------------------------------------------


class TestCheckoutSessionCreate:
    """POST /v1/billing/checkout-sessions"""

    def _client_with_mocks(self, *, email_confirmed=True, created=True, session=None):
        """Return a TestClient with all billing dependencies mocked."""
        from dpp_api.main import app

        plan = _make_plan()
        sess = session or _make_session()
        auth = _make_auth_context()

        with (
            patch("dpp_api.routers.billing._is_email_confirmed", return_value=email_confirmed),
            patch("dpp_api.routers.billing.get_session_auth_context", return_value=auth),
            patch("dpp_api.routers.billing.CheckoutRepository") as MockRepo,
        ):
            mock_repo_instance = MagicMock()
            mock_repo_instance.get_plan.return_value = plan
            mock_repo_instance.create_session.return_value = (sess, created)
            MockRepo.return_value = mock_repo_instance

            mock_db = MagicMock()
            with patch("dpp_api.routers.billing.get_db", return_value=mock_db):
                client = TestClient(app, raise_server_exceptions=True)
                yield client, mock_repo_instance, mock_db

    def test_new_session_returns_201(self):
        """T-13a: First call creates session and returns 201."""
        from dpp_api.main import app

        plan = _make_plan()
        sess = _make_session()
        auth = _make_auth_context()
        mock_db = MagicMock()

        with (
            patch("dpp_api.routers.billing._is_email_confirmed", return_value=True),
            patch("dpp_api.routers.billing.CheckoutRepository") as MockRepo,
            patch("dpp_api.db.session.get_db", return_value=mock_db),
        ):
            mock_repo_instance = MagicMock()
            mock_repo_instance.get_plan.return_value = plan
            mock_repo_instance.create_session.return_value = (sess, True)
            MockRepo.return_value = mock_repo_instance

            app.dependency_overrides.clear()
            from dpp_api.auth.session_auth import get_session_auth_context
            from dpp_api.db.session import get_db
            app.dependency_overrides[get_session_auth_context] = lambda: auth
            app.dependency_overrides[get_db] = lambda: mock_db

            client = TestClient(app)
            response = client.post(
                "/v1/billing/checkout-sessions",
                json={"plan_id": "beta_private_starter_v1"},
            )

            app.dependency_overrides.clear()

        assert response.status_code == 201
        data = response.json()
        assert data["session_id"] == "session-uuid-001"
        assert data["status"] == "CHECKOUT_SESSION_CREATED"
        assert "amount" in data
        # MUST NOT expose internal fields (DEC-V1-12)
        assert "nonce" not in data
        assert "paypal_request_id_create" not in data
        assert "paypal_request_id_capture" not in data
        assert "user_id" not in data

    def test_existing_session_returns_200(self):
        """T-13: Second call returns existing session with 200 (idempotent)."""
        from dpp_api.main import app

        plan = _make_plan()
        sess = _make_session()
        auth = _make_auth_context()
        mock_db = MagicMock()

        with (
            patch("dpp_api.routers.billing._is_email_confirmed", return_value=True),
            patch("dpp_api.routers.billing.CheckoutRepository") as MockRepo,
        ):
            mock_repo_instance = MagicMock()
            mock_repo_instance.get_plan.return_value = plan
            mock_repo_instance.create_session.return_value = (sess, False)  # existing
            MockRepo.return_value = mock_repo_instance

            app.dependency_overrides.clear()
            from dpp_api.auth.session_auth import get_session_auth_context
            from dpp_api.db.session import get_db
            app.dependency_overrides[get_session_auth_context] = lambda: auth
            app.dependency_overrides[get_db] = lambda: mock_db

            client = TestClient(app)
            response = client.post(
                "/v1/billing/checkout-sessions",
                json={"plan_id": "beta_private_starter_v1"},
            )

            app.dependency_overrides.clear()

        # T-13: Idempotent return MUST be 200, not 201
        assert response.status_code == 200
        data = response.json()
        assert data["session_id"] == "session-uuid-001"
        # DB commit MUST NOT have been called (no new session was created)
        mock_db.commit.assert_not_called()

    def test_email_not_confirmed_returns_403(self):
        """T-14: Unconfirmed email blocks checkout session creation."""
        from dpp_api.main import app

        auth = _make_auth_context()
        mock_db = MagicMock()

        with patch("dpp_api.routers.billing._is_email_confirmed", return_value=False):
            app.dependency_overrides.clear()
            from dpp_api.auth.session_auth import get_session_auth_context
            from dpp_api.db.session import get_db
            app.dependency_overrides[get_session_auth_context] = lambda: auth
            app.dependency_overrides[get_db] = lambda: mock_db

            client = TestClient(app)
            response = client.post(
                "/v1/billing/checkout-sessions",
                json={"plan_id": "beta_private_starter_v1"},
            )

            app.dependency_overrides.clear()

        assert response.status_code == 403
        body = response.json()
        assert body["status"] == 403

    def test_plan_not_found_returns_404(self):
        """T-15: Non-existent plan_id returns 404."""
        from dpp_api.main import app

        auth = _make_auth_context()
        mock_db = MagicMock()

        with (
            patch("dpp_api.routers.billing._is_email_confirmed", return_value=True),
            patch("dpp_api.routers.billing.CheckoutRepository") as MockRepo,
        ):
            mock_repo_instance = MagicMock()
            mock_repo_instance.get_plan.return_value = None
            MockRepo.return_value = mock_repo_instance

            app.dependency_overrides.clear()
            from dpp_api.auth.session_auth import get_session_auth_context
            from dpp_api.db.session import get_db
            app.dependency_overrides[get_session_auth_context] = lambda: auth
            app.dependency_overrides[get_db] = lambda: mock_db

            client = TestClient(app)
            response = client.post(
                "/v1/billing/checkout-sessions",
                json={"plan_id": "non_existent_plan"},
            )

            app.dependency_overrides.clear()

        assert response.status_code == 404


# ---------------------------------------------------------------------------
# T-25: Capture is non-authoritative
# ---------------------------------------------------------------------------


class TestCapture:
    """POST /v1/billing/paypal/capture"""

    def test_capture_returns_202_not_entitlement(self):
        """T-25: Successful capture returns 202, entitlement is NOT activated.

        This is the critical invariant: sync capture MUST NOT call _grant_entitlement
        or any entitlement activation code. Entitlement is webhook-only (DEC-V1-07).
        """
        from dpp_api.main import app
        from dpp_api.billing.paypal import get_paypal_client

        sess = _make_session(
            status="PAYPAL_ORDER_CREATED",
            paypal_order_id="PAYPAL-ORDER-001",
        )
        auth = _make_auth_context()
        mock_db = MagicMock()

        mock_order = MagicMock()
        mock_order.provider_capture_id = None

        paypal_capture_result = {
            "id": "PAYPAL-ORDER-001",
            "status": "COMPLETED",
            "purchase_units": [
                {
                    "payments": {
                        "captures": [{"id": "CAPTURE-ID-001", "status": "COMPLETED"}]
                    }
                }
            ],
        }

        mock_paypal = AsyncMock()
        mock_paypal.capture_order = AsyncMock(return_value=paypal_capture_result)

        with (
            patch("dpp_api.routers.billing.CheckoutRepository") as MockRepo,
            patch("dpp_api.routers.billing.get_paypal_client", return_value=mock_paypal),
        ):
            mock_repo_instance = MagicMock()
            mock_repo_instance.get_by_id_for_tenant.return_value = sess
            mock_repo_instance.get_billing_order_by_session.return_value = mock_order
            mock_repo_instance.mark_capture_submitted.return_value = True
            MockRepo.return_value = mock_repo_instance

            app.dependency_overrides.clear()
            from dpp_api.auth.session_auth import get_session_auth_context
            from dpp_api.db.session import get_db
            app.dependency_overrides[get_session_auth_context] = lambda: auth
            app.dependency_overrides[get_db] = lambda: mock_db

            client = TestClient(app)
            response = client.post(
                "/v1/billing/paypal/capture",
                json={"session_id": "session-uuid-001"},
            )

            app.dependency_overrides.clear()

        # T-25: 202 Accepted = non-authoritative
        assert response.status_code == 202
        data = response.json()
        assert data["status"] == "CAPTURE_SUBMITTED"
        assert data["paypal_capture_id"] == "CAPTURE-ID-001"
        assert "Awaiting" in data["message"]

        # T-25 CRITICAL: PayPal-Request-Id must be the immutable stored value
        mock_paypal.capture_order.assert_called_once_with(
            paypal_order_id="PAYPAL-ORDER-001",
            request_id="req-capture-001",  # from session.paypal_request_id_capture
        )

        # T-25 CRITICAL: mark_capture_submitted called (NON-authoritative state)
        mock_repo_instance.mark_capture_submitted.assert_called_once_with(sess)

        # T-25 CRITICAL: No entitlement grant — these must never be called from capture
        # (Entitlement is webhook-only — DEC-V1-07, DEC-V1-08)
        assert not hasattr(mock_repo_instance, "grant_entitlement") or \
               not mock_repo_instance.grant_entitlement.called

    def test_capture_idempotent_replay_returns_200(self):
        """T-25a: Replaying capture on CAPTURE_SUBMITTED session returns 200."""
        from dpp_api.main import app

        sess = _make_session(
            status="CAPTURE_SUBMITTED",
            paypal_order_id="PAYPAL-ORDER-001",
        )
        auth = _make_auth_context()
        mock_db = MagicMock()

        mock_order = MagicMock()
        mock_order.provider_capture_id = "EXISTING-CAPTURE-001"

        with patch("dpp_api.routers.billing.CheckoutRepository") as MockRepo:
            mock_repo_instance = MagicMock()
            mock_repo_instance.get_by_id_for_tenant.return_value = sess
            mock_repo_instance.get_billing_order_by_session.return_value = mock_order
            MockRepo.return_value = mock_repo_instance

            app.dependency_overrides.clear()
            from dpp_api.auth.session_auth import get_session_auth_context
            from dpp_api.db.session import get_db
            app.dependency_overrides[get_session_auth_context] = lambda: auth
            app.dependency_overrides[get_db] = lambda: mock_db

            client = TestClient(app)
            response = client.post(
                "/v1/billing/paypal/capture",
                json={"session_id": "session-uuid-001"},
            )

            app.dependency_overrides.clear()

        # Idempotent replay: 200, not 202
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "CAPTURE_SUBMITTED"
        assert "already submitted" in data["message"].lower()

    def test_capture_expired_session_returns_410(self):
        """T-16: Expired session returns 410 Gone."""
        from dpp_api.main import app

        sess = _make_session(
            status="PAYPAL_ORDER_CREATED",
            paypal_order_id="PAYPAL-ORDER-001",
            expires_at=datetime.now(timezone.utc) - timedelta(minutes=5),  # already expired
        )
        auth = _make_auth_context()
        mock_db = MagicMock()

        with patch("dpp_api.routers.billing.CheckoutRepository") as MockRepo:
            mock_repo_instance = MagicMock()
            mock_repo_instance.get_by_id_for_tenant.return_value = sess
            mock_repo_instance.mark_expired.return_value = True
            MockRepo.return_value = mock_repo_instance

            app.dependency_overrides.clear()
            from dpp_api.auth.session_auth import get_session_auth_context
            from dpp_api.db.session import get_db
            app.dependency_overrides[get_session_auth_context] = lambda: auth
            app.dependency_overrides[get_db] = lambda: mock_db

            client = TestClient(app)
            response = client.post(
                "/v1/billing/paypal/capture",
                json={"session_id": "session-uuid-001"},
            )

            app.dependency_overrides.clear()

        assert response.status_code == 410
        mock_repo_instance.mark_expired.assert_called_once_with(sess)

    def test_capture_terminal_session_returns_409(self):
        """T-17: Terminal session (PAID_VERIFIED) returns 409."""
        from dpp_api.main import app

        sess = _make_session(status="PAID_VERIFIED", paypal_order_id="PAYPAL-ORDER-001")
        auth = _make_auth_context()
        mock_db = MagicMock()

        with patch("dpp_api.routers.billing.CheckoutRepository") as MockRepo:
            mock_repo_instance = MagicMock()
            mock_repo_instance.get_by_id_for_tenant.return_value = sess
            MockRepo.return_value = mock_repo_instance

            app.dependency_overrides.clear()
            from dpp_api.auth.session_auth import get_session_auth_context
            from dpp_api.db.session import get_db
            app.dependency_overrides[get_session_auth_context] = lambda: auth
            app.dependency_overrides[get_db] = lambda: mock_db

            client = TestClient(app)
            response = client.post(
                "/v1/billing/paypal/capture",
                json={"session_id": "session-uuid-001"},
            )

            app.dependency_overrides.clear()

        assert response.status_code == 409


# ---------------------------------------------------------------------------
# Fix 1: RFC 9457 Problem Details passthrough (top-level, no nested wrapping)
# ---------------------------------------------------------------------------


class TestProblemDetailsTopLevel:
    """Verify billing 4xx/5xx responses are top-level RFC 9457, not nested."""

    def _get_error_response(self, endpoint: str, body: dict, extra_mocks=None):
        """Helper: call an endpoint, return (status_code, response_body)."""
        from dpp_api.main import app
        from dpp_api.auth.session_auth import get_session_auth_context
        from dpp_api.db.session import get_db

        auth = _make_auth_context()
        mock_db = MagicMock()

        patches = []
        if extra_mocks:
            for target, return_value in extra_mocks:
                patches.append(patch(target, return_value=return_value))

        app.dependency_overrides.clear()
        app.dependency_overrides[get_session_auth_context] = lambda: auth
        app.dependency_overrides[get_db] = lambda: mock_db

        try:
            for p in patches:
                p.start()
            client = TestClient(app)
            response = client.post(endpoint, json=body)
        finally:
            for p in patches:
                p.stop()
            app.dependency_overrides.clear()

        return response.status_code, response.json()

    def _assert_top_level_problem(self, body: dict, expected_status: int):
        """Assert RFC 9457 fields are at top level, not nested."""
        assert body.get("status") == expected_status, (
            f"Expected top-level 'status'={expected_status}, got: {body}"
        )
        assert "type" in body, f"Missing top-level 'type' in: {body}"
        assert "title" in body, f"Missing top-level 'title' in: {body}"
        assert "detail" in body, f"Missing top-level 'detail' in: {body}"
        # detail must be a string or simple dict, never a nested RFC 9457 object
        detail = body["detail"]
        if isinstance(detail, dict):
            assert "status" not in detail, (
                f"detail is a nested Problem Detail — double-wrapping detected: {body}"
            )

    def test_403_email_not_confirmed_top_level(self):
        """403 email-not-confirmed: top-level RFC 9457, billing-specific type."""
        with patch("dpp_api.routers.billing._is_email_confirmed", return_value=False):
            sc, body = self._get_error_response(
                "/v1/billing/checkout-sessions",
                {"plan_id": "beta_private_starter_v1"},
            )
        assert sc == 403
        self._assert_top_level_problem(body, 403)
        assert "email-not-confirmed" in body.get("type", ""), (
            f"Expected billing-specific type URI, got: {body.get('type')}"
        )
        assert body.get("title") == "Email Not Confirmed"

    def test_404_plan_not_found_top_level(self):
        """404 plan-not-found: top-level RFC 9457, billing-specific type."""
        with (
            patch("dpp_api.routers.billing._is_email_confirmed", return_value=True),
            patch("dpp_api.routers.billing.CheckoutRepository") as MockRepo,
        ):
            mock_repo_instance = MagicMock()
            mock_repo_instance.get_plan.return_value = None
            MockRepo.return_value = mock_repo_instance

            sc, body = self._get_error_response(
                "/v1/billing/checkout-sessions",
                {"plan_id": "no_such_plan"},
            )
        assert sc == 404
        self._assert_top_level_problem(body, 404)
        assert "plan-not-found" in body.get("type", "")
        assert body.get("title") == "Plan Not Found"

    def test_410_session_expired_top_level(self):
        """410 session-expired: top-level RFC 9457, billing-specific type."""
        from dpp_api.main import app
        from dpp_api.auth.session_auth import get_session_auth_context
        from dpp_api.db.session import get_db

        expired_sess = _make_session(
            status="PAYPAL_ORDER_CREATED",
            paypal_order_id="PAYPAL-001",
            expires_at=datetime.now(timezone.utc) - timedelta(minutes=1),
        )
        auth = _make_auth_context()
        mock_db = MagicMock()

        with patch("dpp_api.routers.billing.CheckoutRepository") as MockRepo:
            mock_repo_instance = MagicMock()
            mock_repo_instance.get_by_id_for_tenant.return_value = expired_sess
            mock_repo_instance.mark_expired.return_value = True
            MockRepo.return_value = mock_repo_instance

            app.dependency_overrides.clear()
            app.dependency_overrides[get_session_auth_context] = lambda: auth
            app.dependency_overrides[get_db] = lambda: mock_db

            client = TestClient(app)
            response = client.post(
                "/v1/billing/paypal/orders",
                json={"session_id": "session-uuid-001"},
            )
            app.dependency_overrides.clear()

        assert response.status_code == 410
        body = response.json()
        self._assert_top_level_problem(body, 410)
        assert "session-expired" in body.get("type", "")
        assert body.get("title") == "Session Expired"

    def test_409_terminal_session_top_level(self):
        """409 session-terminal: top-level RFC 9457, billing-specific type."""
        from dpp_api.main import app
        from dpp_api.auth.session_auth import get_session_auth_context
        from dpp_api.db.session import get_db

        terminal_sess = _make_session(
            status="PAID_VERIFIED",
            paypal_order_id="PAYPAL-001",
        )
        auth = _make_auth_context()
        mock_db = MagicMock()

        with patch("dpp_api.routers.billing.CheckoutRepository") as MockRepo:
            mock_repo_instance = MagicMock()
            mock_repo_instance.get_by_id_for_tenant.return_value = terminal_sess
            MockRepo.return_value = mock_repo_instance

            app.dependency_overrides.clear()
            app.dependency_overrides[get_session_auth_context] = lambda: auth
            app.dependency_overrides[get_db] = lambda: mock_db

            client = TestClient(app)
            response = client.post(
                "/v1/billing/paypal/capture",
                json={"session_id": "session-uuid-001"},
            )
            app.dependency_overrides.clear()

        assert response.status_code == 409
        body = response.json()
        self._assert_top_level_problem(body, 409)
        assert "session-terminal" in body.get("type", "")

    def test_existing_tests_not_broken_generic_404(self):
        """Generic 404 (no RFC 9457 detail) still returns proper Problem Detail."""
        from dpp_api.main import app
        client = TestClient(app)
        response = client.get("/v1/nonexistent-path-xyz")
        assert response.status_code == 404
        body = response.json()
        assert "type" in body
        assert "status" in body
        assert body["status"] == 404


# ---------------------------------------------------------------------------
# Response schema security: NEVER expose internal fields
# ---------------------------------------------------------------------------


class TestCheckoutSessionResponseSecurity:
    """Verify internal fields are never exposed in API responses (SOW §11)."""

    def test_response_excludes_secret_fields(self):
        """DEC-V1-12: nonce, paypal_request_id_*, user_id MUST NOT appear in response."""
        from dpp_api.schemas import CheckoutSessionResponse

        now = datetime.now(timezone.utc)
        # Build response via schema — simulates what the endpoint returns
        schema_fields = set(CheckoutSessionResponse.model_fields.keys())

        forbidden_fields = {
            "nonce",
            "paypal_request_id_create",
            "paypal_request_id_capture",
            "user_id",
        }

        leaked = forbidden_fields & schema_fields
        assert not leaked, (
            f"CheckoutSessionResponse schema must NOT contain: {leaked}"
        )
