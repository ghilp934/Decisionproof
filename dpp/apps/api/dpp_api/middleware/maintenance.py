"""Maintenance Mode Middleware.

SMTP Smoke Test: Enable maintenance mode (503) with strict exception routes.

Usage:
    export DP_MAINTENANCE_MODE=1
    export DP_MAINTENANCE_ALLOWLIST="/internal/smoke/email,/custom-path"

Behavior:
- If DP_MAINTENANCE_MODE != "1": normal operation (middleware is no-op)
- If DP_MAINTENANCE_MODE == "1":
  - Requests to allowlisted paths: pass through
  - All other requests: return 503 Service Unavailable (RFC 9457 Problem Details)

Default Allowlist (hardcoded):
- /health (infra health check)
- /readyz (infra readiness check)
- /internal/smoke/email (SMTP smoke test endpoint)

Custom Allowlist (env):
- DP_MAINTENANCE_ALLOWLIST: comma-separated paths to ADD to default allowlist

Security:
- No secrets logged
- RFC 9457 compliant error responses
- Minimal attack surface (only allowlisted paths reachable)
"""

import logging
import os
import uuid
from typing import Callable

from fastapi import Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from dpp_api.context import request_id_var
from dpp_api.schemas import ProblemDetail

logger = logging.getLogger(__name__)


class MaintenanceMiddleware(BaseHTTPMiddleware):
    """Middleware to enforce maintenance mode with allowlist exceptions."""

    # Hardcoded default allowlist (infra + smoke test)
    DEFAULT_ALLOWLIST = [
        "/health",  # Infra health check (ALB/API Gateway)
        "/readyz",  # Infra readiness check (K8s)
        "/internal/smoke/email",  # SMTP smoke test endpoint
    ]

    def __init__(self, app):
        """Initialize middleware.

        Args:
            app: FastAPI application instance
        """
        super().__init__(app)

        # Check if maintenance mode is enabled
        self.maintenance_enabled = os.getenv("DP_MAINTENANCE_MODE") == "1"

        # Build allowlist: default + custom from env
        allowlist = self.DEFAULT_ALLOWLIST.copy()

        custom_allowlist = os.getenv("DP_MAINTENANCE_ALLOWLIST", "")
        if custom_allowlist:
            custom_paths = [p.strip() for p in custom_allowlist.split(",") if p.strip()]
            allowlist.extend(custom_paths)

        self.allowlist = set(allowlist)  # Use set for O(1) lookup

        if self.maintenance_enabled:
            logger.warning(
                "Maintenance mode ENABLED",
                extra={
                    "allowlist": sorted(self.allowlist),
                    "env_custom": custom_allowlist or "(none)",
                },
            )

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        """Dispatch request with maintenance mode check.

        Args:
            request: FastAPI request
            call_next: Next middleware/handler

        Returns:
            Response (503 if maintenance mode, normal response otherwise)
        """
        # If maintenance mode is off, pass through
        if not self.maintenance_enabled:
            return await call_next(request)

        # Check if request path is in allowlist
        request_path = request.url.path

        if request_path in self.allowlist:
            # Allowlisted path: pass through
            return await call_next(request)

        # Not allowlisted: return 503 Maintenance
        request_id = request_id_var.get() or str(uuid.uuid4())

        problem = ProblemDetail(
            type="https://api.decisionproof.ai/problems/maintenance",
            title="Service Unavailable",
            status=503,
            detail="Decisionproof is in maintenance mode.",
            instance=f"urn:decisionproof:trace:{request_id}",
        )

        logger.info(
            "maintenance.blocked",
            extra={
                "path": request_path,
                "method": request.method,
                "request_id": request_id,
            },
        )

        return JSONResponse(
            status_code=503,
            content=problem.model_dump(exclude_none=True),
            media_type="application/problem+json",
            headers={
                "X-Request-ID": request_id,
                "Retry-After": "3600",  # Suggest retry after 1 hour
            },
        )
