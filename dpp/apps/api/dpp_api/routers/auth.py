"""Auth endpoints for email onboarding.

Phase 2: Supabase Auth + AWS SES SMTP integration.

Endpoints:
- POST /v1/auth/signup: Email signup with confirmation email
- POST /v1/auth/login: Email login (returns JWT session)
- GET /v1/auth/confirmed: Email confirmation landing page (HTML)
- GET /v1/auth/recovery: Password recovery landing page (HTML, future)

SECURITY:
- redirectTo URLs are FORCED to API_BASE_URL/v1/auth/confirmed
- No user-controlled redirects (prevents phishing)
- Rate limiting on signup/login
- Passwords never logged
"""

import logging
import os
import uuid

from fastapi import APIRouter, Depends, HTTPException, Response, status
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy.orm import Session

from dpp_api.context import request_id_var
from dpp_api.db.models import Tenant, UserTenant
from dpp_api.db.session import get_db
from dpp_api.schemas import AuthResponse, LoginRequest, ProblemDetail, SignupRequest
from dpp_api.supabase_client import get_supabase_client

router = APIRouter(prefix="/v1/auth", tags=["auth"])
logger = logging.getLogger(__name__)


def _get_api_base_url() -> str:
    """Get API base URL from environment.

    Returns:
        str: API base URL (e.g., https://xxxx.execute-api.ap-northeast-2.amazonaws.com)
    """
    url = os.getenv("API_BASE_URL")
    if not url:
        # Fallback to localhost for development
        return "http://localhost:8000"
    return url


def _get_redirect_url() -> str:
    """Get confirmation redirect URL.

    Returns:
        str: Full redirect URL (API_BASE_URL/v1/auth/confirmed)
    """
    base_url = _get_api_base_url()
    return f"{base_url}/v1/auth/confirmed"


@router.post("/signup", status_code=status.HTTP_202_ACCEPTED, response_model=AuthResponse)
async def signup(request: SignupRequest, db: Session = Depends(get_db)) -> AuthResponse:
    """Register new user with email/password.

    Phase 2: Supabase Auth + AWS SES SMTP integration.
    P0-3.1: Auto-create personal tenant and owner mapping on signup.

    Flow:
    1. User calls this endpoint with email/password
    2. Supabase creates user (email_confirmed=false)
    3. DPP creates personal tenant for user
    4. DPP creates user-tenant mapping (role=owner)
    5. Supabase sends confirmation email via AWS SES SMTP
    6. Email contains link to /v1/auth/confirmed
    7. User clicks link, email gets confirmed
    8. User can then login

    Returns:
        202 Accepted with message to check email

    Raises:
        HTTPException 400: Invalid email/password format (Pydantic validation)
        HTTPException 409: Email already registered
        HTTPException 500: Supabase error
    """
    try:
        supabase = get_supabase_client()

        # Force redirect URL to prevent phishing
        redirect_url = _get_redirect_url()

        # Call Supabase Auth signup
        # NOTE: Do NOT log password
        logger.info(
            "auth.signup.attempt",
            extra={
                "email": request.email,
                "redirect_to": redirect_url,
            },
        )

        response = supabase.auth.sign_up(
            {
                "email": request.email,
                "password": request.password,
                "options": {
                    "email_redirect_to": redirect_url,
                },
            }
        )

        # Check if signup succeeded
        if not response.user:
            # This shouldn't happen, but handle gracefully
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Signup failed: No user returned from Supabase",
            )

        user_id = response.user.id
        user_email = response.user.email or request.email

        # P0-3.1: Auto-create personal tenant and user-tenant mapping
        try:
            # Generate deterministic tenant_id from user_id
            tenant_id = f"user_{user_id[:8]}"

            # Ensure unique tenant_id (collision protection)
            counter = 0
            while db.query(Tenant).filter(Tenant.tenant_id == tenant_id).first():
                counter += 1
                tenant_id = f"user_{user_id[:8]}_{counter}"

            # Create personal tenant
            new_tenant = Tenant(
                tenant_id=tenant_id,
                display_name=user_email,
                status="ACTIVE",
            )
            db.add(new_tenant)

            # Create user-tenant mapping as owner
            user_tenant = UserTenant(
                id=str(uuid.uuid4()),
                user_id=user_id,
                tenant_id=tenant_id,
                role="owner",
                status="active",
            )
            db.add(user_tenant)

            db.commit()

            logger.info(
                "auth.signup.tenant_created",
                extra={
                    "user_id": user_id,
                    "tenant_id": tenant_id,
                    "role": "owner",
                },
            )

        except Exception as tenant_error:
            # Rollback tenant creation, but user is already created in Supabase
            db.rollback()
            logger.error(
                "auth.signup.tenant_creation_failed",
                extra={
                    "user_id": user_id,
                    "error": str(tenant_error),
                },
            )
            # Don't fail the whole signup - user can still login, tenant can be created later
            # This is a degraded state, but better than blocking signup

        logger.info(
            "auth.signup.success",
            extra={
                "user_id": user_id,
                "email": user_email,
                "email_confirmed": response.user.email_confirmed_at is not None,
            },
        )

        return AuthResponse(
            user_id=user_id,
            email=user_email,
            email_confirmed=response.user.email_confirmed_at is not None,
            message="Check your email to confirm your account",
        )

    except HTTPException:
        raise
    except Exception as e:
        # Log error without exposing sensitive details
        logger.error(
            "auth.signup.error",
            extra={
                "error": str(e),
                "error_type": type(e).__name__,
            },
        )

        # Check for common Supabase errors
        error_msg = str(e).lower()
        if "already registered" in error_msg or "already exists" in error_msg:
            request_id = request_id_var.get()
            problem = ProblemDetail(
                type="https://api.decisionproof.ai/problems/auth-conflict",
                title="Email Already Registered",
                status=409,
                detail="This email is already registered. Please login instead.",
                instance=f"urn:decisionproof:trace:{request_id}",
            )
            return JSONResponse(
                status_code=409,
                content=problem.model_dump(exclude_none=True),
                media_type="application/problem+json",
            )

        # Generic error
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Signup failed: {str(e)[:100]}",
        )


@router.post("/login", status_code=status.HTTP_200_OK, response_model=AuthResponse)
async def login(request: LoginRequest) -> AuthResponse:
    """Login with email/password.

    Phase 2: Returns JWT session tokens for authenticated requests.

    Returns:
        200 OK with access_token and refresh_token

    Raises:
        HTTPException 400: Invalid email/password format (Pydantic validation)
        HTTPException 401: Invalid credentials or email not confirmed
        HTTPException 500: Supabase error
    """
    try:
        supabase = get_supabase_client()

        # Call Supabase Auth login
        # NOTE: Do NOT log password
        logger.info(
            "auth.login.attempt",
            extra={
                "email": request.email,
            },
        )

        response = supabase.auth.sign_in_with_password(
            {
                "email": request.email,
                "password": request.password,
            }
        )

        # Check if login succeeded
        if not response.user or not response.session:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid credentials",
                headers={"WWW-Authenticate": "Bearer"},
            )

        logger.info(
            "auth.login.success",
            extra={
                "user_id": response.user.id,
                "email": response.user.email,
            },
        )

        return AuthResponse(
            user_id=response.user.id,
            email=response.user.email or request.email,
            email_confirmed=response.user.email_confirmed_at is not None,
            access_token=response.session.access_token,
            refresh_token=response.session.refresh_token,
        )

    except HTTPException:
        raise
    except Exception as e:
        # Log error without exposing sensitive details
        logger.error(
            "auth.login.error",
            extra={
                "error": str(e),
                "error_type": type(e).__name__,
            },
        )

        # Check for common Supabase errors
        error_msg = str(e).lower()
        if "invalid" in error_msg or "wrong" in error_msg or "not found" in error_msg:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid credentials",
                headers={"WWW-Authenticate": "Bearer"},
            )

        if "not confirmed" in error_msg or "email not verified" in error_msg:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Email not confirmed. Please check your email.",
                headers={"WWW-Authenticate": "Bearer"},
            )

        # Generic error
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Login failed: {str(e)[:100]}",
        )


@router.get("/confirmed", response_class=HTMLResponse)
async def email_confirmed() -> HTMLResponse:
    """Email confirmation landing page.

    Phase 2: Simple HTML page shown after user clicks email confirmation link.

    This is NOT a SPA - just a static message telling the user they can close the tab.

    Security:
    - Query string parameters (token, etc.) are NOT logged
    - No sensitive information displayed
    - No redirects (prevents phishing)

    Returns:
        200 OK with HTML content
    """
    html_content = """
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Email Confirmed - Decisionproof</title>
        <style>
            body {
                font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
                background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                display: flex;
                justify-content: center;
                align-items: center;
                height: 100vh;
                margin: 0;
                padding: 20px;
            }
            .container {
                background: white;
                border-radius: 12px;
                box-shadow: 0 10px 40px rgba(0, 0, 0, 0.2);
                padding: 40px;
                max-width: 500px;
                text-align: center;
            }
            .icon {
                font-size: 64px;
                margin-bottom: 20px;
            }
            h1 {
                color: #333;
                font-size: 28px;
                margin-bottom: 16px;
            }
            p {
                color: #666;
                font-size: 16px;
                line-height: 1.6;
                margin-bottom: 24px;
            }
            .footer {
                color: #999;
                font-size: 14px;
                margin-top: 32px;
            }
        </style>
    </head>
    <body>
        <div class="container">
            <div class="icon">✅</div>
            <h1>Email Confirmed</h1>
            <p>Your email address has been successfully confirmed.</p>
            <p>You can now close this tab and return to your application to log in.</p>
            <div class="footer">
                <p>Decisionproof API Platform</p>
            </div>
        </div>
    </body>
    </html>
    """

    # Log confirmation (without query params)
    logger.info("auth.email_confirmed.view")

    return HTMLResponse(content=html_content, status_code=200)


@router.get("/recovery", response_class=HTMLResponse)
async def password_recovery() -> HTMLResponse:
    """Password recovery landing page.

    Phase 2: Placeholder for future password reset functionality.
    Currently shows same message as /confirmed.

    Phase 3 will implement actual password reset flow.

    Returns:
        200 OK with HTML content
    """
    html_content = """
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Password Recovery - Decisionproof</title>
        <style>
            body {
                font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
                background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                display: flex;
                justify-content: center;
                align-items: center;
                height: 100vh;
                margin: 0;
                padding: 20px;
            }
            .container {
                background: white;
                border-radius: 12px;
                box-shadow: 0 10px 40px rgba(0, 0, 0, 0.2);
                padding: 40px;
                max-width: 500px;
                text-align: center;
            }
            .icon {
                font-size: 64px;
                margin-bottom: 20px;
            }
            h1 {
                color: #333;
                font-size: 28px;
                margin-bottom: 16px;
            }
            p {
                color: #666;
                font-size: 16px;
                line-height: 1.6;
                margin-bottom: 24px;
            }
            .footer {
                color: #999;
                font-size: 14px;
                margin-top: 32px;
            }
        </style>
    </head>
    <body>
        <div class="container">
            <div class="icon">🔑</div>
            <h1>Password Recovery</h1>
            <p>Password reset functionality will be available in Phase 3.</p>
            <p>You can close this tab and contact support if you need assistance.</p>
            <div class="footer">
                <p>Decisionproof API Platform</p>
            </div>
        </div>
    </body>
    </html>
    """

    # Log recovery view (without query params)
    logger.info("auth.password_recovery.view")

    return HTMLResponse(content=html_content, status_code=200)
