"""Demo Smoke Tests — STEP 1 verification.

Confirms the demo surface exists and the LOCK invariants hold:
- /.well-known/openapi-demo.json: status 200, servers lock, paths lock
- POST /v1/demo/runs: accepts valid request (auth bypassed in dev mode)
- GET  /v1/demo/runs/{run_id}: returns 200

These tests use the FastAPI TestClient without real Redis/S3.
The demo router falls back to in-memory store when Redis is unavailable.
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


@pytest.fixture(scope="module")
def client():
    """Simple TestClient (no DB, no Redis required)."""
    return TestClient(app)


# ─── openapi-demo LOCK tests ──────────────────────────────────────────────────

class TestOpenAPIDemoLock:
    """LOCK invariants for /.well-known/openapi-demo.json."""

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


# ─── Demo endpoint smoke tests (auth env vars not set → bypass) ──────────────

class TestDemoEndpointsSmoke:
    """Basic smoke tests for demo endpoints (dev mode, no auth secrets configured)."""

    def test_post_demo_runs_returns_202(self, client):
        """Valid request → 202 Accepted."""
        r = client.post(
            "/v1/demo/runs",
            json={"inputs": {"question": "Should we proceed?"}},
        )
        assert r.status_code == 202, f"Expected 202, got {r.status_code}: {r.text}"

    def test_post_receipt_has_run_id(self, client):
        r = client.post(
            "/v1/demo/runs",
            json={"inputs": {"question": "Test question"}},
        )
        data = r.json()
        assert "run_id" in data
        assert data["run_id"].startswith("demo_")

    def test_post_receipt_has_poll_url(self, client):
        r = client.post(
            "/v1/demo/runs",
            json={"inputs": {"question": "Test question"}},
        )
        data = r.json()
        assert "poll_url" in data
        assert data["run_id"] in data["poll_url"]

    def test_post_receipt_has_poll_delay(self, client):
        r = client.post(
            "/v1/demo/runs",
            json={"inputs": {"question": "Test question"}},
        )
        data = r.json()
        assert "poll" in data
        assert "recommended_delay_ms" in data["poll"]

    def test_post_receipt_has_ai_meta(self, client):
        r = client.post(
            "/v1/demo/runs",
            json={"inputs": {"question": "Test question"}},
        )
        data = r.json()
        assert data["meta"]["ai_generated"] is True
        assert AI_DISCLOSURE in data["meta"]["ai_disclosure"]

    def test_get_demo_run_returns_200(self, client):
        """POST then GET → 200."""
        post_r = client.post(
            "/v1/demo/runs",
            json={"inputs": {"question": "What is the recommendation?"}},
        )
        assert post_r.status_code == 202
        run_id = post_r.json()["run_id"]

        get_r = client.get(f"/v1/demo/runs/{run_id}")
        assert get_r.status_code == 200

    def test_get_completed_has_ai_headers(self, client):
        """COMPLETED GET → X-DP-AI-Generated header present."""
        post_r = client.post(
            "/v1/demo/runs",
            json={"inputs": {"question": "Evaluate this."}},
        )
        run_id = post_r.json()["run_id"]
        get_r = client.get(f"/v1/demo/runs/{run_id}")
        assert get_r.headers.get("X-DP-AI-Generated") == "true"

    def test_get_cache_control_no_store(self, client):
        """COMPLETED GET → Cache-Control: no-store."""
        post_r = client.post(
            "/v1/demo/runs",
            json={"inputs": {"question": "test"}},
        )
        run_id = post_r.json()["run_id"]
        get_r = client.get(f"/v1/demo/runs/{run_id}")
        assert "no-store" in get_r.headers.get("Cache-Control", "")

    def test_get_nonexistent_returns_404(self, client):
        r = client.get("/v1/demo/runs/demo_nonexistent_abc123")
        assert r.status_code == 404
        data = r.json()
        assert data["status"] == 404
        assert "problem+json" in r.headers.get("content-type", "")
