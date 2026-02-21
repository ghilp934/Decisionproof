"""API Token authentication middleware for P0-3.

Opaque Bearer token authentication with rotation grace period.

SECURITY:
- Tokens must be in Authorization: Bearer <token> header
- Uniform 401 responses (no information leakage)
- Supports rotating tokens with grace period
- Updates last_used_at with rate limiting
- Privacy-preserving request logging
"""

import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from dpp_api.auth.token_lifecycle import hash_for_logging, hash_token
from dpp_api.context import request_id_var, tenant_id_var
from dpp_api.db.models import APIToken, AuthRequestLog
from dpp_api.db.session import get_db
from dpp_api.schemas import ProblemDetail

logger = logging.getLogger(__name__)

# HTTPBearer scheme for OpenAPI docs
token_security = HTTPBearer(auto_error=False, description="API Token (opaque Bearer)")


class TokenAuthContext:
    """Authentication context for token-authenticated requests."""

    def __init__(
        self,
        tenant_id: str,
        token_id: str,
        scopes: Optional[list] = None,
    ):
        self.tenant_id = tenant_id
        self.token_id = token_id
        self.scopes = scopes or []


def _create_auth_problem(
    status_code: int,
    title: str,
    detail: str,
    request: Request,
) -> HTTPException:
    """Create RFC 9457 Problem Detail for auth errors.

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


async def get_token_auth_context(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(token_security),
    db: Session = Depends(get_db),
) -> TokenAuthContext:
    """Get authentication context from API token.

    Validates opaque Bearer token and returns principal with tenant_id.

    Args:
        request: FastAPI request
        credentials: HTTP Bearer credentials
        db: Database session

    Returns:
        TokenAuthContext with tenant_id, token_id, scopes

    Raises:
        HTTPException: 401 if authentication fails (RFC 9457 Problem Detail)
    """
    # Check credentials present
    if not credentials:
        raise _create_auth_problem(
            status_code=status.HTTP_401_UNAUTHORIZED,
            title="Unauthorized",
            detail="Missing Authorization header. Provide: Authorization: Bearer <token>",
            request=request,
        )

    raw_token = credentials.credentials

    # Compute token hash
    try:
        token_hash_value = hash_token(raw_token, pepper_version=1)
    except Exception as e:
        logger.error(f"Token hashing failed: {e}", exc_info=True)
        raise _create_auth_problem(
            status_code=status.HTTP_401_UNAUTHORIZED,
            title="Unauthorized",
            detail="Invalid token format",
            request=request,
        )

    # Lookup token in database
    # Status: active OR rotating (grace period)
    # Not expired (expires_at is NULL or future)
    # Not revoked (revoked_at is NULL)
    token = (
        db.query(APIToken)
        .filter(
            APIToken.token_hash == token_hash_value,
            APIToken.status.in_(["active", "rotating"]),
            APIToken.revoked_at.is_(None),
        )
        .first()
    )

    if not token:
        # Stealth 401: Don't reveal whether token exists
        logger.warning(
            "Token authentication failed: token not found or revoked",
            extra={
                "event": "token.auth.failed",
                "reason": "not_found_or_revoked",
                "trace_id": request_id_var.get(),
            },
        )

        # Log failed auth attempt (privacy-preserving)
        _log_auth_request(
            db=db,
            request=request,
            token_id=None,
            tenant_id=None,
            status_code=401,
        )

        raise _create_auth_problem(
            status_code=status.HTTP_401_UNAUTHORIZED,
            title="Unauthorized",
            detail="Invalid or revoked token",
            request=request,
        )

    # Check expiration
    if token.expires_at is not None:
        now = datetime.now(timezone.utc)
        if now >= token.expires_at:
            logger.warning(
                "Token authentication failed: token expired",
                extra={
                    "event": "token.auth.failed",
                    "reason": "expired",
                    "token_id": token.id,
                    "expires_at": token.expires_at.isoformat(),
                },
            )

            # Update status to expired (idempotent)
            if token.status != "expired":
                token.status = "expired"
                db.commit()

            # Log failed auth attempt
            _log_auth_request(
                db=db,
                request=request,
                token_id=token.id,
                tenant_id=token.tenant_id,
                status_code=401,
            )

            raise _create_auth_problem(
                status_code=status.HTTP_401_UNAUTHORIZED,
                title="Token Expired",
                detail="Token has expired. Rotate or create a new token.",
                request=request,
            )

    # Authentication successful
    logger.info(
        "Token authentication successful",
        extra={
            "event": "token.auth.success",
            "token_id": token.id,
            "tenant_id": token.tenant_id,
            "status": token.status,
        },
    )

    # Update last_used_at with rate limiting (once per hour)
    # Prevents write amplification from high-traffic tokens
    _update_last_used_if_needed(db, token)

    # Log successful auth request
    _log_auth_request(
        db=db,
        request=request,
        token_id=token.id,
        tenant_id=token.tenant_id,
        status_code=200,
    )

    # Set tenant_id in context for observability
    tenant_id_var.set(token.tenant_id)

    # Return auth context
    return TokenAuthContext(
        tenant_id=token.tenant_id,
        token_id=token.id,
        scopes=token.scopes or [],
    )


def _update_last_used_if_needed(db: Session, token: APIToken) -> None:
    """Update last_used_at if older than 1 hour (rate limiting).

    Prevents write amplification from high-frequency API calls.

    Args:
        db: Database session
        token: API token object
    """
    now = datetime.now(timezone.utc)

    # Only update if:
    # - last_used_at is NULL (never used)
    # - last_used_at is older than 1 hour
    if token.last_used_at is None:
        token.last_used_at = now
        db.commit()
        return

    seconds_since_last_update = (now - token.last_used_at).total_seconds()
    rate_limit_seconds = 3600  # 1 hour

    if seconds_since_last_update >= rate_limit_seconds:
        token.last_used_at = now
        db.commit()


def _log_auth_request(
    db: Session,
    request: Request,
    token_id: Optional[str],
    tenant_id: Optional[str],
    status_code: int,
) -> None:
    """Log authentication request to auth_request_log (privacy-preserving).

    Args:
        db: Database session
        request: FastAPI request
        token_id: Token ID (None if auth failed before token lookup)
        tenant_id: Tenant ID (None if auth failed)
        status_code: HTTP status code (200 for success, 401 for failure)
    """
    try:
        # Privacy-preserving hashes
        client_ip = request.client.host if request.client else None
        user_agent = request.headers.get("User-Agent")

        ip_hash = hash_for_logging(client_ip) if client_ip else None
        ua_hash = hash_for_logging(user_agent) if user_agent else None

        # Create log entry
        log_entry = AuthRequestLog(
            id=str(uuid.uuid4()),
            token_id=token_id,
            tenant_id=tenant_id,
            route=str(request.url.path),
            method=request.method,
            status_code=status_code,
            ip_hash=ip_hash,
            ua_hash=ua_hash,
            trace_id=request_id_var.get(),
        )

        db.add(log_entry)
        db.commit()

    except Exception as e:
        # Fail-open: don't block auth if logging fails
        logger.warning(f"Failed to log auth request: {e}", exc_info=True)
        db.rollback()


def require_token_owner(
    resource_tenant_id: Optional[str],
    auth: TokenAuthContext = Depends(get_token_auth_context),
) -> TokenAuthContext:
    """Require that the authenticated token owns the resource (BOLA defense).

    Implements "stealth 404" behavior: returns 404 instead of 403
    to avoid leaking information about resource existence.

    Args:
        resource_tenant_id: Tenant ID of the resource (None if resource not found)
        auth: Authentication context from get_token_auth_context

    Returns:
        TokenAuthContext if authorized

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
            "BOLA attempt detected: token from different tenant",
            extra={
                "event": "bola.detected",
                "token_tenant_id": auth.tenant_id,
                "resource_tenant_id": resource_tenant_id,
                "token_id": auth.token_id,
            },
        )

        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Resource not found",
        )

    return auth
