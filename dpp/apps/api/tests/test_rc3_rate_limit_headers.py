"""RC-3 Contract Gate: RateLimit Headers.

Tests for IETF RateLimit header compliance:
- RateLimit-Policy / RateLimit on 2xx responses
- No X-RateLimit-* legacy headers
- 429 with Retry-After + RateLimit headers
- Documentation SSOT alignment
"""

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from dpp_api.main import app
from dpp_api.rate_limiter import DeterministicTestLimiter


@pytest.fixture
def client():
    """Simple test client without DB dependencies."""
    return TestClient(app)


class TestRC3RateLimitHeaders:
    """RC-3: IETF RateLimit Headers Contract Gate."""

    @pytest.fixture(autouse=True)
    def setup(self):
        """Reset rate limiter before each test."""
        # Reset to default limiter
        from dpp_api.rate_limiter import NoOpRateLimiter
        app.state.rate_limiter = NoOpRateLimiter(quota=60, window=60)

    def test_t1_2xx_response_has_ratelimit_headers(self, client):
        """T1: 2xx responses include RateLimit-Policy and RateLimit headers.

        RC-3 Requirement:
        - All /v1/* 2xx responses MUST have RateLimit-Policy
        - All /v1/* 2xx responses MUST have RateLimit
        - Headers MUST NOT be empty
        """
        # Make request to /v1/test-ratelimit (test endpoint without auth)
        response = client.get("/v1/test-ratelimit")

        # Should be 2xx (200 or 204)
        assert 200 <= response.status_code < 300, \
            f"Expected 2xx, got {response.status_code}"

        # Check RateLimit-Policy header exists and not empty
        rate_limit_policy = response.headers.get("RateLimit-Policy")
        assert rate_limit_policy is not None, \
            "RateLimit-Policy header missing on 2xx response"
        assert rate_limit_policy.strip() != "", \
            "RateLimit-Policy header is empty"

        # Check RateLimit header exists and not empty
        rate_limit = response.headers.get("RateLimit")
        assert rate_limit is not None, \
            "RateLimit header missing on 2xx response"
        assert rate_limit.strip() != "", \
            "RateLimit header is empty"

    def test_t2_no_legacy_xratelimit_headers(self, client):
        """T2: No X-RateLimit-* or X-Rate-Limit-* legacy headers.

        RC-3 Requirement:
        - MUST NOT include X-RateLimit-* headers (GitHub style)
        - MUST NOT include X-Rate-Limit-* headers (Twitter style)
        - Only IETF standard headers allowed
        """
        response = client.get("/v1/test-ratelimit")

        # Check all headers
        for header_name in response.headers.keys():
            header_lower = header_name.lower()
            assert not header_lower.startswith("x-ratelimit-"), \
                f"Legacy X-RateLimit-* header found: {header_name}"
            assert not header_lower.startswith("x-rate-limit-"), \
                f"Legacy X-Rate-Limit-* header found: {header_name}"

    def test_t3_deterministic_429_on_second_request(self, client):
        """T3: DeterministicTestLimiter causes 429 on second request.

        RC-3 Requirement:
        - Use app.state.rate_limiter override (NOT time.sleep)
        - DeterministicTestLimiter with q=1, w=60
        - First request: 2xx
        - Second request: 429 (deterministic, no randomness)
        """
        # Override rate limiter with deterministic test limiter
        test_limiter = DeterministicTestLimiter(quota=1, window=60)
        app.state.rate_limiter = test_limiter

        # First request: should succeed
        response1 = client.get("/v1/test-ratelimit")
        assert 200 <= response1.status_code < 300, \
            f"First request should be 2xx, got {response1.status_code}"

        # Second request: MUST be 429
        response2 = client.get("/v1/test-ratelimit")
        assert response2.status_code == 429, \
            f"Second request MUST be 429, got {response2.status_code}"

    def test_t4_429_includes_retry_after_and_ratelimit_headers(self, client):
        """T4: 429 responses include Retry-After + RateLimit headers.

        RC-3 Requirement:
        - 429 MUST include Retry-After header (integer seconds)
        - 429 MUST include RateLimit-Policy header
        - 429 MUST include RateLimit header
        """
        # Override rate limiter to force 429
        test_limiter = DeterministicTestLimiter(quota=1, window=60)
        app.state.rate_limiter = test_limiter

        # First request to consume quota
        client.get("/v1/test-ratelimit")

        # Second request: 429
        response = client.get("/v1/test-ratelimit")

        assert response.status_code == 429

        # Check Retry-After header
        retry_after = response.headers.get("Retry-After")
        assert retry_after is not None, \
            "Retry-After header missing on 429 response"
        assert retry_after.isdigit(), \
            f"Retry-After must be integer, got: {retry_after}"
        retry_after_int = int(retry_after)
        assert retry_after_int > 0, \
            f"Retry-After must be positive, got: {retry_after_int}"

        # Check RateLimit-Policy header
        rate_limit_policy = response.headers.get("RateLimit-Policy")
        assert rate_limit_policy is not None, \
            "RateLimit-Policy header missing on 429 response"

        # Check RateLimit header
        rate_limit = response.headers.get("RateLimit")
        assert rate_limit is not None, \
            "RateLimit header missing on 429 response"

    def test_t5_documentation_ssot_matches_runtime(self):
        """T5: docs/rate-limits.md SSOT block matches runtime configuration.

        RC-3 Requirement:
        - Documentation MUST contain SSOT block with exact header names
        - RateLimit-Policy line must match runtime (policy_id, q, w)
        - RateLimit line must be present
        - Retry-After line must be present
        """
        # Read documentation (SSOT: public/docs/)
        docs_path = Path(__file__).parent.parent.parent.parent / "public" / "docs" / "rate-limits.md"
        assert docs_path.exists(), \
            f"Documentation not found: {docs_path}"

        doc_content = docs_path.read_text(encoding="utf-8")

        # Check for SSOT section
        assert "SSOT" in doc_content or "Single Source of Truth" in doc_content, \
            "Documentation missing SSOT (Single Source of Truth) section"

        # Check for RateLimit-Policy line with correct format
        # Expected: RateLimit-Policy: "default"; q=60; w=60
        policy_pattern = r'RateLimit-Policy:\s*"default";\s*q=60;\s*w=60'
        assert re.search(policy_pattern, doc_content), \
            f"Documentation missing RateLimit-Policy SSOT line with correct format (expected: 'RateLimit-Policy: \"default\"; q=60; w=60')"

        # Check for RateLimit line
        assert "RateLimit:" in doc_content, \
            "Documentation missing RateLimit header specification"

        # Check for Retry-After line
        assert "Retry-After:" in doc_content, \
            "Documentation missing Retry-After header specification"

        # Check that Retry-After shows 60 seconds for 429
        assert "Retry-After: 60" in doc_content, \
            "Documentation must show Retry-After: 60 in SSOT block"

    def test_header_format_validation(self, client):
        """BONUS: Validate exact header format (Structured Fields style).

        RC-3 Specification:
        - RateLimit-Policy: "<policy_id>"; q=<int>; w=<int>
        - RateLimit: "<policy_id>"; r=<int>; t=<int>
        - Semicolons with optional spaces
        """
        response = client.get("/v1/test-ratelimit")

        # RateLimit-Policy format validation
        rate_limit_policy = response.headers.get("RateLimit-Policy")
        assert rate_limit_policy is not None

        # Pattern: "default"; q=60; w=60 (semicolon + optional space)
        policy_pattern = r'^"default";\s*q=\d+;\s*w=\d+$'
        assert re.match(policy_pattern, rate_limit_policy), \
            f"RateLimit-Policy format invalid: {rate_limit_policy} (expected: '\"default\"; q=<int>; w=<int>')"

        # RateLimit format validation
        rate_limit = response.headers.get("RateLimit")
        assert rate_limit is not None

        # Pattern: "default"; r=<int>; t=<int> (semicolon + optional space)
        limit_pattern = r'^"default";\s*r=\d+;\s*t=\d+$'
        assert re.match(limit_pattern, rate_limit), \
            f"RateLimit format invalid: {rate_limit} (expected: '\"default\"; r=<int>; t=<int>')"
