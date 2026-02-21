"""Kill Switch enforcement middleware.

P0-1: Paid Pilot Kill Switch

Enforces operational mode restrictions:
- NORMAL: All operations allowed
- SAFE_MODE: Blocks high-risk operations (onboarding, key issuance, upgrades)
- HARD_STOP: Emergency mode (only health checks allowed)
"""

import logging
import uuid

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from dpp_api.config.kill_switch import KillSwitchMode, get_kill_switch_config
from dpp_api.context import request_id_var
from dpp_api.schemas import ProblemDetail

logger = logging.getLogger(__name__)


class KillSwitchMiddleware(BaseHTTPMiddleware):
    """Middleware to enforce kill switch mode restrictions."""

    # Paths always allowed (health checks)
    ALWAYS_ALLOWED = {
        "/health",
        "/readyz",
        "/status",
    }

    # Paths blocked in SAFE_MODE
    SAFE_MODE_BLOCKED = {
        "/v1/auth/signup",  # New onboarding
        "/v1/keys",  # New API key issuance (POST)
        "/v1/plans/upgrade",  # Plan upgrades
        "/v1/export",  # High-cost exports (if exists)
        "/v1/jobs/batch",  # Large batch jobs (if exists)
    }

    # Paths allowed in HARD_STOP (only health checks)
    HARD_STOP_ALLOWED = ALWAYS_ALLOWED

    async def dispatch(self, request: Request, call_next):
        """Enforce kill switch mode restrictions.

        Args:
            request: Incoming HTTP request
            call_next: Next middleware/handler

        Returns:
            HTTP response (either 503 blocked or normal response)
        """
        # Get current kill switch mode
        config = get_kill_switch_config()
        state = config.get_state()
        mode = state.mode

        # NORMAL mode: allow all
        if mode == KillSwitchMode.NORMAL:
            return await call_next(request)

        path = request.url.path
        method = request.method

        # SAFE_MODE enforcement
        if mode == KillSwitchMode.SAFE_MODE:
            # Always allow health checks
            if path in self.ALWAYS_ALLOWED:
                return await call_next(request)

            # Allow admin endpoints (for kill switch management)
            if path.startswith("/admin/"):
                return await call_next(request)

            # Block high-risk operations
            if self._is_blocked_in_safe_mode(path, method):
                return self._create_503_response(
                    path=path,
                    mode="SAFE_MODE",
                    detail=(
                        "Service is in SAFE_MODE. High-risk operations (onboarding, "
                        "key issuance, plan upgrades) are temporarily disabled. "
                        "Existing operations continue normally."
                    ),
                )

            # Allow all other operations
            return await call_next(request)

        # HARD_STOP enforcement
        if mode == KillSwitchMode.HARD_STOP:
            # Only allow health checks and admin endpoints
            if path in self.HARD_STOP_ALLOWED or path.startswith("/admin/"):
                return await call_next(request)

            # Block everything else
            return self._create_503_response(
                path=path,
                mode="HARD_STOP",
                detail=(
                    "Service is in HARD_STOP mode due to an operational incident. "
                    "Only health checks are available. Normal operations will resume "
                    "after the incident is resolved."
                ),
            )

        # Fallback: allow request (should not reach here)
        return await call_next(request)

    def _is_blocked_in_safe_mode(self, path: str, method: str) -> bool:
        """Check if path/method is blocked in SAFE_MODE.

        Args:
            path: Request path
            method: HTTP method

        Returns:
            True if blocked, False if allowed
        """
        # Check exact path matches
        if path in self.SAFE_MODE_BLOCKED:
            # For /v1/keys, only block POST (key issuance)
            if path == "/v1/keys":
                return method == "POST"
            return True

        # Check prefix matches
        for blocked_path in self.SAFE_MODE_BLOCKED:
            if path.startswith(blocked_path + "/"):
                return True

        return False

    def _create_503_response(self, path: str, mode: str, detail: str) -> JSONResponse:
        """Create RFC 9457 Problem Details response for 503 Service Unavailable.

        Args:
            path: Request path
            mode: Current kill switch mode
            detail: Human-readable explanation

        Returns:
            JSONResponse with 503 status and Problem Details
        """
        # Get request_id from context for instance field
        request_id = request_id_var.get()
        instance = (
            f"urn:decisionproof:trace:{request_id}"
            if request_id
            else f"urn:decisionproof:trace:{uuid.uuid4()}"
        )

        problem = ProblemDetail(
            type="https://api.decisionproof.ai/problems/kill-switch-active",
            title=f"Service Unavailable ({mode})",
            status=503,
            detail=detail,
            instance=instance,
        )

        # Log enforcement action
        logger.warning(
            f"Kill switch blocked request: {path}",
            extra={
                "event": "kill_switch.request_blocked",
                "mode": mode,
                "path": path,
                "request_id": request_id,
            },
        )

        return JSONResponse(
            status_code=503,
            content=problem.model_dump(exclude_none=True),
            media_type="application/problem+json",
            headers={
                "Retry-After": "300",  # 5 minutes default
                "X-Kill-Switch-Mode": mode,
            },
        )
