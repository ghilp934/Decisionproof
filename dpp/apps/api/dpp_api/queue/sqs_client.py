"""SQS client for enqueueing runs."""

import json
import os
from datetime import datetime, timezone
from typing import Any

import boto3


class SQSClient:
    """SQS client wrapper for DPP."""

    def __init__(self):
        """
        Initialize SQS client.

        P0-2: NO implicit localstack in prod.
        - SQS_ENDPOINT_URL: Only used if explicitly set (no default)
        - SQS_QUEUE_URL: Required in prod, allowed default only if endpoint_url exists
        - Credentials: Only use dummy for LocalStack (when endpoint_url is set)
        """
        sqs_endpoint = os.getenv("SQS_ENDPOINT_URL")  # No default!
        sqs_queue_url = os.getenv("SQS_QUEUE_URL")

        # P0-2: Validate queue URL configuration
        if not sqs_endpoint and not sqs_queue_url:
            raise ValueError(
                "SQS_QUEUE_URL is required in production. "
                "Set SQS_QUEUE_URL or SQS_ENDPOINT_URL for LocalStack."
            )

        # If endpoint_url exists (LocalStack), allow default queue URL
        if sqs_endpoint and not sqs_queue_url:
            sqs_queue_url = "http://localhost:4566/000000000000/dpp-runs"

        self.queue_url = sqs_queue_url

        # Build boto3 kwargs
        sqs_kwargs = {"region_name": os.getenv("AWS_REGION", "us-east-1")}

        # P0-2: endpoint_url only if explicitly set
        if sqs_endpoint:
            sqs_kwargs["endpoint_url"] = sqs_endpoint

            # P0-2: Dummy credentials only for LocalStack
            if not os.getenv("AWS_ACCESS_KEY_ID"):
                sqs_kwargs["aws_access_key_id"] = "test"
                sqs_kwargs["aws_secret_access_key"] = "test"

        # Explicit credentials if provided
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
