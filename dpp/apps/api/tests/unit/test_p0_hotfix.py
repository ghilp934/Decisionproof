"""
P0 Hotfix Regression Tests

Tests for:
1. Auth contract: quickstart.md matches implementation
2. 429 response format: application/problem+json + Retry-After + RateLimit headers
3. Metering billable defaults: 2xx and 422 default to billable (safe)
"""

import pytest
from fastapi.testclient import TestClient
from unittest.mock import Mock
from dpp_api.main import app
from dpp_api.pricing.metering import MeteringService
from dpp_api.pricing.models import (
    PricingSSoTModel,
    BillingRulesModel,
    MeterModel
)
from datetime import datetime, timezone


class TestAuthContract:
    """Test auth contract matches documentation"""

    def test_quickstart_auth_matches_implementation(self):
        """
        Regression: quickstart.md says Authorization: Bearer
        Implementation uses HTTPBearer
        """
        client = TestClient(app)

        # Test without auth - should get 401 or 403
        response = client.post(
            "/v1/runs",
            json={
                "workspace_id": "ws_test",
                "run_id": "run_test",
                "plan_id": "plan_test",
                "input": {}
            }
        )

        assert response.status_code in [401, 403], "Missing auth should return 401 or 403"

        # Test with Bearer auth (as documented in quickstart.md)
        # Note: This will still fail auth (invalid key), but verifies header is accepted
        response = client.post(
            "/v1/runs",
            headers={"Authorization": "Bearer sk_test_abc123_xyz789"},
            json={
                "workspace_id": "ws_test",
                "run_id": "run_test",
                "plan_id": "plan_test",
                "input": {}
            }
        )

        # Should not get "Missing authentication credentials" error
        # Should get 401 (invalid key) instead
        assert response.status_code == 401
        if response.headers.get("Content-Type") == "application/problem+json":
            problem = response.json()
            assert problem.get("detail") != "Missing authentication credentials"


class Test429ResponseFormat:
    """Test 429 response format (RFC 9457 + Retry-After)"""

    def test_429_has_problem_json_and_retry_after(self):
        """
        Regression: 429 must return:
        - Content-Type: application/problem+json
        - Retry-After header
        - RFC 9457 structure (type, title, status, detail)
        """
        from fastapi import Request, HTTPException
        from dpp_api.main import http_exception_handler
        from starlette.exceptions import HTTPException as StarletteHTTPException

        # Create mock request
        mock_request = Mock(spec=Request)
        mock_request.url.path = "/v1/runs"

        # Create 429 exception
        exc = StarletteHTTPException(status_code=429, detail="Rate limit exceeded")

        # Call handler
        import asyncio
        response = asyncio.run(http_exception_handler(mock_request, exc))

        # Verify Content-Type
        assert response.media_type == "application/problem+json"

        # Verify Retry-After header
        assert "Retry-After" in response.headers
        assert response.headers["Retry-After"] == "60"

        # Verify RFC 9457 structure
        content = response.body.decode()
        import json
        problem = json.loads(content)

        assert "type" in problem
        assert "title" in problem
        assert "status" in problem
        assert problem["status"] == 429
        assert "detail" in problem


class TestMeteringBillableDefaults:
    """Test metering billable defaults (safe fallback)"""

    def test_2xx_defaults_to_billable(self):
        """
        Regression: If SSoT config is missing, 2xx should default to billable
        to prevent revenue loss
        """
        # Create minimal SSoT with empty billable config (using dict for simplicity)
        ssot = PricingSSoTModel(
            pricing_version="test",
            effective_from=datetime.now(timezone.utc),
            currency={"code": "USD", "symbol": "$", "tax_behavior": "exclusive"},
            unlimited_semantics={"zero_means": "custom_or_unlimited", "applies_to_fields": []},
            meter=MeterModel(aggregation="sum", idempotency_retention_days=45),
            grace_overage={"enabled": False, "policy": "waive_excess", "resolution": "min_of_percent_or_dc", "max_grace_percent": 0, "max_grace_dc": 0, "applies_to": []},
            http={"problem_details": {"rfc": "9457", "content_type": "application/problem+json", "type_uris": {}, "extensions": {}}, "ratelimit_headers": {"enabled": False, "policy_header": "RateLimit-Policy", "limit_header": "RateLimit", "retry_after_precedence": True, "policy_name_conventions": {}, "rate_limit_window_seconds_default": 60}},
            tiers=[],
            billing_rules=BillingRulesModel(
                billable={},  # Empty - no "success" key
                non_billable={},
                rounding="up",
                limit_exceeded_http_status=429,
                limit_exceeded_problem={}
            )
        )

        redis_mock = Mock()
        metering_service = MeteringService(ssot=ssot, redis=redis_mock)

        # Test 200 (success) - should default to billable
        assert metering_service._is_billable(200) is True
        assert metering_service._is_billable(201) is True
        assert metering_service._is_billable(202) is True

    def test_422_defaults_to_billable(self):
        """
        Regression: If SSoT config is missing, 422 should default to billable
        to prevent revenue loss
        """
        # Create minimal SSoT with empty billable config
        ssot = PricingSSoTModel(
            pricing_version="test",
            effective_from=datetime.now(timezone.utc),
            currency={"code": "USD", "symbol": "$", "tax_behavior": "exclusive"},
            unlimited_semantics={"zero_means": "custom_or_unlimited", "applies_to_fields": []},
            meter=MeterModel(aggregation="sum", idempotency_retention_days=45),
            grace_overage={"enabled": False, "policy": "waive_excess", "resolution": "min_of_percent_or_dc", "max_grace_percent": 0, "max_grace_dc": 0, "applies_to": []},
            http={"problem_details": {"rfc": "9457", "content_type": "application/problem+json", "type_uris": {}, "extensions": {}}, "ratelimit_headers": {"enabled": False, "policy_header": "RateLimit-Policy", "limit_header": "RateLimit", "retry_after_precedence": True, "policy_name_conventions": {}, "rate_limit_window_seconds_default": 60}},
            tiers=[],
            billing_rules=BillingRulesModel(
                billable={},  # Empty - no "http_422" key
                non_billable={},
                rounding="up",
                limit_exceeded_http_status=429,
                limit_exceeded_problem={}
            )
        )

        redis_mock = Mock()
        metering_service = MeteringService(ssot=ssot, redis=redis_mock)

        # Test 422 - should default to billable
        assert metering_service._is_billable(422) is True

    def test_4xx_errors_default_to_non_billable(self):
        """
        Regression: 400/401/403/404/429 should default to non-billable (safe)
        """
        # Create minimal SSoT with empty non_billable config
        ssot = PricingSSoTModel(
            pricing_version="test",
            effective_from=datetime.now(timezone.utc),
            currency={"code": "USD", "symbol": "$", "tax_behavior": "exclusive"},
            unlimited_semantics={"zero_means": "custom_or_unlimited", "applies_to_fields": []},
            meter=MeterModel(aggregation="sum", idempotency_retention_days=45),
            grace_overage={"enabled": False, "policy": "waive_excess", "resolution": "min_of_percent_or_dc", "max_grace_percent": 0, "max_grace_dc": 0, "applies_to": []},
            http={"problem_details": {"rfc": "9457", "content_type": "application/problem+json", "type_uris": {}, "extensions": {}}, "ratelimit_headers": {"enabled": False, "policy_header": "RateLimit-Policy", "limit_header": "RateLimit", "retry_after_precedence": True, "policy_name_conventions": {}, "rate_limit_window_seconds_default": 60}},
            tiers=[],
            billing_rules=BillingRulesModel(
                billable={},
                non_billable={},  # Empty
                rounding="up",
                limit_exceeded_http_status=429,
                limit_exceeded_problem={}
            )
        )

        redis_mock = Mock()
        metering_service = MeteringService(ssot=ssot, redis=redis_mock)

        # Test client errors - should default to non-billable
        assert metering_service._is_billable(400) is False
        assert metering_service._is_billable(401) is False
        assert metering_service._is_billable(403) is False
        assert metering_service._is_billable(404) is False
        assert metering_service._is_billable(429) is False

    def test_5xx_errors_default_to_non_billable(self):
        """
        Regression: 5xx errors should default to non-billable (safe)
        """
        # Create minimal SSoT with empty non_billable config
        ssot = PricingSSoTModel(
            pricing_version="test",
            effective_from=datetime.now(timezone.utc),
            currency={"code": "USD", "symbol": "$", "tax_behavior": "exclusive"},
            unlimited_semantics={"zero_means": "custom_or_unlimited", "applies_to_fields": []},
            meter=MeterModel(aggregation="sum", idempotency_retention_days=45),
            grace_overage={"enabled": False, "policy": "waive_excess", "resolution": "min_of_percent_or_dc", "max_grace_percent": 0, "max_grace_dc": 0, "applies_to": []},
            http={"problem_details": {"rfc": "9457", "content_type": "application/problem+json", "type_uris": {}, "extensions": {}}, "ratelimit_headers": {"enabled": False, "policy_header": "RateLimit-Policy", "limit_header": "RateLimit", "retry_after_precedence": True, "policy_name_conventions": {}, "rate_limit_window_seconds_default": 60}},
            tiers=[],
            billing_rules=BillingRulesModel(
                billable={},
                non_billable={},  # Empty
                rounding="up",
                limit_exceeded_http_status=429,
                limit_exceeded_problem={}
            )
        )

        redis_mock = Mock()
        metering_service = MeteringService(ssot=ssot, redis=redis_mock)

        # Test server errors - should default to non-billable
        assert metering_service._is_billable(500) is False
        assert metering_service._is_billable(502) is False
        assert metering_service._is_billable(503) is False


class TestRateLimitHeadersFormat:
    """Test RateLimit headers use IETF format (not X-RateLimit-*)"""

    def test_rate_limit_headers_use_ietf_format(self):
        """
        Regression: RateLimit headers should use IETF draft-ietf-httpapi-ratelimit-headers
        Format: RateLimit-Policy: "{policy}";w={window};q={quota}
                RateLimit: "{policy}";r={remaining};t={ttl}
        """
        from dpp_api.enforce.plan_enforcer import PlanEnforcer
        from dpp_api.db.models import Plan
        from unittest.mock import Mock

        # Create mock Redis
        redis_mock = Mock()
        redis_mock.get.return_value = "5"  # 5 requests used
        redis_mock.ttl.return_value = 42  # 42 seconds remaining

        # Create mock DB session
        db_mock = Mock()

        # Create enforcer
        enforcer = PlanEnforcer(db=db_mock, redis_client=redis_mock)

        # Create mock plan
        plan = Mock(spec=Plan)
        plan.limits_json = {
            "rate_limit_post_per_min": 600,
            "rate_limit_poll_per_min": 300,
        }

        # Test POST /runs headers
        headers_post = enforcer.get_rate_limit_headers_post(plan, "tenant_test")

        assert "RateLimit-Policy" in headers_post
        assert "RateLimit" in headers_post
        assert "X-RateLimit-Limit" not in headers_post
        assert "X-RateLimit-Remaining" not in headers_post
        assert "X-RateLimit-Reset" not in headers_post

        # Verify IETF format
        assert '"post_runs"' in headers_post["RateLimit-Policy"]
        assert "w=60" in headers_post["RateLimit-Policy"]
        assert "q=600" in headers_post["RateLimit-Policy"]

        assert '"post_runs"' in headers_post["RateLimit"]
        assert "r=595" in headers_post["RateLimit"]  # 600 - 5 = 595
        assert "t=42" in headers_post["RateLimit"]

        # Test GET /runs/{id} headers
        headers_poll = enforcer.get_rate_limit_headers_poll(plan, "tenant_test")

        assert "RateLimit-Policy" in headers_poll
        assert "RateLimit" in headers_poll

        # Verify IETF format
        assert '"poll_runs"' in headers_poll["RateLimit-Policy"]
        assert "w=60" in headers_poll["RateLimit-Policy"]
        assert "q=300" in headers_poll["RateLimit-Policy"]

        assert '"poll_runs"' in headers_poll["RateLimit"]
        assert "r=295" in headers_poll["RateLimit"]  # 300 - 5 = 295
        assert "t=42" in headers_poll["RateLimit"]
