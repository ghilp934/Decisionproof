"""
Unit tests for Pricing SSoT Loader (MTS-2).

Tests:
1. SSoT JSON loading and validation
2. JSON Schema validation
3. Tier retrieval
4. Unlimited semantics (zero_means)
"""

import pytest
import json
from pathlib import Path

from dpp_api.pricing.ssot_loader import load_pricing_ssot, get_ssot_loader, validate_ssot_against_schema
from dpp_api.pricing.models import PricingSSoTModel


class TestSSOTLoading:
    """Test SSoT JSON loading and validation."""

    def test_load_ssot_from_fixture(self):
        """SSoT should load successfully from fixture file."""
        ssot = load_pricing_ssot()

        assert ssot is not None
        assert isinstance(ssot, PricingSSoTModel)
        assert ssot.pricing_version == "2026-02-14.v0.2.1"
        assert ssot.currency.code == "KRW"
        assert ssot.currency.symbol == "₩"

    def test_ssot_has_all_required_fields(self):
        """Loaded SSoT must have all required top-level fields."""
        ssot = load_pricing_ssot()

        # Required fields from schema
        assert ssot.pricing_version is not None
        assert ssot.effective_from is not None
        assert ssot.currency is not None
        assert ssot.unlimited_semantics is not None
        assert ssot.meter is not None
        assert ssot.grace_overage is not None
        assert ssot.http is not None
        assert ssot.tiers is not None
        assert ssot.billing_rules is not None

    def test_ssot_has_4_tiers(self):
        """SSoT should define exactly 4 tiers: SANDBOX, STARTER, GROWTH, ENTERPRISE."""
        ssot = load_pricing_ssot()

        assert len(ssot.tiers) == 4
        tier_names = [tier.tier for tier in ssot.tiers]
        assert "SANDBOX" in tier_names
        assert "STARTER" in tier_names
        assert "GROWTH" in tier_names
        assert "ENTERPRISE" in tier_names

    def test_ssot_loader_singleton_pattern(self):
        """SSoT loader should use singleton pattern for caching."""
        ssot1 = get_ssot_loader()
        ssot2 = get_ssot_loader()

        # Should return same instance (singleton)
        assert ssot1 is ssot2


class TestJSONSchemaValidation:
    """Test JSON Schema validation against pricing_ssot_schema.json."""

    def test_fixture_ssot_validates_against_schema(self):
        """Fixture pricing_ssot.json must validate against pricing_ssot_schema.json."""
        # Load SSoT JSON
        ssot_fixture_path = Path(__file__).parent.parent.parent / "dpp_api" / "pricing" / "fixtures" / "pricing_ssot.json"
        with open(ssot_fixture_path) as f:
            ssot_json = json.load(f)

        # Load Schema
        schema_path = Path(__file__).parent.parent.parent / "dpp_api" / "pricing" / "fixtures" / "pricing_ssot_schema.json"
        with open(schema_path) as f:
            schema = json.load(f)

        # Validate (should not raise)
        validate_ssot_against_schema(ssot_json, schema)

    def test_invalid_ssot_fails_validation(self):
        """Invalid SSoT JSON should fail schema validation."""
        invalid_ssot = {
            "pricing_version": "invalid-format",  # Should match pattern
            # Missing required fields
        }

        schema_path = Path(__file__).parent.parent.parent / "dpp_api" / "pricing" / "fixtures" / "pricing_ssot_schema.json"
        with open(schema_path) as f:
            schema = json.load(f)

        # Should raise validation error
        with pytest.raises(Exception):  # jsonschema.ValidationError
            validate_ssot_against_schema(invalid_ssot, schema)

    def test_pricing_version_format(self):
        """pricing_version must match pattern: YYYY-MM-DD.vX.X.X"""
        ssot = load_pricing_ssot()

        # Pattern: ^\d{4}-\d{2}-\d{2}\.v\d+\.\d+\.\d+$
        import re
        pattern = r"^\d{4}-\d{2}-\d{2}\.v\d+\.\d+\.\d+$"
        assert re.match(pattern, ssot.pricing_version) is not None


class TestTierRetrieval:
    """Test tier retrieval by tier name."""

    def test_get_tier_sandbox(self):
        """Should retrieve SANDBOX tier correctly."""
        ssot = load_pricing_ssot()
        tier = ssot.get_tier("SANDBOX")

        assert tier is not None
        assert tier.tier == "SANDBOX"
        assert tier.monthly_base_price == 0
        assert tier.included_dc_per_month == 50

    def test_get_tier_starter(self):
        """Should retrieve STARTER tier correctly."""
        ssot = load_pricing_ssot()
        tier = ssot.get_tier("STARTER")

        assert tier is not None
        assert tier.tier == "STARTER"
        assert tier.monthly_base_price == 29000
        assert tier.included_dc_per_month == 1000

    def test_get_tier_growth(self):
        """Should retrieve GROWTH tier correctly."""
        ssot = load_pricing_ssot()
        tier = ssot.get_tier("GROWTH")

        assert tier is not None
        assert tier.tier == "GROWTH"
        assert tier.monthly_base_price == 149000
        assert tier.included_dc_per_month == 10000

    def test_get_tier_enterprise(self):
        """Should retrieve ENTERPRISE tier correctly."""
        ssot = load_pricing_ssot()
        tier = ssot.get_tier("ENTERPRISE")

        assert tier is not None
        assert tier.tier == "ENTERPRISE"
        # ENTERPRISE has custom pricing (0 = unlimited)
        assert tier.monthly_base_price == 0
        assert tier.included_dc_per_month == 0

    def test_get_tier_invalid_returns_none(self):
        """Should return None for invalid tier name."""
        ssot = load_pricing_ssot()
        tier = ssot.get_tier("INVALID_TIER")

        assert tier is None


class TestUnlimitedSemantics:
    """Test unlimited semantics: zero_means handling."""

    def test_zero_means_custom_or_unlimited(self):
        """unlimited_semantics.zero_means should be 'custom_or_unlimited'."""
        ssot = load_pricing_ssot()

        assert ssot.unlimited_semantics.zero_means == "custom_or_unlimited"

    def test_applies_to_fields(self):
        """unlimited_semantics should apply to specific fields."""
        ssot = load_pricing_ssot()

        applies_to = ssot.unlimited_semantics.applies_to_fields
        assert "included_dc_per_month" in applies_to
        assert "monthly_quota_dc" in applies_to
        assert "rate_limit_rpm" in applies_to
        assert "hard_overage_dc_cap" in applies_to

    def test_enterprise_tier_unlimited_limits(self):
        """ENTERPRISE tier should have unlimited (zero) limits for applicable fields."""
        ssot = load_pricing_ssot()
        tier = ssot.get_tier("ENTERPRISE")

        # Fields with zero_means = unlimited
        assert tier.limits["rate_limit_rpm"] == 0  # Unlimited
        assert tier.limits["monthly_quota_dc"] == 0  # Unlimited
        assert tier.limits["hard_overage_dc_cap"] == 0  # Unlimited

    def test_is_zero_unlimited_helper(self):
        """is_zero_unlimited() helper should check if field is unlimited."""
        ssot = load_pricing_ssot()

        # Fields in applies_to_fields
        assert ssot.is_zero_unlimited("rate_limit_rpm", 0) is True
        assert ssot.is_zero_unlimited("monthly_quota_dc", 0) is True
        assert ssot.is_zero_unlimited("hard_overage_dc_cap", 0) is True

        # Non-zero values should not be unlimited
        assert ssot.is_zero_unlimited("rate_limit_rpm", 600) is False

        # Fields NOT in applies_to_fields should not be unlimited
        assert ssot.is_zero_unlimited("monthly_base_price", 0) is False


class TestMeterConfiguration:
    """Test meter configuration for Decision Credits (DC)."""

    def test_meter_event_name(self):
        """Meter event name should be 'decisionwise.dc'."""
        ssot = load_pricing_ssot()
        assert ssot.meter.event_name == "decisionwise.dc"

    def test_meter_idempotency(self):
        """Meter should use run_id for idempotency with workspace_id scope."""
        ssot = load_pricing_ssot()
        assert ssot.meter.idempotency_key_field == "run_id"
        assert ssot.meter.idempotency_scope == "workspace_id"
        assert ssot.meter.idempotency_retention_days == 45

    def test_meter_aggregation(self):
        """Meter aggregation should be 'sum'."""
        ssot = load_pricing_ssot()
        assert ssot.meter.aggregation == "sum"


class TestGraceOverageConfiguration:
    """Test grace overage configuration."""

    def test_grace_overage_enabled(self):
        """Grace overage should be enabled."""
        ssot = load_pricing_ssot()
        assert ssot.grace_overage.enabled is True

    def test_grace_overage_policy(self):
        """Grace overage policy should be 'waive_excess'."""
        ssot = load_pricing_ssot()
        assert ssot.grace_overage.policy == "waive_excess"

    def test_grace_overage_resolution(self):
        """Grace overage resolution should be 'min_of_percent_or_dc'."""
        ssot = load_pricing_ssot()
        assert ssot.grace_overage.resolution == "min_of_percent_or_dc"

    def test_grace_overage_limits(self):
        """Grace overage should be min(1%, 100 DC)."""
        ssot = load_pricing_ssot()
        assert ssot.grace_overage.max_grace_percent == 1  # 1%
        assert ssot.grace_overage.max_grace_dc == 100  # 100 DC

    def test_grace_overage_applies_to(self):
        """Grace overage should apply to hard_overage_dc_cap."""
        ssot = load_pricing_ssot()
        assert "hard_overage_dc_cap" in ssot.grace_overage.applies_to


class TestBillingRules:
    """Test billing rules configuration."""

    def test_billable_status_codes(self):
        """2xx and 422 should be billable."""
        ssot = load_pricing_ssot()
        assert ssot.billing_rules.billable["success"] is True
        assert ssot.billing_rules.billable["http_422"] is True

    def test_non_billable_status_codes(self):
        """4xx (except 422) and 5xx should be non-billable."""
        ssot = load_pricing_ssot()
        assert ssot.billing_rules.non_billable["http_400"] is True
        assert ssot.billing_rules.non_billable["http_401"] is True
        assert ssot.billing_rules.non_billable["http_429"] is True
        assert ssot.billing_rules.non_billable["http_5xx"] is True

    def test_limit_exceeded_http_status(self):
        """Limit exceeded should return 429."""
        ssot = load_pricing_ssot()
        assert ssot.billing_rules.limit_exceeded_http_status == 429

    def test_limit_exceeded_problem_type(self):
        """Limit exceeded should use quota-exceeded problem type."""
        ssot = load_pricing_ssot()
        assert "quota-exceeded" in ssot.billing_rules.limit_exceeded_problem["type"]
