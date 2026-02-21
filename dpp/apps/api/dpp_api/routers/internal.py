"""Internal endpoints for operational testing.

SMTP Smoke Test: Trigger Supabase Auth emails for SES verification.

WARNING: These endpoints are NOT for public use.
- Protected by secret header
- Rate limited
- Only accessible during maintenance mode
"""

import logging
import os
import secrets
import time
from typing import Literal

import httpx
from fastapi import APIRouter, HTTPException, Header, status
from pydantic import BaseModel, EmailStr, Field

from dpp_api.context import request_id_var

router = APIRouter(prefix="/internal", tags=["internal"])
logger = logging.getLogger(__name__)


# ============================================================================
# Rate Limiter (In-Memory, Simple)
# ============================================================================

class SimpleRateLimiter:
    """In-memory rate limiter for internal endpoints.

    Limit: 1 request per minute per endpoint.
    """

    def __init__(self):
        self._last_call = {}  # {endpoint: timestamp}
        self._window_seconds = 60  # 1 minute

    def check_and_update(self, endpoint: str) -> bool:
        """Check if rate limit allows request and update state.

        Args:
            endpoint: Endpoint identifier

        Returns:
            True if allowed, False if rate limited
        """
        now = time.time()
        last_time = self._last_call.get(endpoint, 0)

        if now - last_time < self._window_seconds:
            # Rate limited
            return False

        # Allow and update
        self._last_call[endpoint] = now
        return True


# Global rate limiter instance
_rate_limiter = SimpleRateLimiter()


# ============================================================================
# Schemas
# ============================================================================

class SmokeEmailRequest(BaseModel):
    """Request for /internal/smoke/email."""

    recipient_email: EmailStr = Field(..., description="Test recipient email")
    mode: Literal["signup+resend", "recover", "resend_signup_only"] = Field(
        default="signup+resend",
        description="Email trigger mode",
    )
    redirect_base: str | None = Field(
        None,
        description="Base URL for redirect (optional, defaults to DP_API_BASE_URL)",
    )


class SupabaseHealthInfo(BaseModel):
    """Supabase Auth health info."""

    health_version: str
    health_ok: bool


class ActionResult(BaseModel):
    """Result of a single email trigger action."""

    name: str
    http_status: int
    ok: bool
    error: str | None = None


class SmokeEmailResponse(BaseModel):
    """Response for /internal/smoke/email."""

    supabase_auth: SupabaseHealthInfo
    actions: list[ActionResult]
    note: str


# ============================================================================
# Helpers
# ============================================================================

def _get_supabase_url() -> str:
    """Get Supabase URL from env."""
    url = os.getenv("SUPABASE_URL")
    if not url:
        raise RuntimeError("SUPABASE_URL not set")
    return url


def _get_supabase_api_key() -> str:
    """Get Supabase publishable key from env.

    Priority:
    1. SB_PUBLISHABLE_KEY (new standard)
    2. SUPABASE_ANON_KEY (legacy fallback)
    """
    key = os.getenv("SB_PUBLISHABLE_KEY")
    if key:
        return key

    key = os.getenv("SUPABASE_ANON_KEY")
    if key:
        return key

    raise RuntimeError(
        "Neither SB_PUBLISHABLE_KEY nor SUPABASE_ANON_KEY is set. "
        "Set SB_PUBLISHABLE_KEY (recommended) or SUPABASE_ANON_KEY (legacy)."
    )


def _get_internal_smoke_key() -> str:
    """Get internal smoke key from env."""
    key = os.getenv("DP_INTERNAL_SMOKE_KEY")
    if not key:
        raise RuntimeError("DP_INTERNAL_SMOKE_KEY not set")
    return key


def _get_redirect_url(redirect_base: str | None) -> str:
    """Get redirect URL for email confirmation.

    Args:
        redirect_base: Optional base URL from request

    Returns:
        Full redirect URL
    """
    base = redirect_base or os.getenv("DP_API_BASE_URL") or "http://localhost:8000"
    return f"{base}/v1/auth/confirmed"


async def _check_supabase_health() -> SupabaseHealthInfo:
    """Check Supabase Auth health.

    Returns:
        Health info with version

    Raises:
        HTTPException: If health check fails
    """
    supabase_url = _get_supabase_url()
    api_key = _get_supabase_api_key()

    health_url = f"{supabase_url}/auth/v1/health"

    async with httpx.AsyncClient() as client:
        try:
            response = await client.get(
                health_url,
                headers={"apikey": api_key},
                timeout=10.0,
            )

            if response.status_code == 200:
                data = response.json()
                version = data.get("version", "unknown")
                return SupabaseHealthInfo(health_version=version, health_ok=True)
            else:
                logger.error(
                    "Supabase health check failed",
                    extra={"status": response.status_code, "body": response.text[:200]},
                )
                return SupabaseHealthInfo(
                    health_version="unknown",
                    health_ok=False,
                )

        except Exception as e:
            logger.error(f"Supabase health check exception: {e}")
            return SupabaseHealthInfo(health_version="unknown", health_ok=False)


async def _trigger_signup(recipient_email: str, redirect_url: str) -> ActionResult:
    """Trigger Supabase Auth signup.

    Args:
        recipient_email: Recipient email
        redirect_url: Redirect URL for confirmation

    Returns:
        Action result
    """
    supabase_url = _get_supabase_url()
    api_key = _get_supabase_api_key()

    signup_url = f"{supabase_url}/auth/v1/signup"

    # Generate test password (deterministic but unique per call)
    test_password = f"Test{secrets.token_hex(8)}!"

    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(
                signup_url,
                headers={
                    "apikey": api_key,
                    "Content-Type": "application/json",
                },
                json={
                    "email": recipient_email,
                    "password": test_password,
                    "options": {
                        "email_redirect_to": redirect_url,
                    },
                },
                timeout=15.0,
            )

            # Supabase returns 200 or 201 on success
            ok = response.status_code in [200, 201]

            if not ok:
                logger.warning(
                    "Signup trigger failed",
                    extra={
                        "status": response.status_code,
                        "body": response.text[:200],
                    },
                )

            return ActionResult(
                name="signup",
                http_status=response.status_code,
                ok=ok,
                error=None if ok else response.text[:100],
            )

        except Exception as e:
            logger.error(f"Signup trigger exception: {e}")
            return ActionResult(
                name="signup",
                http_status=500,
                ok=False,
                error=str(e)[:100],
            )


async def _trigger_resend_signup(recipient_email: str) -> ActionResult:
    """Trigger Supabase Auth resend signup email.

    Args:
        recipient_email: Recipient email

    Returns:
        Action result
    """
    supabase_url = _get_supabase_url()
    api_key = _get_supabase_api_key()

    resend_url = f"{supabase_url}/auth/v1/resend"

    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(
                resend_url,
                headers={
                    "apikey": api_key,
                    "Content-Type": "application/json",
                },
                json={
                    "email": recipient_email,
                    "type": "signup",
                },
                timeout=15.0,
            )

            ok = response.status_code == 200

            if not ok:
                logger.warning(
                    "Resend signup trigger failed",
                    extra={
                        "status": response.status_code,
                        "body": response.text[:200],
                    },
                )

            return ActionResult(
                name="resend_signup",
                http_status=response.status_code,
                ok=ok,
                error=None if ok else response.text[:100],
            )

        except Exception as e:
            logger.error(f"Resend signup trigger exception: {e}")
            return ActionResult(
                name="resend_signup",
                http_status=500,
                ok=False,
                error=str(e)[:100],
            )


async def _trigger_recover(recipient_email: str, redirect_url: str) -> ActionResult:
    """Trigger Supabase Auth password recovery.

    Args:
        recipient_email: Recipient email
        redirect_url: Redirect URL for password reset

    Returns:
        Action result
    """
    supabase_url = _get_supabase_url()
    api_key = _get_supabase_api_key()

    recover_url = f"{supabase_url}/auth/v1/recover"

    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(
                recover_url,
                headers={
                    "apikey": api_key,
                    "Content-Type": "application/json",
                },
                json={
                    "email": recipient_email,
                    "options": {
                        "redirect_to": redirect_url,
                    },
                },
                timeout=15.0,
            )

            ok = response.status_code == 200

            if not ok:
                logger.warning(
                    "Recover trigger failed",
                    extra={
                        "status": response.status_code,
                        "body": response.text[:200],
                    },
                )

            return ActionResult(
                name="recover",
                http_status=response.status_code,
                ok=ok,
                error=None if ok else response.text[:100],
            )

        except Exception as e:
            logger.error(f"Recover trigger exception: {e}")
            return ActionResult(
                name="recover",
                http_status=500,
                ok=False,
                error=str(e)[:100],
            )


# ============================================================================
# Endpoint
# ============================================================================

@router.post("/smoke/email", response_model=SmokeEmailResponse)
async def smoke_email(
    request: SmokeEmailRequest,
    x_internal_smoke_key: str = Header(..., alias="X-Internal-Smoke-Key"),
) -> SmokeEmailResponse:
    """Trigger Supabase Auth emails for SMTP smoke testing.

    INTERNAL USE ONLY. Protected by secret header.

    Purpose:
    - Trigger real Auth emails via Supabase Auth API
    - Verify AWS SES SMTP integration
    - Measure SES sending stats delta

    Flow:
    1. Check Supabase Auth health
    2. Trigger email(s) based on mode:
       - signup+resend: signup + resend_signup
       - recover: password recovery
       - resend_signup_only: only resend
    3. Return action results

    Note: This does NOT verify inbox delivery.
    Use local smoke script to check SES stats delta.

    Args:
        request: Smoke email request
        x_internal_smoke_key: Secret header (must match DP_INTERNAL_SMOKE_KEY)

    Returns:
        Smoke email response with action results

    Raises:
        HTTPException 401: Invalid secret header
        HTTPException 429: Rate limit exceeded
        HTTPException 500: Internal error
    """
    # Security: Verify secret header (constant-time comparison)
    try:
        expected_key = _get_internal_smoke_key()
    except RuntimeError as e:
        logger.error(f"Internal smoke key not configured: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal smoke key not configured",
        )

    if not secrets.compare_digest(x_internal_smoke_key, expected_key):
        request_id = request_id_var.get()
        logger.warning(
            "Invalid smoke key attempt",
            extra={"request_id": request_id},
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid X-Internal-Smoke-Key",
            headers={"WWW-Authenticate": "Header"},
        )

    # Rate limiting (1 req/min)
    if not _rate_limiter.check_and_update("/internal/smoke/email"):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Rate limit: 1 request per minute",
            headers={"Retry-After": "60"},
        )

    # Log smoke test (without exposing email in production logs)
    logger.info(
        "smoke.email.triggered",
        extra={
            "mode": request.mode,
            "has_recipient": bool(request.recipient_email),
        },
    )

    # Step 1: Supabase health check
    health_info = await _check_supabase_health()

    if not health_info.health_ok:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Supabase Auth health check failed",
        )

    # Step 2: Trigger email(s) based on mode
    actions: list[ActionResult] = []
    redirect_url = _get_redirect_url(request.redirect_base)

    if request.mode == "signup+resend":
        # Trigger signup first, then resend
        signup_result = await _trigger_signup(request.recipient_email, redirect_url)
        actions.append(signup_result)

        # Brief delay before resend (avoid race condition)
        await httpx.AsyncClient().aclose()  # Close any lingering connections
        import asyncio
        await asyncio.sleep(1.0)

        resend_result = await _trigger_resend_signup(request.recipient_email)
        actions.append(resend_result)

    elif request.mode == "recover":
        # Trigger password recovery
        recover_result = await _trigger_recover(request.recipient_email, redirect_url)
        actions.append(recover_result)

    elif request.mode == "resend_signup_only":
        # Only trigger resend
        resend_result = await _trigger_resend_signup(request.recipient_email)
        actions.append(resend_result)

    # Step 3: Return results
    return SmokeEmailResponse(
        supabase_auth=health_info,
        actions=actions,
        note="SES metrics are 15-min buckets; run the local smoke script to confirm send delta.",
    )
