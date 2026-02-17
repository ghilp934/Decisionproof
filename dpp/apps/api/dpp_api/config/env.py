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


def get_dpp_env() -> str:
    """Get DPP environment name.

    Priority:
    1. DPP_ENV (canonical)
    2. DP_ENV (legacy worker compat)
    3. Default: "local"

    Returns:
        Environment name (lowercase)
    """
    return (
        os.getenv("DPP_ENV")
        or os.getenv("DP_ENV")
        or "local"
    ).lower()


def is_production_env() -> bool:
    """Determine if running in production/operation environment (A2).

    Production criteria (any of):
    - DPP_ENV or DP_ENV in {"prod", "production"}
    - IRSA environment detected (EKS production)

    Returns:
        True if production environment, False otherwise
    """
    env = get_dpp_env()
    return env in {"prod", "production"} or is_irsa_environment()


def has_static_aws_credentials() -> bool:
    """Check if static AWS credentials are present in environment.

    Static credentials markers:
    - AWS_ACCESS_KEY_ID
    - AWS_SECRET_ACCESS_KEY
    - AWS_SESSION_TOKEN

    Returns:
        True if any static credentials detected, False otherwise
    """
    return bool(
        os.getenv("AWS_ACCESS_KEY_ID")
        or os.getenv("AWS_SECRET_ACCESS_KEY")
        or os.getenv("AWS_SESSION_TOKEN")
    )


def get_aws_region(require_in_prod: bool = True) -> str:
    """Get AWS region from environment (A3).

    Priority:
    1. AWS_REGION (canonical)
    2. AWS_DEFAULT_REGION (boto3 compat)

    Args:
        require_in_prod: If True, fail in production if region missing

    Returns:
        AWS region name

    Raises:
        ValueError: If region missing and required in production
    """
    region = os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION")

    if not region and require_in_prod and is_production_env():
        raise ValueError(
            "AWS_REGION (or AWS_DEFAULT_REGION) is required in production environment. "
            "Set AWS_REGION in your deployment configuration."
        )

    return region or "us-east-1"  # Default fallback for local/dev


def assert_no_static_aws_creds(service_name: str) -> None:
    """Assert no static AWS credentials in production (A1, A5).

    Rules:
    - A1: IRSA environment → NEVER allow static credentials (no exceptions)
    - A5: Production environment → Deny static credentials unless DPP_ALLOW_STATIC_AWS_CREDS=1

    Args:
        service_name: AWS service name (for error messages)

    Raises:
        ValueError: If static credentials detected and not allowed
    """
    if not has_static_aws_credentials():
        return  # No static creds, OK

    # A1: IRSA environment NEVER allows static credentials
    if is_irsa_environment():
        raise ValueError(
            f"PRODUCTION GUARDRAIL (A1): Static AWS credentials detected in IRSA environment for {service_name}. "
            f"IRSA provides role-based authentication via AWS_ROLE_ARN / AWS_WEB_IDENTITY_TOKEN_FILE. "
            f"Remove AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, and AWS_SESSION_TOKEN from deployment config. "
            f"This restriction has NO override toggle for security."
        )

    # A5: Production environment denies static credentials unless explicitly allowed
    if is_production_env():
        if os.getenv("DPP_ALLOW_STATIC_AWS_CREDS") != "1":
            raise ValueError(
                f"PRODUCTION GUARDRAIL (A5): Static AWS credentials detected for {service_name} in production. "
                f"Production should use IAM roles (ECS Task Role, EKS IRSA) instead of static keys. "
                f"To override (NOT recommended): DPP_ALLOW_STATIC_AWS_CREDS=1"
            )


def assert_no_custom_endpoint_in_prod(endpoint_url: Optional[str], service_name: str) -> None:
    """Assert no custom AWS endpoint in production (A4).

    Rule:
    - Production environment → Deny custom endpoints unless LocalStack or DPP_ALLOW_CUSTOM_AWS_ENDPOINTS=1

    Args:
        endpoint_url: Custom endpoint URL (can be None)
        service_name: AWS service name (for error messages)

    Raises:
        ValueError: If custom endpoint detected and not allowed in production
    """
    if not endpoint_url:
        return  # No custom endpoint, OK

    # LocalStack endpoints are always OK (dev/test)
    if is_localstack_endpoint(endpoint_url):
        return

    # Production environment denies custom endpoints unless explicitly allowed
    if is_production_env():
        if os.getenv("DPP_ALLOW_CUSTOM_AWS_ENDPOINTS") != "1":
            raise ValueError(
                f"PRODUCTION GUARDRAIL (A4): Custom {service_name.upper()}_ENDPOINT_URL detected in production: {endpoint_url}. "
                f"Production should use standard AWS endpoints. "
                f"To override (NOT recommended): DPP_ALLOW_CUSTOM_AWS_ENDPOINTS=1"
            )


def get_s3_server_side_encryption_kwargs(endpoint_url: Optional[str]) -> dict[str, str]:
    """Get S3 ServerSideEncryption kwargs for put_object (S1, S2, S3).

    Rules:
    - S1: Production → default ServerSideEncryption=AES256
    - S2: S3_SSE_MODE controls encryption mode
    - S3: LocalStack → no SSE headers by default (compatibility)

    Environment variables:
    - S3_SSE_MODE: "AES256" (default prod), "aws:kms"/"kms", "none"/"off"
    - S3_SSE_KMS_KEY_ID: KMS key ID (only if mode=kms)

    Args:
        endpoint_url: S3 endpoint URL (for LocalStack detection)

    Returns:
        Dictionary of SSE kwargs for boto3 put_object(**kwargs)
    """
    # S3: LocalStack → no SSE by default (compatibility)
    if is_localstack_endpoint(endpoint_url):
        return {}

    # Determine SSE mode
    sse_mode = os.getenv("S3_SSE_MODE", "").lower()

    # Default: production=AES256, non-production=none
    if not sse_mode:
        sse_mode = "aes256" if is_production_env() else "none"

    # S2: Apply SSE mode
    if sse_mode in {"none", "off"}:
        return {}

    if sse_mode in {"aws:kms", "kms"}:
        kwargs = {"ServerSideEncryption": "aws:kms"}
        kms_key_id = os.getenv("S3_SSE_KMS_KEY_ID")
        if kms_key_id:
            kwargs["SSEKMSKeyId"] = kms_key_id
        return kwargs

    # Default: AES256
    return {"ServerSideEncryption": "AES256"}
