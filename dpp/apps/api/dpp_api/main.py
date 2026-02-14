"""DPP API - FastAPI Application Entry Point."""

import json
import logging
import os
import uuid
from pathlib import Path

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from dpp_api.context import request_id_var
from dpp_api.enforce import PlanViolationError
from dpp_api.routers import health, runs, usage
from dpp_api.schemas import ProblemDetail
from dpp_api.utils import configure_json_logging

# MTS-3.1: Base URL from environment variables
base_url = os.getenv("API_BASE_URL", "https://api.decisionwise.ai")
sandbox_url = os.getenv("API_SANDBOX_URL", "https://sandbox-api.decisionwise.ai")

app = FastAPI(
    title="Decisionwise API",
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
    expose_headers=["X-DPP-Cost-Reserved", "X-DPP-Cost-Actual", "X-DPP-Cost-Minimum-Fee"],  # P1-6
)


# ============================================================================
# P1-9: Request ID Middleware
# ============================================================================


@app.middleware("http")
async def request_id_middleware(request: Request, call_next):
    """Generate and propagate request_id for observability.

    P1-9: Each request gets a unique request_id (UUID v4).
    - Accepts X-Request-ID header from client (optional)
    - Generates new UUID if not provided
    - Sets context variable for logging
    - Returns X-Request-ID in response headers
    """
    # Get or generate request_id
    request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())

    # Set context variable for logging
    request_id_var.set(request_id)

    # Process request
    response = await call_next(request)

    # Add request_id to response headers
    response.headers["X-Request-ID"] = request_id

    return response


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
# RFC 9457 Global Exception Handlers
# ============================================================================


@app.exception_handler(PlanViolationError)
async def plan_violation_handler(request: Request, exc: PlanViolationError) -> JSONResponse:
    """Handle plan violation errors with RFC 9457 Problem Details format.

    Returns application/problem+json with plan-specific error details.
    P1-2: Includes Retry-After header for 429 responses using exc.retry_after field.
    """
    problem = ProblemDetail(
        type=exc.error_type,
        title=exc.title,
        status=exc.status_code,
        detail=exc.detail,
        instance=request.url.path,
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
    """
    # P0-1: Don't force-cast detail to str - preserve dict if provided
    detail_value = exc.detail if exc.detail is not None else _get_title_for_status(exc.status_code)

    problem = ProblemDetail(
        type=f"https://dpp.example.com/problems/http-{exc.status_code}",
        title=_get_title_for_status(exc.status_code),
        status=exc.status_code,
        detail=detail_value,
        instance=request.url.path,
    )

    return JSONResponse(
        status_code=exc.status_code,
        content=problem.model_dump(exclude_none=True),
        media_type="application/problem+json",
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    """Handle request validation errors with RFC 9457 Problem Details format.

    Returns 400 Bad Request with application/problem+json.
    """
    # Extract first error for detail message
    first_error = exc.errors()[0] if exc.errors() else {}
    field = ".".join(str(loc) for loc in first_error.get("loc", []))
    msg = first_error.get("msg", "Validation error")

    problem = ProblemDetail(
        type="https://dpp.example.com/problems/validation-error",
        title="Request Validation Failed",
        status=status.HTTP_400_BAD_REQUEST,
        detail=f"Invalid field '{field}': {msg}",
        instance=request.url.path,
    )

    return JSONResponse(
        status_code=status.HTTP_400_BAD_REQUEST,
        content=problem.model_dump(exclude_none=True),
        media_type="application/problem+json",
    )


@app.exception_handler(Exception)
async def general_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Handle uncaught exceptions with RFC 9457 Problem Details format.

    Returns 500 Internal Server Error with application/problem+json.
    """
    problem = ProblemDetail(
        type="https://dpp.example.com/problems/internal-error",
        title="Internal Server Error",
        status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail="An unexpected error occurred. Please try again later.",
        instance=request.url.path,
    )

    # Log the actual exception for debugging (TODO: add structured logging)
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
    base_url = os.getenv("API_BASE_URL", "https://api.decisionwise.ai")

    spec = {
        "spec_version": "2026-02-14.v0.2.0",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "base_url": base_url,
        "auth": {
            "type": "api_key",
            "header": "X-API-Key",
            "format": "dw_live_* or dw_test_*",
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
        "service": "Decisionwise API",
        "version": "0.4.2.2",
        "status": "running",
        "docs": "/llms.txt",
    }


# ============================================================================
# MTS-3: Static File Serving (llms.txt, docs)
# ============================================================================

# Mount static files last (after all routes)
# This serves /public directory at root path
public_dir = Path(__file__).parent.parent.parent.parent / "public"
if public_dir.exists():
    app.mount("/", StaticFiles(directory=str(public_dir), html=True), name="static")
