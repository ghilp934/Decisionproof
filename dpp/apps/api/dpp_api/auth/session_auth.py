"""Session authentication for token management endpoints.

Supabase JWT-based session auth for user-authenticated operations.

FLOW:
1. User logs in via POST /v1/auth/login -> receives JWT access_token
2. User calls token management endpoint with Authorization: Bearer <jwt>
3. Middleware validates JWT, extracts user_id, looks up tenant_id
4. Returns SessionAuthContext(user_id, tenant_id, role)

SECURITY:
- JWT signature verified by Supabase
- User-to-tenant mapping enforced via user_tenants table
- Only active user-tenant relationships allowed
"""

import logging
from typing import Optional

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from dpp_api.context import request_id_var
from dpp_api.db.models import UserTenant
from dpp_api.db.session import get_db
from dpp_api.schemas import ProblemDetail
from dpp_api.supabase_client import get_supabase_client

logger = logging.getLogger(__name__)

# HTTPBearer scheme for session JWT
session_security = HTTPBearer(auto_error=False, description="Supabase JWT Session Token")


class SessionAuthContext:
    """Session authentication context for user-authenticated requests."""

    def __init__(
        self,
        user_id: str,
        tenant_id: str,
        role: str = "member",
        email: Optional[str] = None,
    ):
        self.user_id = user_id
        self.tenant_id = tenant_id
        self.role = role
        self.email = email


def _create_session_problem(
    status_code: int,
    title: str,
    detail: str,
    request: Request,
) -> HTTPException:
    """Create RFC 9457 Problem Detail for session auth errors.

    Args:
        status_code: HTTP status code
        title: Problem title
        detail: Human-readable detail
        request: FastAPI request

    Returns:
        HTTPException with problem+json response
    """
    trace_id = request_id_var.get()

    problem = ProblemDetail(
        type=f"https://api.decisionproof.ai/problems/{title.lower().replace(' ', '-')}",
        title=title,
        status=status_code,
        detail=detail,
        instance=str(request.url.path),
        trace_id=trace_id,
    )

    return HTTPException(
        status_code=status_code,
        detail=problem.model_dump(exclude_none=True),
        headers={"WWW-Authenticate": "Bearer"},
    )


async def get_session_auth_context(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(session_security),
    db: Session = Depends(get_db),
) -> SessionAuthContext:
    """Get session authentication context from Supabase JWT.

    Validates JWT token and returns user_id + tenant_id.

    Args:
        request: FastAPI request
        credentials: HTTP Bearer credentials (JWT)
        db: Database session

    Returns:
        SessionAuthContext with user_id, tenant_id, role

    Raises:
        HTTPException: 401 if authentication fails (RFC 9457 Problem Detail)
    """
    # Check credentials present
    if not credentials:
        raise _create_session_problem(
            status_code=status.HTTP_401_UNAUTHORIZED,
            title="Unauthorized",
            detail="Missing Authorization header. Please log in first.",
            request=request,
        )

    jwt_token = credentials.credentials

    # Validate JWT with Supabase
    try:
        supabase = get_supabase_client()

        # Get user from JWT
        # Supabase client validates JWT signature and expiration
        user_response = supabase.auth.get_user(jwt_token)

        if not user_response or not user_response.user:
            raise _create_session_problem(
                status_code=status.HTTP_401_UNAUTHORIZED,
                title="Unauthorized",
                detail="Invalid or expired session token. Please log in again.",
                request=request,
            )

        user = user_response.user
        user_id = user.id
        email = user.email

        logger.info(
            "Session JWT validated",
            extra={
                "event": "session.jwt.validated",
                "user_id": user_id,
                "email": email,
            },
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"JWT validation failed: {e}", exc_info=True)
        raise _create_session_problem(
            status_code=status.HTTP_401_UNAUTHORIZED,
            title="Unauthorized",
            detail="Session validation failed. Please log in again.",
            request=request,
        )

    # Look up user's primary tenant
    user_tenant = (
        db.query(UserTenant)
        .filter(
            UserTenant.user_id == user_id,
            UserTenant.status == "active",
        )
        .order_by(
            # Prioritize owner role, then by creation date
            UserTenant.role.desc(),
            UserTenant.created_at.asc(),
        )
        .first()
    )

    if not user_tenant:
        # User has no active tenant - this should not happen in normal flow
        # but could occur if user is deleted from tenant
        logger.warning(
            "User has no active tenant",
            extra={
                "event": "session.no_tenant",
                "user_id": user_id,
                "email": email,
            },
        )

        raise _create_session_problem(
            status_code=status.HTTP_403_FORBIDDEN,
            title="No Active Tenant",
            detail="Your account is not associated with any active tenant. Please contact support.",
            request=request,
        )

    logger.info(
        "Session authentication successful",
        extra={
            "event": "session.auth.success",
            "user_id": user_id,
            "tenant_id": user_tenant.tenant_id,
            "role": user_tenant.role,
        },
    )

    return SessionAuthContext(
        user_id=user_id,
        tenant_id=user_tenant.tenant_id,
        role=user_tenant.role,
        email=email,
    )


def require_session_owner(
    resource_tenant_id: Optional[str],
    auth: SessionAuthContext = Depends(get_session_auth_context),
) -> SessionAuthContext:
    """Require that the authenticated session user owns the resource (BOLA defense).

    Implements "stealth 404" behavior: returns 404 instead of 403.

    Args:
        resource_tenant_id: Tenant ID of the resource (None if resource not found)
        auth: Authentication context from get_session_auth_context

    Returns:
        SessionAuthContext if authorized

    Raises:
        HTTPException: 404 if not authorized (stealth behavior)
    """
    if resource_tenant_id is None:
        # Resource not found
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Resource not found",
        )

    if resource_tenant_id != auth.tenant_id:
        # Not authorized, but return 404 (stealth BOLA defense)
        logger.warning(
            "BOLA attempt detected: session user from different tenant",
            extra={
                "event": "bola.detected",
                "user_tenant_id": auth.tenant_id,
                "resource_tenant_id": resource_tenant_id,
                "user_id": auth.user_id,
            },
        )

        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Resource not found",
        )

    return auth


def require_admin_role(
    auth: SessionAuthContext = Depends(get_session_auth_context),
) -> SessionAuthContext:
    """Require admin or owner role within tenant.

    Args:
        auth: Authentication context

    Returns:
        SessionAuthContext if authorized

    Raises:
        HTTPException: 403 if not admin/owner
    """
    if auth.role not in ("owner", "admin"):
        logger.warning(
            "Insufficient permissions: admin role required",
            extra={
                "event": "auth.insufficient_permissions",
                "user_id": auth.user_id,
                "tenant_id": auth.tenant_id,
                "role": auth.role,
            },
        )

        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin or owner role required for this operation",
        )

    return auth
