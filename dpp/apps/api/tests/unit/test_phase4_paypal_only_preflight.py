"""Phase 4 verification tests: PayPal-only billing preflight (Toss dormant).

Purpose
-------
Verifies that Phase 4 Toss isolation changes are correct:

1. Preflight result NEVER contains a 'toss' key (not launch-critical).
2. A missing TOSS_SECRET_KEY does NOT cause startup failure.
3. Readyz stays healthy when only PayPal secrets are present.
4. PayPal-only preflight succeeds end-to-end.
5. Preflight cache is idempotent — second call returns cached result, no extra network I/O.
6. REQUIRED=0 code path (fallback edge-case only — NOT the pilot default).
   Pilot and production both run with REQUIRED=1 (fail-fast).
   This test covers the fallback branch that exists in the codebase;
   it is NOT the expected normal pilot behaviour.

Normal pilot expected readyz: {"status": "ok", "billing_preflight": {"paypal": "ok"}}
REQUIRED=1 is the only accepted state for pilot / production startup.

All tests are pure unit tests — no real network calls, no K8s, no Secrets Manager.
"""

from __future__ import annotations

import os
import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from dpp_api.billing.active_preflight import (
    run_billing_secrets_active_preflight,
    get_billing_preflight_status,
    _reset_preflight_cache,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def reset_cache():
    """Isolate each test — clear module-level preflight cache before and after."""
    _reset_preflight_cache()
    yield
    _reset_preflight_cache()


@pytest.fixture
def paypal_env(monkeypatch):
    """Inject minimal valid PayPal env vars (sandbox)."""
    monkeypatch.setenv("PAYPAL_CLIENT_ID", "test-client-id")
    monkeypatch.setenv("PAYPAL_CLIENT_SECRET", "test-client-secret")
    monkeypatch.setenv("PAYPAL_ENV", "sandbox")
    monkeypatch.setenv("DPP_BILLING_PREFLIGHT_REQUIRED", "1")
    # Toss vars intentionally ABSENT — simulates pilot after Phase 4 SecretProviderClass removal
    monkeypatch.delenv("TOSS_SECRET_KEY", raising=False)
    monkeypatch.delenv("TOSS_WEBHOOK_SECRET", raising=False)


@pytest.fixture
def paypal_ok_response():
    """Mock httpx response: PayPal OAuth token endpoint 200 + access_token."""
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"access_token": "A21AAXXXXXX", "token_type": "Bearer"}
    return mock_resp


# ---------------------------------------------------------------------------
# Test 1: Preflight result has NO 'toss' key
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_preflight_result_has_no_toss_key(paypal_env, paypal_ok_response):
    """Phase 4 requirement: 'toss' key must be absent from preflight result dict."""
    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=paypal_ok_response)
        mock_client_cls.return_value = mock_client

        result = await run_billing_secrets_active_preflight()

    assert "toss" not in result, (
        f"Phase 4 violation: 'toss' key must not appear in preflight result. Got: {result}"
    )


# ---------------------------------------------------------------------------
# Test 2: Missing TOSS_SECRET_KEY does not cause RuntimeError
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_missing_toss_env_does_not_raise(paypal_env, paypal_ok_response):
    """Missing TOSS_* env vars must not raise RuntimeError even with REQUIRED=1."""
    # Toss env vars are absent (paypal_env fixture removes them)
    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=paypal_ok_response)
        mock_client_cls.return_value = mock_client

        # Must NOT raise
        result = await run_billing_secrets_active_preflight()

    assert result.get("paypal") == "ok"


# ---------------------------------------------------------------------------
# Test 3: PayPal-only preflight succeeds (no Toss required)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_paypal_only_preflight_ok(paypal_env, paypal_ok_response):
    """Preflight returns {'paypal': 'ok'} with only PayPal secrets present."""
    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=paypal_ok_response)
        mock_client_cls.return_value = mock_client

        result = await run_billing_secrets_active_preflight()

    assert result == {"paypal": "ok"}, f"Expected {{'paypal': 'ok'}}, got: {result}"


# ---------------------------------------------------------------------------
# Test 4: get_billing_preflight_status() reflects cached result
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_preflight_status_cached_after_run(paypal_env, paypal_ok_response):
    """get_billing_preflight_status() returns cached result without extra network I/O."""
    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=paypal_ok_response)
        mock_client_cls.return_value = mock_client

        await run_billing_secrets_active_preflight()

    # After preflight ran, status should be readable without any network call
    status = get_billing_preflight_status()
    assert status == {"paypal": "ok"}
    assert "toss" not in status


# ---------------------------------------------------------------------------
# Test 5: Preflight is idempotent — second call is a no-op
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_preflight_idempotent(paypal_env, paypal_ok_response):
    """Second call to run_billing_secrets_active_preflight() must not make additional HTTP calls."""
    call_count = 0

    async def mock_post(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        return paypal_ok_response

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = mock_post
        mock_client_cls.return_value = mock_client

        await run_billing_secrets_active_preflight()
        await run_billing_secrets_active_preflight()  # second call — must be a no-op

    assert call_count == 1, (
        f"Preflight must call PayPal OAuth exactly once. Called {call_count} times."
    )


# ---------------------------------------------------------------------------
# Test 6: REQUIRED=0 code path — fallback branch coverage only
# NOTE: Pilot and production BOTH use REQUIRED=1. This test exercises the
# fallback code path that exists for non-prod/local environments.
# It does NOT represent normal pilot startup behaviour.
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_required_0_degrades_gracefully_on_paypal_failure(monkeypatch):
    """Fallback branch: REQUIRED=0 produces err:... instead of RuntimeError on 401.
    This is NOT the pilot startup path (pilot uses REQUIRED=1 / fail-fast)."""
    monkeypatch.setenv("PAYPAL_CLIENT_ID", "bad-id")
    monkeypatch.setenv("PAYPAL_CLIENT_SECRET", "bad-secret")
    monkeypatch.setenv("PAYPAL_ENV", "sandbox")
    monkeypatch.setenv("DPP_BILLING_PREFLIGHT_REQUIRED", "0")
    monkeypatch.delenv("TOSS_SECRET_KEY", raising=False)
    monkeypatch.delenv("TOSS_WEBHOOK_SECRET", raising=False)

    mock_resp = MagicMock()
    mock_resp.status_code = 401
    mock_resp.json.return_value = {"error": "invalid_client"}

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_resp)
        mock_client_cls.return_value = mock_client

        # Must NOT raise RuntimeError
        result = await run_billing_secrets_active_preflight()

    assert result.get("paypal", "").startswith("err:"), (
        f"Expected paypal=err:..., got: {result}"
    )
    assert "toss" not in result


# ---------------------------------------------------------------------------
# Test 7: Pre-run status returns 'skipped' sentinel
# ---------------------------------------------------------------------------

def test_preflight_status_before_run_returns_skipped():
    """Before preflight has run, get_billing_preflight_status() returns {'status': 'skipped'}."""
    # cache is reset by autouse fixture
    status = get_billing_preflight_status()
    assert status == {"status": "skipped"}
