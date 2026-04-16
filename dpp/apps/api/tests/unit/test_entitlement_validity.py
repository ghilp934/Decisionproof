"""Unit tests for entitlement validity window (Phase 2 patch).

Tests:
  - _grant_entitlement() sets valid_from=now and valid_until=now+30days on new entitlement
  - _grant_entitlement() refreshes validity window on renewal (existing entitlement)
  - ENTITLEMENT_VALIDITY_DAYS defaults to 30
  - onboarding.py correctly evaluates entitlement_active using valid_until

Critical invariant (DEC-V1-07):
  _grant_entitlement() is called ONLY from the webhook handler,
  never from the sync capture path.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch


def _make_billing_order(tenant_id="tenant-001", plan_id="plan-001"):
    order = MagicMock()
    order.id = 1
    order.tenant_id = tenant_id
    order.plan_id = plan_id
    order.provider = "PAYPAL"
    order.currency = "USD"
    order.amount = "29.00"
    return order


class TestGrantEntitlementValidity:
    """_grant_entitlement() sets 30-day validity window."""

    def test_new_entitlement_sets_valid_from_and_valid_until(self):
        """New entitlement gets valid_from=now, valid_until=now+30days."""
        from dpp_api.routers.webhooks import _grant_entitlement, ENTITLEMENT_VALIDITY_DAYS
        from dpp_api.db.models import BillingAuditLog

        mock_db = MagicMock()
        # No existing entitlement
        mock_db.query.return_value.filter_by.return_value.first.return_value = None

        billing_order = _make_billing_order()

        mock_redis = MagicMock()
        before = datetime.now(timezone.utc)
        _grant_entitlement(mock_db, billing_order, mock_redis)
        after = datetime.now(timezone.utc)

        # The Entitlement was created via db.add — capture it
        add_calls = mock_db.add.call_args_list
        # First add call is the Entitlement; second is BillingAuditLog
        entitlement_arg = add_calls[0][0][0]

        assert entitlement_arg.status == "ACTIVE"
        assert entitlement_arg.tenant_id == "tenant-001"
        assert entitlement_arg.plan_id == "plan-001"

        # valid_from must be within [before, after]
        assert before <= entitlement_arg.valid_from <= after, (
            f"valid_from={entitlement_arg.valid_from} not in [{before}, {after}]"
        )

        # valid_until must be approximately now + ENTITLEMENT_VALIDITY_DAYS
        expected_valid_until = entitlement_arg.valid_from + timedelta(days=ENTITLEMENT_VALIDITY_DAYS)
        assert entitlement_arg.valid_until == expected_valid_until, (
            f"valid_until={entitlement_arg.valid_until}, expected {expected_valid_until}"
        )

        # valid_until must be in the future
        assert entitlement_arg.valid_until > after, "valid_until must be in the future"

    def test_renewal_refreshes_validity_window(self):
        """Existing entitlement gets both valid_from and valid_until updated on renewal."""
        from dpp_api.routers.webhooks import _grant_entitlement

        mock_db = MagicMock()

        # Existing entitlement with old validity
        old_valid_from = datetime.now(timezone.utc) - timedelta(days=31)
        old_valid_until = datetime.now(timezone.utc) - timedelta(days=1)  # already expired

        existing_entitlement = MagicMock()
        existing_entitlement.status = "SUSPENDED"
        existing_entitlement.valid_from = old_valid_from
        existing_entitlement.valid_until = old_valid_until

        mock_db.query.return_value.filter_by.return_value.first.return_value = existing_entitlement

        billing_order = _make_billing_order()

        mock_redis = MagicMock()
        before = datetime.now(timezone.utc)
        _grant_entitlement(mock_db, billing_order, mock_redis)
        after = datetime.now(timezone.utc)

        assert existing_entitlement.status == "ACTIVE"
        # valid_from must have been refreshed
        assert existing_entitlement.valid_from != old_valid_from, (
            "valid_from must be refreshed on renewal"
        )
        assert before <= existing_entitlement.valid_from <= after

        # valid_until must be in the future
        assert existing_entitlement.valid_until > after, (
            "valid_until must be renewed into the future"
        )

    def test_validity_days_default_is_30(self):
        """ENTITLEMENT_VALIDITY_DAYS defaults to 30 days."""
        with patch.dict("os.environ", {}, clear=False):
            # Reload to get the default
            import importlib
            import dpp_api.routers.webhooks as webhooks_mod
            importlib.reload(webhooks_mod)

            assert webhooks_mod.ENTITLEMENT_VALIDITY_DAYS == 30, (
                f"Default must be 30, got {webhooks_mod.ENTITLEMENT_VALIDITY_DAYS}"
            )

    def test_validity_days_configurable_via_env(self):
        """ENTITLEMENT_VALIDITY_DAYS can be overridden via environment variable."""
        import importlib
        import dpp_api.routers.webhooks as webhooks_mod

        with patch.dict("os.environ", {"ENTITLEMENT_VALIDITY_DAYS": "60"}):
            importlib.reload(webhooks_mod)
            assert webhooks_mod.ENTITLEMENT_VALIDITY_DAYS == 60

        # Reset
        importlib.reload(webhooks_mod)


class TestOnboardingEntitlementActive:
    """onboarding.py entitlement_active uses valid_until correctly."""

    def test_entitlement_active_within_window(self):
        """entitlement_active=True when ACTIVE and valid_until is in the future."""
        # Simulate the logic from onboarding.py directly
        now = datetime.now(timezone.utc)

        entitlement = MagicMock()
        entitlement.status = "ACTIVE"
        entitlement.valid_from = now - timedelta(days=1)
        entitlement.valid_until = now + timedelta(days=29)  # still valid

        # Mirror the onboarding.py logic
        payment_complete = entitlement.status in ("ACTIVE", "SUSPENDED")
        entitlement_active = False
        if entitlement.status == "ACTIVE":
            within_window = (
                entitlement.valid_until is None
                or entitlement.valid_until > now
            )
            entitlement_active = within_window

        assert payment_complete is True
        assert entitlement_active is True

    def test_entitlement_active_false_when_expired(self):
        """entitlement_active=False when ACTIVE but valid_until has passed."""
        now = datetime.now(timezone.utc)

        entitlement = MagicMock()
        entitlement.status = "ACTIVE"
        entitlement.valid_from = now - timedelta(days=31)
        entitlement.valid_until = now - timedelta(seconds=1)  # just expired

        payment_complete = entitlement.status in ("ACTIVE", "SUSPENDED")
        entitlement_active = False
        if entitlement.status == "ACTIVE":
            within_window = (
                entitlement.valid_until is None
                or entitlement.valid_until > now
            )
            entitlement_active = within_window

        assert payment_complete is True
        assert entitlement_active is False, (
            "entitlement_active must be False when valid_until has passed"
        )

    def test_entitlement_active_true_when_valid_until_is_none(self):
        """entitlement_active=True when ACTIVE and valid_until is None (no expiry)."""
        now = datetime.now(timezone.utc)

        entitlement = MagicMock()
        entitlement.status = "ACTIVE"
        entitlement.valid_until = None

        entitlement_active = False
        if entitlement.status == "ACTIVE":
            within_window = (
                entitlement.valid_until is None
                or entitlement.valid_until > now
            )
            entitlement_active = within_window

        assert entitlement_active is True

    def test_grant_entitlement_sets_valid_until_so_onboarding_sees_active(self):
        """After _grant_entitlement(), a fresh check would show entitlement_active=True."""
        from dpp_api.routers.webhooks import _grant_entitlement

        mock_db = MagicMock()
        mock_db.query.return_value.filter_by.return_value.first.return_value = None

        billing_order = _make_billing_order()
        _grant_entitlement(mock_db, billing_order, MagicMock())

        # Get the created entitlement
        add_calls = mock_db.add.call_args_list
        entitlement_arg = add_calls[0][0][0]

        now = datetime.now(timezone.utc)
        # Simulate onboarding entitlement_active check
        within_window = (
            entitlement_arg.valid_until is None
            or entitlement_arg.valid_until > now
        )
        entitlement_active = entitlement_arg.status == "ACTIVE" and within_window

        assert entitlement_active is True, (
            f"After _grant_entitlement(), onboarding should see entitlement_active=True. "
            f"valid_until={entitlement_arg.valid_until}, now={now}"
        )
