"""
Unit tests for Pricing Problem Details (MTS-2).

Tests:
1. RFC 9457 compliant Problem Details structure
2. 429 quota-exceeded with violated-policies extension
3. Multiple policy violations in single response
"""

import pytest
from fastapi import status
from fastapi.responses import JSONResponse

from dpp_api.pricing.problem_details import (
    ProblemDetails,
    ViolatedPolicy,
    create_problem_details_response
)


class TestProblemDetailsStructure:
    """Test RFC 9457 compliant Problem Details structure."""

    def test_problem_details_required_fields(self):
        """Problem Details must have type, title, status fields."""
        problem = ProblemDetails(
            type="https://iana.org/assignments/http-problem-types#quota-exceeded",
            title="Request cannot be satisfied as assigned quota has been exceeded",
            status=429
        )

        assert problem.type == "https://iana.org/assignments/http-problem-types#quota-exceeded"
        assert problem.title == "Request cannot be satisfied as assigned quota has been exceeded"
        assert problem.status == 429

    def test_problem_details_optional_fields(self):
        """Problem Details can include detail, instance, extensions."""
        problem = ProblemDetails(
            type="https://iana.org/assignments/http-problem-types#quota-exceeded",
            title="Quota exceeded",
            status=429,
            detail="Monthly DC quota of 2000 has been exceeded",
            instance="/api/v1/runs/run_abc123"
        )

        assert problem.detail == "Monthly DC quota of 2000 has been exceeded"
        assert problem.instance == "/api/v1/runs/run_abc123"

    def test_problem_details_serialization(self):
        """Problem Details must serialize to JSON correctly."""
        problem = ProblemDetails(
            type="https://iana.org/assignments/http-problem-types#quota-exceeded",
            title="Quota exceeded",
            status=429
        )

        json_dict = problem.model_dump(exclude_none=True)

        assert "type" in json_dict
        assert "title" in json_dict
        assert "status" in json_dict
        assert json_dict["type"] == "https://iana.org/assignments/http-problem-types#quota-exceeded"
        assert json_dict["title"] == "Quota exceeded"
        assert json_dict["status"] == 429


class TestViolatedPoliciesExtension:
    """Test violated-policies extension for quota enforcement."""

    def test_single_policy_violation_rpm(self):
        """RPM policy violation should be represented correctly."""
        violated_policy = ViolatedPolicy(
            policy="rpm",
            limit=600,
            current=601,
            window_seconds=60
        )

        assert violated_policy.policy == "rpm"
        assert violated_policy.limit == 600
        assert violated_policy.current == 601
        assert violated_policy.window_seconds == 60

    def test_single_policy_violation_monthly_dc(self):
        """Monthly DC policy violation should be represented correctly."""
        violated_policy = ViolatedPolicy(
            policy="monthly_dc",
            limit=2000,
            current=2050,
            window_seconds=None
        )

        assert violated_policy.policy == "monthly_dc"
        assert violated_policy.limit == 2000
        assert violated_policy.current == 2050
        assert violated_policy.window_seconds is None

    def test_single_policy_violation_hard_overage_cap(self):
        """Hard overage cap policy violation should be represented correctly."""
        violated_policy = ViolatedPolicy(
            policy="hard_overage_cap",
            limit=1000,
            current=1050,
            window_seconds=None
        )

        assert violated_policy.policy == "hard_overage_cap"
        assert violated_policy.limit == 1000
        assert violated_policy.current == 1050

    def test_multiple_policy_violations(self):
        """Multiple policy violations should be representable."""
        violations = [
            ViolatedPolicy(policy="rpm", limit=600, current=601, window_seconds=60),
            ViolatedPolicy(policy="monthly_dc", limit=2000, current=2050, window_seconds=None)
        ]

        assert len(violations) == 2
        assert violations[0].policy == "rpm"
        assert violations[1].policy == "monthly_dc"


class Test429QuotaExceeded:
    """Test 429 quota-exceeded Problem Details with violated-policies."""

    def test_rpm_violation_429_response(self):
        """RPM violation should return 429 with violated-policies."""
        violated_policies = [
            ViolatedPolicy(policy="rpm", limit=600, current=601, window_seconds=60)
        ]

        response = create_problem_details_response(
            type_uri="https://iana.org/assignments/http-problem-types#quota-exceeded",
            title="Request cannot be satisfied as assigned quota has been exceeded",
            status=429,
            detail="RPM limit of 600 has been exceeded",
            violated_policies=violated_policies
        )

        assert isinstance(response, JSONResponse)
        assert response.status_code == 429
        assert response.headers["content-type"] == "application/problem+json"

        # Parse response body
        import json
        body = json.loads(response.body.decode())

        assert body["type"] == "https://iana.org/assignments/http-problem-types#quota-exceeded"
        assert body["title"] == "Request cannot be satisfied as assigned quota has been exceeded"
        assert body["status"] == 429
        assert body["detail"] == "RPM limit of 600 has been exceeded"
        assert "violated-policies" in body
        assert len(body["violated-policies"]) == 1
        assert body["violated-policies"][0]["policy"] == "rpm"
        assert body["violated-policies"][0]["limit"] == 600
        assert body["violated-policies"][0]["current"] == 601

    def test_monthly_dc_violation_429_response(self):
        """Monthly DC violation should return 429 with violated-policies."""
        violated_policies = [
            ViolatedPolicy(policy="monthly_dc", limit=2000, current=2050, window_seconds=None)
        ]

        response = create_problem_details_response(
            type_uri="https://iana.org/assignments/http-problem-types#quota-exceeded",
            title="Request cannot be satisfied as assigned quota has been exceeded",
            status=429,
            detail="Monthly DC quota of 2000 has been exceeded",
            violated_policies=violated_policies
        )

        assert response.status_code == 429

        import json
        body = json.loads(response.body.decode())

        assert body["violated-policies"][0]["policy"] == "monthly_dc"
        assert body["violated-policies"][0]["limit"] == 2000
        assert body["violated-policies"][0]["current"] == 2050

    def test_hard_overage_cap_violation_429_response(self):
        """Hard overage cap violation should return 429 with violated-policies."""
        violated_policies = [
            ViolatedPolicy(policy="hard_overage_cap", limit=1000, current=1050, window_seconds=None)
        ]

        response = create_problem_details_response(
            type_uri="https://iana.org/assignments/http-problem-types#quota-exceeded",
            title="Request cannot be satisfied as assigned quota has been exceeded",
            status=429,
            detail="Hard overage cap of 1000 DC has been exceeded",
            violated_policies=violated_policies
        )

        assert response.status_code == 429

        import json
        body = json.loads(response.body.decode())

        assert body["violated-policies"][0]["policy"] == "hard_overage_cap"
        assert body["violated-policies"][0]["limit"] == 1000
        assert body["violated-policies"][0]["current"] == 1050

    def test_content_type_is_application_problem_json(self):
        """Problem Details response must use application/problem+json content type."""
        violated_policies = [
            ViolatedPolicy(policy="rpm", limit=600, current=601, window_seconds=60)
        ]

        response = create_problem_details_response(
            type_uri="https://iana.org/assignments/http-problem-types#quota-exceeded",
            title="Quota exceeded",
            status=429,
            violated_policies=violated_policies
        )

        assert response.headers["content-type"] == "application/problem+json"

    def test_violated_policies_field_name(self):
        """Extension field must be named 'violated-policies' (hyphenated)."""
        violated_policies = [
            ViolatedPolicy(policy="rpm", limit=600, current=601, window_seconds=60)
        ]

        response = create_problem_details_response(
            type_uri="https://iana.org/assignments/http-problem-types#quota-exceeded",
            title="Quota exceeded",
            status=429,
            violated_policies=violated_policies
        )

        import json
        body = json.loads(response.body.decode())

        # Must use "violated-policies" not "violated_policies"
        assert "violated-policies" in body
        assert "violated_policies" not in body
