"""Environment variable resolution utilities.

Ops Hardening: Canonical env names + fail-fast validation.
"""

import os
from typing import Optional


def get_s3_result_bucket() -> str:
    """Get S3 result bucket from environment.

    Canonical: S3_RESULT_BUCKET
    Fallback (backward compat): DPP_RESULTS_BUCKET

    Returns:
        S3 bucket name

    Raises:
        ValueError: If neither env var is set
    """
    bucket = os.getenv("S3_RESULT_BUCKET") or os.getenv("DPP_RESULTS_BUCKET")
    if not bucket:
        raise ValueError(
            "S3_RESULT_BUCKET (or legacy DPP_RESULTS_BUCKET) is required. "
            "Set S3_RESULT_BUCKET in your environment configuration."
        )
    return bucket


def get_sqs_queue_url() -> str:
    """Get SQS queue URL from environment.

    Required: SQS_QUEUE_URL

    Returns:
        SQS queue URL

    Raises:
        ValueError: If SQS_QUEUE_URL is not set
    """
    queue_url = os.getenv("SQS_QUEUE_URL")
    if not queue_url:
        raise ValueError(
            "SQS_QUEUE_URL is required. "
            "Set SQS_QUEUE_URL to your SQS queue URL (e.g., https://sqs.us-east-1.amazonaws.com/123/dpp-runs)."
        )
    return queue_url


def is_localstack_endpoint(endpoint: Optional[str]) -> bool:
    """Detect if endpoint is LocalStack/local development.

    Markers: localhost, 127.0.0.1, localstack, host.docker.internal

    Args:
        endpoint: Endpoint URL (can be None)

    Returns:
        True if endpoint appears to be LocalStack, False otherwise
    """
    if endpoint is None:
        return False

    endpoint_lower = endpoint.lower()
    markers = ["localhost", "127.0.0.1", "localstack", "host.docker.internal"]
    return any(marker in endpoint_lower for marker in markers)


def is_irsa_environment() -> bool:
    """Detect if running in EKS with IRSA (IAM Roles for Service Accounts).

    P1-1: EKS/IRSA environments provide web identity token authentication.
    Static credentials (aws_access_key_id/aws_secret_access_key) should
    NEVER be injected in IRSA environments, even for LocalStack endpoints.

    IRSA Markers:
    - AWS_ROLE_ARN: IAM role ARN for the service account
    - AWS_WEB_IDENTITY_TOKEN_FILE: Token file path for web identity auth

    Returns:
        True if IRSA markers detected (production EKS), False otherwise
    """
    return bool(
        os.getenv("AWS_ROLE_ARN") or os.getenv("AWS_WEB_IDENTITY_TOKEN_FILE")
    )
