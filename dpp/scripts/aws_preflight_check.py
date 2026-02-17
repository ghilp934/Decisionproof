#!/usr/bin/env python3
"""AWS Production Preflight Validator (P0).

운영 배포 전 AWS 환경/계정/권한을 빠르게 확인하는 도구.

Usage:
    # Offline mode (default): 환경변수/토글만 검사 (네트워크 호출 없음)
    python scripts/aws_preflight_check.py

    # Online mode: sts:GetCallerIdentity 호출 + 계정 검증
    DPP_AWS_PREFLIGHT_ONLINE=1 python scripts/aws_preflight_check.py

Exit Codes:
    0: PASS (all checks passed)
    1: FAIL (validation failed)

Environment Variables:
    DP_ENV or DPP_ENV: Environment name (prod/production for checks)
    AWS_REGION or AWS_DEFAULT_REGION: AWS region (required in prod)
    AWS_ROLE_ARN / AWS_WEB_IDENTITY_TOKEN_FILE: IRSA markers
    AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY: Static credentials (detected)
    S3_ENDPOINT_URL / SQS_ENDPOINT_URL: Custom endpoints (detected)
    DPP_AWS_PREFLIGHT_ONLINE: "1" enables online mode
    DPP_AWS_ACCOUNT_ID_EXPECTED: Expected AWS account ID (online mode only)
"""

import argparse
import os
import sys


def _is_localstack_endpoint(endpoint: str | None) -> bool:
    """Check if endpoint is LocalStack/local development."""
    if endpoint is None:
        return False
    endpoint_lower = endpoint.lower()
    markers = ["localhost", "127.0.0.1", "localstack", "host.docker.internal"]
    return any(marker in endpoint_lower for marker in markers)


def _is_irsa_environment() -> bool:
    """Detect if running in EKS with IRSA."""
    return bool(
        os.getenv("AWS_ROLE_ARN") or os.getenv("AWS_WEB_IDENTITY_TOKEN_FILE")
    )


def _is_production_env() -> bool:
    """Determine if production environment."""
    env = (os.getenv("DPP_ENV") or os.getenv("DP_ENV") or "local").lower()
    return env in {"prod", "production"} or _is_irsa_environment()


def _has_static_aws_credentials() -> bool:
    """Check if static AWS credentials are present."""
    return bool(
        os.getenv("AWS_ACCESS_KEY_ID")
        or os.getenv("AWS_SECRET_ACCESS_KEY")
        or os.getenv("AWS_SESSION_TOKEN")
    )


def validate_offline() -> tuple[bool, list[str]]:
    """Offline validation (no network calls).

    Returns:
        (is_valid, error_messages)
    """
    errors = []

    # Check: Production environment detection
    is_prod = _is_production_env()
    is_irsa = _is_irsa_environment()
    env_name = os.getenv("DPP_ENV") or os.getenv("DP_ENV") or "local"

    print(f"Environment: {env_name}")
    print(f"Production: {is_prod}")
    print(f"IRSA: {is_irsa}")

    if not is_prod:
        print("INFO: Non-production environment, skipping most checks.")
        return True, []

    # A1: IRSA + Static credentials = FAIL
    if is_irsa and _has_static_aws_credentials():
        errors.append(
            "ERROR (A1): IRSA environment detected with static AWS credentials. "
            "Remove AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY (use IRSA role-based auth)."
        )

    # A3: Production + Missing region = FAIL
    region = os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION")
    if not region:
        errors.append(
            "ERROR (A3): AWS_REGION (or AWS_DEFAULT_REGION) required in production. "
            "Set AWS_REGION in deployment config."
        )
    else:
        print(f"AWS Region: {region}")

    # A4: Production + Custom endpoint (non-LocalStack) = WARN/FAIL
    s3_endpoint = os.getenv("S3_ENDPOINT_URL")
    sqs_endpoint = os.getenv("SQS_ENDPOINT_URL")

    for endpoint, service in [(s3_endpoint, "S3"), (sqs_endpoint, "SQS")]:
        if endpoint and not _is_localstack_endpoint(endpoint):
            if os.getenv("DPP_ALLOW_CUSTOM_AWS_ENDPOINTS") != "1":
                errors.append(
                    f"ERROR (A4): Custom {service}_ENDPOINT_URL in production: {endpoint}. "
                    f"Override: DPP_ALLOW_CUSTOM_AWS_ENDPOINTS=1 (NOT recommended)."
                )
            else:
                print(f"WARNING: Custom {service}_ENDPOINT_URL allowed (override active): {endpoint}")

    # A5: Production + Static credentials = WARN/FAIL
    if _has_static_aws_credentials() and not is_irsa:
        if os.getenv("DPP_ALLOW_STATIC_AWS_CREDS") != "1":
            errors.append(
                "ERROR (A5): Static AWS credentials in production. "
                "Use IAM roles (ECS Task Role, EKS IRSA). "
                "Override: DPP_ALLOW_STATIC_AWS_CREDS=1 (NOT recommended)."
            )
        else:
            print("WARNING: Static AWS credentials allowed (override active).")

    return len(errors) == 0, errors


def validate_online() -> tuple[bool, list[str]]:
    """Online validation (sts:GetCallerIdentity).

    Returns:
        (is_valid, error_messages)
    """
    errors = []

    try:
        import boto3
    except ImportError:
        errors.append("ERROR: boto3 not installed. Install with: pip install boto3")
        return False, errors

    try:
        sts = boto3.client("sts")
        response = sts.get_caller_identity()

        account = response.get("Account")
        arn = response.get("Arn")
        user_id = response.get("UserId")

        print(f"AWS Account: {account}")
        print(f"AWS ARN: {arn}")
        print(f"AWS User ID: {user_id}")

        # Validate expected account ID
        expected_account = os.getenv("DPP_AWS_ACCOUNT_ID_EXPECTED")
        if expected_account and account != expected_account:
            errors.append(
                f"ERROR: AWS Account mismatch. Expected: {expected_account}, Got: {account}. "
                f"Check credentials or DPP_AWS_ACCOUNT_ID_EXPECTED."
            )

    except Exception as e:
        errors.append(f"ERROR: sts:GetCallerIdentity failed: {e}")

    return len(errors) == 0, errors


def main():
    parser = argparse.ArgumentParser(
        description="AWS Production Preflight Validator",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Offline mode (default)
  python scripts/aws_preflight_check.py

  # Online mode with account validation
  DPP_AWS_PREFLIGHT_ONLINE=1 DPP_AWS_ACCOUNT_ID_EXPECTED=123456789012 python scripts/aws_preflight_check.py
        """,
    )
    args = parser.parse_args()

    print("=================================================================")
    print("AWS Production Preflight Check (P0)")
    print("=================================================================")

    # Offline checks (always run)
    offline_valid, offline_errors = validate_offline()

    all_errors = offline_errors

    # Online checks (optional)
    online_mode = os.getenv("DPP_AWS_PREFLIGHT_ONLINE") == "1"
    if online_mode:
        print("\nOnline Mode: Calling sts:GetCallerIdentity...")
        online_valid, online_errors = validate_online()
        all_errors.extend(online_errors)

    # Print results
    print("=================================================================")
    if all_errors:
        print("FAIL: AWS preflight validation failed")
        print()
        for error in all_errors:
            print(error)
        print()
        print("Fix errors above and re-run preflight check.")
        sys.exit(1)
    else:
        mode = "online" if online_mode else "offline"
        print(f"PASS: AWS preflight validation successful ({mode} mode)")
        sys.exit(0)


if __name__ == "__main__":
    main()
