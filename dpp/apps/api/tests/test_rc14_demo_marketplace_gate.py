"""RC-14: Mini Demo Marketplace Gate — P0 Lockdown.

Enforces four categories of non-negotiable invariants for the
Decisionproof Mini Demo public API surface:

  (1) openapi-demo LOCK     — GET /.well-known/openapi-demo.json invariants
  (2) Fail-Closed           — 503 when RAPIDAPI_PROXY_SECRET is not set
  (3) Auth enforcement      — 401 for wrong/missing proxy secret; Bearer optional (present+wrong → 401)
  (4) Poll rate limit       — 429 + Retry-After on immediate double poll

STOP RULES (any failure → CI BLOCKED):
  - RAPIDAPI_PROXY_SECRET missing → demo returns 202/200/401 (not 503)
  - Any error response is NOT application/problem+json
  - 429 response is missing Retry-After header
  - openapi-demo paths drift from exactly {/v1/demo/runs, /v1/demo/runs/{run_id}}
  - secretKeyRef optional:true (K8s manifest — checked separately)
"""

import sys
import time
from pathlib import Path
from typing import Optional
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dpp_api.main import app
import dpp_api.routers.demo_runs as demo_runs_mod

DEMO_BASE_URL = "https://api.decisionproof.io.kr"
ALLOWED_PATHS = {"/v1/demo/runs", "/v1/demo/runs/{run_id}"}

# Test credentials — never use in production
RC14_PROXY_SECRET = "rc14-proxy-secret-abc"
RC14_BEARER_TOKEN = "rc14-bearer-token-xyz"
RC14_ACTOR_SALT = "rc14-actor-salt-qrs"


# ─── Common assertion helper ──────────────────────────────────────────────────

def assert_problem_json(resp, expected_status: int) -> None:
    """Assert response is RFC 9457 application/problem+json with all required fields."""
    ct = resp.headers.get("content-type", "")
    assert "application/problem+json" in ct, (
        f"Expected application/problem+json, got '{ct}'"
    )
    data = resp.json()
    for key in ("type", "title", "status", "detail", "instance"):
        assert key in data, f"Missing required RFC 9457 key '{key}' in: {data}"
    assert data["status"] == expected_status, (
        f"Expected status={expected_status}, got {data['status']}"
    )


# ─── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def demo_env(monkeypatch):
    """Set all three demo auth env vars (Fail-Closed compliant)."""
    monkeypatch.setenv("RAPIDAPI_PROXY_SECRET", RC14_PROXY_SECRET)
    monkeypatch.setenv("DP_DEMO_SHARED_TOKEN", RC14_BEARER_TOKEN)
    monkeypatch.setenv("DEMO_ACTOR_KEY_SALT", RC14_ACTOR_SALT)
    return {
        "proxy": RC14_PROXY_SECRET,
        "token": RC14_BEARER_TOKEN,
        "salt": RC14_ACTOR_SALT,
    }


@pytest.fixture
def valid_headers(demo_env):
    """Headers that satisfy all auth checks."""
    return {
        "X-RapidAPI-Proxy-Secret": demo_env["proxy"],
        "Authorization": f"Bearer {demo_env['token']}",
        "X-RapidAPI-Subscription": "BASIC",
    }


@pytest.fixture
def mem_store(demo_env):
    """In-memory store + auth env (demo_env already set via fixture dependency)."""
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
        patch.object(demo_runs_mod, "_store_result_in_s3", return_value=None),
        patch.object(demo_runs_mod, "_generate_presigned_url", return_value=None),
    ):
        yield store


# ─── (1) openapi-demo LOCK ────────────────────────────────────────────────────

class TestRC14OpenAPIDemoLock:
    """Gate: /.well-known/openapi-demo.json LOCK invariants.

    This endpoint requires no auth. Failures here block the marketplace listing.
    """

    def test_status_200(self, client):
        r = client.get("/.well-known/openapi-demo.json")
        assert r.status_code == 200

    def test_servers_length_is_1(self, client):
        data = client.get("/.well-known/openapi-demo.json").json()
        assert len(data["servers"]) == 1, (
            f"servers must have exactly 1 entry, got {len(data['servers'])}"
        )

    def test_servers_url_locked(self, client):
        data = client.get("/.well-known/openapi-demo.json").json()
        assert data["servers"][0]["url"] == DEMO_BASE_URL, (
            f"servers[0].url must be '{DEMO_BASE_URL}', got '{data['servers'][0]['url']}'"
        )

    def test_paths_exact_allowlist(self, client):
        data = client.get("/.well-known/openapi-demo.json").json()
        actual = set(data["paths"].keys())
        assert actual == ALLOWED_PATHS, (
            f"Path drift detected. "
            f"Leaked: {sorted(actual - ALLOWED_PATHS)}, "
            f"Missing: {sorted(ALLOWED_PATHS - actual)}"
        )


# ─── (2) Fail-Closed: 503 when RAPIDAPI_PROXY_SECRET not set ─────────────────

class TestRC14FailClosed:
    """Gate: RAPIDAPI_PROXY_SECRET missing → 503 (never 200/202/401).

    This is the P0 regression guard. If this gate fails, the bypass is back.
    """

    def test_post_without_env_returns_503(self, client, monkeypatch):
        """RAPIDAPI_PROXY_SECRET not set → POST must return 503 problem+json."""
        monkeypatch.delenv("RAPIDAPI_PROXY_SECRET", raising=False)
        monkeypatch.setenv("RAPIDAPI_PROXY_SECRET", "")  # Empty string = misconfigured

        r = client.post(
            "/v1/demo/runs",
            json={"inputs": {"question": "Should we proceed?"}},
            headers={"X-RapidAPI-Proxy-Secret": "any-value", "Authorization": "Bearer any"},
        )
        # STOP RULE: must be 503, not 202/200/401
        assert r.status_code == 503, (
            f"STOP RULE VIOLATED: RAPIDAPI_PROXY_SECRET not set but got "
            f"{r.status_code} instead of 503. Silent bypass detected!"
        )
        assert_problem_json(r, 503)

    def test_get_without_env_returns_503(self, client, monkeypatch):
        """RAPIDAPI_PROXY_SECRET not set → GET must return 503 problem+json."""
        monkeypatch.delenv("RAPIDAPI_PROXY_SECRET", raising=False)
        monkeypatch.setenv("RAPIDAPI_PROXY_SECRET", "")

        r = client.get(
            "/v1/demo/runs/demo_test_run_id",
            headers={"X-RapidAPI-Proxy-Secret": "any-value", "Authorization": "Bearer any"},
        )
        assert r.status_code == 503, (
            f"STOP RULE VIOLATED: RAPIDAPI_PROXY_SECRET not set but got "
            f"{r.status_code} instead of 503. Silent bypass detected!"
        )
        assert_problem_json(r, 503)

    def test_503_detail_mentions_secret(self, client, monkeypatch):
        """503 detail must mention missing RAPIDAPI_PROXY_SECRET (no sensitive info)."""
        monkeypatch.setenv("RAPIDAPI_PROXY_SECRET", "")

        r = client.post(
            "/v1/demo/runs",
            json={"inputs": {"question": "test"}},
        )
        assert r.status_code == 503
        data = r.json()
        # Must mention what is missing (operator guidance) but no secret values
        assert "RAPIDAPI_PROXY_SECRET" in data["detail"]


# ─── (3) Auth enforcement: 401 for wrong/missing headers ─────────────────────

class TestRC14Auth:
    """Gate: auth failures always return 401 application/problem+json.

    Env vars are set (Fail-Closed satisfied), but headers are intentionally wrong.
    """

    def test_missing_proxy_secret_header_returns_401(self, client, demo_env):
        """X-RapidAPI-Proxy-Secret header absent → 401."""
        r = client.post(
            "/v1/demo/runs",
            json={"inputs": {"question": "test"}},
            headers={"Authorization": f"Bearer {RC14_BEARER_TOKEN}"},
        )
        assert r.status_code == 401
        assert_problem_json(r, 401)

    def test_wrong_proxy_secret_returns_401(self, client, demo_env):
        """X-RapidAPI-Proxy-Secret header has wrong value → 401."""
        r = client.post(
            "/v1/demo/runs",
            json={"inputs": {"question": "test"}},
            headers={
                "X-RapidAPI-Proxy-Secret": "this-is-wrong",
                "Authorization": f"Bearer {RC14_BEARER_TOKEN}",
            },
        )
        assert r.status_code == 401
        assert_problem_json(r, 401)

    def test_wrong_bearer_returns_401(self, client, demo_env):
        """Bearer token has wrong value → 401."""
        r = client.post(
            "/v1/demo/runs",
            json={"inputs": {"question": "test"}},
            headers={
                "X-RapidAPI-Proxy-Secret": RC14_PROXY_SECRET,
                "Authorization": "Bearer wrong-token-value",
            },
        )
        assert r.status_code == 401
        assert_problem_json(r, 401)


# ─── (4) Poll rate limit: 429 + Retry-After ───────────────────────────────────

class TestRC14PollRateLimit:
    """Gate: immediate double poll must return 429 with Retry-After (positive int).

    Verifies that the poll min-interval guard works correctly.
    """

    def test_immediate_double_poll_returns_429(self, client, mem_store, valid_headers):
        """POST → GET → immediate GET → 429 problem+json."""
        # Create a run
        post_r = client.post(
            "/v1/demo/runs",
            json={"inputs": {"question": "RC-14 poll rate limit test."}},
            headers=valid_headers,
        )
        assert post_r.status_code == 202
        run_id = post_r.json()["run_id"]

        # First poll — OK
        r1 = client.get(f"/v1/demo/runs/{run_id}", headers=valid_headers)
        assert r1.status_code == 200, f"First poll should be 200, got {r1.status_code}"

        # Immediate second poll — must be 429
        r2 = client.get(f"/v1/demo/runs/{run_id}", headers=valid_headers)
        assert r2.status_code == 429, (
            f"STOP RULE: Immediate double poll must return 429, got {r2.status_code}"
        )
        assert_problem_json(r2, 429)

    def test_429_has_positive_retry_after(self, client, mem_store, valid_headers):
        """429 response must include Retry-After header with a positive integer."""
        post_r = client.post(
            "/v1/demo/runs",
            json={"inputs": {"question": "Retry-After header test."}},
            headers=valid_headers,
        )
        run_id = post_r.json()["run_id"]

        client.get(f"/v1/demo/runs/{run_id}", headers=valid_headers)   # First poll
        r = client.get(f"/v1/demo/runs/{run_id}", headers=valid_headers)  # Immediate second

        assert r.status_code == 429
        retry_after = r.headers.get("Retry-After")
        assert retry_after is not None, (
            "STOP RULE: 429 response must include Retry-After header"
        )
        assert int(retry_after) > 0, (
            f"Retry-After must be a positive integer, got '{retry_after}'"
        )
