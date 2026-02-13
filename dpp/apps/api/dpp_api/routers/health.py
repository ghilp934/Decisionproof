"""Health check endpoints."""

import logging
import os

import boto3
from fastapi import APIRouter, Response, status
from pydantic import BaseModel

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
        with engine.connect() as conn:
            conn.execute("SELECT 1")
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

    Returns:
        str: "up" if healthy, error message otherwise
    """
    try:
        # P1-J: List queues to verify SQS connection
        sqs_endpoint = os.getenv("SQS_ENDPOINT_URL", "http://localhost:4566")
        sqs_client = boto3.client(
            "sqs",
            endpoint_url=sqs_endpoint,
            region_name="us-east-1",
            aws_access_key_id="test",
            aws_secret_access_key="test",
        )
        sqs_client.list_queues()
        return "up"
    except Exception as e:
        logger.error(f"SQS health check failed: {e}")
        return f"down: {str(e)[:50]}"


def check_s3() -> str:
    """Check S3 connectivity.

    Returns:
        str: "up" if healthy, error message otherwise
    """
    try:
        # P1-J: List buckets to verify S3 connection
        s3_endpoint = os.getenv("S3_ENDPOINT_URL", "http://localhost:4566")
        s3_client = boto3.client(
            "s3",
            endpoint_url=s3_endpoint,
            region_name="us-east-1",
            aws_access_key_id="test",
            aws_secret_access_key="test",
        )
        s3_client.list_buckets()
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
