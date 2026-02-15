"""SQS client for enqueueing runs.

Ops Hardening v2: Remove hardcoded localhost queue URL, enhance LocalStack detection.
"""

import json
import os
from datetime import datetime, timezone
from typing import Any

import boto3

from dpp_api.config.env import get_sqs_queue_url, is_irsa_environment, is_localstack_endpoint


class SQSClient:
    """SQS client wrapper for DPP."""

    def __init__(self):
        """
        Initialize SQS client.

        Ops Hardening v2: NO hardcoded localhost defaults, enhanced LocalStack detection.
        - SQS_ENDPOINT_URL: Only used if explicitly set (no default)
        - SQS_QUEUE_URL: ALWAYS required (fail-fast if missing)
        - Credentials: Only inject "test" for LocalStack (4-marker detection)

        Raises:
            ValueError: If SQS_QUEUE_URL is missing
        """
        sqs_endpoint = os.getenv("SQS_ENDPOINT_URL")  # Optional: LocalStack only

        # Ops Hardening v2: SQS_QUEUE_URL is ALWAYS required (no hardcoded defaults)
        self.queue_url = get_sqs_queue_url()  # Raises ValueError if missing

        # Build boto3 kwargs
        sqs_kwargs = {"region_name": os.getenv("AWS_REGION", "us-east-1")}

        # Ops Hardening v2: endpoint_url only if explicitly set
        if sqs_endpoint:
            sqs_kwargs["endpoint_url"] = sqs_endpoint

            # P1-1: Test credentials ONLY for LocalStack AND NOT in IRSA/production
            # IRSA environments (EKS) use web identity tokens, NEVER static credentials
            if (
                is_localstack_endpoint(sqs_endpoint)
                and not os.getenv("AWS_ACCESS_KEY_ID")
                and not is_irsa_environment()
            ):
                sqs_kwargs["aws_access_key_id"] = "test"
                sqs_kwargs["aws_secret_access_key"] = "test"

        # Explicit credentials if provided (override test creds)
        if os.getenv("AWS_ACCESS_KEY_ID"):
            sqs_kwargs["aws_access_key_id"] = os.getenv("AWS_ACCESS_KEY_ID")
        if os.getenv("AWS_SECRET_ACCESS_KEY"):
            sqs_kwargs["aws_secret_access_key"] = os.getenv("AWS_SECRET_ACCESS_KEY")

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
