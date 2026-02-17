"""S3 Storage Client for DPP.

P1-1: Presigned URL generation for completed run results.
Ops Hardening v2: Remove silent bucket defaults, fail-fast on misconfig.
"""

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Optional

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from dpp_api.config import env

logger = logging.getLogger(__name__)


class S3Client:
    """S3 client for result storage and presigned URL generation."""

    def __init__(
        self,
        bucket: Optional[str] = None,
        region: Optional[str] = None,
        endpoint_url: Optional[str] = None,
    ):
        """Initialize S3 client.

        Ops Hardening v2: NO silent defaults for bucket - fail-fast on misconfig.
        AWS Guardrails (P0): Production validation for credentials/endpoints/region.

        Args:
            bucket: S3 bucket name (default from env: S3_RESULT_BUCKET or DPP_RESULTS_BUCKET)
            region: AWS region (default from env: AWS_REGION)
            endpoint_url: Custom endpoint URL (for LocalStack/MinIO testing)

        Raises:
            ValueError: If bucket cannot be resolved from args or env
            ValueError: If production guardrails fail (A1/A3/A4/A5)
        """
        # Ops Hardening v2: Fail-fast if bucket missing (no silent "dpp-results" default)
        if bucket:
            self.bucket = bucket
        else:
            self.bucket = env.get_s3_result_bucket()  # Raises ValueError if missing

        # AWS Guardrails (P0): Production validation
        self.endpoint_url = endpoint_url or os.getenv("S3_ENDPOINT_URL")
        env.assert_no_custom_endpoint_in_prod(self.endpoint_url, "s3")  # A4
        env.assert_no_static_aws_creds("s3")  # A1, A5

        # AWS Region (A3: required in production)
        self.region = region or env.get_aws_region(require_in_prod=True)

        # Configure boto3 client with timeouts and retries
        config = Config(
            region_name=self.region,
            signature_version="s3v4",
            retries={"max_attempts": 3, "mode": "standard"},
            connect_timeout=10,
            read_timeout=60,
        )

        # Initialize S3 client
        self.client = boto3.client(
            "s3",
            config=config,
            endpoint_url=self.endpoint_url,
        )

        logger.info(f"S3Client initialized: region={self.region}, bucket={self.bucket}")

    def generate_presigned_url(
        self,
        bucket: str,
        key: str,
        ttl_seconds: int = 600,
    ) -> tuple[str, datetime]:
        """Generate presigned URL for downloading S3 object.

        P1-1: Generate presigned URL with 600 second TTL for completed runs.

        Args:
            bucket: S3 bucket name
            key: S3 object key
            ttl_seconds: Time-to-live in seconds (default 600 = 10 minutes)

        Returns:
            Tuple of (presigned_url, expires_at)

        Raises:
            ClientError: If S3 operation fails
        """
        try:
            # Generate presigned URL
            presigned_url = self.client.generate_presigned_url(
                ClientMethod="get_object",
                Params={
                    "Bucket": bucket,
                    "Key": key,
                },
                ExpiresIn=ttl_seconds,
            )

            # Calculate expiration timestamp
            expires_at = datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)

            return presigned_url, expires_at

        except ClientError as e:
            # Log error and re-raise
            import logging
            logger = logging.getLogger(__name__)
            logger.error(
                f"Failed to generate presigned URL for s3://{bucket}/{key}: {e}",
                exc_info=True,
            )
            raise

    def object_exists(self, bucket: str, key: str) -> bool:
        """Check if S3 object exists.

        P0-2: Used by Reconcile Loop to determine roll-forward vs roll-back.

        Args:
            bucket: S3 bucket name
            key: S3 object key

        Returns:
            True if object exists, False otherwise
        """
        try:
            self.client.head_object(Bucket=bucket, Key=key)
            return True
        except ClientError as e:
            if e.response["Error"]["Code"] == "404":
                return False
            # Other errors (permissions, etc.) should be raised
            raise

    def estimate_actual_cost_from_s3(
        self, bucket: str, key: str, fallback_max_cost: int
    ) -> int:
        """Estimate actual cost from S3 metadata (MS-6 Safety Guard #2).

        Priority 1: Read from S3 metadata 'actual-cost-usd-micros'
        Priority 2: Conservative fallback to reservation_max_cost

        This is critical for idempotent finalize reconciliation when:
        - Worker uploaded result to S3 with actual_cost metadata
        - Redis settle succeeded (reservation consumed)
        - DB commit failed (stuck in CLAIMED+RESERVED limbo)

        Args:
            bucket: S3 bucket name
            key: S3 object key
            fallback_max_cost: Conservative fallback (reservation_max_cost_usd_micros)

        Returns:
            Estimated actual cost in USD micros

        Raises:
            ClientError: If S3 operation fails (not 404)
        """
        try:
            # Priority 1: Read metadata from S3
            response = self.client.head_object(Bucket=bucket, Key=key)
            metadata = response.get("Metadata", {})

            # S3 metadata keys are lowercase
            actual_cost_str = metadata.get("actual-cost-usd-micros")
            if actual_cost_str:
                actual_cost = int(actual_cost_str)
                logger.info(
                    f"MS-6: Extracted actual_cost={actual_cost} from S3 metadata "
                    f"s3://{bucket}/{key}"
                )
                return actual_cost

            # Metadata not found - use fallback
            logger.warning(
                f"MS-6: No actual-cost metadata in s3://{bucket}/{key}, "
                f"using fallback={fallback_max_cost}"
            )
            return fallback_max_cost

        except ClientError as e:
            if e.response["Error"]["Code"] == "404":
                # Object doesn't exist - use fallback
                logger.warning(
                    f"MS-6: S3 object not found s3://{bucket}/{key}, "
                    f"using fallback={fallback_max_cost}"
                )
                return fallback_max_cost

            # Other errors (permissions, etc.) should be raised
            logger.error(
                f"MS-6: Failed to read S3 metadata s3://{bucket}/{key}: {e}",
                exc_info=True,
            )
            raise

    def upload_file(
        self,
        file_path: str,
        bucket: str,
        key: str,
        metadata: Optional[dict[str, str]] = None,
    ) -> str:
        """Upload file to S3.

        Args:
            file_path: Local file path to upload
            bucket: S3 bucket name
            key: S3 object key
            metadata: Optional metadata dict

        Returns:
            S3 URI (s3://bucket/key)

        Raises:
            ClientError: If upload fails
        """
        try:
            extra_args = {}
            if metadata:
                extra_args["Metadata"] = metadata

            self.client.upload_file(file_path, bucket, key, ExtraArgs=extra_args)

            return f"s3://{bucket}/{key}"

        except ClientError as e:
            import logging
            logger = logging.getLogger(__name__)
            logger.error(
                f"Failed to upload {file_path} to s3://{bucket}/{key}: {e}",
                exc_info=True,
            )
            raise

    def upload_bytes(
        self,
        data: bytes,
        bucket: str,
        key: str,
        content_type: str = "application/octet-stream",
        metadata: Optional[dict[str, str]] = None,
    ) -> str:
        """Upload bytes to S3.

        Args:
            data: Bytes to upload
            bucket: S3 bucket name
            key: S3 object key
            content_type: Content-Type header
            metadata: Optional metadata dict

        Returns:
            S3 URI (s3://bucket/key)

        Raises:
            ClientError: If upload fails
        """
        try:
            extra_args = {"ContentType": content_type}
            if metadata:
                extra_args["Metadata"] = metadata

            self.client.put_object(
                Bucket=bucket,
                Key=key,
                Body=data,
                **extra_args,
            )

            return f"s3://{bucket}/{key}"

        except ClientError as e:
            import logging
            logger = logging.getLogger(__name__)
            logger.error(
                f"Failed to upload bytes to s3://{bucket}/{key}: {e}",
                exc_info=True,
            )
            raise

    def delete_object(self, bucket: str, key: str) -> bool:
        """Delete S3 object (P0-6: Retention Cleanup).

        Args:
            bucket: S3 bucket name
            key: S3 object key

        Returns:
            True if object was deleted or didn't exist, False on error

        Raises:
            ClientError: If deletion fails (except 404)
        """
        try:
            self.client.delete_object(Bucket=bucket, Key=key)
            logger.info(f"Deleted S3 object: s3://{bucket}/{key}")
            return True

        except ClientError as e:
            # 404 is OK (object already deleted)
            if e.response["Error"]["Code"] == "404":
                logger.warning(f"S3 object not found (already deleted): s3://{bucket}/{key}")
                return True

            # Other errors should be logged and raised
            logger.error(
                f"Failed to delete S3 object s3://{bucket}/{key}: {e}",
                exc_info=True,
            )
            raise


# Singleton instance
_s3_client: Optional[S3Client] = None


def get_s3_client() -> S3Client:
    """Get singleton S3 client instance.

    Returns:
        S3Client instance
    """
    global _s3_client
    if _s3_client is None:
        _s3_client = S3Client()
    return _s3_client
