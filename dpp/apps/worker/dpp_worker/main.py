"""DPP Worker main entry point."""

import logging
import os

import boto3
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

# P0-2: Removed sys.path manipulation - PYTHONPATH handles this in Dockerfile
# Container images include dpp_api via COPY and ENV PYTHONPATH

from dpp_api.budget import BudgetManager
from dpp_api.db.redis_client import RedisClient
from dpp_api.utils import configure_json_logging
from dpp_worker.loops.sqs_loop import WorkerLoop

# P1-H: Configure structured JSON logging (same as API)
configure_json_logging(log_level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger(__name__)


def main() -> None:
    """Main entry point for worker."""
    # ENV-01: Configuration from environment with fail-fast
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        # Default to docker-compose configuration (ENV-01: unified to 'dpp')
        database_url = "postgresql://dpp_user:dpp_pass@localhost:5432/dpp"
        logger.warning(
            "DATABASE_URL not set, using default: %s",
            database_url.replace("dpp_pass", "***"),
        )

    # P0-A: NO DEFAULTS for production safety
    sqs_queue_url = os.getenv("SQS_QUEUE_URL")
    if not sqs_queue_url:
        raise ValueError(
            "SQS_QUEUE_URL is required. "
            "Set SQS_QUEUE_URL (and optionally SQS_ENDPOINT_URL for LocalStack)."
        )

    # P0-B: Canonical env var with backward compatibility
    s3_result_bucket = os.getenv("S3_RESULT_BUCKET") or os.getenv("DPP_RESULTS_BUCKET")
    if not s3_result_bucket:
        raise ValueError(
            "S3_RESULT_BUCKET (or DPP_RESULTS_BUCKET) is required."
        )

    # P0-A: AWS clients - NO defaults for endpoint_url
    sqs_endpoint = os.getenv("SQS_ENDPOINT_URL")  # None if not set
    s3_endpoint = os.getenv("S3_ENDPOINT_URL")    # None if not set

    # P0-A: Enhanced LocalStack detection
    def is_localstack(endpoint: str | None) -> bool:
        """Check if endpoint is LocalStack or local development."""
        if endpoint is None:
            return False
        endpoint_lower = endpoint.lower()
        return any(
            marker in endpoint_lower
            for marker in ["localhost", "127.0.0.1", "localstack", "host.docker.internal"]
        )

    def is_irsa_environment() -> bool:
        """P1-1: Detect EKS/IRSA environment (web identity tokens).

        IRSA environments use AWS_ROLE_ARN + AWS_WEB_IDENTITY_TOKEN_FILE.
        NEVER inject static credentials in IRSA environments.
        """
        return bool(os.getenv("AWS_ROLE_ARN") or os.getenv("AWS_WEB_IDENTITY_TOKEN_FILE"))

    # SQS client: conditional endpoint_url
    sqs_kwargs = {"region_name": os.getenv("AWS_REGION", "us-east-1")}

    if sqs_endpoint:
        sqs_kwargs["endpoint_url"] = sqs_endpoint
        # P1-1: Test credentials ONLY for LocalStack AND NOT in IRSA/production
        if (
            is_localstack(sqs_endpoint)
            and not os.getenv("AWS_ACCESS_KEY_ID")
            and not is_irsa_environment()
        ):
            sqs_kwargs["aws_access_key_id"] = "test"
            sqs_kwargs["aws_secret_access_key"] = "test"
            logger.info("Using LocalStack test credentials for SQS")

    sqs_client = boto3.client("sqs", **sqs_kwargs)

    # S3 client: conditional endpoint_url
    s3_kwargs = {"region_name": os.getenv("AWS_REGION", "us-east-1")}

    if s3_endpoint:
        s3_kwargs["endpoint_url"] = s3_endpoint
        # P1-1: Test credentials ONLY for LocalStack AND NOT in IRSA/production
        if (
            is_localstack(s3_endpoint)
            and not os.getenv("AWS_ACCESS_KEY_ID")
            and not is_irsa_environment()
        ):
            s3_kwargs["aws_access_key_id"] = "test"
            s3_kwargs["aws_secret_access_key"] = "test"
            logger.info("Using LocalStack test credentials for S3")

    s3_client = boto3.client("s3", **s3_kwargs)

    # Database
    engine = create_engine(database_url, echo=False)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    db_session = SessionLocal()

    # Redis
    redis_client = RedisClient.get_client()

    # Budget manager
    budget_manager = BudgetManager(redis_client, db_session)

    # Worker loop
    # P0-1: Pass session_factory for HeartbeatThread thread-safety
    worker = WorkerLoop(
        sqs_client=sqs_client,
        s3_client=s3_client,
        db_session=db_session,
        session_factory=SessionLocal,
        budget_manager=budget_manager,
        queue_url=sqs_queue_url,
        result_bucket=s3_result_bucket,
        redis_client=redis_client,
        lease_ttl_sec=120,
    )

    logger.info("Starting DPP Worker...")
    logger.info(f"Queue URL: {sqs_queue_url}")
    logger.info(f"Result bucket: {s3_result_bucket}")

    # P0-3: Create readiness file for k8s readinessProbe
    ready_file_path = "/tmp/worker-ready"
    try:
        with open(ready_file_path, "w") as f:
            f.write("ready\n")
        logger.info(f"Readiness file created: {ready_file_path}")
    except Exception as e:
        logger.error(f"Failed to create readiness file: {e}")
        raise

    try:
        worker.run_forever()
    except KeyboardInterrupt:
        logger.info("Worker stopped by user")
    finally:
        # P0-3: Remove readiness file on shutdown
        try:
            if os.path.exists(ready_file_path):
                os.remove(ready_file_path)
        except Exception:
            pass
        db_session.close()
        logger.info("Worker shutdown complete")


if __name__ == "__main__":
    main()
