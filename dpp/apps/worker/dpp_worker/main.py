"""DPP Worker main entry point."""

import logging
import os
from pathlib import Path

import boto3
from botocore.config import Config
from sqlalchemy.orm import Session

# P0-2: Removed sys.path manipulation - PYTHONPATH handles this in Dockerfile
# Container images include dpp_api via COPY and ENV PYTHONPATH

from dpp_api.budget import BudgetManager
from dpp_api.config import env
from dpp_api.db.engine import build_engine, build_sessionmaker
from dpp_api.db.redis_client import RedisClient
from dpp_api.utils import configure_json_logging
from dpp_worker.loops.sqs_loop import WorkerLoop

# P1-H: Configure structured JSON logging (same as API)
configure_json_logging(log_level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger(__name__)


def main() -> None:
    """Main entry point for worker."""
    # P0-2: Defensive clear of any pre-existing readiness file (e.g., from old image layers)
    Path("/tmp/worker-ready").unlink(missing_ok=True)

    # ENV-01: Configuration from environment with fail-fast
    # Use canonical DPP_ENV/DP_ENV resolution (env.get_dpp_env)
    dpp_env_name = env.get_dpp_env()
    database_url = os.getenv("DATABASE_URL")

    if env.is_production_env():
        # Production: DATABASE_URL is required (fail-fast)
        if not database_url:
            raise RuntimeError(
                f"DATABASE_URL environment variable is required in production (environment: {dpp_env_name}). "
                "Check deployment configuration and secrets injection."
            )
        logger.info(f"DATABASE_URL: set (production mode, environment: {dpp_env_name})")
    else:
        # Development/CI: fallback to docker-compose default
        if not database_url:
            database_url = "postgresql://dpp_user:dpp_pass@localhost:5432/dpp"
            logger.info("DATABASE_URL: unset, using docker-compose default")
        else:
            logger.info("DATABASE_URL: set")

    # P0-A: NO DEFAULTS for production safety
    sqs_queue_url = env.get_sqs_queue_url()  # Raises ValueError if missing
    s3_result_bucket = env.get_s3_result_bucket()  # Raises ValueError if missing

    # AWS Guardrails (P0): Production validation
    sqs_endpoint = os.getenv("SQS_ENDPOINT_URL")
    s3_endpoint = os.getenv("S3_ENDPOINT_URL")

    env.assert_no_custom_endpoint_in_prod(sqs_endpoint, "sqs")  # A4
    env.assert_no_custom_endpoint_in_prod(s3_endpoint, "s3")    # A4
    env.assert_no_static_aws_creds("worker")  # A1, A5

    # AWS Region (A3: required in production)
    region_name = env.get_aws_region(require_in_prod=True)

    # SQS client with Guardrails and Config
    sqs_config = Config(
        region_name=region_name,
        retries={"max_attempts": 3, "mode": "standard"},
        connect_timeout=10,
        read_timeout=30,
    )

    sqs_kwargs = {"config": sqs_config}

    if sqs_endpoint:
        sqs_kwargs["endpoint_url"] = sqs_endpoint
        # A6: Test credentials ONLY for LocalStack AND NOT in IRSA/production
        if (
            env.is_localstack_endpoint(sqs_endpoint)
            and not os.getenv("AWS_ACCESS_KEY_ID")
            and not env.is_irsa_environment()
        ):
            sqs_kwargs["aws_access_key_id"] = "test"
            sqs_kwargs["aws_secret_access_key"] = "test"
            logger.info("Using LocalStack test credentials for SQS")

    sqs_client = boto3.client("sqs", **sqs_kwargs)

    # S3 client with Guardrails and Config
    s3_config = Config(
        region_name=region_name,
        signature_version="s3v4",
        retries={"max_attempts": 3, "mode": "standard"},
        connect_timeout=10,
        read_timeout=60,
    )

    s3_kwargs = {"config": s3_config}

    if s3_endpoint:
        s3_kwargs["endpoint_url"] = s3_endpoint
        # A6: Test credentials ONLY for LocalStack AND NOT in IRSA/production
        if (
            env.is_localstack_endpoint(s3_endpoint)
            and not os.getenv("AWS_ACCESS_KEY_ID")
            and not env.is_irsa_environment()
        ):
            s3_kwargs["aws_access_key_id"] = "test"
            s3_kwargs["aws_secret_access_key"] = "test"
            logger.info("Using LocalStack test credentials for S3")

    s3_client = boto3.client("s3", **s3_kwargs)

    # Database (using SSOT engine builder)
    engine = build_engine(database_url)
    SessionLocal = build_sessionmaker(engine)
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
