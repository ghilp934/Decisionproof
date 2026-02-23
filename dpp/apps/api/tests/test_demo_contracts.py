"""Demo Contract Tests — STEP 3 enforcement.

These tests enforce the full DEC spec for the 2 demo endpoints:
  POST /v1/demo/runs   (operationId: demo_run_create)
  GET  /v1/demo/runs/{run_id}  (operationId: demo_run_get)

Test categories:
  - openapi-demo AC (servers=1, url lock, paths=2, operationId, error examples)
  - POST contracts: valid → 202, extra key → 422, question too long → 422,
                    invalid auth → 401, body too large → 413
  - GET contracts:  immediate double poll → 429 + Retry-After,
                    COMPLETED response fields (meta.ai_generated, result_inline, headers),
                    unknown run_id → 404 (problem+json)

All tests use monkeypatching to control:
  - in-memory store (no Redis required)
  - auth env vars (RAPIDAPI_PROXY_SECRET, DP_DEMO_SHARED_TOKEN)
  - S3 unavailable (graceful fallback)

NOTE: After Fail-Closed (A1), RAPIDAPI_PROXY_SECRET MUST be set for any demo
endpoint call to succeed. The mem_store fixture sets it automatically.
Tests that test auth failures (401) explicitly use auth_env + wrong headers.
"""

import json
import sys
import time
from pathlib import Path
from typing import Any, Optional
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dpp_api.main import app
from dpp_api.schemas_demo import AI_DISCLOSURE
import dpp_api.routers.demo_runs as demo_runs_mod

DEMO_BASE_URL = "https://api.decisionproof.io.kr"
ALLOWED_PATHS = {"/v1/demo/runs", "/v1/demo/runs/{run_id}"}

# Fixed test auth credentials
TEST_PROXY_SECRET = "test-proxy-secret-abc123"
TEST_BEARER_TOKEN = "test-bearer-token-xyz789"

# Auth headers for all requests that should pass auth (Fail-Closed compliant)
VALID_AUTH_HEADERS = {
    "X-RapidAPI-Proxy-Secret": TEST_PROXY_SECRET,
    "Authorization": f"Bearer {TEST_BEARER_TOKEN}",
}


# ─── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def mem_store(monkeypatch):
    """Provide a clean in-memory dict as the demo store for each test.

    Also sets RAPIDAPI_PROXY_SECRET and DP_DEMO_SHARED_TOKEN env vars so that
    the Fail-Closed auth guard passes. Tests that test auth failures use
    auth_env explicitly and send wrong headers — the env var being set is correct.
    """
    # Fail-Closed: must set RAPIDAPI_PROXY_SECRET for demo endpoints to respond
    monkeypatch.setenv("RAPIDAPI_PROXY_SECRET", TEST_PROXY_SECRET)
    monkeypatch.setenv("DP_DEMO_SHARED_TOKEN", TEST_BEARER_TOKEN)

    store: dict[str, tuple[str, Optional[float]]] = {}

    def fake_get(key: str) -> Optional[str]:
        entry = store.get(key)
        if entry is None:
            return None
        value, expire_at = entry
        if expire_at and time.time() > expire_at:
            del store[key]
            return None
        return value

    def fake_set(key: str, value: str, ex: Optional[int] = None) -> None:
        expire_at = time.time() + ex if ex else None
        store[key] = (value, expire_at)

    def fake_incr(key: str, ex: Optional[int] = None) -> int:
        entry = store.get(key)
        if entry is None or (entry[1] and time.time() > entry[1]):
            new_val = 1
            expire_at = time.time() + ex if ex else None
        else:
            new_val = int(entry[0]) + 1
            expire_at = entry[1]
        store[key] = (str(new_val), expire_at)
        return new_val

    def fake_decr(key: str) -> int:
        entry = store.get(key)
        current = int(entry[0]) if entry else 0
        new_val = max(0, current - 1)
        store[key] = (str(new_val), entry[1] if entry else None)
        return new_val

    def fake_delete(key: str) -> None:
        store.pop(key, None)

    with (
        patch.object(demo_runs_mod, "_store_get", side_effect=fake_get),
        patch.object(demo_runs_mod, "_store_set", side_effect=fake_set),
        patch.object(demo_runs_mod, "_store_incr", side_effect=fake_incr),
        patch.object(demo_runs_mod, "_store_decr", side_effect=fake_decr),
        patch.object(demo_runs_mod, "_store_delete", side_effect=fake_delete),
        # S3 unavailable in tests → graceful fallback (result_inline only)
        patch.object(demo_runs_mod, "_store_result_in_s3", return_value=None),
        patch.object(demo_runs_mod, "_generate_presigned_url", return_value=None),
    ):
        yield store


@pytest.fixture
def auth_env(monkeypatch):
    """Set auth env vars for tests that require auth enforcement."""
    monkeypatch.setenv("RAPIDAPI_PROXY_SECRET", TEST_PROXY_SECRET)
    monkeypatch.setenv("DP_DEMO_SHARED_TOKEN", TEST_BEARER_TOKEN)


@pytest.fixture
def valid_headers():
    """Headers that pass auth validation."""
    return {
        "X-RapidAPI-Proxy-Secret": TEST_PROXY_SECRET,
        "Authorization": f"Bearer {TEST_BEARER_TOKEN}",
        "X-RapidAPI-Subscription": "BASIC",
    }


@pytest.fixture
def valid_body():
    return {"inputs": {"question": "Should we approve the proposal?"}}


# ─── openapi-demo AC tests ────────────────────────────────────────────────────

class TestOpenAPIDemoAC:
    """Acceptance criteria for /.well-known/openapi-demo.json."""

    def test_ac1_status_200(self, client):
        assert client.get("/.well-known/openapi-demo.json").status_code == 200

    def test_ac2_openapi_version(self, client):
        data = client.get("/.well-known/openapi-demo.json").json()
        assert data["openapi"] == "3.1.0"

    def test_ac3_servers_length_is_1(self, client):
        data = client.get("/.well-known/openapi-demo.json").json()
        assert len(data["servers"]) == 1

    def test_ac3_servers_url_locked(self, client):
        data = client.get("/.well-known/openapi-demo.json").json()
        assert data["servers"][0]["url"] == DEMO_BASE_URL

    def test_ac4_paths_exact_match(self, client):
        data = client.get("/.well-known/openapi-demo.json").json()
        actual = set(data["paths"].keys())
        leaked = actual - ALLOWED_PATHS
        missing = ALLOWED_PATHS - actual
        assert not leaked, f"Leaked paths: {sorted(leaked)}"
        assert not missing, f"Missing paths: {sorted(missing)}"

    def test_ac4b_no_extra_paths(self, client):
        data = client.get("/.well-known/openapi-demo.json").json()
        extra = set(data["paths"].keys()) - ALLOWED_PATHS
        assert len(extra) == 0

    def test_ac5_content_type_json(self, client):
        r = client.get("/.well-known/openapi-demo.json")
        assert "application/json" in r.headers.get("content-type", "")

    def test_operation_id_post_fixed(self, client):
        data = client.get("/.well-known/openapi-demo.json").json()
        assert data["paths"]["/v1/demo/runs"]["post"]["operationId"] == "demo_run_create"

    def test_operation_id_get_fixed(self, client):
        data = client.get("/.well-known/openapi-demo.json").json()
        assert data["paths"]["/v1/demo/runs/{run_id}"]["get"]["operationId"] == "demo_run_get"

    def test_error_examples_401_problem_json(self, client):
        data = client.get("/.well-known/openapi-demo.json").json()
        post_resp = data["paths"]["/v1/demo/runs"]["post"]["responses"]
        assert "401" in post_resp
        assert "problem+json" in str(post_resp["401"])

    def test_error_examples_422_exists(self, client):
        data = client.get("/.well-known/openapi-demo.json").json()
        post_resp = data["paths"]["/v1/demo/runs"]["post"]["responses"]
        assert "422" in post_resp

    def test_error_examples_429_problem_json(self, client):
        data = client.get("/.well-known/openapi-demo.json").json()
        post_resp = data["paths"]["/v1/demo/runs"]["post"]["responses"]
        assert "429" in post_resp
        assert "problem+json" in str(post_resp["429"])

    def test_get_has_401_and_429(self, client):
        data = client.get("/.well-known/openapi-demo.json").json()
        get_resp = data["paths"]["/v1/demo/runs/{run_id}"]["get"]["responses"]
        assert "401" in get_resp
        assert "429" in get_resp


# ─── POST /v1/demo/runs contracts ─────────────────────────────────────────────

class TestPostDemoRunsContracts:
    """Contract tests for POST /v1/demo/runs.

    All non-auth-failure tests send VALID_AUTH_HEADERS to pass Fail-Closed guard.
    Auth-failure tests use auth_env (env var set) but send intentionally wrong headers.
    """

    # ── Happy path ────────────────────────────────────────────────────────────

    def test_valid_request_returns_202(self, client, mem_store, valid_body):
        r = client.post("/v1/demo/runs", json=valid_body, headers=VALID_AUTH_HEADERS)
        assert r.status_code == 202

    def test_receipt_has_run_id(self, client, mem_store, valid_body):
        r = client.post("/v1/demo/runs", json=valid_body, headers=VALID_AUTH_HEADERS)
        data = r.json()
        assert "run_id" in data
        assert data["run_id"].startswith("demo_")

    def test_receipt_has_poll_url(self, client, mem_store, valid_body):
        r = client.post("/v1/demo/runs", json=valid_body, headers=VALID_AUTH_HEADERS)
        data = r.json()
        assert "poll_url" in data

    def test_receipt_has_created_at(self, client, mem_store, valid_body):
        r = client.post("/v1/demo/runs", json=valid_body, headers=VALID_AUTH_HEADERS)
        data = r.json()
        assert "created_at" in data

    def test_receipt_has_poll_delay_basic(self, client, mem_store, valid_body):
        """BASIC plan → poll.recommended_delay_ms == 3000."""
        r = client.post(
            "/v1/demo/runs",
            json=valid_body,
            headers={**VALID_AUTH_HEADERS, "X-RapidAPI-Subscription": "BASIC"},
        )
        data = r.json()
        assert data["poll"]["recommended_delay_ms"] == 3000

    def test_receipt_has_poll_delay_pro(self, client, mem_store, valid_body):
        """PRO plan → poll.recommended_delay_ms == 2000."""
        r = client.post(
            "/v1/demo/runs",
            json=valid_body,
            headers={**VALID_AUTH_HEADERS, "X-RapidAPI-Subscription": "PRO"},
        )
        data = r.json()
        assert data["poll"]["recommended_delay_ms"] == 2000

    def test_receipt_ai_meta(self, client, mem_store, valid_body):
        r = client.post("/v1/demo/runs", json=valid_body, headers=VALID_AUTH_HEADERS)
        data = r.json()
        assert data["meta"]["ai_generated"] is True
        assert data["meta"]["ai_disclosure"] == AI_DISCLOSURE

    def test_response_header_no_store(self, client, mem_store, valid_body):
        r = client.post("/v1/demo/runs", json=valid_body, headers=VALID_AUTH_HEADERS)
        assert "no-store" in r.headers.get("Cache-Control", "")

    def test_response_header_ai_generated(self, client, mem_store, valid_body):
        r = client.post("/v1/demo/runs", json=valid_body, headers=VALID_AUTH_HEADERS)
        assert r.headers.get("X-DP-AI-Generated") == "true"

    def test_response_header_ai_disclosure(self, client, mem_store, valid_body):
        r = client.post("/v1/demo/runs", json=valid_body, headers=VALID_AUTH_HEADERS)
        assert AI_DISCLOSURE in r.headers.get("X-DP-AI-Disclosure", "")

    # ── Validation errors ─────────────────────────────────────────────────────

    def test_extra_top_level_key_returns_422(self, client, mem_store):
        """Extra key at top level → 422 problem+json."""
        r = client.post(
            "/v1/demo/runs",
            json={
                "inputs": {"question": "Valid question"},
                "evil_extra_key": "injected",
            },
            headers=VALID_AUTH_HEADERS,
        )
        assert r.status_code == 422
        data = r.json()
        assert data["status"] == 422
        assert "problem+json" in r.headers.get("content-type", "")

    def test_extra_nested_key_in_inputs_returns_422(self, client, mem_store):
        """Extra key inside inputs → 422 problem+json."""
        r = client.post(
            "/v1/demo/runs",
            json={"inputs": {"question": "q", "injected": "bad"}},
            headers=VALID_AUTH_HEADERS,
        )
        assert r.status_code == 422
        assert r.json()["status"] == 422
        assert "problem+json" in r.headers.get("content-type", "")

    def test_extra_key_in_reservation_returns_422(self, client, mem_store):
        """Extra key inside reservation → 422."""
        r = client.post(
            "/v1/demo/runs",
            json={
                "inputs": {"question": "valid"},
                "reservation": {"unauthorized_field": "value"},
            },
            headers=VALID_AUTH_HEADERS,
        )
        assert r.status_code == 422
        assert "problem+json" in r.headers.get("content-type", "")

    def test_question_too_long_returns_422(self, client, mem_store):
        """question > 512 chars → 422 problem+json."""
        r = client.post(
            "/v1/demo/runs",
            json={"inputs": {"question": "x" * 513}},
            headers=VALID_AUTH_HEADERS,
        )
        assert r.status_code == 422
        data = r.json()
        assert data["status"] == 422
        assert "problem+json" in r.headers.get("content-type", "")

    def test_question_exactly_512_chars_ok(self, client, mem_store):
        """question == 512 chars → 202 (boundary check)."""
        r = client.post(
            "/v1/demo/runs",
            json={"inputs": {"question": "q" * 512}},
            headers=VALID_AUTH_HEADERS,
        )
        assert r.status_code == 202

    def test_missing_inputs_returns_422(self, client, mem_store):
        """inputs field missing → 422."""
        r = client.post(
            "/v1/demo/runs",
            json={"meta": {}},
            headers=VALID_AUTH_HEADERS,
        )
        assert r.status_code == 422

    def test_missing_question_returns_422(self, client, mem_store):
        """question missing from inputs → 422."""
        r = client.post(
            "/v1/demo/runs",
            json={"inputs": {}},
            headers=VALID_AUTH_HEADERS,
        )
        assert r.status_code == 422

    # ── Auth enforcement ──────────────────────────────────────────────────────

    def test_invalid_proxy_secret_returns_401(
        self, client, mem_store, auth_env, valid_body
    ):
        """Wrong X-RapidAPI-Proxy-Secret → 401 problem+json."""
        r = client.post(
            "/v1/demo/runs",
            json=valid_body,
            headers={
                "X-RapidAPI-Proxy-Secret": "wrong-secret",
                "Authorization": f"Bearer {TEST_BEARER_TOKEN}",
            },
        )
        assert r.status_code == 401
        data = r.json()
        assert data["status"] == 401
        assert "problem+json" in r.headers.get("content-type", "")

    def test_missing_proxy_secret_returns_401(
        self, client, mem_store, auth_env, valid_body
    ):
        """No X-RapidAPI-Proxy-Secret → 401."""
        r = client.post(
            "/v1/demo/runs",
            json=valid_body,
            headers={"Authorization": f"Bearer {TEST_BEARER_TOKEN}"},
        )
        assert r.status_code == 401
        assert "problem+json" in r.headers.get("content-type", "")

    def test_invalid_bearer_token_returns_401(
        self, client, mem_store, auth_env, valid_body
    ):
        """Wrong Bearer token → 401 problem+json."""
        r = client.post(
            "/v1/demo/runs",
            json=valid_body,
            headers={
                "X-RapidAPI-Proxy-Secret": TEST_PROXY_SECRET,
                "Authorization": "Bearer wrong-token",
            },
        )
        assert r.status_code == 401
        assert r.json()["status"] == 401

    # ── Body size enforcement ─────────────────────────────────────────────────

    def test_body_over_4096_bytes_returns_413(self, client, mem_store):
        """Body > 4096 bytes → 413 problem+json (checked before Pydantic validation).

        Auth runs first (Depends), then body size guard. After Fail-Closed,
        we must include valid auth headers so the 413 guard is reached.
        """
        big_body = '{"inputs": {"question": "' + "q" * 512 + '"}, "padding": "' + "p" * 4000 + '"}'
        assert len(big_body.encode()) > 4096, "Test setup error: body must exceed 4096 bytes"
        r = client.post(
            "/v1/demo/runs",
            content=big_body.encode(),
            headers={
                "Content-Type": "application/json",
                **VALID_AUTH_HEADERS,
            },
        )
        # Body size check (> 4096) fires before Pydantic → must be 413
        assert r.status_code == 413, (
            f"Expected 413 for {len(big_body)}-byte body, got {r.status_code}: {r.text}"
        )
        data = r.json()
        assert data["status"] == 413
        assert "problem+json" in r.headers.get("content-type", "")


# ─── GET /v1/demo/runs/{run_id} contracts ─────────────────────────────────────

class TestGetDemoRunContracts:
    """Contract tests for GET /v1/demo/runs/{run_id}."""

    def _post_and_get_run_id(self, client, mem_store) -> str:
        """POST a valid run and return run_id. Includes auth headers (Fail-Closed)."""
        r = client.post(
            "/v1/demo/runs",
            json={"inputs": {"question": "Contract test question."}},
            headers=VALID_AUTH_HEADERS,
        )
        assert r.status_code == 202
        return r.json()["run_id"]

    # ── COMPLETED response structure ──────────────────────────────────────────

    def test_completed_status_200(self, client, mem_store):
        run_id = self._post_and_get_run_id(client, mem_store)
        r = client.get(f"/v1/demo/runs/{run_id}", headers=VALID_AUTH_HEADERS)
        assert r.status_code == 200

    def test_completed_has_status_field(self, client, mem_store):
        run_id = self._post_and_get_run_id(client, mem_store)
        data = client.get(f"/v1/demo/runs/{run_id}", headers=VALID_AUTH_HEADERS).json()
        assert data["status"] == "COMPLETED"

    def test_completed_meta_ai_generated(self, client, mem_store):
        """meta.ai_generated must be True."""
        run_id = self._post_and_get_run_id(client, mem_store)
        data = client.get(f"/v1/demo/runs/{run_id}", headers=VALID_AUTH_HEADERS).json()
        assert data["meta"]["ai_generated"] is True

    def test_completed_meta_ai_disclosure(self, client, mem_store):
        """meta.ai_disclosure must equal AI_DISCLOSURE."""
        run_id = self._post_and_get_run_id(client, mem_store)
        data = client.get(f"/v1/demo/runs/{run_id}", headers=VALID_AUTH_HEADERS).json()
        assert data["meta"]["ai_disclosure"] == AI_DISCLOSURE

    def test_completed_result_inline_exists(self, client, mem_store):
        """COMPLETED response must contain result_inline."""
        run_id = self._post_and_get_run_id(client, mem_store)
        data = client.get(f"/v1/demo/runs/{run_id}", headers=VALID_AUTH_HEADERS).json()
        assert "result_inline" in data, "COMPLETED must have result_inline"

    def test_completed_result_inline_is_ai_generated(self, client, mem_store):
        """result_inline.is_ai_generated must be True."""
        run_id = self._post_and_get_run_id(client, mem_store)
        data = client.get(f"/v1/demo/runs/{run_id}", headers=VALID_AUTH_HEADERS).json()
        ri = data["result_inline"]
        assert ri["is_ai_generated"] is True

    def test_completed_result_inline_disclaimer(self, client, mem_store):
        """result_inline.disclaimer must equal AI_DISCLOSURE."""
        run_id = self._post_and_get_run_id(client, mem_store)
        data = client.get(f"/v1/demo/runs/{run_id}", headers=VALID_AUTH_HEADERS).json()
        ri = data["result_inline"]
        assert ri["disclaimer"] == AI_DISCLOSURE

    def test_completed_header_ai_generated(self, client, mem_store):
        """X-DP-AI-Generated: true on COMPLETED response."""
        run_id = self._post_and_get_run_id(client, mem_store)
        r = client.get(f"/v1/demo/runs/{run_id}", headers=VALID_AUTH_HEADERS)
        assert r.headers.get("X-DP-AI-Generated") == "true"

    def test_completed_header_ai_disclosure(self, client, mem_store):
        """X-DP-AI-Disclosure header present."""
        run_id = self._post_and_get_run_id(client, mem_store)
        r = client.get(f"/v1/demo/runs/{run_id}", headers=VALID_AUTH_HEADERS)
        assert AI_DISCLOSURE in r.headers.get("X-DP-AI-Disclosure", "")

    def test_completed_cache_control_no_store(self, client, mem_store):
        """Cache-Control: no-store on COMPLETED response."""
        run_id = self._post_and_get_run_id(client, mem_store)
        r = client.get(f"/v1/demo/runs/{run_id}", headers=VALID_AUTH_HEADERS)
        assert "no-store" in r.headers.get("Cache-Control", "")

    # ── Poll rate limiting ────────────────────────────────────────────────────

    def test_immediate_double_poll_returns_429(self, client, mem_store):
        """Two immediate GETs → second returns 429 with Retry-After."""
        run_id = self._post_and_get_run_id(client, mem_store)

        # First GET: sets last_poll timestamp
        r1 = client.get(f"/v1/demo/runs/{run_id}", headers=VALID_AUTH_HEADERS)
        assert r1.status_code == 200

        # Second GET immediately → must be 429 (< 3s min interval for BASIC)
        r2 = client.get(f"/v1/demo/runs/{run_id}", headers=VALID_AUTH_HEADERS)
        assert r2.status_code == 429, (
            f"Expected 429 on immediate double poll, got {r2.status_code}"
        )
        data = r2.json()
        assert data["status"] == 429
        assert "problem+json" in r2.headers.get("content-type", "")

    def test_429_has_retry_after_header(self, client, mem_store):
        """429 response must include Retry-After header."""
        run_id = self._post_and_get_run_id(client, mem_store)
        client.get(f"/v1/demo/runs/{run_id}", headers=VALID_AUTH_HEADERS)  # First poll
        r = client.get(f"/v1/demo/runs/{run_id}", headers=VALID_AUTH_HEADERS)  # Immediate second
        assert r.status_code == 429
        retry_after = r.headers.get("Retry-After")
        assert retry_after is not None, "429 must include Retry-After header"
        assert int(retry_after) > 0

    def test_429_content_type_problem_json(self, client, mem_store):
        """429 Content-Type must be application/problem+json."""
        run_id = self._post_and_get_run_id(client, mem_store)
        client.get(f"/v1/demo/runs/{run_id}", headers=VALID_AUTH_HEADERS)
        r = client.get(f"/v1/demo/runs/{run_id}", headers=VALID_AUTH_HEADERS)
        assert r.status_code == 429
        assert "problem+json" in r.headers.get("content-type", "")

    # ── 404 handling ──────────────────────────────────────────────────────────

    def test_nonexistent_run_returns_404(self, client, mem_store):
        r = client.get("/v1/demo/runs/demo_nonexistent_xyz000", headers=VALID_AUTH_HEADERS)
        assert r.status_code == 404
        data = r.json()
        assert data["status"] == 404
        assert "problem+json" in r.headers.get("content-type", "")

    def test_404_is_problem_json(self, client, mem_store):
        r = client.get("/v1/demo/runs/demo_unknown_run", headers=VALID_AUTH_HEADERS)
        assert r.status_code == 404
        assert "problem+json" in r.headers.get("content-type", "")

    # ── 410 tombstone simulation ──────────────────────────────────────────────

    def test_expired_run_owner_gets_410(self, client, mem_store):
        """Simulated expiry: owner actor → 410 Gone."""
        import json as _json
        from datetime import datetime, timezone, timedelta

        # Manually inject a run that is already expired
        run_id = "demo_expired_owner_test"
        past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        run_data = {
            "run_id": run_id,
            "status": "COMPLETED",
            "plan": "BASIC",
            "created_at": past,
            "owner_key": "owner-hmac-abc",
            "actor_key": "owner-hmac-abc",
            "inputs_hash": "hash",
            "inputs_len": 5,
            "result_sha256": "sha",
            "retention_until": past,   # Already expired
        }
        # Inject into mem_store via _store_set
        from dpp_api.routers.demo_runs import _rk_run
        demo_runs_mod._store_set(_rk_run(run_id), _json.dumps(run_data))

        # Patch actor_key derivation to match owner
        with patch.object(demo_runs_mod, "_derive_actor_key", return_value="owner-hmac-abc"):
            r = client.get(f"/v1/demo/runs/{run_id}", headers=VALID_AUTH_HEADERS)

        assert r.status_code == 410
        data = r.json()
        assert data["status"] == 410
        assert "problem+json" in r.headers.get("content-type", "")

    def test_expired_run_nonowner_gets_404(self, client, mem_store):
        """Simulated expiry: non-owner actor → 404 (stealth)."""
        import json as _json
        from datetime import datetime, timezone, timedelta

        run_id = "demo_expired_nonowner_test"
        past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        run_data = {
            "run_id": run_id,
            "status": "COMPLETED",
            "plan": "BASIC",
            "created_at": past,
            "owner_key": "real-owner-hmac",
            "actor_key": "real-owner-hmac",
            "inputs_hash": "hash",
            "inputs_len": 5,
            "result_sha256": "sha",
            "retention_until": past,
        }
        from dpp_api.routers.demo_runs import _rk_run
        demo_runs_mod._store_set(_rk_run(run_id), _json.dumps(run_data))

        # Different actor → non-owner
        with patch.object(demo_runs_mod, "_derive_actor_key", return_value="different-actor-hmac"):
            r = client.get(f"/v1/demo/runs/{run_id}", headers=VALID_AUTH_HEADERS)

        assert r.status_code == 404
        assert "problem+json" in r.headers.get("content-type", "")
