"""
Unit tests for Pricing Enforcement (MTS-2).

Tests:
1. RPM (Requests Per Minute) enforcement with INCR-first pattern
2. Monthly DC quota enforcement
3. Hard overage cap with grace overage: min(1%, 100 DC)
"""

import pytest
from datetime import datetime, timezone
from unittest.mock import Mock

from dpp_api.pricing.enforcement import EnforcementEngine
from dpp_api.pricing.models import PricingSSoTModel, TierModel


@pytest.fixture
def mock_ssot_with_tiers():
    """Mock SSoT configuration with STARTER tier for testing."""
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
            "max_grace_percent": 1,  # 1%
            "max_grace_dc": 100,  # 100 DC
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
        tiers=[
            TierModel(
                tier="STARTER",
                monthly_base_price=29000,
                included_dc_per_month=1000,
                overage_price_per_dc=39,
                features={"replay": False},
                limits={
                    "rate_limit_rpm": 600,
                    "rate_limit_window_seconds": 60,
                    "monthly_quota_dc": 2000,
                    "hard_overage_dc_cap": 1000,
                    "overage_behavior": "block_on_breach",
                    "max_execution_seconds": 30,
                    "max_input_tokens": 16000,
                    "max_output_tokens": 4000
                },
                policies={
                    "rpm_policy_name": "rpm",
                    "monthly_dc_policy_name": "monthly_dc",
                    "hard_overage_cap_policy_name": "hard_overage_cap"
                },
                safety={
                    "overage_alerts": True,
                    "hard_spending_limit": True
                }
            )
        ],
        billing_rules={
            "rounding": "ceil_at_month_end_only",
            "billable": {"success": True, "http_422": True},
            "non_billable": {"http_429": True, "http_5xx": True},
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
    redis_mock.incr = Mock(return_value=1)
    redis_mock.expire = Mock(return_value=True)
    redis_mock.decr = Mock(return_value=0)
    redis_mock.ttl = Mock(return_value=30)
    redis_mock.get = Mock(return_value="0")
    return redis_mock


class TestRPMEnforcement:
    """Test RPM (Requests Per Minute) enforcement with INCR-first pattern."""

    def test_rpm_within_limit_allows_request(self, mock_ssot_with_tiers, mock_redis):
        """Request within RPM limit should be allowed (returns None)."""
        engine = EnforcementEngine(mock_ssot_with_tiers, mock_redis)
        tier = mock_ssot_with_tiers.tiers[0]

        # Mock: current count is 100 (below 600 limit)
        mock_redis.incr.return_value = 100

        result = engine.check_rpm_limit("ws_123", tier)

        # None = allowed
        assert result is None
        mock_redis.incr.assert_called_once()

    def test_rpm_exceeds_limit_blocks_request(self, mock_ssot_with_tiers, mock_redis):
        """Request exceeding RPM limit should be blocked (returns ProblemDetails)."""
        engine = EnforcementEngine(mock_ssot_with_tiers, mock_redis)
        tier = mock_ssot_with_tiers.tiers[0]

        # Mock: current count is 601 (exceeds 600 limit)
        mock_redis.incr.return_value = 601

        result = engine.check_rpm_limit("ws_123", tier)

        # ProblemDetails = blocked
        assert result is not None
        assert result.status == 429
        assert "RPM limit" in result.detail
        assert len(result.violated_policies) == 1
        assert result.violated_policies[0].policy == "rpm"
        assert result.violated_policies[0].limit == 600

        # Should rollback (decr)
        mock_redis.decr.assert_called_once()

    def test_rpm_zero_means_unlimited(self, mock_ssot_with_tiers, mock_redis):
        """RPM = 0 should mean unlimited (no enforcement, returns None)."""
        engine = EnforcementEngine(mock_ssot_with_tiers, mock_redis)

        # Modify tier to have unlimited RPM
        tier = mock_ssot_with_tiers.tiers[0]
        tier.limits["rate_limit_rpm"] = 0

        result = engine.check_rpm_limit("ws_123", tier)

        # None = unlimited
        assert result is None
        # INCR should NOT be called for unlimited
        mock_redis.incr.assert_not_called()


class TestMonthlyQuotaEnforcement:
    """Test monthly DC quota enforcement."""

    def test_quota_within_limit_allows_usage(self, mock_ssot_with_tiers, mock_redis):
        """Usage within monthly quota should be allowed (returns None)."""
        engine = EnforcementEngine(mock_ssot_with_tiers, mock_redis)
        tier = mock_ssot_with_tiers.tiers[0]

        # Mock: current usage is 1500 DC (below 2000 limit)
        mock_redis.get.return_value = "1500"

        result = engine.check_monthly_dc_quota(
            "ws_123", tier, dc_amount=100, occurred_at=datetime.now(timezone.utc)
        )

        # None = allowed (projected 1600 < 2000)
        assert result is None

    def test_quota_exceeds_limit_blocks_usage(self, mock_ssot_with_tiers, mock_redis):
        """Usage exceeding monthly quota should be blocked (returns ProblemDetails)."""
        engine = EnforcementEngine(mock_ssot_with_tiers, mock_redis)
        tier = mock_ssot_with_tiers.tiers[0]

        # Mock: current usage is 1950 DC, adding 100 DC would exceed 2000 limit
        mock_redis.get.return_value = "1950"

        result = engine.check_monthly_dc_quota(
            "ws_123", tier, dc_amount=100, occurred_at=datetime.now(timezone.utc)
        )

        # ProblemDetails = blocked (projected 2050 > 2000)
        assert result is not None
        assert result.status == 429
        assert "Monthly DC quota" in result.detail
        assert len(result.violated_policies) == 1
        assert result.violated_policies[0].policy == "monthly_dc"
        assert result.violated_policies[0].limit == 2000

    def test_quota_zero_means_unlimited(self, mock_ssot_with_tiers, mock_redis):
        """Monthly quota = 0 should mean unlimited (no enforcement, returns None)."""
        engine = EnforcementEngine(mock_ssot_with_tiers, mock_redis)

        # Modify tier to have unlimited quota
        tier = mock_ssot_with_tiers.tiers[0]
        tier.limits["monthly_quota_dc"] = 0

        result = engine.check_monthly_dc_quota(
            "ws_123", tier, dc_amount=100, occurred_at=datetime.now(timezone.utc)
        )

        # None = unlimited
        assert result is None


class TestGraceOverageEnforcement:
    """Test hard overage cap with grace overage: min(1%, 100 DC)."""

    def test_hard_cap_within_limit_allows_usage(self, mock_ssot_with_tiers, mock_redis):
        """Usage within hard cap should be allowed (returns None)."""
        engine = EnforcementEngine(mock_ssot_with_tiers, mock_redis)
        tier = mock_ssot_with_tiers.tiers[0]

        # Mock: current usage is 2500 DC (monthly_quota=2000, overage=500, cap=1000)
        mock_redis.get.return_value = "2500"

        result = engine.check_hard_overage_cap(
            "ws_123", tier, dc_amount=100, occurred_at=datetime.now(timezone.utc)
        )

        # None = allowed (projected 2600, total_cap=3000, within limit)
        assert result is None

    def test_hard_cap_exceeds_limit_applies_grace(self, mock_ssot_with_tiers, mock_redis):
        """Usage exceeding hard cap should apply grace overage: min(1%, 100 DC)."""
        engine = EnforcementEngine(mock_ssot_with_tiers, mock_redis)
        tier = mock_ssot_with_tiers.tiers[0]

        # Mock: current usage is 2950 DC
        # Total cap = 2000 (quota) + 1000 (hard_overage) = 3000
        # Grace = min(1% of 1000, 100) = min(10, 100) = 10
        # Effective cap = 3000 + 10 = 3010
        # Projected = 2950 + 100 = 3050 > 3010 → blocked
        mock_redis.get.return_value = "2950"

        result = engine.check_hard_overage_cap(
            "ws_123", tier, dc_amount=100, occurred_at=datetime.now(timezone.utc)
        )

        # ProblemDetails = blocked
        assert result is not None
        assert result.status == 429
        assert "Hard overage cap" in result.detail
        assert len(result.violated_policies) == 1
        assert result.violated_policies[0].policy == "hard_overage_cap"

    def test_grace_overage_min_of_percent_or_dc(self, mock_ssot_with_tiers, mock_redis):
        """Grace overage should be min(1% of cap, 100 DC)."""
        engine = EnforcementEngine(mock_ssot_with_tiers, mock_redis)
        tier = mock_ssot_with_tiers.tiers[0]

        # Test 1: cap = 1000 → min(10, 100) = 10
        tier.limits["hard_overage_dc_cap"] = 1000
        grace = engine._calculate_grace_overage(tier)
        assert grace == 10

        # Test 2: cap = 20000 → min(200, 100) = 100
        tier.limits["hard_overage_dc_cap"] = 20000
        grace = engine._calculate_grace_overage(tier)
        assert grace == 100

        # Test 3: cap = 5000 → min(50, 100) = 50
        tier.limits["hard_overage_dc_cap"] = 5000
        grace = engine._calculate_grace_overage(tier)
        assert grace == 50

    def test_hard_cap_zero_means_unlimited(self, mock_ssot_with_tiers, mock_redis):
        """Hard overage cap = 0 should mean unlimited (no enforcement, returns None)."""
        engine = EnforcementEngine(mock_ssot_with_tiers, mock_redis)

        # Modify tier to have unlimited hard cap
        tier = mock_ssot_with_tiers.tiers[0]
        tier.limits["hard_overage_dc_cap"] = 0

        result = engine.check_hard_overage_cap(
            "ws_123", tier, dc_amount=100, occurred_at=datetime.now(timezone.utc)
        )

        # None = unlimited
        assert result is None

    def test_grace_overage_within_grace_allows_usage(self, mock_ssot_with_tiers, mock_redis):
        """Usage within grace overage should be allowed (returns None)."""
        engine = EnforcementEngine(mock_ssot_with_tiers, mock_redis)
        tier = mock_ssot_with_tiers.tiers[0]

        # Mock: current usage is 3005 DC
        # Total cap = 3000
        # Grace = 10
        # Effective cap = 3010
        # Projected = 3005 + 0 = 3005 < 3010 → allowed
        mock_redis.get.return_value = "3005"

        result = engine.check_hard_overage_cap(
            "ws_123", tier, dc_amount=0, occurred_at=datetime.now(timezone.utc)
        )

        # None = within grace
        assert result is None
