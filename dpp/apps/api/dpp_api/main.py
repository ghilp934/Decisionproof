"""DPP API - FastAPI Application Entry Point."""

import json
import logging
import os
import time
import uuid
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from dpp_api.context import budget_decision_var, plan_key_var, request_id_var, run_id_var
from dpp_api.enforce import PlanViolationError
from dpp_api.rate_limiter import NoOpRateLimiter, RateLimiter
from dpp_api.routers import health, runs, usage
from dpp_api.schemas import ProblemDetail
from dpp_api.utils import configure_json_logging

# MTS-3.1: Base URL from environment variables
base_url = os.getenv("API_BASE_URL", "https://api.decisionproof.ai")
sandbox_url = os.getenv("API_SANDBOX_URL", "https://sandbox-api.decisionproof.ai")

app = FastAPI(
    title="Decisionproof API",
    description="Agent-centric decision execution platform with idempotent metering, RFC 9457 error handling, and IETF RateLimit headers.",
    version="0.4.2.2",
    docs_url="/api-docs",  # MTS-3: Moved to /api-docs to free /docs for documentation
    redoc_url="/redoc",
    openapi_version="3.1.0",  # MTS-3: Locked to OpenAPI 3.1.0
    servers=[
        {"url": base_url, "description": "Production"},
        {"url": sandbox_url, "description": "Sandbox"},
        {"url": "http://localhost:8000", "description": "Local development"},
    ],
)

# P1-9: Configure structured JSON logging
# Set DPP_JSON_LOGS=false to disable (defaults to true for production)
if os.getenv("DPP_JSON_LOGS", "true").lower() != "false":
    configure_json_logging(log_level=os.getenv("LOG_LEVEL", "INFO"))
    logger = logging.getLogger(__name__)
    logger.info("Structured JSON logging enabled")

# P1-G: CORS middleware with browser-compatible security
# MDN: credentials mode CANNOT use wildcard origins
cors_origins_env = os.getenv("CORS_ALLOWED_ORIGINS", "")
if cors_origins_env:
    # Production: explicit allowlist (comma-separated)
    allowed_origins = [origin.strip() for origin in cors_origins_env.split(",") if origin.strip()]
else:
    # Dev fallback: localhost variants (safe default)
    allowed_origins = [
        "http://localhost:3000",
        "http://localhost:8000",
        "http://127.0.0.1:3000",
        "http://127.0.0.1:8000",
    ]

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,  # P1-G: Never "*" with credentials
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],  # Explicit methods
    allow_headers=["Authorization", "Content-Type", "Idempotency-Key"],  # Explicit headers
    expose_headers=[
        "X-DPP-Cost-Reserved", "X-DPP-Cost-Actual", "X-DPP-Cost-Minimum-Fee",  # P1-6
        "RateLimit-Policy", "RateLimit", "Retry-After"  # P0 Hotfix: IETF rate limit headers
    ],
)


# ============================================================================
# MTS-3.3: Static File Caching Middleware
# ============================================================================


@app.middleware("http")
async def static_cache_middleware(request: Request, call_next):
    """
    Add Cache-Control headers for static files.

    MTS-3.3: Performance optimization for documentation and llms.txt.
    - /llms.txt, /llms-full.txt: max-age=300 (5 minutes)
    - /docs/*.md: max-age=3600 (1 hour)
    - /.well-known/openapi.json: max-age=300 (5 minutes)
    - /pricing/ssot.json: max-age=300 (5 minutes)
    """
    response = await call_next(request)

    # Apply caching based on path
    path = request.url.path

    if path in ["/llms.txt", "/llms-full.txt", "/.well-known/openapi.json", "/pricing/ssot.json"]:
        # Short cache for frequently updated files (5 minutes)
        response.headers["Cache-Control"] = "public, max-age=300"
    elif path.startswith("/docs/") and path.endswith(".md"):
        # Longer cache for documentation (1 hour)
        response.headers["Cache-Control"] = "public, max-age=3600"
    elif path.startswith("/docs/") or path.startswith("/public/"):
        # Default cache for other static files (1 hour)
        response.headers["Cache-Control"] = "public, max-age=3600"

    return response


# ============================================================================
# RC-3: IETF RateLimit Headers Middleware
# ============================================================================


@app.middleware("http")
async def rate_limit_middleware(request: Request, call_next):
    """Add IETF RateLimit headers to /v1/* API responses.

    RC-3: Contract Gate - RateLimit Headers
    - Applies to: /v1/* endpoints only
    - 2xx responses: RateLimit-Policy + RateLimit
    - 429 responses: RateLimit-Policy + RateLimit + Retry-After (handled by exception handler)
    - Format: Structured Fields style
      - RateLimit-Policy: "default"; q=60; w=60
      - RateLimit: "default"; r=<int>; t=<int>
    """
    # Only apply to /v1/* API endpoints
    if not request.url.path.startswith("/v1/"):
        return await call_next(request)

    # Get rate limiter from app.state (can be overridden in tests)
    rate_limiter: RateLimiter = getattr(app.state, "rate_limiter", None)
    if not rate_limiter:
        # Fallback if not initialized
        rate_limiter = NoOpRateLimiter()

    # Extract identifier from request (use Authorization header or IP)
    auth_header = request.headers.get("authorization", "")
    if auth_header.startswith("Bearer "):
        key = auth_header[7:]  # Extract token
    else:
        key = request.client.host if request.client else "anonymous"

    # Check rate limit
    result = rate_limiter.check_rate_limit(key, request.url.path)

    # If rate limited, return 429 with Problem Details
    if not result.allowed:
        request_id = request_id_var.get()
        instance = f"urn:decisionproof:trace:{request_id}" if request_id else f"urn:decisionproof:trace:{uuid.uuid4()}"

        problem = ProblemDetail(
            type="https://api.decisionproof.ai/problems/http-429",
            title="Too Many Requests",
            status=429,
            detail="Rate limit exceeded. Please retry after the specified time.",
            instance=instance,
        )

        # Build RateLimit headers
        rate_limit_policy = f'"{result.policy_id}"; q={result.quota}; w={result.window}'
        rate_limit = f'"{result.policy_id}"; r={result.remaining}; t={result.reset}'

        return JSONResponse(
            status_code=429,
            content=problem.model_dump(exclude_none=True),
            media_type="application/problem+json",
            headers={
                "RateLimit-Policy": rate_limit_policy,
                "RateLimit": rate_limit,
                "Retry-After": str(result.reset),
            },
        )

    # Process request normally
    response = await call_next(request)

    # Add RateLimit headers to successful responses
    if 200 <= response.status_code < 300:
        rate_limit_policy = f'"{result.policy_id}"; q={result.quota}; w={result.window}'
        rate_limit = f'"{result.policy_id}"; r={result.remaining}; t={result.reset}'

        response.headers["RateLimit-Policy"] = rate_limit_policy
        response.headers["RateLimit"] = rate_limit

    return response


# ============================================================================
# RC-6: HTTP Request Completion Logging Middleware (MUST BE LAST)
# ============================================================================


@app.middleware("http")
async def http_completion_logging_middleware(request: Request, call_next):
    """Log every HTTP request completion with observability fields.

    RC-6: Observability Contract
    - Every HTTP request emits "http.request.completed" log
    - Fields: request_id, method, path, status_code, duration_ms
    - Additional fields from context: tenant_id, run_id, plan_key, budget_decision
    - Logs even on exceptions (status_code=500)

    RC-6 Hardening:
    - Clears per-request contextvars at start and end to prevent leakage
    - Ensures contextvars don't leak across requests in async task reuse scenarios

    IMPORTANT: This MUST be the LAST middleware to ensure it wraps all other middlewares
    and logs are emitted even when inner middlewares return early (e.g., 429 responses).
    """
    # RC-6 Hardening: Clear per-request contextvars at start
    # This prevents context leakage if async tasks are reused across requests
    run_id_var.set("")
    plan_key_var.set("")
    budget_decision_var.set("")

    start_time = time.perf_counter()
    status_code = 500  # Default to 500 in case of unhandled exception

    try:
        response = await call_next(request)
        status_code = response.status_code
        return response
    finally:
        # Calculate duration
        duration_ms = (time.perf_counter() - start_time) * 1000

        # Get logger (use module logger if available)
        log = logging.getLogger(__name__)

        # RC-6: Log completion with observability fields
        # Context variables (request_id, tenant_id, run_id, plan_key, budget_decision)
        # are automatically included by JSONFormatter in production environments.
        # Note: TestClient's BlockingPortal may create a separate async context,
        # preventing contextvar propagation in tests. Use response headers in tests.
        log.info(
            "http.request.completed",
            extra={
                "method": request.method,
                "path": request.url.path,
                "status_code": status_code,
                "duration_ms": round(duration_ms, 2),
            },
        )

        # RC-6 Hardening: Clear per-request contextvars after logging
        # Ensures clean state for next request (defense in depth)
        run_id_var.set("")
        plan_key_var.set("")
        budget_decision_var.set("")


# ============================================================================
# P1-9: Request ID Middleware (MUST BE OUTERMOST)
# ============================================================================


@app.middleware("http")
async def request_id_middleware(request: Request, call_next):
    """Generate and propagate request_id for observability.

    P1-9: Each request gets a unique request_id (UUID v4).
    - Accepts X-Request-ID header from client (optional)
    - Generates new UUID if not provided
    - Sets context variable for logging
    - Returns X-Request-ID in response headers

    IMPORTANT: This MUST be registered LAST (outermost middleware) to ensure
    request_id is set in the parent async context before other middlewares execute.
    This allows contextvars to propagate correctly to inner middlewares.
    """
    # Get or generate request_id
    request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())

    # Set context variable for logging (in outermost async context)
    request_id_var.set(request_id)

    # Process request
    response = await call_next(request)

    # Add request_id to response headers
    response.headers["X-Request-ID"] = request_id

    return response


# ============================================================================
# RFC 9457 Global Exception Handlers
# ============================================================================


@app.exception_handler(PlanViolationError)
async def plan_violation_handler(request: Request, exc: PlanViolationError) -> JSONResponse:
    """Handle plan violation errors with RFC 9457 Problem Details format.

    Returns application/problem+json with plan-specific error details.
    P1-2: Includes Retry-After header for 429 responses using exc.retry_after field.
    RC-2: Uses opaque instance identifier (urn:decisionproof:trace:{request_id}).
    """
    # RC-2: Opaque instance using request_id from context
    request_id = request_id_var.get()
    instance = f"urn:decisionproof:trace:{request_id}" if request_id else f"urn:decisionproof:trace:{uuid.uuid4()}"

    problem = ProblemDetail(
        type=exc.error_type,
        title=exc.title,
        status=exc.status_code,
        detail=exc.detail,
        instance=instance,
    )

    headers = {}
    # P1-2: Add Retry-After header using retry_after field (no regex parsing)
    if exc.status_code == 429 and exc.retry_after is not None:
        headers["Retry-After"] = str(exc.retry_after)

    return JSONResponse(
        status_code=exc.status_code,
        content=problem.model_dump(exclude_none=True),
        media_type="application/problem+json",
        headers=headers,
    )


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    """Handle HTTP exceptions with RFC 9457 Problem Details format.

    Returns application/problem+json with top-level RFC 9457 fields.
    No {"detail": ...} wrapper.

    P0-1: Preserves dict detail fields for structured error responses (RFC 9457 compliant).
    P0 Hotfix: Adds Retry-After header for 429 responses.
    RC-2: Uses opaque instance identifier and proper domain.
    """
    # P0-1: Don't force-cast detail to str - preserve dict if provided
    detail_value = exc.detail if exc.detail is not None else _get_title_for_status(exc.status_code)

    # RC-2: Opaque instance using request_id from context
    request_id = request_id_var.get()
    instance = f"urn:decisionproof:trace:{request_id}" if request_id else f"urn:decisionproof:trace:{uuid.uuid4()}"

    problem = ProblemDetail(
        type=f"https://api.decisionproof.ai/problems/http-{exc.status_code}",
        title=_get_title_for_status(exc.status_code),
        status=exc.status_code,
        detail=detail_value,
        instance=instance,
    )

    headers = {}
    # P0 Hotfix: Add Retry-After header for 429 (default to 60 seconds)
    if exc.status_code == 429:
        headers["Retry-After"] = "60"

    return JSONResponse(
        status_code=exc.status_code,
        content=problem.model_dump(exclude_none=True),
        media_type="application/problem+json",
        headers=headers,
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    """Handle request validation errors with RFC 9457 Problem Details format.

    Returns 422 Unprocessable Entity with application/problem+json.
    RC-2: Uses opaque instance identifier and proper domain.
    """
    # Extract first error for detail message
    first_error = exc.errors()[0] if exc.errors() else {}
    field = ".".join(str(loc) for loc in first_error.get("loc", []))
    msg = first_error.get("msg", "Validation error")

    # RC-2: Opaque instance using request_id from context
    request_id = request_id_var.get()
    instance = f"urn:decisionproof:trace:{request_id}" if request_id else f"urn:decisionproof:trace:{uuid.uuid4()}"

    problem = ProblemDetail(
        type="https://api.decisionproof.ai/problems/validation-error",
        title="Request Validation Failed",
        status=422,
        detail=f"Invalid field '{field}': {msg}",
        instance=instance,
    )

    return JSONResponse(
        status_code=422,
        content=problem.model_dump(exclude_none=True),
        media_type="application/problem+json",
    )


@app.exception_handler(Exception)
async def general_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Handle uncaught exceptions with RFC 9457 Problem Details format.

    Returns 500 Internal Server Error with application/problem+json.
    RC-2: Uses opaque instance identifier and proper domain.
    """
    # RC-2: Opaque instance using request_id from context
    request_id = request_id_var.get()
    instance = f"urn:decisionproof:trace:{request_id}" if request_id else f"urn:decisionproof:trace:{uuid.uuid4()}"

    problem = ProblemDetail(
        type="https://api.decisionproof.ai/problems/internal-error",
        title="Internal Server Error",
        status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail="An unexpected error occurred. Please try again later.",
        instance=instance,
    )

    # Log the actual exception for debugging (structured JSON logging with exc_info)
    import logging
    logger = logging.getLogger(__name__)
    logger.error(f"Unhandled exception: {exc}", exc_info=True)

    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content=problem.model_dump(exclude_none=True),
        media_type="application/problem+json",
    )


def _get_title_for_status(status_code: int) -> str:
    """Get human-readable title for HTTP status code."""
    titles = {
        400: "Bad Request",
        401: "Unauthorized",
        402: "Payment Required",
        403: "Forbidden",
        404: "Not Found",
        409: "Conflict",
        429: "Too Many Requests",
        500: "Internal Server Error",
        502: "Bad Gateway",
        503: "Service Unavailable",
    }
    return titles.get(status_code, f"HTTP {status_code}")


# Include routers
app.include_router(health.router, tags=["health"])
app.include_router(runs.router)  # API-01: Runs endpoints
app.include_router(usage.router)  # STEP D: Usage analytics


# ============================================================================
# MTS-3.2: OpenAPI Schema Customization (Examples)
# ============================================================================


def custom_openapi():
    """
    Customize OpenAPI schema with request/response examples.

    MTS-3.2: Adds practical examples for AI/Agent integration.
    """
    if app.openapi_schema:
        return app.openapi_schema

    # Import get_openapi to avoid recursion
    from fastapi.openapi.utils import get_openapi

    openapi_schema = get_openapi(
        title=app.title,
        version=app.version,
        description=app.description,
        routes=app.routes,
        servers=app.servers,
    )

    # Add example for POST /v1/runs (200 Success)
    if "/v1/runs" in openapi_schema.get("paths", {}):
        post_runs = openapi_schema["paths"]["/v1/runs"].get("post", {})

        # Request example
        if "requestBody" in post_runs:
            post_runs["requestBody"]["content"]["application/json"]["example"] = {
                "workspace_id": "ws_abc123",
                "run_id": "run_unique_001",
                "plan_id": "plan_xyz789",
                "input": {"question": "What is 2+2?"},
            }

        # Response examples
        if "responses" not in post_runs:
            post_runs["responses"] = {}

        # 202 Accepted (Success)
        post_runs["responses"]["202"] = {
            "description": "Run accepted and queued for execution",
            "content": {
                "application/json": {
                    "example": {
                        "run_id": "run_unique_001",
                        "status": "queued",
                        "poll_url": "/v1/runs/run_unique_001",
                        "estimated_cost": "0.15 USD",
                    }
                }
            },
        }

        # 422 Unprocessable Entity (Billable error)
        post_runs["responses"]["422"] = {
            "description": "Invalid plan configuration (billable)",
            "content": {
                "application/problem+json": {
                    "example": {
                        "type": "https://iana.org/assignments/http-problem-types#unprocessable-entity",
                        "title": "Unprocessable Entity",
                        "status": 422,
                        "detail": "Plan 'plan_invalid' does not exist",
                    }
                }
            },
        }

        # 429 Rate Limit Exceeded (Non-billable)
        post_runs["responses"]["429"] = {
            "description": "Rate limit or quota exceeded (non-billable)",
            "content": {
                "application/problem+json": {
                    "example": {
                        "type": "https://iana.org/assignments/http-problem-types#quota-exceeded",
                        "title": "Request cannot be satisfied as assigned quota has been exceeded",
                        "status": 429,
                        "detail": "RPM limit of 600 requests per minute exceeded",
                        "violated-policies": [
                            {
                                "policy": "rpm",
                                "limit": 600,
                                "current": 601,
                                "window_seconds": 60,
                            }
                        ],
                    }
                }
            },
        }

    # Add example for GET /pricing/ssot.json
    if "/pricing/ssot.json" in openapi_schema.get("paths", {}):
        get_ssot = openapi_schema["paths"]["/pricing/ssot.json"].get("get", {})

        if "responses" not in get_ssot:
            get_ssot["responses"] = {}

        get_ssot["responses"]["200"] = {
            "description": "Canonical Pricing SSoT v0.2.1",
            "content": {
                "application/json": {
                    "example": {
                        "pricing_version": "2026-02-14.v0.2.1",
                        "effective_from": "2026-03-01T00:00:00Z",
                        "currency": {"code": "KRW", "symbol": "₩"},
                        "tiers": [
                            {
                                "tier": "STARTER",
                                "monthly_base_price": 29000,
                                "included_dc_per_month": 1000,
                                "limits": {
                                    "rate_limit_rpm": 600,
                                    "monthly_quota_dc": 2000,
                                },
                            }
                        ],
                    }
                }
            },
        }

    # Add security scheme (Bearer auth)
    if "components" not in openapi_schema:
        openapi_schema["components"] = {}
    if "securitySchemes" not in openapi_schema["components"]:
        openapi_schema["components"]["securitySchemes"] = {}

    openapi_schema["components"]["securitySchemes"]["BearerAuth"] = {
        "type": "http",
        "scheme": "bearer",
        "bearerFormat": "opaque-token",
        "description": "Bearer token in format: sk_{environment}_{key_id}_{secret} (e.g., sk_live_abc123_xyz789...)",
    }

    # Apply security globally
    openapi_schema["security"] = [{"BearerAuth": []}]

    app.openapi_schema = openapi_schema
    return app.openapi_schema


app.openapi = custom_openapi


# ============================================================================
# MTS-3: AI-Friendly Documentation Endpoints
# ============================================================================


@app.get("/.well-known/openapi.json")
async def well_known_openapi():
    """
    OpenAPI 3.1.0 specification endpoint.

    MTS-3.0-DOC: Provides machine-readable API specification at well-known location.
    Returns: OpenAPI 3.1.0 JSON schema
    """
    return JSONResponse(content=app.openapi())


@app.get("/pricing/ssot.json")
async def pricing_ssot():
    """
    Canonical Pricing Single Source of Truth (SSoT).

    MTS-3.0-DOC: Provides machine-readable pricing configuration.
    Returns: Pricing SSoT v0.2.1 JSON
    """
    # Load SSoT from canonical file
    ssot_path = Path(__file__).parent / "pricing" / "fixtures" / "pricing_ssot.json"

    try:
        with open(ssot_path, "r", encoding="utf-8") as f:
            ssot_data = json.load(f)
        return JSONResponse(content=ssot_data)
    except FileNotFoundError:
        # Return 500 with ProblemDetails if SSoT file is missing
        problem = ProblemDetail(
            type="https://iana.org/assignments/http-problem-types#internal-error",
            title="Pricing SSoT Not Found",
            status=500,
            detail="Pricing configuration file not found. Contact support.",
        )
        return JSONResponse(
            status_code=500,
            content=problem.model_dump(exclude_none=True),
            media_type="application/problem+json",
        )
    except json.JSONDecodeError:
        # Return 500 with ProblemDetails if SSoT file is malformed
        problem = ProblemDetail(
            type="https://iana.org/assignments/http-problem-types#internal-error",
            title="Pricing SSoT Parse Error",
            status=500,
            detail="Pricing configuration file is malformed. Contact support.",
        )
        return JSONResponse(
            status_code=500,
            content=problem.model_dump(exclude_none=True),
            media_type="application/problem+json",
        )


@app.get("/docs/function-calling-specs.json")
async def function_calling_specs():
    """
    Function Calling Specifications for AI/Agent integration.

    MTS-3.0-DOC v0.2: Machine-readable tool specifications with examples.
    Returns: Function calling specs JSON with tools, parameters, and examples.
    """
    from datetime import datetime, timezone

    # Derive base URL from environment or default
    base_url = os.getenv("API_BASE_URL", "https://api.decisionproof.ai")

    spec = {
        "spec_version": "2026-02-14.v0.2.0",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "base_url": base_url,
        "auth": {
            "type": "http",
            "scheme": "bearer",
            "bearer_format": "opaque-token",
            "format": "sk_{environment}_{key_id}_{secret} (e.g., sk_live_*, sk_test_*)",
            "docs": f"{base_url}/docs/auth.md",
        },
        "tools": [
            {
                "name": "create_decision_run",
                "description": "Submit a decision run for asynchronous execution",
                "endpoint": "/v1/runs",
                "method": "POST",
                "parameters": {
                    "type": "object",
                    "required": ["workspace_id", "run_id", "plan_id", "input"],
                    "properties": {
                        "workspace_id": {
                            "type": "string",
                            "description": "Workspace identifier",
                            "pattern": "^ws_[a-zA-Z0-9]+$",
                        },
                        "run_id": {
                            "type": "string",
                            "description": "Unique idempotency key (45-day retention)",
                            "pattern": "^run_[a-zA-Z0-9_-]+$",
                        },
                        "plan_id": {
                            "type": "string",
                            "description": "Decision plan identifier",
                            "pattern": "^plan_[a-zA-Z0-9]+$",
                        },
                        "input": {
                            "type": "object",
                            "description": "Input data for decision execution",
                        },
                    },
                },
                "examples": [
                    {
                        "description": "Simple decision request",
                        "request": {
                            "workspace_id": "ws_abc123",
                            "run_id": "run_unique_001",
                            "plan_id": "plan_xyz789",
                            "input": {"question": "What is the capital of France?"},
                        },
                        "response": {
                            "status": 202,
                            "body": {
                                "run_id": "run_unique_001",
                                "status": "queued",
                                "poll_url": "/v1/runs/run_unique_001",
                            },
                        },
                    },
                    {
                        "description": "Complex decision with structured input",
                        "request": {
                            "workspace_id": "ws_abc123",
                            "run_id": "run_unique_002",
                            "plan_id": "plan_analysis",
                            "input": {
                                "data": [1, 2, 3, 4, 5],
                                "operation": "statistical_summary",
                            },
                        },
                        "response": {
                            "status": 202,
                            "body": {
                                "run_id": "run_unique_002",
                                "status": "queued",
                                "poll_url": "/v1/runs/run_unique_002",
                            },
                        },
                    },
                ],
            },
            {
                "name": "get_run_status",
                "description": "Poll run execution status",
                "endpoint": "/v1/runs/{run_id}",
                "method": "GET",
                "parameters": {
                    "type": "object",
                    "required": ["run_id"],
                    "properties": {
                        "run_id": {
                            "type": "string",
                            "description": "Run identifier to poll",
                            "pattern": "^run_[a-zA-Z0-9_-]+$",
                        }
                    },
                },
                "examples": [
                    {
                        "description": "Poll completed run",
                        "request": {"run_id": "run_unique_001"},
                        "response": {
                            "status": 200,
                            "body": {
                                "run_id": "run_unique_001",
                                "status": "completed",
                                "result": {"answer": "Paris"},
                            },
                        },
                    },
                    {
                        "description": "Poll pending run",
                        "request": {"run_id": "run_unique_002"},
                        "response": {
                            "status": 200,
                            "body": {
                                "run_id": "run_unique_002",
                                "status": "running",
                                "poll_after_seconds": 2,
                            },
                        },
                    },
                ],
            },
        ],
        "links": {
            "openapi": f"{base_url}/.well-known/openapi.json",
            "quickstart": f"{base_url}/docs/quickstart.md",
            "auth": f"{base_url}/docs/auth.md",
            "rate_limits": f"{base_url}/docs/rate-limits.md",
            "problem_types": f"{base_url}/docs/problem-types.md",
            "pricing_ssot": f"{base_url}/pricing/ssot.json",
        },
    }

    return JSONResponse(content=spec)


@app.get("/")
async def root() -> dict[str, str]:
    """Root endpoint."""
    return {
        "service": "Decisionproof API",
        "version": "0.4.2.2",
        "status": "running",
        "docs": "/llms.txt",
    }


# ============================================================================
# RC-2: Test Endpoint (Temporary - for verification only)
# ============================================================================

@app.get("/test/rate-limit-429")
async def test_rate_limit_429():
    """
    Test endpoint that always returns 429 with Retry-After.

    RC-2 Verification: Real-world smoke test for:
    - 429 status code
    - Retry-After header presence
    - Problem Details JSON format
    - Opaque instance identifier

    TEMPORARY: Remove after RC-2 verification complete.
    """
    raise HTTPException(status_code=429, detail="Test rate limit exceeded for RC-2 verification")


@app.get("/v1/test-ratelimit")
async def test_ratelimit():
    """
    Test endpoint for RC-3 RateLimit headers verification.

    RC-3 Verification: Tests RateLimit headers on /v1/* endpoints.
    Returns simple JSON response without auth requirements.

    TEMPORARY: Remove after RC-3 verification complete.
    """
    return {"status": "ok", "test": "ratelimit"}


# ============================================================================
# RC-3: Application Lifecycle (Rate Limiter Initialization)
# ============================================================================

@app.on_event("startup")
async def startup_event():
    """Initialize application state on startup.

    RC-3: Initialize rate limiter with default configuration.
    - Production: NoOpRateLimiter (q=60, w=60)
    - Tests can override app.state.rate_limiter with DeterministicTestLimiter
    """
    app.state.rate_limiter = NoOpRateLimiter(quota=60, window=60)


# ============================================================================
# MTS-3: Static File Serving (llms.txt, docs)
# ============================================================================

# Mount static files last (after all routes)
# This serves /public directory at root path
public_dir = Path(__file__).parent.parent.parent.parent / "public"
if public_dir.exists():
    app.mount("/", StaticFiles(directory=str(public_dir), html=True), name="static")
