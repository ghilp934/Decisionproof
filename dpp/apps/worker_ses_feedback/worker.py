"""SES Feedback Worker - Main polling loop

Polls SQS for SES notifications (bounce/complaint/delivery) and stores in DB.
Uses IRSA for AWS access (NO static keys).

SSL Policy (Spec Lock — inline mirror of dpp_api.db.ssl_policy):
  This is a standalone container that does NOT depend on the dpp_api package.
  SSL logic is duplicated inline (_ensure_sslmode, _build_ssl_connect_kwargs).
  Keep in sync with dpp_api/db/ssl_policy.py when policy changes.

  ENV Contract:
    DPP_DB_SSLMODE:      "verify-full" | "verify-ca" | "require"
                          Default: verify-full (PROD+Supabase) / require (else)
    DPP_DB_SSLROOTCERT:  CA bundle path (required for verify-ca/verify-full)
    DATABASE_SSL_ROOT_CERT: legacy alias for DPP_DB_SSLROOTCERT
    DP_ENV:              Deployment environment ("prod"/"production" triggers verify-full)

  ENV SSOT Rule:
    DPP_DB_SSLMODE set → ENV wins over URL sslmode (psycopg2 kwargs > DSN params).
    DPP_DB_SSLMODE unset → URL sslmode if present; else default per env/host.
"""

import json
import logging
import os
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import boto3
import psycopg2
from psycopg2.extras import Json


# ---------------------------------------------------------------------------
# Inline SSL helpers (standalone — no dpp_api dependency)
# ---------------------------------------------------------------------------

def _ensure_sslmode(url: str, default_mode: str = "require") -> str:
    """Inject sslmode into the URL for Supabase hosts if not already present.

    Inline equivalent of dpp_api.db.url_policy.ensure_sslmode.
    Note: _build_ssl_connect_kwargs() passes sslmode as a psycopg2 kwarg which
    takes precedence over the URL param. This function is belt-and-suspenders only.

    Spec Lock (URL Precedence):
      Non-Supabase host         → URL unchanged.
      sslmode already in URL    → URL unchanged (URL is SSOT here).
      Supabase host, no sslmode → append ?sslmode=<default_mode>.
    """
    is_supabase = ".supabase.co" in url or ".pooler.supabase.com" in url
    if not is_supabase:
        return url
    if "sslmode=" in url:
        return url
    sep = "&" if "?" in url else "?"
    return f"{url}{sep}sslmode={default_mode}"


def _build_ssl_connect_kwargs(url: str, dp_env: str) -> dict:
    """Return psycopg2 SSL keyword arguments for Supabase hosts.

    Inline mirror of dpp_api.db.ssl_policy.resolve_ssl_settings().
    Kept inline because this worker is a standalone container without dpp_api.

    Spec Lock (ENV SSOT):
      DPP_DB_SSLMODE set   → ENV is authoritative (kwargs override URL sslmode).
      DPP_DB_SSLMODE unset → default: verify-full (PROD+Supabase) or require (else).
      verify-ca/verify-full without readable sslrootcert → RuntimeError (fail-fast).
      Non-Supabase host → returns {} (no SSL enforcement from this layer).

    Args:
        url: Database connection URL (Supabase host detection).
        dp_env: Deployment environment string.

    Returns:
        Dict of psycopg2 SSL kwargs: {"sslmode": str} or {"sslmode": str, "sslrootcert": str}

    Raises:
        RuntimeError: verify-full/verify-ca without readable sslrootcert.
    """
    is_supabase = ".supabase.co" in url or ".pooler.supabase.com" in url
    if not is_supabase:
        return {}

    is_prod = dp_env.lower() in {"prod", "production"}
    env_sslmode = os.getenv("DPP_DB_SSLMODE")

    if env_sslmode:
        mode = env_sslmode.lower()
    elif is_prod:
        mode = "verify-full"
    else:
        mode = "require"

    sslrootcert = os.getenv("DPP_DB_SSLROOTCERT") or os.getenv("DATABASE_SSL_ROOT_CERT")

    # Fail-fast: CA modes require a readable cert file.
    if mode in ("verify-ca", "verify-full"):
        if not sslrootcert:
            raise RuntimeError(
                f"SSL POLICY: sslmode={mode!r} requires DPP_DB_SSLROOTCERT. "
                "Mount the Supabase CA ConfigMap and set "
                "DPP_DB_SSLROOTCERT=/etc/ssl/certs/supabase-ca/supabase-ca.crt. "
                "See ops/runbooks/db_ssl_verify_full.md."
            )
        if not os.path.isfile(sslrootcert):
            raise RuntimeError(
                f"SSL POLICY: sslmode={mode!r} but CA bundle not found or not readable: "
                f"{sslrootcert!r}. "
                "Ensure the CA ConfigMap is mounted correctly at the expected path. "
                "See ops/runbooks/db_ssl_verify_full.md."
            )

    kwargs: dict = {"sslmode": mode}
    if sslrootcert:
        kwargs["sslrootcert"] = sslrootcert
    return kwargs


# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class SESFeedbackWorker:
    """Worker that polls SQS and stores SES feedback in Supabase."""

    def __init__(self):
        """Initialize worker with config from environment."""
        self.aws_region = os.getenv("AWS_REGION", "ap-northeast-2")
        self.queue_url = os.getenv("SQS_QUEUE_URL")
        self.database_url = os.getenv("DATABASE_URL")
        self.dp_env = os.getenv("DP_ENV", "").lower()
        self.poll_interval = int(os.getenv("POLL_INTERVAL_SEC", "20"))
        self.max_messages = int(os.getenv("MAX_MESSAGES", "10"))

        if not self.queue_url:
            raise ValueError("SQS_QUEUE_URL environment variable is required")
        if not self.database_url:
            raise ValueError("DATABASE_URL environment variable is required")

        # SSL enforcement: normalise URL (belt-and-suspenders) then build connect kwargs.
        # Spec Lock: _build_ssl_connect_kwargs() is the authoritative source;
        #   psycopg2 kwargs take precedence over URL-embedded sslmode.
        ssl_default = os.getenv("DPP_DB_SSLMODE", "require")
        self.database_url = _ensure_sslmode(self.database_url, default_mode=ssl_default)
        self._ssl_kwargs = _build_ssl_connect_kwargs(self.database_url, self.dp_env)

        logger.info(
            "SSL policy applied: sslmode=%s, sslrootcert=%s",
            self._ssl_kwargs.get("sslmode", "none"),
            self._ssl_kwargs.get("sslrootcert", "(not set)"),
        )

        # Initialize AWS SQS client (uses IRSA, no keys needed)
        logger.info(f"Initializing SQS client for region: {self.aws_region}")
        self.sqs = boto3.client('sqs', region_name=self.aws_region)

        # Verify AWS identity (should show assumed role from IRSA)
        sts = boto3.client('sts', region_name=self.aws_region)
        identity = sts.get_caller_identity()
        logger.info(f"AWS Identity: {identity['Arn']}")

        if "assumed-role" not in identity['Arn']:
            logger.warning("Not using assumed role - IRSA may not be configured correctly")

        logger.info(f"Worker initialized: queue={self.queue_url}")

    def run(self):
        """Main polling loop."""
        logger.info("Starting SES feedback worker...")

        while True:
            try:
                self._poll_and_process()
            except KeyboardInterrupt:
                logger.info("Shutting down gracefully...")
                break
            except Exception as e:
                logger.error(f"Unexpected error in polling loop: {e}", exc_info=True)
                time.sleep(5)  # Back off on error

    def _poll_and_process(self):
        """Poll SQS and process messages."""
        logger.debug(f"Polling SQS (max {self.max_messages} messages)...")

        response = self.sqs.receive_message(
            QueueUrl=self.queue_url,
            MaxNumberOfMessages=self.max_messages,
            WaitTimeSeconds=self.poll_interval,
            AttributeNames=['All'],
            MessageAttributeNames=['All']
        )

        messages = response.get('Messages', [])

        if not messages:
            logger.debug("No messages received")
            return

        logger.info(f"Received {len(messages)} message(s)")

        for message in messages:
            try:
                self._process_message(message)
            except Exception as e:
                logger.error(f"Failed to process message: {e}", exc_info=True)
                # Do NOT delete message on error - let it go to DLQ after max retries

    def _process_message(self, message: Dict[str, Any]):
        """Process a single SQS message."""
        receipt_handle = message['ReceiptHandle']
        body = message['Body']

        try:
            # Parse message body
            parsed = self._parse_ses_notification(body)

            if not parsed:
                logger.warning(f"Could not parse message: {body[:200]}")
                # Delete unparseable messages to avoid infinite retry
                self._delete_message(receipt_handle)
                return

            # Store in database
            self._store_feedback(parsed)

            # Delete message from SQS after successful processing
            self._delete_message(receipt_handle)

            logger.info(f"Processed {parsed['notification_type']} notification: {parsed['message_id']}")

        except Exception as e:
            logger.error(f"Error processing message: {e}", exc_info=True)
            raise  # Re-raise to prevent deletion

    def _parse_ses_notification(self, body: str) -> Optional[Dict[str, Any]]:
        """Parse SES notification from SQS message body.

        Handles both:
        - Raw SES JSON (if RawMessageDelivery=true)
        - SNS envelope JSON (default)
        """
        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            logger.error("Invalid JSON in message body")
            return None

        # Check if it's an SNS envelope
        if 'Type' in data and data['Type'] == 'Notification':
            # Extract SES notification from SNS message
            try:
                ses_message = json.loads(data['Message'])
            except json.JSONDecodeError:
                logger.error("Invalid JSON in SNS Message field")
                return None
        else:
            # Assume it's raw SES JSON
            ses_message = data

        # Extract notification type
        notification_type = ses_message.get('notificationType')
        if notification_type not in ['Bounce', 'Complaint', 'Delivery']:
            logger.warning(f"Unknown notification type: {notification_type}")
            return None

        # Extract common fields
        mail = ses_message.get('mail', {})
        message_id = mail.get('messageId', str(uuid.uuid4()))
        timestamp = mail.get('timestamp', datetime.now(timezone.utc).isoformat())
        source = mail.get('source', '')
        destinations = mail.get('destination', [])

        # Extract type-specific data
        type_data = {}
        if notification_type == 'Bounce':
            bounce = ses_message.get('bounce', {})
            type_data = {
                'bounce_type': bounce.get('bounceType'),
                'bounce_sub_type': bounce.get('bounceSubType'),
                'bounced_recipients': [r.get('emailAddress') for r in bounce.get('bouncedRecipients', [])]
            }
        elif notification_type == 'Complaint':
            complaint = ses_message.get('complaint', {})
            type_data = {
                'complaint_feedback_type': complaint.get('complaintFeedbackType'),
                'complained_recipients': [r.get('emailAddress') for r in complaint.get('complainedRecipients', [])]
            }
        elif notification_type == 'Delivery':
            delivery = ses_message.get('delivery', {})
            type_data = {
                'processing_time_millis': delivery.get('processingTimeMillis'),
                'smtp_response': delivery.get('smtpResponse')
            }

        # Primary recipient (first destination)
        primary_recipient = destinations[0] if destinations else None

        return {
            'notification_type': notification_type,
            'message_id': message_id,
            'timestamp': timestamp,
            'source': source,
            'destinations': destinations,
            'primary_recipient': primary_recipient,
            'type_data': type_data,
            'payload': ses_message  # Full payload for debugging
        }

    def _store_feedback(self, parsed: Dict[str, Any]):
        """Store feedback in Supabase database.

        Uses self._ssl_kwargs (sslmode + sslrootcert) as psycopg2 keyword args.
        These take precedence over any sslmode param embedded in self.database_url.
        """
        conn = psycopg2.connect(self.database_url, **self._ssl_kwargs)
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO public.ses_feedback_events (
                        id,
                        received_at,
                        notification_type,
                        message_id,
                        source,
                        destinations,
                        primary_recipient,
                        type_data,
                        payload
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s, %s
                    )
                """, (
                    str(uuid.uuid4()),
                    datetime.now(timezone.utc),
                    parsed['notification_type'],
                    parsed['message_id'],
                    parsed['source'],
                    Json(parsed['destinations']),
                    parsed['primary_recipient'],
                    Json(parsed['type_data']),
                    Json(parsed['payload'])
                ))
                conn.commit()
        finally:
            conn.close()

    def _delete_message(self, receipt_handle: str):
        """Delete message from SQS."""
        self.sqs.delete_message(
            QueueUrl=self.queue_url,
            ReceiptHandle=receipt_handle
        )
        logger.debug("Message deleted from SQS")


def main():
    """Entry point."""
    worker = SESFeedbackWorker()
    worker.run()


if __name__ == "__main__":
    main()
