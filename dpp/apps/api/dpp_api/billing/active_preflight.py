"""Phase 6.1 / Phase 4: Billing secrets active healthcheck (startup-time only).

Purpose
-------
Validates that injected PayPal secrets are *working* credentials,
not merely *present* strings, by performing the lightest possible
authenticated request to the PayPal API.

Phase 4 change (v1.0 PayPal-only beta):
  Toss is EXCLUDED from the startup-critical preflight path.
  _check_toss() is preserved in this file for Phase 4+ reactivation,
  but is NOT called from run_billing_secrets_active_preflight().
  A missing or invalid Toss secret must not block startup or readyz.

Rules (NON-NEGOTIABLE)
-----------------------
- Network calls happen ONCE at startup; results are cached in module-level state.
- /health and /readyz endpoints read the *cached* result — zero extra network calls.
- Secret values are NEVER logged. Only status codes and error codes are recorded.
- Timeout is configurable via DPP_BILLING_PREFLIGHT_TIMEOUT_SECONDS (default: 5).
- DPP_BILLING_PREFLIGHT_REQUIRED=1 → failure raises RuntimeError (Fail-Fast).
- DPP_BILLING_PREFLIGHT_REQUIRED=0 → failure logs CRITICAL and caches "degraded".

Usage
-----
    from dpp_api.billing.active_preflight import run_billing_secrets_active_preflight

    # In startup_event():
    await run_billing_secrets_active_preflight()

    # In readyz handler (no network call):
    from dpp_api.billing.active_preflight import get_billing_preflight_status
    status = get_billing_preflight_status()  # {"paypal": "ok"} | errors (v1.0)
"""

from __future__ import annotations

import base64
import logging
import os
import uuid
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level cache (populated once at startup)
# ---------------------------------------------------------------------------

_preflight_result: dict[str, str] | None = None  # None = not yet run


def get_billing_preflight_status() -> dict[str, str]:
    """Return the cached billing preflight result (no network call).

    Phase 4 (v1.0 PayPal-only): returns {"paypal": "ok"|"err:..."}.
    Toss key is absent — a missing Toss result is not a failure.

    Returns:
        Dict with key "paypal", value "ok" or "err:<code>".
        If preflight has not run yet (e.g., disabled), returns {"status": "skipped"}.
    """
    if _preflight_result is None:
        return {"status": "skipped"}
    return dict(_preflight_result)


def _reset_preflight_cache() -> None:
    """Reset the module-level cache. Used in tests for isolation."""
    global _preflight_result
    _preflight_result = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_timeout() -> float:
    try:
        return float(os.getenv("DPP_BILLING_PREFLIGHT_TIMEOUT_SECONDS", "5"))
    except ValueError:
        return 5.0


def _is_required() -> bool:
    return os.getenv("DPP_BILLING_PREFLIGHT_REQUIRED", "1").strip() == "1"


def _paypal_base_url() -> str:
    env = os.getenv("PAYPAL_ENV", "sandbox").strip().lower()
    return (
        "https://api-m.sandbox.paypal.com"
        if env == "sandbox"
        else "https://api-m.paypal.com"
    )


# ---------------------------------------------------------------------------
# PayPal active check
# ---------------------------------------------------------------------------

async def _check_paypal(timeout: float) -> str:
    """POST /v1/oauth2/token with grant_type=client_credentials.

    Returns:
        "ok" on success (200 + access_token present).
        "err:<code>" on auth failure (401/403) or other errors.

    Raises:
        RuntimeError("BILLING_SECRET_PREFLIGHT_FAILED:paypal:...")
            if DPP_BILLING_PREFLIGHT_REQUIRED=1 and check fails.
    """
    client_id = os.getenv("PAYPAL_CLIENT_ID", "").strip()
    client_secret = os.getenv("PAYPAL_CLIENT_SECRET", "").strip()

    if not client_id or not client_secret:
        reason = "PAYPAL_CLIENT_ID_OR_SECRET_MISSING"
        logger.critical(
            "BILLING_PREFLIGHT_PAYPAL_MISSING_SECRETS",
            extra={"reason": reason},
        )
        if _is_required():
            raise RuntimeError(f"BILLING_SECRET_PREFLIGHT_FAILED:paypal:{reason}")
        return f"err:{reason}"

    credentials = f"{client_id}:{client_secret}"
    encoded = base64.b64encode(credentials.encode()).decode()
    url = f"{_paypal_base_url()}/v1/oauth2/token"

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(
                url,
                headers={
                    "Authorization": f"Basic {encoded}",
                    "Content-Type": "application/x-www-form-urlencoded",
                },
                data={"grant_type": "client_credentials"},
            )
    except (httpx.TimeoutException, httpx.ConnectError, httpx.RequestError) as exc:
        reason = f"NETWORK_ERROR:{type(exc).__name__}"
        logger.critical(
            "BILLING_PREFLIGHT_PAYPAL_NETWORK_ERROR",
            extra={"error_type": type(exc).__name__},
        )
        if _is_required():
            raise RuntimeError(f"BILLING_SECRET_PREFLIGHT_FAILED:paypal:{reason}") from exc
        return f"err:{reason}"

    if response.status_code == 200:
        try:
            body = response.json()
            if "access_token" in body:
                # token value is NOT logged
                logger.info(
                    "BILLING_PREFLIGHT_PAYPAL_OK",
                    extra={"status_code": 200},
                )
                return "ok"
            reason = "NO_ACCESS_TOKEN_IN_RESPONSE"
        except Exception:
            reason = "INVALID_JSON_RESPONSE"
    else:
        reason = f"HTTP_{response.status_code}"

    logger.critical(
        "BILLING_PREFLIGHT_PAYPAL_FAILED",
        extra={"status_code": response.status_code, "reason": reason},
    )
    if _is_required():
        raise RuntimeError(f"BILLING_SECRET_PREFLIGHT_FAILED:paypal:{reason}")
    return f"err:{reason}"


# ---------------------------------------------------------------------------
# Toss active check
# Phase 4: DORMANT — not called from run_billing_secrets_active_preflight().
# Preserved for Phase 4+ reactivation when Toss is re-enabled as a provider.
# To reactivate: add `result["toss"] = await _check_toss(timeout)` below.
# ---------------------------------------------------------------------------

async def _check_toss(timeout: float) -> str:  # noqa: DORMANT_PHASE4
    """GET /v1/payments/orders/{probe_id} — auth probe only.

    Uses a synthetic orderId that will never exist in the system.

    - 401/403 → authentication failure → FAIL
    - 404/400 → "order not found / bad request" → auth OK → PASS
    - 5xx / timeout → external service error → FAIL if REQUIRED=1

    Returns:
        "ok" or "err:<reason>".
    """
    secret_key = os.getenv("TOSS_SECRET_KEY", "").strip()

    if not secret_key:
        reason = "TOSS_SECRET_KEY_MISSING"
        logger.critical(
            "BILLING_PREFLIGHT_TOSS_MISSING_SECRET",
            extra={"reason": reason},
        )
        if _is_required():
            raise RuntimeError(f"BILLING_SECRET_PREFLIGHT_FAILED:toss:{reason}")
        return f"err:{reason}"

    credentials = f"{secret_key}:"
    encoded = base64.b64encode(credentials.encode()).decode()

    # Synthetic orderId — guaranteed non-existent
    probe_id = f"dpp_preflight_{uuid.uuid4().hex}"
    url = f"https://api.tosspayments.com/v1/payments/orders/{probe_id}"

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.get(
                url,
                headers={"Authorization": f"Basic {encoded}"},
            )
    except (httpx.TimeoutException, httpx.ConnectError, httpx.RequestError) as exc:
        reason = f"NETWORK_ERROR:{type(exc).__name__}"
        logger.critical(
            "BILLING_PREFLIGHT_TOSS_NETWORK_ERROR",
            extra={"error_type": type(exc).__name__},
        )
        if _is_required():
            raise RuntimeError(f"BILLING_SECRET_PREFLIGHT_FAILED:toss:{reason}") from exc
        return f"err:{reason}"

    status = response.status_code

    if status in (400, 404):
        # Expected: order not found → authentication was accepted
        try:
            body = response.json()
            err_code = body.get("code", "UNKNOWN")  # only code, never full body
        except Exception:
            err_code = "NO_JSON"
        logger.info(
            "BILLING_PREFLIGHT_TOSS_OK",
            extra={"status_code": status, "toss_error_code": err_code, "probe_id": probe_id},
        )
        return "ok"

    if status in (401, 403):
        reason = f"AUTH_FAILED:HTTP_{status}"
        logger.critical(
            "BILLING_PREFLIGHT_TOSS_AUTH_FAILED",
            extra={"status_code": status, "reason": reason},
        )
        if _is_required():
            raise RuntimeError(f"BILLING_SECRET_PREFLIGHT_FAILED:toss:{reason}")
        return f"err:{reason}"

    # 5xx or other unexpected status
    reason = f"HTTP_{status}"
    logger.critical(
        "BILLING_PREFLIGHT_TOSS_UNEXPECTED_STATUS",
        extra={"status_code": status, "reason": reason},
    )
    if _is_required():
        raise RuntimeError(f"BILLING_SECRET_PREFLIGHT_FAILED:toss:{reason}")
    return f"err:{reason}"


# ---------------------------------------------------------------------------
# Main entry point (called from startup_event once)
# ---------------------------------------------------------------------------

async def run_billing_secrets_active_preflight() -> dict[str, Any]:
    """Run PayPal active preflight check (PayPal-only for v1.0 beta).

    Called ONCE from startup_event(). Results cached in module state.
    Subsequent calls are no-ops (idempotent guard).

    Phase 4: Toss check is EXCLUDED from this path.
    A missing or invalid Toss secret does NOT block startup or readyz.

    Returns:
        {"paypal": "ok"|"err:..."}

    Raises:
        RuntimeError("BILLING_SECRET_PREFLIGHT_FAILED:paypal:...")
            if DPP_BILLING_PREFLIGHT_REQUIRED=1 and PayPal check fails.
    """
    global _preflight_result

    if _preflight_result is not None:
        # Idempotent: startup already ran
        return dict(_preflight_result)

    timeout = _get_timeout()
    result: dict[str, str] = {}

    # PayPal — launch-critical for v1.0 beta
    paypal_status = await _check_paypal(timeout)
    result["paypal"] = paypal_status

    # Toss — Phase 4: EXCLUDED (not launch-critical for v1.0 PayPal-only beta)
    # To re-enable Toss: result["toss"] = await _check_toss(timeout)

    _preflight_result = result

    if result.get("paypal") == "ok":
        logger.info(
            "BILLING_SECRET_PREFLIGHT_OK",
            extra={"providers": "paypal"},
        )
    else:
        failed = [k for k, v in result.items() if v != "ok"]
        logger.critical(
            "BILLING_SECRET_PREFLIGHT_DEGRADED",
            extra={"failed_providers": ",".join(failed)},
        )

    return dict(result)
