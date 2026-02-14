"""
RC-2 Contract Gate: Error Format (RFC 9457 Problem Details)

Tests that all error responses follow RFC 9457 Problem Details format:
- Content-Type: application/problem+json
- Required fields: type, title, status, detail, instance
- 429 must include Retry-After header
- instance must be opaque (no path/DB PK leaks)

Status codes tested: 401, 403, 402, 409, 422, 429
"""

import re
import pytest
from fastapi import FastAPI, HTTPException, Header
from fastapi.testclient import TestClient
from dpp_api.main import app


@pytest.fixture
def client():
    """Test client for FastAPI app."""
    return TestClient(app)


def assert_problem_details(resp, expected_status: int):
    """
    Assert response follows RFC 9457 Problem Details format.

    Checks:
    1. Content-Type is application/problem+json
    2. Has required fields: type, title, status, detail, instance
    3. status matches expected
    4. instance is opaque (urn:decisionproof:trace:... or run:...)
    5. instance does NOT contain "/" or numeric-only values
    """
    # 1. Content-Type check
    content_type = resp.headers.get("content-type", "")
    assert content_type.startswith("application/problem+json"), \
        f"Expected application/problem+json, got: {content_type}"

    # 2. Required fields
    data = resp.json()
    required_fields = ["type", "title", "status", "detail", "instance"]
    for field in required_fields:
        assert field in data, f"Missing required field: {field}"

    # 3. Status code match
    assert data["status"] == expected_status, \
        f"Expected status {expected_status}, got {data['status']}"

    # 4. Instance format (opaque trace ID)
    instance = data["instance"]
    assert re.match(r"^urn:decisionproof:(trace|run):[A-Za-z0-9._:-]{8,}$", instance), \
        f"Invalid instance format: {instance}"

    # 5. No path leaks (/) or numeric-only
    assert "/" not in instance, f"Instance contains path leak: {instance}"
    # Extract the ID part after the last ':'
    instance_id = instance.split(":")[-1]
    assert not instance_id.isdigit(), f"Instance is numeric-only (DB PK leak): {instance}"


class TestRC2ErrorFormat:
    """RC-2: All error responses must follow RFC 9457 Problem Details."""

    def test_401_unauthorized(self, client):
        """
        401: Missing or invalid authentication.
        Test by calling protected endpoint without auth.
        """
        # Call POST /v1/runs without Authorization header
        response = client.post("/v1/runs", json={
            "workspace_id": "ws_test",
            "run_id": "run_test_001",
            "plan_id": "plan_test",
            "input": {"test": "data"}
        }, headers={"Idempotency-Key": "test_key_001"})

        assert response.status_code == 401
        assert_problem_details(response, 401)

    def test_403_forbidden(self, client):
        """
        403: Authenticated but not authorized.
        Use test-only mini app to verify handler behavior.
        """
        # Create test app with same exception handler
        from dpp_api.main import http_exception_handler
        from starlette.exceptions import HTTPException as StarletteHTTPException

        test_app = FastAPI()
        test_app.add_exception_handler(StarletteHTTPException, http_exception_handler)

        @test_app.get("/forbidden")
        async def forbidden_endpoint():
            raise HTTPException(status_code=403, detail="Forbidden resource")

        test_client = TestClient(test_app)
        response = test_client.get("/forbidden")

        assert response.status_code == 403
        assert_problem_details(response, 403)

    def test_402_payment_required(self, client):
        """
        402: Insufficient funds or max_cost exceeds plan limit.
        Use test-only mini app to verify handler behavior.
        """
        # Create test app with same exception handler
        from dpp_api.main import http_exception_handler
        from starlette.exceptions import HTTPException as StarletteHTTPException

        test_app = FastAPI()
        test_app.add_exception_handler(StarletteHTTPException, http_exception_handler)

        @test_app.get("/payment_required")
        async def payment_required_endpoint():
            raise HTTPException(status_code=402, detail="Maximum cost exceeded")

        test_client = TestClient(test_app)
        response = test_client.get("/payment_required")

        assert response.status_code == 402
        assert_problem_details(response, 402)

    def test_409_conflict(self, client):
        """
        409: Idempotency conflict or resource conflict.
        Use test-only mini app to verify handler behavior.
        """
        # Create test app with same exception handler
        from dpp_api.main import http_exception_handler
        from starlette.exceptions import HTTPException as StarletteHTTPException

        test_app = FastAPI()
        test_app.add_exception_handler(StarletteHTTPException, http_exception_handler)

        @test_app.get("/conflict")
        async def conflict_endpoint():
            raise HTTPException(status_code=409, detail="Idempotency key conflict")

        test_client = TestClient(test_app)
        response = test_client.get("/conflict")

        assert response.status_code == 409
        assert_problem_details(response, 409)

    def test_422_validation_error(self, client):
        """
        422: Request validation error.
        FastAPI default 422 must be wrapped in Problem Details.
        Use test-only mini app to verify handler behavior.
        """
        # Create test app with same exception handler
        from dpp_api.main import validation_exception_handler
        from fastapi.exceptions import RequestValidationError as FastAPIRequestValidationError
        from pydantic import BaseModel, Field

        test_app = FastAPI()
        test_app.add_exception_handler(FastAPIRequestValidationError, validation_exception_handler)

        class TestRequest(BaseModel):
            required_field: str = Field(..., min_length=1)
            number_field: int = Field(..., gt=0)

        @test_app.post("/test_validation")
        async def test_endpoint(request: TestRequest):
            return {"ok": True}

        test_client = TestClient(test_app)

        # Send invalid request (missing required fields)
        response = test_client.post("/test_validation", json={"invalid_field": "test"})

        assert response.status_code == 422
        assert_problem_details(response, 422)

    def test_429_rate_limit_exceeded(self, client):
        """
        429: Rate limit exceeded.
        Must include Retry-After header.
        Use test-only mini app to verify handler behavior.
        """
        # Create test app with same exception handler
        from dpp_api.main import http_exception_handler
        from starlette.exceptions import HTTPException as StarletteHTTPException

        test_app = FastAPI()
        test_app.add_exception_handler(StarletteHTTPException, http_exception_handler)

        @test_app.get("/rate_limited")
        async def rate_limited_endpoint():
            raise HTTPException(status_code=429, detail="Rate limit exceeded")

        test_client = TestClient(test_app)
        response = test_client.get("/rate_limited")

        assert response.status_code == 429
        assert_problem_details(response, 429)

        # RC-2: 429 MUST include Retry-After header
        assert "Retry-After" in response.headers, "429 response missing Retry-After header"
        retry_after = response.headers["Retry-After"]
        assert retry_after.isdigit(), f"Retry-After must be numeric, got: {retry_after}"
        assert int(retry_after) > 0, f"Retry-After must be positive, got: {retry_after}"

    def test_429_retry_after_header(self, client):
        """
        429 must include Retry-After header with positive integer.
        Verify Retry-After header format and value.
        """
        # Create test app with same exception handler
        from dpp_api.main import http_exception_handler
        from starlette.exceptions import HTTPException as StarletteHTTPException

        test_app = FastAPI()
        test_app.add_exception_handler(StarletteHTTPException, http_exception_handler)

        @test_app.get("/rate_limit_retry")
        async def rate_limit_retry_endpoint():
            raise HTTPException(status_code=429, detail="Too many requests")

        test_client = TestClient(test_app)
        response = test_client.get("/rate_limit_retry")

        # Verify Retry-After header exists and is valid
        assert "Retry-After" in response.headers, "429 must include Retry-After header"
        retry_after = response.headers["Retry-After"]

        # Retry-After must be a positive integer (seconds)
        assert retry_after.isdigit(), f"Retry-After must be numeric seconds, got: {retry_after}"
        retry_seconds = int(retry_after)
        assert retry_seconds > 0, f"Retry-After must be positive, got: {retry_seconds}"

        # Verify it's the expected default value (60 seconds per main.py)
        assert retry_seconds == 60, f"Expected Retry-After: 60, got: {retry_seconds}"


class TestRC2InstanceFormat:
    """Test instance field format compliance."""

    def test_instance_no_path_leak(self, client):
        """Instance must not contain '/' (path leak)."""
        response = client.post("/v1/runs", json={
            "workspace_id": "ws_test",
            "run_id": "run_test_leak",
            "plan_id": "plan_test",
            "input": {"test": "data"}
        }, headers={"Idempotency-Key": "test_key_leak"})

        assert response.status_code == 401
        data = response.json()
        assert "/" not in data["instance"], "Instance contains path leak"

    def test_instance_no_numeric_only(self, client):
        """Instance ID part must not be numeric-only (DB PK leak)."""
        response = client.post("/v1/runs", json={
            "workspace_id": "ws_test",
            "run_id": "run_test_numeric",
            "plan_id": "plan_test",
            "input": {"test": "data"}
        }, headers={"Idempotency-Key": "test_key_numeric"})

        assert response.status_code == 401
        data = response.json()
        instance_id = data["instance"].split(":")[-1]
        assert not instance_id.isdigit(), "Instance is numeric-only (DB PK leak)"
