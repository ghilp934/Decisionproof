"""
Unit tests for Idempotent Metering (MTS-2)

Tests:
1. Idempotency: Same (workspace_id, run_id) charged only once
2. Billability: 2xx + 422 billable; 429 + 5xx non-billable
3. Redis usage tracking
"""

import pytest
from datetime import datetime, timezone
from unittest.mock import Mock

from dpp_api.pricing.metering import MeteringService
from dpp_api.pricing.models import PricingSSoTModel, MeterModel, BillingRulesModel


@pytest.fixture
def mock_ssot():
    """Mock SSoT configuration for metering tests."""
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
            "applies_to_fields": ["monthly_quota_dc", "rate_limit_rpm", "hard_overage_dc_cap"]
        },
        meter=MeterModel(
            event_name="decisionwise.dc",
            quantity_field="dc_amount",
            idempotency_key_field="run_id",
            aggregation="sum",
            timestamp_source="occurred_at",
            idempotency_scope="workspace_id",
            idempotency_retention_days=45
        ),
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
                "policy_name_conventions": {
                    "rpm": "rpm",
                    "monthly_dc": "monthly_dc",
                    "hard_overage_cap": "hard_overage_cap"
                },
                "rate_limit_window_seconds_default": 60
            }
        },
        tiers=[],
        billing_rules=BillingRulesModel(
            rounding="ceil_at_month_end_only",
            billable={"success": True, "http_422": True},
            non_billable={"http_429": True, "http_5xx": True},
            limit_exceeded_http_status=429,
            limit_exceeded_problem={
                "type": "https://iana.org/assignments/http-problem-types#quota-exceeded",
                "title": "Request cannot be satisfied as assigned quota has been exceeded",
                "violated_policies_field": "violated-policies"
            }
        )
    )


@pytest.fixture
def mock_redis():
    """Mock Redis client for testing."""
    redis_mock = Mock()
    redis_mock.set = Mock(return_value=True)  # SET NX EX returns True if key was set
    redis_mock.incrby = Mock(return_value=100)
    redis_mock.expire = Mock(return_value=True)
    redis_mock.get = Mock(return_value="0")
    return redis_mock


class TestIdempotency:
    """Test idempotency: Same (workspace_id, run_id) charged only once."""

    def test_first_request_creates_idempotency_key(self, mock_ssot, mock_redis):
        """First request with (workspace_id, run_id) should create idempotency key."""
        service = MeteringService(mock_ssot, mock_redis)

        # Mock: SET NX EX returns True (key was created)
        mock_redis.set.return_value = True

        result = service.record_usage(
            workspace_id="ws_123",
            run_id="run_456",
            dc_amount=100,
            http_status=200,
            occurred_at=datetime(2026, 2, 14, 12, 0, 0, tzinfo=timezone.utc),
            tier_monthly_quota=2000
        )

        # Should return "new" deduplication status
        assert result.deduplication_status == "new"
        assert result.dc_charged == 100
        assert result.event_id == "run_456"

        # Verify SET NX EX was called
        mock_redis.set.assert_called_once()
        args = mock_redis.set.call_args
        assert args[0][0] == "idempotency:ws_123:run_456"  # key
        assert args[0][1] == "1"  # value
        assert args[1]["nx"] is True  # nx=True
        assert args[1]["ex"] == 45 * 86400  # 45 days retention

    def test_duplicate_request_does_not_charge(self, mock_ssot, mock_redis):
        """Duplicate request with same (workspace_id, run_id) should NOT charge DC."""
        service = MeteringService(mock_ssot, mock_redis)

        # Mock: SET NX EX returns False (key already exists = duplicate)
        mock_redis.set.return_value = False

        result = service.record_usage(
            workspace_id="ws_123",
            run_id="run_456",
            dc_amount=100,
            http_status=200,
            occurred_at=datetime(2026, 2, 14, 12, 0, 0, tzinfo=timezone.utc),
            tier_monthly_quota=2000
        )

        # Should return "duplicate" deduplication status
        assert result.deduplication_status == "duplicate"
        assert result.dc_charged == 0  # No charge
        assert result.event_id == "run_456"

        # Verify incrby was NOT called (no usage tracking for duplicates)
        mock_redis.incrby.assert_not_called()

    def test_different_run_ids_are_independent(self, mock_ssot, mock_redis):
        """Different run_ids should be treated as separate requests."""
        service = MeteringService(mock_ssot, mock_redis)

        # Mock: Both requests are new (SET NX EX returns True)
        mock_redis.set.return_value = True

        # Request 1
        result1 = service.record_usage(
            workspace_id="ws_123",
            run_id="run_001",
            dc_amount=100,
            http_status=200,
            occurred_at=datetime(2026, 2, 14, 12, 0, 0, tzinfo=timezone.utc),
            tier_monthly_quota=2000
        )

        # Request 2
        result2 = service.record_usage(
            workspace_id="ws_123",
            run_id="run_002",
            dc_amount=100,
            http_status=200,
            occurred_at=datetime(2026, 2, 14, 12, 0, 0, tzinfo=timezone.utc),
            tier_monthly_quota=2000
        )

        # Both should be "new"
        assert result1.deduplication_status == "new"
        assert result2.deduplication_status == "new"
        assert result1.dc_charged == 100
        assert result2.dc_charged == 100


class TestBillability:
    """Test billability rules: 2xx + 422 billable; 429 + 5xx non-billable."""

    def test_2xx_is_billable(self, mock_ssot, mock_redis):
        """2xx responses should be billable."""
        service = MeteringService(mock_ssot, mock_redis)
        mock_redis.set.return_value = True

        result = service.record_usage(
            workspace_id="ws_123",
            run_id="run_200",
            dc_amount=100,
            http_status=200,
            occurred_at=datetime(2026, 2, 14, 12, 0, 0, tzinfo=timezone.utc),
            tier_monthly_quota=2000
        )

        # Should charge DC
        assert result.dc_charged == 100
        mock_redis.incrby.assert_called_once()

    def test_422_is_billable(self, mock_ssot, mock_redis):
        """422 Unprocessable Entity should be billable."""
        service = MeteringService(mock_ssot, mock_redis)
        mock_redis.set.return_value = True

        result = service.record_usage(
            workspace_id="ws_123",
            run_id="run_422",
            dc_amount=100,
            http_status=422,
            occurred_at=datetime(2026, 2, 14, 12, 0, 0, tzinfo=timezone.utc),
            tier_monthly_quota=2000
        )

        # Should charge DC
        assert result.dc_charged == 100
        mock_redis.incrby.assert_called_once()

    def test_429_is_non_billable(self, mock_ssot, mock_redis):
        """429 Too Many Requests should be non-billable."""
        service = MeteringService(mock_ssot, mock_redis)
        mock_redis.set.return_value = True

        result = service.record_usage(
            workspace_id="ws_123",
            run_id="run_429",
            dc_amount=100,
            http_status=429,
            occurred_at=datetime(2026, 2, 14, 12, 0, 0, tzinfo=timezone.utc),
            tier_monthly_quota=2000
        )

        # Should NOT charge DC
        assert result.dc_charged == 0
        mock_redis.incrby.assert_not_called()

    def test_5xx_is_non_billable(self, mock_ssot, mock_redis):
        """5xx Server Errors should be non-billable."""
        service = MeteringService(mock_ssot, mock_redis)
        mock_redis.set.return_value = True

        for status in [500, 502, 503, 504]:
            result = service.record_usage(
                workspace_id="ws_123",
                run_id=f"run_{status}",
                dc_amount=100,
                http_status=status,
                occurred_at=datetime(2026, 2, 14, 12, 0, 0, tzinfo=timezone.utc),
                tier_monthly_quota=2000
            )

            # Should NOT charge DC
            assert result.dc_charged == 0


class TestUsageTracking:
    """Test Redis usage tracking."""

    def test_usage_key_format(self, mock_ssot, mock_redis):
        """Usage key should follow format: usage:{workspace_id}:{YYYY-MM}."""
        service = MeteringService(mock_ssot, mock_redis)
        mock_redis.set.return_value = True

        service.record_usage(
            workspace_id="ws_123",
            run_id="run_001",
            dc_amount=100,
            http_status=200,
            occurred_at=datetime(2026, 2, 14, 12, 0, 0, tzinfo=timezone.utc),
            tier_monthly_quota=2000
        )

        # Verify incrby was called with correct key
        mock_redis.incrby.assert_called_once()
        args = mock_redis.incrby.call_args[0]
        assert args[0] == "usage:ws_123:2026-02"  # usage key
        assert args[1] == 100  # dc_amount

    def test_usage_key_ttl_set(self, mock_ssot, mock_redis):
        """Usage key should have TTL of 90 days."""
        service = MeteringService(mock_ssot, mock_redis)
        mock_redis.set.return_value = True

        service.record_usage(
            workspace_id="ws_123",
            run_id="run_001",
            dc_amount=100,
            http_status=200,
            occurred_at=datetime(2026, 2, 14, 12, 0, 0, tzinfo=timezone.utc),
            tier_monthly_quota=2000
        )

        # Verify expire was called
        mock_redis.expire.assert_called_once()
        args = mock_redis.expire.call_args[0]
        assert args[0] == "usage:ws_123:2026-02"  # usage key
        assert args[1] == 90 * 86400  # 90 days TTL

    def test_remaining_dc_calculation(self, mock_ssot, mock_redis):
        """Remaining DC should be calculated correctly."""
        service = MeteringService(mock_ssot, mock_redis)
        mock_redis.set.return_value = True

        # Mock: current usage is 500 DC
        mock_redis.get.return_value = "500"

        result = service.record_usage(
            workspace_id="ws_123",
            run_id="run_001",
            dc_amount=100,
            http_status=200,
            occurred_at=datetime(2026, 2, 14, 12, 0, 0, tzinfo=timezone.utc),
            tier_monthly_quota=2000
        )

        # Remaining = 2000 (quota) - 500 (current) = 1500
        assert result.workspace_remaining_dc == 1500
