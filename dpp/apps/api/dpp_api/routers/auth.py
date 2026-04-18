"""Auth endpoints — hotfix: Google OAuth only.

Email/password signup and login are DISABLED (SES production access unavailable).
All new auth uses Google OAuth via Supabase Auth (client-side PKCE flow).

Active endpoints:
- POST /v1/auth/provision   — idempotent tenant provisioning for OAuth users
- GET  /v1/auth/confirmed   — email confirmation redirect (legacy, retained)
- GET  /v1/auth/recovery    — password recovery placeholder (legacy, retained)

Disabled (returns 503):
- POST /v1/auth/signup
- POST /v1/auth/login

SECURITY:
- Signup/login return 503 with explicit "use Google OAuth" message
- provision validates Supabase JWT before any DB write
- No user-controlled redirects
"""

import logging
import os
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from dpp_api.context import request_id_var
from dpp_api.db.models import Tenant, UserTenant
from dpp_api.db.session import get_db
from dpp_api.schemas import ProblemDetail
from dpp_api.supabase_client import get_supabase_client

router = APIRouter(prefix="/v1/auth", tags=["auth"])
logger = logging.getLogger(__name__)

_bearer = HTTPBearer(auto_error=False)


# ---------------------------------------------------------------------------
# POST /v1/auth/signup  — DISABLED
# ---------------------------------------------------------------------------


@router.post("/signup", status_code=status.HTTP_503_SERVICE_UNAVAILABLE)
async def signup() -> JSONResponse:
    """Email signup temporarily unavailable — use Google OAuth."""
    request_id = request_id_var.get()
    logger.warning("auth.signup.disabled_path_hit")
    problem = ProblemDetail(
        type="https://api.decisionproof.io.kr/problems/auth-unavailable",
        title="Email Signup Unavailable",
        status=503,
        detail=(
            "Email signup is temporarily unavailable. "
            "Please sign in with Google at decisionproof.io.kr/login.html."
        ),
        instance=f"urn:decisionproof:trace:{request_id}",
    )
    return JSONResponse(
        status_code=503,
        content=problem.model_dump(exclude_none=True),
        media_type="application/problem+json",
    )


# ---------------------------------------------------------------------------
# POST /v1/auth/login  — DISABLED
# ---------------------------------------------------------------------------


@router.post("/login", status_code=status.HTTP_503_SERVICE_UNAVAILABLE)
async def login() -> JSONResponse:
    """Email/password login temporarily unavailable — use Google OAuth."""
    request_id = request_id_var.get()
    logger.warning("auth.login.disabled_path_hit")
    problem = ProblemDetail(
        type="https://api.decisionproof.io.kr/problems/auth-unavailable",
        title="Email Login Unavailable",
        status=503,
        detail=(
            "Email/password login is temporarily unavailable. "
            "Please sign in with Google at decisionproof.io.kr/login.html."
        ),
        instance=f"urn:decisionproof:trace:{request_id}",
    )
    return JSONResponse(
        status_code=503,
        content=problem.model_dump(exclude_none=True),
        media_type="application/problem+json",
    )


# ---------------------------------------------------------------------------
# POST /v1/auth/provision  — idempotent tenant provisioning for OAuth users
# ---------------------------------------------------------------------------


@router.post("/provision")
async def provision_tenant(
    credentials: HTTPAuthorizationCredentials = Depends(_bearer),
    db: Session = Depends(get_db),
) -> JSONResponse:
    """Provision personal tenant for a freshly OAuth-authenticated user.

    Called by the client immediately after the OAuth callback saves the session.
    Idempotent — safe to call repeatedly. If a tenant already exists, returns
    it. If not, creates tenant + owner mapping and returns 201.

    Validates Supabase JWT directly (no tenant required yet).
    """
    if not credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing Authorization header.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    jwt_token = credentials.credentials

    # Validate JWT with Supabase
    try:
        supabase = get_supabase_client()
        user_response = supabase.auth.get_user(jwt_token)
        if not user_response or not user_response.user:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or expired token.",
                headers={"WWW-Authenticate": "Bearer"},
            )
        user = user_response.user
        user_id = user.id
        user_email = user.email or ""
    except HTTPException:
        raise
    except Exception as e:
        logger.error("auth.provision.jwt_validation_failed", extra={"error": str(e)})
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token validation failed.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Check for existing active tenant (idempotent path)
    existing = (
        db.query(UserTenant)
        .filter(UserTenant.user_id == user_id, UserTenant.status == "active")
        .first()
    )

    if existing:
        logger.info(
            "auth.provision.existing_tenant",
            extra={"user_id": user_id, "tenant_id": existing.tenant_id},
        )
        return JSONResponse(
            status_code=200,
            content={"tenant_id": existing.tenant_id, "created": False},
        )

    # Create new personal tenant
    try:
        tenant_id = f"user_{user_id[:8]}"
        counter = 0
        while db.query(Tenant).filter(Tenant.tenant_id == tenant_id).first():
            counter += 1
            tenant_id = f"user_{user_id[:8]}_{counter}"

        new_tenant = Tenant(
            tenant_id=tenant_id,
            display_name=user_email,
            status="ACTIVE",
        )
        db.add(new_tenant)

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
            "auth.provision.tenant_created",
            extra={"user_id": user_id, "tenant_id": tenant_id, "email": user_email},
        )
        return JSONResponse(
            status_code=201,
            content={"tenant_id": tenant_id, "created": True},
        )

    except Exception as e:
        db.rollback()
        logger.error(
            "auth.provision.tenant_creation_failed",
            extra={"user_id": user_id, "error": str(e)},
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to provision tenant. Please try again or contact support.",
        )


# ---------------------------------------------------------------------------
# GET /v1/auth/confirmed  — email confirmation redirect (retained, legacy)
# ---------------------------------------------------------------------------


@router.get("/confirmed")
async def email_confirmed() -> RedirectResponse:
    """Email confirmation redirect (retained for any in-flight links).

    Redirects to login page. No new email confirmations are sent,
    but existing confirmation links may still arrive.
    """
    site_base = os.getenv("CHECKOUT_SITE_BASE_URL", "https://decisionproof.io.kr")
    redirect_url = f"{site_base}/login.html?confirmed=1"
    logger.info("auth.email_confirmed.redirect", extra={"redirect_to": redirect_url})
    return RedirectResponse(url=redirect_url, status_code=302)


# ---------------------------------------------------------------------------
# GET /v1/auth/recovery  — password recovery placeholder (retained)
# ---------------------------------------------------------------------------


@router.get("/recovery", response_class=HTMLResponse)
async def password_recovery() -> HTMLResponse:
    """Password recovery landing page — placeholder, not yet active."""
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
            h1 { color: #333; font-size: 28px; margin-bottom: 16px; }
            p { color: #666; font-size: 16px; line-height: 1.6; margin-bottom: 24px; }
            .footer { color: #999; font-size: 14px; margin-top: 32px; }
        </style>
    </head>
    <body>
        <div class="container">
            <div style="font-size:64px; margin-bottom:20px;">🔑</div>
            <h1>Password Recovery</h1>
            <p>Password reset is not available during the current beta period.</p>
            <p>Please sign in with Google or contact support.</p>
            <div class="footer"><p>Decisionproof API Platform</p></div>
        </div>
    </body>
    </html>
    """
    logger.info("auth.password_recovery.view")
    return HTMLResponse(content=html_content, status_code=200)
