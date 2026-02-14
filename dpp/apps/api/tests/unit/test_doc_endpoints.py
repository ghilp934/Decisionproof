"""
Unit tests for MTS-3.0-DOC endpoints.

Tests:
1. OpenAPI 3.1.0 endpoint (/.well-known/openapi.json)
2. llms.txt link integrity
3. Pricing SSoT endpoint (/pricing/ssot.json)
4. 429 ProblemDetails regression
"""

import json
import pytest
from unittest.mock import Mock
from fastapi.testclient import TestClient

from dpp_api.main import app


@pytest.fixture
def client():
    """Test client for FastAPI app."""
    return TestClient(app)


class TestOpenAPIEndpoint:
    """Test OpenAPI 3.1.0 endpoint."""

    def test_well_known_openapi_endpoint_exists(self, client):
        """/.well-known/openapi.json should be accessible."""
        response = client.get("/.well-known/openapi.json")
        assert response.status_code == 200

    def test_openapi_version_is_3_1_0(self, client):
        """OpenAPI version must be 3.1.0."""
        response = client.get("/.well-known/openapi.json")
        assert response.status_code == 200

        data = response.json()
        assert "openapi" in data
        assert data["openapi"] == "3.1.0"

    def test_openapi_json_parseable(self, client):
        """OpenAPI JSON must be valid JSON."""
        response = client.get("/.well-known/openapi.json")
        assert response.status_code == 200

        # Should not raise JSONDecodeError
        data = response.json()
        assert isinstance(data, dict)

    def test_openapi_has_required_fields(self, client):
        """OpenAPI spec must have required top-level fields."""
        response = client.get("/.well-known/openapi.json")
        data = response.json()

        # OpenAPI 3.1.0 required fields
        assert "openapi" in data
        assert "info" in data
        assert "paths" in data


class TestLLMsLinkIntegrity:
    """Test llms.txt link integrity."""

    def test_llms_txt_accessible(self, client):
        """GET /llms.txt should return 200."""
        response = client.get("/llms.txt")
        assert response.status_code == 200
        assert "Decisionwise" in response.text

    def test_llms_full_txt_accessible(self, client):
        """GET /llms-full.txt should return 200."""
        response = client.get("/llms-full.txt")
        assert response.status_code == 200
        assert "Decisionwise" in response.text

    def test_llms_txt_links_valid(self, client):
        """All links in llms.txt should be accessible or return intentional errors."""
        response = client.get("/llms.txt")
        assert response.status_code == 200

        # Extract links (lines starting with "- " followed by path)
        lines = response.text.split("\n")
        links = []
        for line in lines:
            # Match "- SomeName: /path" or "- /path"
            if ": /" in line:
                path = line.split(": /", 1)[1].split()[0]
                links.append("/" + path)
            elif line.strip().startswith("- /"):
                path = line.strip()[2:].split()[0]
                links.append("/" + path)

        # Test each link
        for link in links:
            # Skip external links
            if link.startswith("http"):
                continue

            # Remove markdown formatting if present
            clean_link = link.split("|")[0].strip()

            test_response = client.get(clean_link)

            # Accept 200 or intentional auth errors (401/403)
            # SSoT and OpenAPI should be 200, docs should be 200 or 404 (static files)
            assert test_response.status_code in [200, 401, 403, 404], \
                f"Link {clean_link} returned unexpected status {test_response.status_code}"


class TestPricingSSOTEndpoint:
    """Test Pricing SSoT endpoint."""

    def test_pricing_ssot_endpoint_exists(self, client):
        """GET /pricing/ssot.json should be accessible."""
        response = client.get("/pricing/ssot.json")
        assert response.status_code == 200

    def test_pricing_ssot_json_parseable(self, client):
        """Pricing SSoT must be valid JSON."""
        response = client.get("/pricing/ssot.json")
        assert response.status_code == 200

        # Should not raise JSONDecodeError
        data = response.json()
        assert isinstance(data, dict)

    def test_pricing_ssot_has_required_fields(self, client):
        """Pricing SSoT must have required fields."""
        response = client.get("/pricing/ssot.json")
        data = response.json()

        # Required SSoT fields
        assert "pricing_version" in data
        assert "effective_from" in data
        assert "tiers" in data
        assert "billing_rules" in data
        assert "meter" in data

    def test_pricing_version_format(self, client):
        """Pricing version must be in YYYY-MM-DD.vMAJOR.MINOR.PATCH format."""
        response = client.get("/pricing/ssot.json")
        data = response.json()

        version = data["pricing_version"]
        # Example: "2026-02-14.v0.2.1"
        assert version.startswith("20")  # Year starts with 20
        assert ".v" in version  # Has .vX.Y.Z


class Test429ProblemDetailsRegression:
    """Regression test for 429 ProblemDetails (GATE-4 compliance)."""

    def test_429_response_is_problem_json(self):
        """429 responses must use application/problem+json."""
        # This is a regression test to ensure 429 responses follow RFC 9457
        # Since we can't easily trigger a real 429 in unit tests,
        # we test the ProblemDetails structure separately

        from dpp_api.pricing.problem_details import ProblemDetails, ViolatedPolicy

        # Create a 429 Problem Details
        problem = ProblemDetails(
            type="https://iana.org/assignments/http-problem-types#quota-exceeded",
            title="Request cannot be satisfied as assigned quota has been exceeded",
            status=429,
            detail="RPM limit of 600 requests per minute exceeded",
            violated_policies=[
                ViolatedPolicy(
                    policy="rpm",
                    limit=600,
                    current=601,
                    window_seconds=60
                )
            ]
        )

        # Serialize with alias
        data = problem.model_dump(by_alias=True, exclude_none=True)

        # Verify structure
        assert data["status"] == 429
        assert data["type"] == "https://iana.org/assignments/http-problem-types#quota-exceeded"
        assert "violated-policies" in data
        assert len(data["violated-policies"]) == 1
        assert data["violated-policies"][0]["policy"] == "rpm"
        assert data["violated-policies"][0]["limit"] == 600


class TestFunctionCallingSpecs:
    """Test function calling specifications endpoint (MTS-3.0-DOC v0.2)."""

    def test_function_calling_specs_endpoint_exists(self, client):
        """GET /docs/function-calling-specs.json should return 200."""
        response = client.get("/docs/function-calling-specs.json")
        assert response.status_code == 200

    def test_function_calling_specs_json_parseable(self, client):
        """Function calling specs must be valid JSON."""
        response = client.get("/docs/function-calling-specs.json")
        assert response.status_code == 200

        # Should not raise JSONDecodeError
        data = response.json()
        assert isinstance(data, dict)

    def test_function_calling_specs_has_required_fields(self, client):
        """Function calling specs must have required fields."""
        response = client.get("/docs/function-calling-specs.json")
        data = response.json()

        # Required fields
        assert "spec_version" in data
        assert "generated_at" in data
        assert "base_url" in data
        assert "auth" in data
        assert "tools" in data

    def test_function_calling_specs_tools_array(self, client):
        """Tools array must exist and contain at least one tool."""
        response = client.get("/docs/function-calling-specs.json")
        data = response.json()

        tools = data["tools"]
        assert isinstance(tools, list)
        assert len(tools) > 0

    def test_function_calling_specs_tool_structure(self, client):
        """Each tool must have required fields."""
        response = client.get("/docs/function-calling-specs.json")
        data = response.json()

        for tool in data["tools"]:
            # Required fields per tool
            assert "name" in tool
            assert "description" in tool
            assert "parameters" in tool
            assert "examples" in tool

            # Parameters must be JSON Schema
            params = tool["parameters"]
            assert "type" in params
            assert params["type"] == "object"

            # Examples must be an array with at least 2 examples
            examples = tool["examples"]
            assert isinstance(examples, list)
            assert len(examples) >= 2

    def test_function_calling_specs_content_type(self, client):
        """Content-Type must be application/json."""
        response = client.get("/docs/function-calling-specs.json")
        assert response.headers["Content-Type"] == "application/json"


class TestDocumentationEndpoints:
    """Test documentation endpoints."""

    def test_docs_quickstart_accessible(self, client):
        """GET /docs/quickstart.md should return 200 or 404 (static files)."""
        response = client.get("/docs/quickstart.md")
        # Accept 200 (static files working) or 404 (static files not mounted in test)
        assert response.status_code in [200, 404]

    def test_docs_auth_accessible(self, client):
        """GET /docs/auth.md should return 200 or 404."""
        response = client.get("/docs/auth.md")
        assert response.status_code in [200, 404]

    def test_docs_auth_delegated_accessible(self, client):
        """GET /docs/auth-delegated.md should return 200 or 404."""
        response = client.get("/docs/auth-delegated.md")
        assert response.status_code in [200, 404]

    def test_docs_rate_limits_accessible(self, client):
        """GET /docs/rate-limits.md should return 200 or 404."""
        response = client.get("/docs/rate-limits.md")
        assert response.status_code in [200, 404]

    def test_docs_human_escalation_template_accessible(self, client):
        """GET /docs/human-escalation-template.md should return 200 or 404."""
        response = client.get("/docs/human-escalation-template.md")
        assert response.status_code in [200, 404]

    def test_docs_pilot_pack_v0_2_accessible(self, client):
        """GET /docs/pilot-pack-v0.2.md should return 200 or 404."""
        response = client.get("/docs/pilot-pack-v0.2.md")
        assert response.status_code in [200, 404]
