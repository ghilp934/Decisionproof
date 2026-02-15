"""
T1: RateLimit middleware non-override regression test.

P0-4: Verify that handler-set RateLimit headers are not overridden by middleware.
"""

import pytest
from fastapi import APIRouter, Response
from fastapi.testclient import TestClient


def test_ratelimit_middleware_respects_handler_headers(monkeypatch):
    """
    T1: RateLimit middleware MUST NOT override headers set by handlers.

    P0-4 Contract:
    - If handler sets RateLimit-Policy/RateLimit, middleware preserves them
    - If handler does NOT set them, middleware adds default headers
    """
    # Import app after monkeypatch to ensure clean state
    from dpp_api.main import app
    from dpp_api.rate_limiter import NoOpRateLimiter

    # Ensure NoOpRateLimiter for predictable behavior
    app.state.rate_limiter = NoOpRateLimiter(quota=60, window=60)

    # Create a test router with custom RateLimit headers
    test_router = APIRouter()

    @test_router.get("/v1/test-custom-ratelimit")
    def custom_ratelimit_handler(response: Response):
        """Handler that sets custom RateLimit headers."""
        response.headers["RateLimit-Policy"] = '"custom"; q=100; w=3600'
        response.headers["RateLimit"] = '"custom"; r=99; t=3500'
        return {"message": "custom headers set"}

    @test_router.get("/v1/test-no-ratelimit")
    def no_ratelimit_handler():
        """Handler that does NOT set RateLimit headers."""
        return {"message": "no custom headers"}

    # Add test routes to app
    app.include_router(test_router)

    client = TestClient(app)

    # Test 1: Handler sets custom headers -> middleware preserves them
    resp1 = client.get("/v1/test-custom-ratelimit")
    assert resp1.status_code == 200
    assert resp1.headers["RateLimit-Policy"] == '"custom"; q=100; w=3600'
    assert resp1.headers["RateLimit"] == '"custom"; r=99; t=3500'

    # Test 2: Handler does NOT set headers -> middleware adds defaults
    resp2 = client.get("/v1/test-no-ratelimit")
    assert resp2.status_code == 200
    assert "RateLimit-Policy" in resp2.headers
    assert "RateLimit" in resp2.headers
    # Defaults from NoOpRateLimiter (quota=60, window=60)
    assert 'q=60' in resp2.headers["RateLimit-Policy"]
    assert 'w=60' in resp2.headers["RateLimit-Policy"]
