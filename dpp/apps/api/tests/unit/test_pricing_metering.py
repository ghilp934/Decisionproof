"""
Unit tests for Pricing Metering (MTS-2).

Tests:
1. Idempotency: (workspace_id, run_id) deduplication
2. Billability rules: 2xx/422 billable, 4xx/5xx non-billable
"""

import pytest
from datetime import datetime, timezone
from unittest.mock import Mock, patch

from dpp_api.pricing.metering import MeteringService
from dpp_api.pricing.models import PricingSSoTModel


@pytest.fixture
def mock_ssot():
    """Mock SSoT configuration for testing."""
    return PricingSSoTModel(
        pricing_version="2026-02-14.v0.2.1",
        effective_from=datetime.now(timezone.utc),
        effective_to=None,
        currency={
            "code": "KRW",
            "symbol": "₩",
            "tax_behavior": "exclusive"
        },
        unlimited_semantics={
            "zero_means": "custom_or_unlimited",
            "applies_to_fields": []
        },
        meter={
            "event_name": "decisionwise.dc",
            "quantity_field": "dc_amount",
            "idempotency_key_field": "run_id",
            "aggregation": "sum",
            "timestamp_source": "occurred_at",
            "idempotency_scope": "workspace_id",
            "idempotency_retention_days": 45
        },
        grace_overage={
            "enabled": True,
            "policy": "waive_excess",
            "resolution": "min_of_percent_or_dc",
            "max_grace_percent": 1,
            "max_grace_dc": 100,
            "applies_to": ["hard_overage_dc_cap"]
        },
        http={
            "problem_details": {
                "rfc": "9457",
                "content_type": "application/problem+json",
                "type_uris": {
                    "quota_exceeded": "https://iana.org/assignments/http-problem-types#quota-exceeded"
                },
                "extensions": {
                    "violated_policies_field": "violated-policies"
                }
            },
            "ratelimit_headers": {
                "enabled": True,
                "policy_header": "RateLimit-Policy",
                "limit_header": "RateLimit",
                "retry_after_precedence": True,
                "policy_name_conventions": {},
                "rate_limit_window_seconds_default": 60
            }
        },
        tiers=[],
        billing_rules={
            "rounding": "ceil_at_month_end_only",
            "billable": {
                "success": True,
                "http_422": True
            },
            "non_billable": {
                "http_400": True,
                "http_401": True,
                "http_403": True,
                "http_404": True,
                "http_409": True,
                "http_412": True,
                "http_413": True,
                "http_415": True,
                "http_429": True,
                "http_5xx": True
            },
            "limit_exceeded_http_status": 429,
            "limit_exceeded_problem": {
                "type": "https://iana.org/assignments/http-problem-types#quota-exceeded",
                "title": "Request cannot be satisfied as assigned quota has been exceeded",
                "violated_policies_field": "violated-policies"
            }
        }
    )


@pytest.fixture
def mock_redis():
    """Mock Redis client for testing."""
    redis_mock = Mock()
    redis_mock.setex = Mock(return_value=True)
    redis_mock.get = Mock(return_value=None)
    redis_mock.incr = Mock(return_value=1)
    return redis_mock


class TestIdempotency:
    """Test idempotent metering with (workspace_id, run_id) deduplication."""

    def test_first_usage_records_successfully(self, mock_ssot, mock_redis):
        """First usage with new run_id should be recorded."""
        service = MeteringService(mock_ssot, mock_redis)

        # First call with run_id
        mock_redis.get.return_value = None  # Key doesn't exist

        result = service.record_usage(
            workspace_id="ws_123",
            run_id="run_abc",
            dc_amount=10,
            http_status=200,
            occurred_at=datetime.now(timezone.utc)
        )

        assert result["recorded"] is True
        assert result["dc_amount"] == 10
        assert result["billable"] is True

        # Verify idempotency key was set
        idempotency_key = service._generate_idempotency_key("ws_123", "run_abc")
        mock_redis.setex.assert_called_once()
        call_args = mock_redis.setex.call_args
        assert call_args[0][0] == idempotency_key
        assert call_args[0][2] == "1"  # Value = "1" (already recorded)

    def test_duplicate_usage_is_rejected(self, mock_ssot, mock_redis):
        """Duplicate usage with same (workspace_id, run_id) should be rejected."""
        service = MeteringService(mock_ssot, mock_redis)

        # Simulate key already exists (previous recording)
        mock_redis.get.return_value = "1"

        result = service.record_usage(
            workspace_id="ws_123",
            run_id="run_abc",
            dc_amount=10,
            http_status=200,
            occurred_at=datetime.now(timezone.utc)
        )

        assert result["recorded"] is False
        assert result["reason"] == "duplicate_run_id"

        # Verify setex was NOT called (no new key created)
        mock_redis.setex.assert_not_called()

    def test_different_workspace_same_run_id_records_separately(self, mock_ssot, mock_redis):
        """Different workspace with same run_id should record separately."""
        service = MeteringService(mock_ssot, mock_redis)

        # Mock: check different keys
        def get_side_effect(key):
            if "ws_123" in key:
                return "1"  # ws_123 already recorded
            return None  # ws_456 not recorded yet

        mock_redis.get.side_effect = get_side_effect

        # ws_456 + run_abc should succeed
        result = service.record_usage(
            workspace_id="ws_456",
            run_id="run_abc",
            dc_amount=15,
            http_status=200,
            occurred_at=datetime.now(timezone.utc)
        )

        assert result["recorded"] is True
        assert result["dc_amount"] == 15


class TestBillabilityRules:
    """Test billability rules: 2xx/422 billable, 4xx/5xx non-billable."""

    def test_2xx_is_billable(self, mock_ssot, mock_redis):
        """2xx status codes should be billable."""
        service = MeteringService(mock_ssot, mock_redis)

        for status in [200, 201, 204]:
            assert service._is_billable(status) is True, f"Status {status} should be billable"

    def test_422_is_billable(self, mock_ssot, mock_redis):
        """422 Unprocessable Entity should be billable."""
        service = MeteringService(mock_ssot, mock_redis)
        assert service._is_billable(422) is True

    def test_4xx_non_billable(self, mock_ssot, mock_redis):
        """400/401/403/404/409/412/413/415/429 should be non-billable."""
        service = MeteringService(mock_ssot, mock_redis)

        non_billable_4xx = [400, 401, 403, 404, 409, 412, 413, 415, 429]
        for status in non_billable_4xx:
            assert service._is_billable(status) is False, f"Status {status} should be non-billable"

    def test_5xx_non_billable(self, mock_ssot, mock_redis):
        """5xx status codes should be non-billable."""
        service = MeteringService(mock_ssot, mock_redis)

        for status in [500, 502, 503, 504]:
            assert service._is_billable(status) is False, f"Status {status} should be non-billable"

    def test_non_billable_usage_not_metered(self, mock_ssot, mock_redis):
        """Non-billable usage should not increment DC counter."""
        service = MeteringService(mock_ssot, mock_redis)
        mock_redis.get.return_value = None  # New run_id

        result = service.record_usage(
            workspace_id="ws_123",
            run_id="run_500",
            dc_amount=10,
            http_status=500,  # Non-billable
            occurred_at=datetime.now(timezone.utc)
        )

        assert result["recorded"] is True
        assert result["billable"] is False
        assert result["dc_amount"] == 10  # Amount tracked but not billed

        # Verify Redis INCR was NOT called for usage counter
        mock_redis.incr.assert_not_called()
