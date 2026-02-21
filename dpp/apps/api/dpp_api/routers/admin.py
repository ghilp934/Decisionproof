"""Admin endpoints for operational management.

P0-1: Paid Pilot Kill Switch Control

WARNING: These endpoints are for authorized operators only.
- Protected by ADMIN_TOKEN header
- All actions are audit logged
"""

import logging
import os
import secrets
from typing import Optional

from fastapi import APIRouter, HTTPException, Header, Request, status
from pydantic import BaseModel, Field

from dpp_api.audit.kill_switch_audit import build_kill_switch_audit_record
from dpp_api.audit.sinks import AuditSink, AuditSinkConfigError, get_default_audit_sink
from dpp_api.config.kill_switch import (
    KillSwitchMode,
    KillSwitchState,
    get_kill_switch_config,
)
from dpp_api.context import request_id_var

router = APIRouter(prefix="/admin", tags=["admin"])
logger = logging.getLogger(__name__)

# ── P5.3: Audit sink (module-level singleton; monkeypatchable in tests) ────────
_audit_sink: AuditSink | None = None


def _get_audit_sink() -> AuditSink:
    """Return the configured audit sink.

    P5.6: AuditSinkConfigError (REQUIRED=1 but bucket unset) is NOT caught here —
    it propagates to the endpoint handler which returns HTTP 500 fail-closed.
    The singleton stays None on config error so every request re-validates config.
    """
    global _audit_sink
    if _audit_sink is None:
        _audit_sink = get_default_audit_sink()  # may raise AuditSinkConfigError
    return _audit_sink


# ============================================================================
# Schemas
# ============================================================================


class SetKillSwitchRequest(BaseModel):
    """Request for POST /admin/kill-switch."""

    mode: KillSwitchMode = Field(..., description="Target kill switch mode")
    reason: str = Field(
        ...,
        min_length=1,
        max_length=200,
        description="Explanation for mode change (max 200 chars)",
    )
    ttl_minutes: int = Field(
        default=0,
        ge=0,
        description="Auto-restore to NORMAL after N minutes (0 = no auto-restore, SAFE_MODE only)",
    )


class KillSwitchResponse(BaseModel):
    """Response for kill switch endpoints."""

    mode: str
    reason: str
    set_at: Optional[str] = None
    set_by_ip: Optional[str] = None
    ttl_minutes: int
    expires_at: Optional[str] = None
    audit_write_ok: Optional[bool] = None  # P5.3: WORM audit write status


# ============================================================================
# Helpers
# ============================================================================


def _get_admin_token() -> str:
    """Get admin token from environment.

    Returns:
        Admin token

    Raises:
        RuntimeError: If ADMIN_TOKEN not set
    """
    token = os.getenv("ADMIN_TOKEN")
    if not token:
        raise RuntimeError(
            "ADMIN_TOKEN not set. Configure ADMIN_TOKEN environment variable."
        )
    return token


def _verify_admin_token(provided_token: str) -> None:
    """Verify admin token using constant-time comparison.

    Args:
        provided_token: Token from X-Admin-Token header

    Raises:
        HTTPException 401: If token is invalid
        HTTPException 500: If ADMIN_TOKEN not configured
    """
    try:
        expected_token = _get_admin_token()
    except RuntimeError as e:
        logger.error(f"Admin token not configured: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Admin token not configured on server",
        )

    # Constant-time comparison to prevent timing attacks
    if not secrets.compare_digest(provided_token, expected_token):
        request_id = request_id_var.get()
        logger.warning(
            "Invalid admin token attempt",
            extra={
                "event": "admin.auth_failed",
                "request_id": request_id,
            },
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid X-Admin-Token",
            headers={"WWW-Authenticate": "Header"},
        )


def _get_client_ip(request: Request) -> str:
    """Extract client IP from request.

    Args:
        request: FastAPI request

    Returns:
        Client IP address
    """
    # Check X-Forwarded-For header first (for proxied requests)
    forwarded_for = request.headers.get("X-Forwarded-For")
    if forwarded_for:
        # Take first IP in chain (original client)
        return forwarded_for.split(",")[0].strip()

    # Fallback to direct client IP
    return request.client.host if request.client else "unknown"


# ============================================================================
# Endpoints
# ============================================================================


@router.get("/kill-switch", response_model=KillSwitchResponse)
async def get_kill_switch(
    request: Request,
    x_admin_token: str = Header(..., alias="X-Admin-Token"),
) -> KillSwitchResponse:
    """Get current kill switch state.

    ADMIN ONLY. Requires valid X-Admin-Token header.

    Returns:
        Current kill switch state with timestamps in KST

    Raises:
        HTTPException 401: Invalid admin token
        HTTPException 500: Server configuration error
    """
    # Verify admin authentication
    _verify_admin_token(x_admin_token)

    # Get current state
    config = get_kill_switch_config()
    state = config.get_state()

    # Log access (audit trail)
    actor_ip = _get_client_ip(request)
    logger.info(
        "Kill switch state accessed",
        extra={
            "event": "admin.kill_switch.get",
            "actor_ip": actor_ip,
            "mode": state.mode.value,
        },
    )

    # Convert to KST for display
    kst_data = state.to_kst_display()

    return KillSwitchResponse(**kst_data)


@router.post("/kill-switch", response_model=KillSwitchResponse)
async def set_kill_switch(
    request_body: SetKillSwitchRequest,
    request: Request,
    x_admin_token: str = Header(..., alias="X-Admin-Token"),
) -> KillSwitchResponse:
    """Set kill switch mode.

    ADMIN ONLY. Requires valid X-Admin-Token header.

    Mode Restrictions:
    - NORMAL: No restrictions
    - SAFE_MODE: Can have TTL for auto-restore
    - HARD_STOP: No TTL allowed (requires manual intervention)

    Args:
        request_body: Kill switch mode change request
        request: FastAPI request
        x_admin_token: Admin authentication token

    Returns:
        Updated kill switch state with timestamps in KST

    Raises:
        HTTPException 401: Invalid admin token
        HTTPException 400: Invalid ttl_minutes value
        HTTPException 500: Server configuration error
    """
    # Step 1: Verify admin authentication
    _verify_admin_token(x_admin_token)

    # Step 2: Validate TTL constraints
    actor_ip = _get_client_ip(request)
    if request_body.mode == KillSwitchMode.HARD_STOP and request_body.ttl_minutes > 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="HARD_STOP mode cannot have TTL (requires manual intervention)",
        )

    # Step 3: Read current state (before mutation)
    config = get_kill_switch_config()
    old_state = config.get_state()
    request_id = request_id_var.get()

    # Step 4: Build WORM audit record (before any state change)
    # P5.9: build_kill_switch_audit_record() calls fingerprint_token() which may raise
    # RuntimeError (FINGERPRINT_PEPPER_NOT_SET / INVALID_FINGERPRINT_KID) in REQUIRED/STRICT
    # mode.  Catch here and return 500 fail-closed — state must not be mutated.
    try:
        audit_record = build_kill_switch_audit_record(
            request_id=request_id,
            actor_token=x_admin_token,
            actor_ip=actor_ip,
            mode_from=old_state.mode.value,
            mode_to=request_body.mode.value,
            reason=request_body.reason,
            ttl_minutes=request_body.ttl_minutes,
            result="ok",
        )
    except RuntimeError as fp_exc:
        # Extract deterministic error code (first token before ":") for structured logging
        error_code = str(fp_exc).split(":")[0]
        logger.error(
            "KILL_SWITCH_AUDIT_RECORD_BUILD_FAILED",
            extra={
                "event": "admin.kill_switch.audit_record_failed",
                "error_code": error_code,
                "error_type": type(fp_exc).__name__,
            },
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="KILL_SWITCH_AUDIT_RECORD_BUILD_FAILED",
        ) from fp_exc

    # Step 5: Write WORM audit record BEFORE state change (P5.3 fail-closed)
    strict_mode = os.getenv("KILL_SWITCH_AUDIT_STRICT", "0").strip() == "1"
    audit_write_ok: Optional[bool] = None

    # P5.6: Obtain sink — AuditSinkConfigError means REQUIRED=1 but no bucket (hard config error)
    try:
        sink = _get_audit_sink()
    except AuditSinkConfigError as cfg_exc:
        logger.error(
            "KILL_SWITCH_AUDIT_SINK_MISCONFIGURED",
            extra={
                "event": "admin.kill_switch.audit_misconfigured",
                "error_type": "AuditSinkConfigError",
            },
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="KILL_SWITCH_AUDIT_SINK_MISCONFIGURED",
        ) from cfg_exc

    audit_key = f"kill-switch/{audit_record['timestamp']}-{request_id or 'noid'}.json"

    try:
        sink.put_record(audit_key, audit_record)
        audit_write_ok = True
    except Exception as sink_exc:
        if strict_mode:
            logger.error(
                "KILL_SWITCH_AUDIT_SINK_FAILED",
                extra={
                    "event": "admin.kill_switch.audit_failed",
                    "strict_mode": True,
                    "error_type": type(sink_exc).__name__,
                },
            )
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="KILL_SWITCH_AUDIT_SINK_FAILED",
            )
        # STRICT=0 → warn and continue
        audit_write_ok = False
        logger.warning(
            "KILL_SWITCH_AUDIT_SINK_DEGRADED",
            extra={
                "event": "admin.kill_switch.audit_degraded",
                "strict_mode": False,
                "error_type": type(sink_exc).__name__,
            },
        )

    # Step 6: Mutate state only after audit record is persisted (or STRICT=0 with warning)
    new_state = config.set_state(
        mode=request_body.mode,
        reason=request_body.reason,
        actor_ip=actor_ip,
        ttl_minutes=request_body.ttl_minutes,
    )

    # Step 7: Structured audit log (no raw IP / token)
    logger.info(
        "KILL_SWITCH_CHANGED",
        extra={
            "event": "admin.kill_switch.set",
            "mode_from": old_state.mode.value,
            "mode_to": new_state.mode.value,
            "reason": request_body.reason,
            "ttl_minutes": request_body.ttl_minutes,
            "audit_write_ok": audit_write_ok,
        },
    )

    # Step 8: Return updated state
    kst_data = new_state.to_kst_display()
    return KillSwitchResponse(**kst_data, audit_write_ok=audit_write_ok)
