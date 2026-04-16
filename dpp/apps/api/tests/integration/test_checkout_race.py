"""Integration tests for checkout session race condition handling.

Merge-blocker test:
  T-30: First-writer guard — concurrent session creation for same (tenant, plan)
        must return the same session, not raise an uncaught error.

Design:
  CheckoutRepository.create_session() catches SQLAlchemy IntegrityError
  (from DB unique partial index uq_cs_tenant_plan_active) and falls back to
  fetching the existing session. This prevents duplicate sessions while remaining
  concurrency-safe.

These tests use a mock DB session to simulate the IntegrityError path without
needing a real PostgreSQL instance.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, call, patch

import pytest
from sqlalchemy.exc import IntegrityError


# ---------------------------------------------------------------------------
# Helper: build a realistic-looking CheckoutSession mock
# ---------------------------------------------------------------------------


def _make_existing_session(tenant_id: str = "tenant-001", plan_id: str = "plan-001"):
    session = MagicMock()
    session.id = "existing-session-uuid"
    session.tenant_id = tenant_id
    session.plan_id = plan_id
    session.amount_usd_cents = 2900
    session.currency = "USD"
    session.status = "CHECKOUT_SESSION_CREATED"
    session.paypal_request_id_create = "existing-req-create"
    session.paypal_request_id_capture = "existing-req-capture"
    session.expires_at = datetime.now(timezone.utc) + timedelta(minutes=25)
    session.created_at = datetime.now(timezone.utc)
    session.updated_at = None
    return session


# ---------------------------------------------------------------------------
# T-30: Race condition — IntegrityError path
# ---------------------------------------------------------------------------


class TestCheckoutRaceCondition:
    """T-30: first-writer guard via DB unique partial index."""

    def test_integrity_error_returns_existing_session(self):
        """T-30: When DB raises IntegrityError on INSERT, create_session()
        falls back to fetching and returning the existing session.

        This simulates the second writer losing the race.
        """
        from dpp_api.db.repo_checkout import CheckoutRepository

        existing = _make_existing_session()

        mock_db = MagicMock()
        # flush() raises IntegrityError (simulating uq_cs_tenant_plan_active fired)
        mock_db.flush.side_effect = IntegrityError(
            statement="INSERT INTO checkout_sessions ...",
            params={},
            orig=Exception("unique constraint violation"),
        )
        # Simulate rollback restoring clean state
        mock_db.rollback.return_value = None

        # After rollback, get_active_session_for_tenant_plan finds the winner's session
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = existing
        mock_db.execute.return_value = mock_result

        repo = CheckoutRepository(mock_db)
        session, created = repo.create_session(
            user_id="user-uuid-001",
            tenant_id="tenant-001",
            plan_id="plan-001",
            amount_usd_cents=2900,
        )

        # T-30: Must return existing session, not raise
        assert created is False
        assert session.id == "existing-session-uuid"
        assert session.tenant_id == "tenant-001"

        # Rollback must have been called
        mock_db.rollback.assert_called_once()

    def test_first_writer_succeeds(self):
        """T-30a: When INSERT succeeds (first writer), create_session() returns new session."""
        from dpp_api.db.repo_checkout import CheckoutRepository

        mock_db = MagicMock()
        mock_db.flush.return_value = None  # no conflict

        # add_event is called after flush succeeds
        repo = CheckoutRepository(mock_db)
        with patch.object(repo, "add_event") as mock_add_event:
            session, created = repo.create_session(
                user_id="user-uuid-001",
                tenant_id="tenant-001",
                plan_id="plan-001",
                amount_usd_cents=2900,
            )

        assert created is True
        assert session is not None
        assert session.tenant_id == "tenant-001"
        assert session.plan_id == "plan-001"
        assert session.amount_usd_cents == 2900
        assert session.currency == "USD"
        assert session.status == "CHECKOUT_SESSION_CREATED"

        # add_event must be called for audit trail
        mock_add_event.assert_called_once_with(
            session.id, "CS_CREATED", actor="SYSTEM"
        )

    def test_integrity_error_no_surviving_session_reraises(self):
        """T-30b: If IntegrityError fires but active session is also gone
        (extremely rare: winner expired between INSERT and SELECT), the error
        is re-raised so the caller can handle it.
        """
        from dpp_api.db.repo_checkout import CheckoutRepository

        mock_db = MagicMock()
        mock_db.flush.side_effect = IntegrityError(
            statement="INSERT INTO checkout_sessions ...",
            params={},
            orig=Exception("unique constraint violation"),
        )
        mock_db.rollback.return_value = None

        # No active session found after rollback (winner expired)
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_db.execute.return_value = mock_result

        repo = CheckoutRepository(mock_db)

        with pytest.raises(IntegrityError):
            repo.create_session(
                user_id="user-uuid-001",
                tenant_id="tenant-001",
                plan_id="plan-001",
                amount_usd_cents=2900,
            )

    def test_paypal_request_ids_are_unique_per_session(self):
        """T-30c: Each call to create_session generates unique PayPal-Request-Ids.

        DEC-V1-14/15: Request IDs must be unique per session (UUID v4).
        """
        from dpp_api.db.repo_checkout import CheckoutRepository

        mock_db = MagicMock()
        mock_db.flush.return_value = None

        sessions = []
        for _ in range(3):
            repo = CheckoutRepository(mock_db)
            with patch.object(repo, "add_event"):
                sess, _ = repo.create_session(
                    user_id="user-uuid-001",
                    tenant_id="tenant-001",
                    plan_id="plan-001",
                    amount_usd_cents=2900,
                )
            sessions.append(sess)

        req_ids_create = {s.paypal_request_id_create for s in sessions}
        req_ids_capture = {s.paypal_request_id_capture for s in sessions}

        assert len(req_ids_create) == 3, (
            "Each session must have a unique paypal_request_id_create (DEC-V1-14)"
        )
        assert len(req_ids_capture) == 3, (
            "Each session must have a unique paypal_request_id_capture (DEC-V1-15)"
        )


# ---------------------------------------------------------------------------
# T-30d: Status transition guard (SOW §8.3)
# ---------------------------------------------------------------------------


class TestStatusTransitionGuard:
    """Conditional UPDATE prevents status downgrade in capture-vs-webhook race."""

    def test_transition_status_skipped_when_terminal(self):
        """T-30d: transition_status() returns False when session is already terminal.

        Simulates the scenario where the webhook arrives before or concurrently
        with the sync capture path trying to transition the session.
        """
        from dpp_api.db.repo_checkout import CheckoutRepository

        mock_db = MagicMock()
        # rowcount = 0 means the WHERE status NOT IN (terminal) condition prevented the update
        mock_result = MagicMock()
        mock_result.rowcount = 0
        mock_db.execute.return_value = mock_result

        existing_session = _make_existing_session()
        existing_session.status = "PAID_VERIFIED"

        repo = CheckoutRepository(mock_db)
        updated = repo.transition_status(existing_session, "CAPTURE_SUBMITTED")

        assert updated is False, (
            "transition_status() must return False when session is already terminal "
            "(capture-vs-webhook race guard — SOW §8.3)"
        )
        # refresh must NOT be called since no update happened
        mock_db.refresh.assert_not_called()

    def test_transition_status_succeeds_for_non_terminal(self):
        """T-30e: transition_status() returns True when session is not terminal."""
        from dpp_api.db.repo_checkout import CheckoutRepository

        mock_db = MagicMock()
        mock_result = MagicMock()
        mock_result.rowcount = 1  # row was updated
        mock_db.execute.return_value = mock_result

        existing_session = _make_existing_session()
        existing_session.status = "PAYPAL_ORDER_CREATED"

        repo = CheckoutRepository(mock_db)
        updated = repo.transition_status(existing_session, "CAPTURE_SUBMITTED")

        assert updated is True
        mock_db.refresh.assert_called_once_with(existing_session)

    def test_format_amount_helper(self):
        """Verify cent-to-decimal conversion for key amounts."""
        from dpp_api.db.repo_checkout import _format_amount

        assert _format_amount(2900) == "29.00"
        assert _format_amount(100) == "1.00"
        assert _format_amount(9999) == "99.99"
        assert _format_amount(0) == "0.00"
        assert _format_amount(100000) == "1000.00"
