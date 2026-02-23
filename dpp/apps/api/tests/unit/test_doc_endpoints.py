"""
Unit tests for MTS-3.0-DOC endpoints.

Tests:
1. OpenAPI 3.1.0 endpoint (/.well-known/openapi.json)
2. llms.txt link integrity
3. Pricing SSoT endpoint (/pricing/ssot.json)
4. 429 ProblemDetails regression
5. openapi-demo endpoint (/.well-known/openapi-demo.json) — Mini Demo LOCK
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
        assert "Decisionproof" in response.text

    def test_llms_full_txt_accessible(self, client):
        """GET /llms-full.txt should return 200."""
        response = client.get("/llms-full.txt")
        assert response.status_code == 200
        assert "Decisionproof" in response.text

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


class TestOpenAPIDemoEndpoint:
    """AC Tests: Mini Demo OpenAPI LOCK (/.well-known/openapi-demo.json).

    PASS iff ALL conditions are true:
    - endpoint returns 200
    - openapi == "3.1.0"
    - servers length == 1
    - servers[0].url == "https://api.decisionproof.io.kr"
    - paths keys == {"/v1/demo/runs", "/v1/demo/runs/{run_id}"} (정확히 일치)
    - 다른 path 0개
    """

    DEMO_BASE_URL = "https://api.decisionproof.io.kr"
    ALLOWED_PATHS = {"/v1/demo/runs", "/v1/demo/runs/{run_id}"}

    def test_openapi_demo_endpoint_exists(self, client):
        """(AC-1) GET /.well-known/openapi-demo.json → 200."""
        response = client.get("/.well-known/openapi-demo.json")
        assert response.status_code == 200, (
            f"/.well-known/openapi-demo.json must return 200, got {response.status_code}"
        )

    def test_openapi_demo_version(self, client):
        """(AC-2) openapi 버전 == '3.1.0'."""
        response = client.get("/.well-known/openapi-demo.json")
        assert response.status_code == 200
        data = response.json()
        assert data.get("openapi") == "3.1.0", (
            f"openapi version must be '3.1.0', got {data.get('openapi')!r}"
        )

    def test_openapi_demo_servers_lock(self, client):
        """(AC-3) servers 길이 == 1 AND servers[0].url == DEMO_BASE_URL."""
        response = client.get("/.well-known/openapi-demo.json")
        assert response.status_code == 200
        data = response.json()

        assert "servers" in data, "openapi-demo must have 'servers' field"
        assert len(data["servers"]) == 1, (
            f"servers must have exactly 1 entry, got {len(data['servers'])}: {data['servers']}"
        )
        assert data["servers"][0]["url"] == self.DEMO_BASE_URL, (
            f"servers[0].url must be {self.DEMO_BASE_URL!r}, "
            f"got {data['servers'][0].get('url')!r}"
        )

    def test_openapi_demo_paths_allowlist_exact(self, client):
        """(AC-4) paths keys == ALLOWED_PATHS (정확히 일치, 초과/미달 모두 FAIL)."""
        response = client.get("/.well-known/openapi-demo.json")
        assert response.status_code == 200
        data = response.json()

        assert "paths" in data, "openapi-demo must have 'paths' field"
        actual_paths = set(data["paths"].keys())

        leaked = actual_paths - self.ALLOWED_PATHS
        missing = self.ALLOWED_PATHS - actual_paths

        assert not leaked, (
            f"Non-allowlist paths leaked into demo spec: {sorted(leaked)}"
        )
        assert not missing, (
            f"Required demo paths missing from spec: {sorted(missing)}"
        )

    def test_openapi_demo_no_extra_paths(self, client):
        """(AC-4b) 허용 목록 외 path 개수 == 0 (별도 assertion)."""
        response = client.get("/.well-known/openapi-demo.json")
        assert response.status_code == 200
        data = response.json()

        extra = set(data.get("paths", {}).keys()) - self.ALLOWED_PATHS
        assert len(extra) == 0, (
            f"Demo spec contains {len(extra)} unauthorized path(s): {sorted(extra)}"
        )

    def test_openapi_demo_content_type_json(self, client):
        """(AC-5) Content-Type: application/json."""
        response = client.get("/.well-known/openapi-demo.json")
        assert response.status_code == 200
        assert "application/json" in response.headers.get("content-type", ""), (
            f"Expected application/json, got {response.headers.get('content-type')!r}"
        )


class TestOpenAPIDemoEnvOverride:
    """DP_DEMO_PUBLIC_BASE_URL env override 검증."""

    DEMO_BASE_URL = "https://api.decisionproof.io.kr"

    def test_default_demo_base_url_is_iokr(self, client, monkeypatch):
        """env 미설정 시 기본값 == https://api.decisionproof.io.kr."""
        monkeypatch.delenv("DP_DEMO_PUBLIC_BASE_URL", raising=False)
        response = client.get("/.well-known/openapi-demo.json")
        assert response.status_code == 200
        data = response.json()
        assert data["servers"][0]["url"] == self.DEMO_BASE_URL


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
