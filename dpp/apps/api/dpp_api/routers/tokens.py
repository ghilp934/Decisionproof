"""Token management endpoints for P0-3.

Opaque Bearer token lifecycle: create, list, rotate, revoke, revoke-all.

AUTHENTICATION:
- Session-based: Supabase JWT (POST /v1/auth/login)
- User must be logged in with valid session
- Automatic tenant resolution from user_id
- Admin/owner role required for management operations

SECURITY:
- Display-once: Raw tokens returned only at issuance/rotation
- BOLA defense: Enforced tenant boundary on all operations
- Quota limits: Max tokens per tenant (default 5)
- Audit logging: All token lifecycle events logged
"""

import logging
import os
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from dpp_api.auth.session_auth import SessionAuthContext, get_session_auth_context, require_admin_role
from dpp_api.auth.token_lifecycle import generate_token, hash_token
from dpp_api.context import request_id_var
from dpp_api.db.models import APIToken, Tenant, TokenEvent
from dpp_api.db.session import get_db
from dpp_api.schemas import (
    TokenCreateRequest,
    TokenCreateResponse,
    TokenListItem,
    TokenListResponse,
    TokenRevokeAllResponse,
    TokenRevokeResponse,
    TokenRotateResponse,
)

router = APIRouter(prefix="/v1/tokens", tags=["tokens"])
logger = logging.getLogger(__name__)

# Configuration
MAX_TOKENS_PER_TENANT = int(os.getenv("MAX_TOKENS_PER_TENANT", "5"))
ROTATION_GRACE_MINUTES = int(os.getenv("TOKEN_ROTATION_GRACE_MINUTES", "10"))


# ============================================================================
# Token Management Endpoints (Session Auth Required)
# ============================================================================


@router.post("", status_code=status.HTTP_201_CREATED, response_model=TokenCreateResponse)
async def create_token(
    request: TokenCreateRequest,
    auth: SessionAuthContext = Depends(require_admin_role),
    db: Session = Depends(get_db),
) -> TokenCreateResponse:
    """Create new API token.

    Returns raw token ONCE (never stored, never returned again).

    Requires: Admin or owner role in tenant.

    Args:
        request: Token creation request
        auth: Session authentication context (user_id, tenant_id, role)
        db: Database session

    Returns:
        TokenCreateResponse with raw token (display once)

    Raises:
        HTTPException 401: Not authenticated (no session)
        HTTPException 403: Not admin/owner, or quota exceeded
        HTTPException 404: Tenant not found
    """
    tenant_id = auth.tenant_id
    user_id = auth.user_id
    # Verify tenant exists
    tenant = db.query(Tenant).filter(Tenant.tenant_id == tenant_id).first()
    if not tenant:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Tenant not found: {tenant_id}",
        )

    # Check quota: max active + rotating tokens per tenant
    active_count = (
        db.query(APIToken)
        .filter(
            APIToken.tenant_id == tenant_id,
            APIToken.status.in_(["active", "rotating"]),
        )
        .count()
    )

    if active_count >= MAX_TOKENS_PER_TENANT:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Token quota exceeded: max {MAX_TOKENS_PER_TENANT} active tokens per tenant",
        )

    # Generate token
    prefix = "dp_live"  # TODO: Support dp_test for sandbox
    raw_token, last4 = generate_token(prefix)

    # Hash token (never store raw token)
    token_hash = hash_token(raw_token, pepper_version=1)

    # Calculate expiration
    expires_at = None
    if request.expires_in_days:
        expires_at = datetime.now(timezone.utc) + timedelta(days=request.expires_in_days)

    # Create token record
    token_id = str(uuid.uuid4())
    api_token = APIToken(
        id=token_id,
        tenant_id=tenant_id,
        name=request.name,
        token_hash=token_hash,
        prefix=prefix,
        last4=last4,
        scopes=request.scopes or [],
        status="active",
        expires_at=expires_at,
        pepper_version=1,
        created_by_user_id=user_id,  # Session user
        user_agent=None,  # TODO: Extract from request
        ip_address=None,  # TODO: Extract from request
    )

    db.add(api_token)

    # Audit event
    token_event = TokenEvent(
        id=str(uuid.uuid4()),
        tenant_id=tenant_id,
        token_id=token_id,
        actor_user_id=user_id,  # Session user
        event_type="issued",
        event_meta={
            "name": request.name,
            "expires_in_days": request.expires_in_days,
            "actor_email": auth.email,
        },
    )
    db.add(token_event)

    db.commit()
    db.refresh(api_token)

    logger.info(
        "Token created",
        extra={
            "event": "token.created",
            "token_id": token_id,
            "tenant_id": tenant_id,
            "name": request.name,
        },
    )

    # Return response with raw token (DISPLAY ONCE)
    return TokenCreateResponse(
        token=raw_token,  # Raw token returned ONCE
        token_id=token_id,
        prefix=prefix,
        last4=last4,
        name=request.name,
        scopes=request.scopes or [],
        status="active",
        created_at=api_token.created_at,
        expires_at=expires_at,
    )


@router.get("", response_model=TokenListResponse)
async def list_tokens(
    auth: SessionAuthContext = Depends(get_session_auth_context),
    db: Session = Depends(get_db),
) -> TokenListResponse:
    """List all tokens for tenant (without raw tokens).

    Requires: Valid session (any role can view tokens).

    Args:
        auth: Session authentication context
        db: Database session

    Returns:
        TokenListResponse with token list (no raw tokens)
    """
    tenant_id = auth.tenant_id
    tokens = (
        db.query(APIToken)
        .filter(APIToken.tenant_id == tenant_id)
        .order_by(APIToken.created_at.desc())
        .all()
    )

    token_items = [
        TokenListItem(
            token_id=t.id,
            name=t.name,
            prefix=t.prefix,
            last4=t.last4,
            scopes=t.scopes or [],
            status=t.status,
            created_at=t.created_at,
            expires_at=t.expires_at,
            revoked_at=t.revoked_at,
            last_used_at=t.last_used_at,
        )
        for t in tokens
    ]

    return TokenListResponse(tokens=token_items)


@router.post("/{token_id}/revoke", response_model=TokenRevokeResponse)
async def revoke_token(
    token_id: str,
    auth: SessionAuthContext = Depends(require_admin_role),
    db: Session = Depends(get_db),
) -> TokenRevokeResponse:
    """Revoke token immediately.

    Requires: Admin or owner role in tenant.

    Args:
        token_id: Token UUID to revoke
        auth: Session authentication context (BOLA defense)
        db: Database session

    Returns:
        TokenRevokeResponse with revocation details

    Raises:
        HTTPException 401: Not authenticated
        HTTPException 403: Not admin/owner
        HTTPException 404: Token not found or not owned by tenant
    """
    tenant_id = auth.tenant_id
    user_id = auth.user_id
    # BOLA defense: only allow revoking own tokens
    token = (
        db.query(APIToken)
        .filter(
            APIToken.id == token_id,
            APIToken.tenant_id == tenant_id,
        )
        .first()
    )

    if not token:
        # Stealth 404: don't reveal whether token exists
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Token not found",
        )

    # Idempotent: already revoked
    if token.status == "revoked":
        return TokenRevokeResponse(
            token_id=token_id,
            status="revoked",
            revoked_at=token.revoked_at,
        )

    # Revoke token
    now = datetime.now(timezone.utc)
    token.status = "revoked"
    token.revoked_at = now

    # Audit event
    token_event = TokenEvent(
        id=str(uuid.uuid4()),
        tenant_id=tenant_id,
        token_id=token_id,
        actor_user_id=user_id,  # Session user
        event_type="revoked",
        event_meta={"name": token.name, "actor_email": auth.email},
    )
    db.add(token_event)

    db.commit()

    logger.warning(
        "Token revoked",
        extra={
            "event": "token.revoked",
            "token_id": token_id,
            "tenant_id": tenant_id,
            "name": token.name,
        },
    )

    return TokenRevokeResponse(
        token_id=token_id,
        status="revoked",
        revoked_at=now,
    )


@router.post("/{token_id}/rotate", status_code=status.HTTP_201_CREATED, response_model=TokenRotateResponse)
async def rotate_token(
    token_id: str,
    auth: SessionAuthContext = Depends(require_admin_role),
    db: Session = Depends(get_db),
) -> TokenRotateResponse:
    """Rotate token (issue new, old enters grace period).

    Grace period: Old token usable for ROTATION_GRACE_MINUTES (default 10).
    After grace period, old token becomes unusable.

    Requires: Admin or owner role in tenant.

    Args:
        token_id: Token UUID to rotate
        auth: Session authentication context (BOLA defense)
        db: Database session

    Returns:
        TokenRotateResponse with new raw token (display once)

    Raises:
        HTTPException 401: Not authenticated
        HTTPException 403: Not admin/owner, or quota exceeded
        HTTPException 404: Token not found or not owned by tenant
    """
    tenant_id = auth.tenant_id
    user_id = auth.user_id
    # BOLA defense: only allow rotating own tokens
    old_token = (
        db.query(APIToken)
        .filter(
            APIToken.id == token_id,
            APIToken.tenant_id == tenant_id,
        )
        .first()
    )

    if not old_token:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Token not found",
        )

    # Cannot rotate if already revoked or expired
    if old_token.status in ("revoked", "expired"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot rotate {old_token.status} token",
        )

    # Check quota (old token will become rotating, new will be active)
    active_count = (
        db.query(APIToken)
        .filter(
            APIToken.tenant_id == tenant_id,
            APIToken.status.in_(["active", "rotating"]),
        )
        .count()
    )

    # Allow rotation even at quota (one token becomes rotating)
    # Only block if we would exceed quota
    if active_count >= MAX_TOKENS_PER_TENANT and old_token.status == "rotating":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Token quota exceeded: max {MAX_TOKENS_PER_TENANT} active tokens",
        )

    # Generate new token
    prefix = old_token.prefix
    new_raw_token, new_last4 = generate_token(prefix)
    new_token_hash = hash_token(new_raw_token, pepper_version=1)

    # Create new token (active)
    new_token_id = str(uuid.uuid4())
    new_token = APIToken(
        id=new_token_id,
        tenant_id=tenant_id,
        name=old_token.name,  # Keep same name
        token_hash=new_token_hash,
        prefix=prefix,
        last4=new_last4,
        scopes=old_token.scopes,
        status="active",
        expires_at=old_token.expires_at,  # Keep same expiration
        pepper_version=1,
        created_by_user_id=user_id,  # Session user
        user_agent=None,
        ip_address=None,
    )
    db.add(new_token)

    # Update old token to rotating status with grace period
    now = datetime.now(timezone.utc)
    grace_expiry = now + timedelta(minutes=ROTATION_GRACE_MINUTES)

    old_token.status = "rotating"
    old_token.expires_at = grace_expiry

    # Audit event
    token_event = TokenEvent(
        id=str(uuid.uuid4()),
        tenant_id=tenant_id,
        token_id=new_token_id,
        actor_user_id=user_id,  # Session user
        event_type="rotated",
        event_meta={
            "old_token_id": token_id,
            "grace_period_minutes": ROTATION_GRACE_MINUTES,
            "actor_email": auth.email,
        },
    )
    db.add(token_event)

    db.commit()
    db.refresh(new_token)

    logger.info(
        "Token rotated",
        extra={
            "event": "token.rotated",
            "old_token_id": token_id,
            "new_token_id": new_token_id,
            "tenant_id": tenant_id,
            "grace_period_minutes": ROTATION_GRACE_MINUTES,
        },
    )

    return TokenRotateResponse(
        new_token=new_raw_token,  # Raw token returned ONCE
        new_token_id=new_token_id,
        old_token_id=token_id,
        old_status="rotating",
        old_expires_at=grace_expiry,
        grace_period_minutes=ROTATION_GRACE_MINUTES,
    )


@router.post("/revoke-all", response_model=TokenRevokeAllResponse)
async def revoke_all_tokens(
    auth: SessionAuthContext = Depends(require_admin_role),
    db: Session = Depends(get_db),
) -> TokenRevokeAllResponse:
    """Revoke all active/rotating tokens for tenant (panic button).

    Immediately revokes all usable tokens. Use when:
    - Token compromise suspected
    - Employee offboarding
    - Security incident

    Requires: Admin or owner role in tenant.

    Args:
        auth: Session authentication context
        db: Database session

    Returns:
        TokenRevokeAllResponse with count and IDs

    Raises:
        HTTPException 401: Not authenticated
        HTTPException 403: Not admin/owner
    """
    tenant_id = auth.tenant_id
    user_id = auth.user_id
    # Find all active/rotating tokens
    tokens = (
        db.query(APIToken)
        .filter(
            APIToken.tenant_id == tenant_id,
            APIToken.status.in_(["active", "rotating"]),
        )
        .all()
    )

    now = datetime.now(timezone.utc)
    revoked_ids = []

    for token in tokens:
        token.status = "revoked"
        token.revoked_at = now
        revoked_ids.append(token.id)

    # Audit event (single event for revoke-all)
    token_event = TokenEvent(
        id=str(uuid.uuid4()),
        tenant_id=tenant_id,
        token_id=None,  # NULL for revoke-all
        actor_user_id=user_id,  # Session user
        event_type="revoke_all",
        event_meta={
            "revoked_count": len(revoked_ids),
            "revoked_token_ids": revoked_ids,
            "actor_email": auth.email,
        },
    )
    db.add(token_event)

    db.commit()

    logger.warning(
        "All tokens revoked (panic button)",
        extra={
            "event": "token.revoke_all",
            "tenant_id": tenant_id,
            "revoked_count": len(revoked_ids),
        },
    )

    return TokenRevokeAllResponse(
        revoked_count=len(revoked_ids),
        revoked_token_ids=revoked_ids,
    )
