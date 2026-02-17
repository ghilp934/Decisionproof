"""SQS client for enqueueing runs.

Ops Hardening v2: Remove hardcoded localhost queue URL, enhance LocalStack detection.
"""

import json
import os
from datetime import datetime, timezone
from typing import Any

import boto3
from botocore.config import Config

from dpp_api.config import env


class SQSClient:
    """SQS client wrapper for DPP."""

    def __init__(self):
        """
        Initialize SQS client.

        Ops Hardening v2: NO hardcoded localhost defaults, enhanced LocalStack detection.
        AWS Guardrails (P0): Production validation for credentials/endpoints/region.
        - SQS_ENDPOINT_URL: Only used if explicitly set (no default)
        - SQS_QUEUE_URL: ALWAYS required (fail-fast if missing)
        - Credentials: Only inject "test" for LocalStack AND NOT in IRSA (A6)

        Raises:
            ValueError: If SQS_QUEUE_URL is missing
            ValueError: If production guardrails fail (A1/A3/A4/A5)
        """
        # AWS Guardrails (P0): Production validation
        sqs_endpoint = os.getenv("SQS_ENDPOINT_URL")
        env.assert_no_custom_endpoint_in_prod(sqs_endpoint, "sqs")  # A4
        env.assert_no_static_aws_creds("sqs")  # A1, A5

        # Ops Hardening v2: SQS_QUEUE_URL is ALWAYS required (no hardcoded defaults)
        self.queue_url = env.get_sqs_queue_url()  # Raises ValueError if missing

        # AWS Region (A3: required in production)
        region_name = env.get_aws_region(require_in_prod=True)

        # Configure boto3 client with timeouts and retries
        config = Config(
            region_name=region_name,
            retries={"max_attempts": 3, "mode": "standard"},
            connect_timeout=10,
            read_timeout=30,
        )

        # Build boto3 kwargs
        sqs_kwargs = {"config": config}

        # Ops Hardening v2: endpoint_url only if explicitly set
        if sqs_endpoint:
            sqs_kwargs["endpoint_url"] = sqs_endpoint

            # A6: Test credentials ONLY for LocalStack AND NOT in IRSA/production
            # IRSA environments (EKS) use web identity tokens, NEVER static credentials
            if (
                env.is_localstack_endpoint(sqs_endpoint)
                and not os.getenv("AWS_ACCESS_KEY_ID")
                and not env.is_irsa_environment()
            ):
                sqs_kwargs["aws_access_key_id"] = "test"
                sqs_kwargs["aws_secret_access_key"] = "test"

        self.client = boto3.client("sqs", **sqs_kwargs)

    def enqueue_run(self, run_id: str, tenant_id: str, pack_type: str, trace_id: str | None = None) -> str:
        """
        Enqueue a run for processing.

        Args:
            run_id: Run ID (UUID)
            tenant_id: Tenant ID
            pack_type: Pack type
            trace_id: Trace ID for observability (optional)

        Returns:
            SQS Message ID

        Raises:
            Exception: If enqueue fails
        """
        message_body = {
            "run_id": run_id,
            "tenant_id": tenant_id,
            "pack_type": pack_type,
            "enqueued_at": datetime.now(timezone.utc).isoformat(),
            "schema_version": "1",
            "trace_id": trace_id,  # Observability: trace across API → Worker → Reaper
        }

        response = self.client.send_message(
            QueueUrl=self.queue_url,
            MessageBody=json.dumps(message_body),
        )

        return response["MessageId"]


# Singleton instance
_sqs_client: SQSClient | None = None


def get_sqs_client() -> SQSClient:
    """Get SQS client singleton."""
    global _sqs_client
    if _sqs_client is None:
        _sqs_client = SQSClient()
    return _sqs_client
