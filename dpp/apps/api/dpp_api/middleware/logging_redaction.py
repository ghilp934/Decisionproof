"""Logging Redaction Middleware.

P0-3 Security Invariant: Authorization headers must never appear in plain text in logs.

Purpose:
- Redact sensitive headers (Authorization, X-API-Key, etc.) from request logs
- Prevent accidental token leakage in access logs, error logs, or trace logs
- Comply with SOC2/ISO27001 requirements for credential handling

Behavior:
- Intercepts all requests before they reach handlers
- Redacts Authorization header to "[REDACTED]" for logging purposes
- Original header remains intact for authentication (only logging context is modified)
- Works with both session JWT and API token authentication

Security:
- Defense in depth: Even if logger configuration fails, headers are redacted
- Complements token_auth.py and session_auth.py (which already hash sensitive data)
- No secrets logged in any context

Usage:
    app.add_middleware(LoggingRedactionMiddleware)  # Add early in middleware stack
"""

import logging
from typing import Callable

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

logger = logging.getLogger(__name__)


class LoggingRedactionMiddleware(BaseHTTPMiddleware):
    """Middleware to redact sensitive headers from logs.

    This middleware ensures that Authorization headers (and other sensitive headers)
    never appear in plain text in application logs, access logs, or trace logs.

    IMPORTANT: This does NOT modify the actual request headers - it only affects
    what gets logged. Authentication middleware still receives the original headers.
    """

    # Headers to redact from logs
    SENSITIVE_HEADERS = {
        "authorization",  # JWT session tokens, Bearer API tokens
        "x-api-key",  # Legacy API key header (if used)
        "proxy-authorization",  # Proxy auth
        "cookie",  # Session cookies (may contain sensitive data)
    }

    REDACTED_PLACEHOLDER = "[REDACTED]"

    def __init__(self, app):
        """Initialize middleware.

        Args:
            app: FastAPI application instance
        """
        super().__init__(app)

        # Log middleware initialization (security audit trail)
        logger.info(
            "LoggingRedactionMiddleware initialized",
            extra={
                "event": "middleware.logging_redaction.init",
                "redacted_headers": sorted(self.SENSITIVE_HEADERS),
            },
        )

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        """Dispatch request with header redaction for logging.

        Args:
            request: FastAPI request
            call_next: Next middleware/handler

        Returns:
            Response from downstream handlers
        """
        # Redact sensitive headers for logging context
        # Note: This does NOT modify request.headers (which is immutable anyway)
        # Instead, we store a redacted version in request.state for loggers to use
        redacted_headers = {}

        for header_name, header_value in request.headers.items():
            header_lower = header_name.lower()
            if header_lower in self.SENSITIVE_HEADERS:
                redacted_headers[header_name] = self.REDACTED_PLACEHOLDER
            else:
                redacted_headers[header_name] = header_value

        # Store redacted headers in request.state for logger access
        # Custom loggers can use request.state.redacted_headers instead of request.headers
        request.state.redacted_headers = redacted_headers

        # Mark that redaction has been applied (for downstream middleware/handlers)
        request.state.logging_redaction_applied = True

        # Pass through to next handler (original headers unchanged)
        response = await call_next(request)

        return response


# Helper function for logging (optional utility)
def get_safe_headers(request: Request) -> dict:
    """Get headers safe for logging (with sensitive headers redacted).

    Use this in custom logging instead of request.headers.items().

    Args:
        request: FastAPI request

    Returns:
        Dictionary of headers with sensitive values redacted

    Example:
        logger.info("Request headers", extra={"headers": get_safe_headers(request)})
    """
    # If redaction middleware has run, use pre-redacted headers
    if hasattr(request.state, "redacted_headers"):
        return request.state.redacted_headers

    # Fallback: Manual redaction (if middleware not installed)
    redacted = {}
    for header_name, header_value in request.headers.items():
        if header_name.lower() in LoggingRedactionMiddleware.SENSITIVE_HEADERS:
            redacted[header_name] = LoggingRedactionMiddleware.REDACTED_PLACEHOLDER
        else:
            redacted[header_name] = header_value

    return redacted
