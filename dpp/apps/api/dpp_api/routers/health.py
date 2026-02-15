"""Health check endpoints."""

import logging
import os

import boto3
from fastapi import APIRouter, Response, status
from pydantic import BaseModel
from sqlalchemy import text

from dpp_api.db.redis_client import RedisClient
from dpp_api.db.session import engine

router = APIRouter()
logger = logging.getLogger(__name__)


class HealthResponse(BaseModel):
    """Health check response model."""

    status: str
    version: str
    services: dict[str, str]


def check_database() -> str:
    """Check database connectivity.

    Returns:
        str: "up" if healthy, error message otherwise
    """
    try:
        # P1-J: Execute simple query to verify DB connection
        # P0 Hotfix: Use text() for SQLAlchemy 2.0 compatibility
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return "up"
    except Exception as e:
        logger.error(f"Database health check failed: {e}")
        return f"down: {str(e)[:50]}"


def check_redis() -> str:
    """Check Redis connectivity.

    Returns:
        str: "up" if healthy, error message otherwise
    """
    try:
        # P1-J: PING Redis to verify connection
        redis_client = RedisClient.get_client()
        redis_client.ping()
        return "up"
    except Exception as e:
        logger.error(f"Redis health check failed: {e}")
        return f"down: {str(e)[:50]}"


def check_sqs() -> str:
    """Check SQS connectivity.

    P0-2: Reuse get_sqs_client() instead of hardcoding test credentials.

    Returns:
        str: "up" if healthy, error message otherwise
    """
    try:
        from dpp_api.queue.sqs_client import get_sqs_client

        sqs_client = get_sqs_client()
        # Simple list_queues to verify connectivity
        sqs_client.client.list_queues()
        return "up"
    except Exception as e:
        logger.error(f"SQS health check failed: {e}")
        return f"down: {str(e)[:50]}"


def check_s3() -> str:
    """Check S3 connectivity.

    P0-2 + P1-1: Bucket-scoped check (no ListAllMyBuckets).
    Uses head_bucket on DPP_RESULTS_BUCKET instead.

    Returns:
        str: "up" if healthy, error message otherwise
    """
    try:
        # P1-1: Use bucket-scoped check
        results_bucket = os.getenv("DPP_RESULTS_BUCKET")
        if not results_bucket:
            return "down: DPP_RESULTS_BUCKET not configured"

        # P0-2: Conditional endpoint_url and credentials
        s3_endpoint = os.getenv("S3_ENDPOINT_URL")
        s3_kwargs = {"region_name": os.getenv("AWS_REGION", "us-east-1")}

        if s3_endpoint:
            s3_kwargs["endpoint_url"] = s3_endpoint
            # Dummy credentials only for LocalStack
            if not os.getenv("AWS_ACCESS_KEY_ID"):
                s3_kwargs["aws_access_key_id"] = "test"
                s3_kwargs["aws_secret_access_key"] = "test"

        # Explicit credentials if provided
        if os.getenv("AWS_ACCESS_KEY_ID"):
            s3_kwargs["aws_access_key_id"] = os.getenv("AWS_ACCESS_KEY_ID")
        if os.getenv("AWS_SECRET_ACCESS_KEY"):
            s3_kwargs["aws_secret_access_key"] = os.getenv("AWS_SECRET_ACCESS_KEY")

        s3_client = boto3.client("s3", **s3_kwargs)

        # P1-1: head_bucket instead of list_buckets
        s3_client.head_bucket(Bucket=results_bucket)
        return "up"
    except Exception as e:
        logger.error(f"S3 health check failed: {e}")
        return f"down: {str(e)[:50]}"


@router.get("/health", response_model=HealthResponse)
async def health_check() -> HealthResponse:
    """
    Health check endpoint.

    Returns service status and dependency health.
    Always returns 200 OK (use /readyz for dependency checks).
    """
    return HealthResponse(
        status="healthy",
        version="0.4.2.2",
        services={
            "api": "up",
            "database": check_database(),
            "redis": check_redis(),
            "s3": check_s3(),
            "sqs": check_sqs(),
        },
    )


@router.get("/readyz", response_model=HealthResponse)
async def readiness_check(response: Response) -> HealthResponse:
    """
    Readiness check endpoint (P1-J).

    Returns whether the service is ready to accept requests.
    Returns 503 if any dependency is down.
    """
    # P1-J: Check all critical dependencies
    services = {
        "api": "up",
        "database": check_database(),
        "redis": check_redis(),
        "s3": check_s3(),
        "sqs": check_sqs(),
    }

    # If any service is down, return 503
    any_down = any("down" in svc_status for svc_status in services.values())

    if any_down:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return HealthResponse(
            status="not_ready",
            version="0.4.2.2",
            services=services,
        )

    return HealthResponse(
        status="ready",
        version="0.4.2.2",
        services=services,
    )
