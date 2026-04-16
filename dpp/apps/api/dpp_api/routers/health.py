"""Health check endpoints.

Ops Hardening v2: Use centralized env helpers.
"""

import asyncio
import logging
import os

import boto3
from fastapi import APIRouter, Response, status
from pydantic import BaseModel
from sqlalchemy import text

from dpp_api.billing.active_preflight import get_billing_preflight_status
from dpp_api.config.env import get_s3_result_bucket, is_irsa_environment, is_localstack_endpoint
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

    Ops Hardening v2 + P1: Use GetQueueAttributes on configured queue (more specific than list_queues).

    Returns:
        str: "up" if healthy, error message otherwise
    """
    try:
        from dpp_api.queue.sqs_client import get_sqs_client

        sqs_client = get_sqs_client()
        # P1: Use GetQueueAttributes on the configured queue URL (more specific check)
        # This verifies both connectivity AND that the queue exists/is accessible
        sqs_client.client.get_queue_attributes(
            QueueUrl=sqs_client.queue_url,
            AttributeNames=["ApproximateNumberOfMessages"]
        )
        return "up"
    except ValueError as e:
        # Config error (missing env var)
        logger.error(f"SQS config error: {e}")
        return f"down: config error - {str(e)[:40]}"
    except Exception as e:
        logger.error(f"SQS health check failed: {e}")
        return f"down: {str(e)[:50]}"


def check_s3() -> str:
    """Check S3 connectivity.

    Ops Hardening v2: Use centralized bucket resolver (fail-fast if missing).

    Returns:
        str: "up" if healthy, error message otherwise
    """
    try:
        # Ops Hardening v2: Use centralized resolver (raises ValueError if missing)
        results_bucket = get_s3_result_bucket()

        # P0-2: Conditional endpoint_url and credentials
        s3_endpoint = os.getenv("S3_ENDPOINT_URL")
        s3_kwargs = {"region_name": os.getenv("AWS_REGION", "us-east-1")}

        if s3_endpoint:
            s3_kwargs["endpoint_url"] = s3_endpoint
            # P1-1: Test credentials ONLY for LocalStack AND NOT in IRSA/production
            if (
                is_localstack_endpoint(s3_endpoint)
                and not os.getenv("AWS_ACCESS_KEY_ID")
                and not is_irsa_environment()
            ):
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
    except ValueError as e:
        # Config error (missing env var)
        logger.error(f"S3 config error: {e}")
        return f"down: config error - {str(e)[:40]}"
    except Exception as e:
        logger.error(f"S3 health check failed: {e}")
        return f"down: {str(e)[:50]}"


@router.get("/health", response_model=HealthResponse)
async def health_check() -> HealthResponse:
    """
    Liveness and startup probe endpoint.

    Returns 200 immediately with NO dependency checks.
    Calling sync check_* functions from an async handler blocks the event loop,
    causing startup probe (timeout=5s) to hit context deadline exceeded even
    when the app itself is healthy.
    Use /readyz for full dependency health (readiness probe only).
    """
    return HealthResponse(
        status="healthy",
        version="0.4.2.2",
        services={"api": "up"},
    )


@router.get("/readyz", response_model=HealthResponse)
async def readiness_check(response: Response) -> HealthResponse:
    """
    Readiness check endpoint (P1-J).

    Returns whether the service is ready to accept requests.
    Returns 503 if any dependency is down.

    Sync check_* functions run in a thread pool via asyncio.to_thread so they
    do not block the event loop. Without this, a 15s Redis timeout would freeze
    the entire server for the duration of the probe.
    """
    # P1-J: Check all critical dependencies concurrently, off the event loop
    # P6.1: billing_secrets reads cached preflight result — NO network call
    db_status, redis_status, s3_status, sqs_status = await asyncio.gather(
        asyncio.to_thread(check_database),
        asyncio.to_thread(check_redis),
        asyncio.to_thread(check_s3),
        asyncio.to_thread(check_sqs),
    )

    billing_cache = get_billing_preflight_status()
    if "status" in billing_cache and billing_cache["status"] == "skipped":
        billing_status = "skipped"
    elif all(v == "ok" for v in billing_cache.values()):
        billing_status = "up"
    else:
        failed = [f"{k}:{v}" for k, v in billing_cache.items() if v != "ok"]
        billing_status = "down: " + ", ".join(failed)

    services = {
        "api": "up",
        "database": db_status,
        "redis": redis_status,
        "s3": s3_status,
        "sqs": sqs_status,
        "billing_secrets": billing_status,
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
