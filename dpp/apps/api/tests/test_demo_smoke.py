"""Demo Smoke Tests — STEP 1 verification.

Confirms the demo surface exists and the LOCK invariants hold:
- /.well-known/openapi-demo.json: status 200, servers lock, paths lock
- POST /v1/demo/runs: accepts valid request (Fail-Closed: auth always required)
- GET  /v1/demo/runs/{run_id}: returns 200

These tests use the FastAPI TestClient without real Redis/S3.
The demo router falls back to in-memory store when Redis is unavailable.

NOTE: After Fail-Closed (A1 patch), RAPIDAPI_PROXY_SECRET must be set
for any demo endpoint to respond. The set_demo_env fixture handles this.
All demo endpoint requests include SMOKE_AUTH_HEADERS.
"""

import json
import os
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dpp_api.main import app
from dpp_api.schemas_demo import AI_DISCLOSURE

DEMO_BASE_URL = "https://api.decisionproof.io.kr"
ALLOWED_PATHS = {"/v1/demo/runs", "/v1/demo/runs/{run_id}"}

# Smoke test auth credentials (used by set_demo_env + SMOKE_AUTH_HEADERS)
SMOKE_PROXY_SECRET = "smoke-proxy-secret-789xyz"
SMOKE_BEARER_TOKEN = "smoke-bearer-token-012abc"
SMOKE_AUTH_HEADERS = {
    "X-RapidAPI-Proxy-Secret": SMOKE_PROXY_SECRET,
    "Authorization": f"Bearer {SMOKE_BEARER_TOKEN}",
}


@pytest.fixture(scope="module", autouse=True)
def set_demo_env():
    """Set demo auth env vars for the entire module (Fail-Closed compliance).

    Uses os.environ directly since monkeypatch is function-scoped and cannot
    be used with scope="module". Cleans up after all tests in this module.
    """
    os.environ["RAPIDAPI_PROXY_SECRET"] = SMOKE_PROXY_SECRET
    os.environ["DP_DEMO_SHARED_TOKEN"] = SMOKE_BEARER_TOKEN
    yield
    os.environ.pop("RAPIDAPI_PROXY_SECRET", None)
    os.environ.pop("DP_DEMO_SHARED_TOKEN", None)


@pytest.fixture(scope="module")
def client():
    """Simple TestClient (no DB, no Redis required)."""
    return TestClient(app)


# ─── openapi-demo LOCK tests ──────────────────────────────────────────────────

class TestOpenAPIDemoLock:
    """LOCK invariants for /.well-known/openapi-demo.json.

    These tests call the well-known endpoint which requires no auth.
    """

    def test_endpoint_returns_200(self, client):
        r = client.get("/.well-known/openapi-demo.json")
        assert r.status_code == 200

    def test_content_type_is_json(self, client):
        r = client.get("/.well-known/openapi-demo.json")
        assert "application/json" in r.headers.get("content-type", "")

    def test_openapi_version_is_3_1_0(self, client):
        data = client.get("/.well-known/openapi-demo.json").json()
        assert data["openapi"] == "3.1.0"

    def test_servers_exactly_one(self, client):
        data = client.get("/.well-known/openapi-demo.json").json()
        assert len(data["servers"]) == 1

    def test_servers_url_locked(self, client):
        data = client.get("/.well-known/openapi-demo.json").json()
        assert data["servers"][0]["url"] == DEMO_BASE_URL

    def test_paths_exact_allowlist(self, client):
        data = client.get("/.well-known/openapi-demo.json").json()
        actual = set(data["paths"].keys())
        assert actual == ALLOWED_PATHS, (
            f"Path drift detected. Leaked: {actual - ALLOWED_PATHS}, "
            f"Missing: {ALLOWED_PATHS - actual}"
        )

    def test_no_extra_paths(self, client):
        data = client.get("/.well-known/openapi-demo.json").json()
        extra = set(data["paths"].keys()) - ALLOWED_PATHS
        assert len(extra) == 0, f"Unauthorized paths in demo spec: {sorted(extra)}"

    def test_operation_id_post(self, client):
        data = client.get("/.well-known/openapi-demo.json").json()
        post_op = data["paths"]["/v1/demo/runs"]["post"]
        assert post_op["operationId"] == "demo_run_create"

    def test_operation_id_get(self, client):
        data = client.get("/.well-known/openapi-demo.json").json()
        get_op = data["paths"]["/v1/demo/runs/{run_id}"]["get"]
        assert get_op["operationId"] == "demo_run_get"

    def test_error_examples_401_in_post(self, client):
        data = client.get("/.well-known/openapi-demo.json").json()
        post_responses = data["paths"]["/v1/demo/runs"]["post"]["responses"]
        assert "401" in post_responses, "POST must document 401 response"
        ct = str(post_responses["401"])
        assert "problem+json" in ct

    def test_error_examples_422_in_post(self, client):
        data = client.get("/.well-known/openapi-demo.json").json()
        post_responses = data["paths"]["/v1/demo/runs"]["post"]["responses"]
        assert "422" in post_responses, "POST must document 422 response"

    def test_error_examples_429_in_post(self, client):
        data = client.get("/.well-known/openapi-demo.json").json()
        post_responses = data["paths"]["/v1/demo/runs"]["post"]["responses"]
        assert "429" in post_responses, "POST must document 429 response"
        ct = str(post_responses["429"])
        assert "problem+json" in ct

    def test_error_examples_401_in_get(self, client):
        data = client.get("/.well-known/openapi-demo.json").json()
        get_responses = data["paths"]["/v1/demo/runs/{run_id}"]["get"]["responses"]
        assert "401" in get_responses, "GET must document 401 response"

    def test_error_examples_429_in_get(self, client):
        data = client.get("/.well-known/openapi-demo.json").json()
        get_responses = data["paths"]["/v1/demo/runs/{run_id}"]["get"]["responses"]
        assert "429" in get_responses, "GET must document 429 response"


# ─── Demo endpoint smoke tests (auth required — Fail-Closed) ──────────────────

class TestDemoEndpointsSmoke:
    """Basic smoke tests for demo endpoints.

    All requests include SMOKE_AUTH_HEADERS to satisfy Fail-Closed auth guard.
    """

    def test_post_demo_runs_returns_202(self, client):
        """Valid request with auth → 202 Accepted."""
        r = client.post(
            "/v1/demo/runs",
            json={"inputs": {"question": "Should we proceed?"}},
            headers=SMOKE_AUTH_HEADERS,
        )
        assert r.status_code == 202, f"Expected 202, got {r.status_code}: {r.text}"

    def test_post_receipt_has_run_id(self, client):
        r = client.post(
            "/v1/demo/runs",
            json={"inputs": {"question": "Test question"}},
            headers=SMOKE_AUTH_HEADERS,
        )
        data = r.json()
        assert "run_id" in data
        assert data["run_id"].startswith("demo_")

    def test_post_receipt_has_poll_url(self, client):
        r = client.post(
            "/v1/demo/runs",
            json={"inputs": {"question": "Test question"}},
            headers=SMOKE_AUTH_HEADERS,
        )
        data = r.json()
        assert "poll_url" in data
        assert data["run_id"] in data["poll_url"]

    def test_post_receipt_has_poll_delay(self, client):
        r = client.post(
            "/v1/demo/runs",
            json={"inputs": {"question": "Test question"}},
            headers=SMOKE_AUTH_HEADERS,
        )
        data = r.json()
        assert "poll" in data
        assert "recommended_delay_ms" in data["poll"]

    def test_post_receipt_has_ai_meta(self, client):
        r = client.post(
            "/v1/demo/runs",
            json={"inputs": {"question": "Test question"}},
            headers=SMOKE_AUTH_HEADERS,
        )
        data = r.json()
        assert data["meta"]["ai_generated"] is True
        assert AI_DISCLOSURE in data["meta"]["ai_disclosure"]

    def test_get_demo_run_returns_200(self, client):
        """POST then GET → 200."""
        post_r = client.post(
            "/v1/demo/runs",
            json={"inputs": {"question": "What is the recommendation?"}},
            headers=SMOKE_AUTH_HEADERS,
        )
        assert post_r.status_code == 202
        run_id = post_r.json()["run_id"]

        get_r = client.get(f"/v1/demo/runs/{run_id}", headers=SMOKE_AUTH_HEADERS)
        assert get_r.status_code == 200

    def test_get_completed_has_ai_headers(self, client):
        """COMPLETED GET → X-DP-AI-Generated header present."""
        post_r = client.post(
            "/v1/demo/runs",
            json={"inputs": {"question": "Evaluate this."}},
            headers=SMOKE_AUTH_HEADERS,
        )
        run_id = post_r.json()["run_id"]
        get_r = client.get(f"/v1/demo/runs/{run_id}", headers=SMOKE_AUTH_HEADERS)
        assert get_r.headers.get("X-DP-AI-Generated") == "true"

    def test_get_cache_control_no_store(self, client):
        """COMPLETED GET → Cache-Control: no-store."""
        post_r = client.post(
            "/v1/demo/runs",
            json={"inputs": {"question": "test"}},
            headers=SMOKE_AUTH_HEADERS,
        )
        run_id = post_r.json()["run_id"]
        get_r = client.get(f"/v1/demo/runs/{run_id}", headers=SMOKE_AUTH_HEADERS)
        assert "no-store" in get_r.headers.get("Cache-Control", "")

    def test_get_nonexistent_returns_404(self, client):
        r = client.get(
            "/v1/demo/runs/demo_nonexistent_abc123",
            headers=SMOKE_AUTH_HEADERS,
        )
        assert r.status_code == 404
        data = r.json()
        assert data["status"] == 404
        assert "problem+json" in r.headers.get("content-type", "")
