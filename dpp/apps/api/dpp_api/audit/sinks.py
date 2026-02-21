"""P5.3/P5.6/P5.8: Audit sink implementations for kill-switch WORM records.

Sink selection (get_default_audit_sink):
  KILL_SWITCH_AUDIT_REQUIRED=1 + KILL_SWITCH_AUDIT_BUCKET set
    → S3WormAuditSink (GOVERNANCE|COMPLIANCE Object Lock, 7-year retention) [REQUIRED mode]
  KILL_SWITCH_AUDIT_REQUIRED=1 + bucket MISSING
    → raises AuditSinkConfigError immediately (fail-fast; no fallback allowed)
  KILL_SWITCH_AUDIT_REQUIRED=1 + WORM_MODE missing
    → raises AuditSinkConfigError (WORM_MODE_REQUIRED_BUT_NOT_SET)
  KILL_SWITCH_AUDIT_REQUIRED=1 + WORM_MODE invalid
    → raises AuditSinkConfigError (INVALID_WORM_MODE)
  KILL_SWITCH_AUDIT_BUCKET set (REQUIRED=0)
    → S3WormAuditSink [optional upgrade, defaults to GOVERNANCE]
  neither set (REQUIRED=0)
    → FileAuditSink  (local file; CI / dev fallback only)

P5.6 additions:
  AuditSinkConfigError     — raised when REQUIRED=1 but bucket not configured
  audit_required()         — True when KILL_SWITCH_AUDIT_REQUIRED=1
  validate_audit_required_config() — fail-fast check (no network calls)

P5.8 additions:
  KILL_SWITCH_AUDIT_WORM_MODE env var (GOVERNANCE | COMPLIANCE)
  _VALID_WORM_MODES        — frozenset of valid values
  _DEFAULT_WORM_MODE       — "GOVERNANCE" (safe default for non-required mode)
  S3WormAuditSink.mode     — configurable; no bypass behavior ever included
  validate_audit_required_config() — extended to check WORM_MODE when REQUIRED=1

Test helpers:
  FailingAuditSink → always raises RuntimeError (used in Test D / Test E)
"""

import json
import logging
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol, runtime_checkable

logger = logging.getLogger(__name__)


# ── P5.6: Config error ────────────────────────────────────────────────────────

class AuditSinkConfigError(RuntimeError):
    """Raised when KILL_SWITCH_AUDIT_REQUIRED=1 but audit sink is not configured.

    This is a hard configuration error — the application must not allow
    kill-switch changes without a WORM audit trail when required mode is on.
    """


# ── P5.8: WORM mode constants ─────────────────────────────────────────────────

_VALID_WORM_MODES: frozenset[str] = frozenset({"GOVERNANCE", "COMPLIANCE"})
_DEFAULT_WORM_MODE: str = "GOVERNANCE"  # Safe default for non-required (dev/CI) mode


# ── P5.6/P5.8: Config helpers ─────────────────────────────────────────────────

def audit_required() -> bool:
    """Return True when KILL_SWITCH_AUDIT_REQUIRED=1 (WORM is mandatory)."""
    return os.getenv("KILL_SWITCH_AUDIT_REQUIRED", "0").strip() == "1"


def validate_audit_required_config() -> None:
    """Validate audit sink configuration when REQUIRED mode is enabled.

    Called at boot time (startup preflight) and lazily by get_default_audit_sink.
    Does NOT make any network calls — validates env-var presence only.

    P5.8 additions: also validates KILL_SWITCH_AUDIT_WORM_MODE when REQUIRED=1.
    Operators must explicitly choose GOVERNANCE (pilot) or COMPLIANCE (locked);
    silent defaults are not allowed in production-required mode.

    Raises:
        AuditSinkConfigError: If KILL_SWITCH_AUDIT_REQUIRED=1 and:
            - KILL_SWITCH_AUDIT_BUCKET is not set, OR
            - KILL_SWITCH_AUDIT_WORM_MODE is not set (WORM_MODE_REQUIRED_BUT_NOT_SET), OR
            - KILL_SWITCH_AUDIT_WORM_MODE is not GOVERNANCE|COMPLIANCE (INVALID_WORM_MODE)
    """
    if not audit_required():
        return

    if not os.getenv("KILL_SWITCH_AUDIT_BUCKET"):
        raise AuditSinkConfigError(
            "AUDIT_SINK_REQUIRED_BUT_NOT_CONFIGURED: "
            "KILL_SWITCH_AUDIT_REQUIRED=1 but KILL_SWITCH_AUDIT_BUCKET is not set. "
            "Set KILL_SWITCH_AUDIT_BUCKET to an S3 bucket with Object Lock enabled, "
            "or set KILL_SWITCH_AUDIT_REQUIRED=0 to allow FileAuditSink fallback."
        )

    worm_mode = os.getenv("KILL_SWITCH_AUDIT_WORM_MODE", "").strip()
    if not worm_mode:
        raise AuditSinkConfigError(
            "WORM_MODE_REQUIRED_BUT_NOT_SET: "
            "KILL_SWITCH_AUDIT_REQUIRED=1 but KILL_SWITCH_AUDIT_WORM_MODE is not set. "
            "Set KILL_SWITCH_AUDIT_WORM_MODE to GOVERNANCE (pilot) or COMPLIANCE (locked). "
            "Operators must explicitly choose — silent defaults are forbidden in required mode."
        )

    if worm_mode not in _VALID_WORM_MODES:
        raise AuditSinkConfigError(
            f"INVALID_WORM_MODE: KILL_SWITCH_AUDIT_WORM_MODE={worm_mode!r} is not valid. "
            f"Allowed values: {sorted(_VALID_WORM_MODES)}. "
            "Check for typos (values are case-sensitive and must be uppercase)."
        )


# ── Protocol ──────────────────────────────────────────────────────────────────

@runtime_checkable
class AuditSink(Protocol):
    """Minimal interface for all audit sinks."""

    def put_record(self, key: str, data: dict, *, content_type: str = "application/json") -> None:
        """Write an immutable audit record.

        Args:
            key: Object key / file name (must be unique per record).
            data: Record payload dict (will be JSON-serialised).
            content_type: MIME type of the payload.

        Raises:
            RuntimeError: If the write fails and the caller must treat it as fatal.
        """
        ...


# ── S3 WORM sink ──────────────────────────────────────────────────────────────

class S3WormAuditSink:
    """Write audit records to S3 with Object Lock (GOVERNANCE|COMPLIANCE, 7-year retention).

    Requires:
      - S3 bucket with Object Lock enabled (created at bucket-level)
      - IAM: s3:PutObject + s3:PutObjectRetention on the bucket
      - Env: KILL_SWITCH_AUDIT_BUCKET (bucket name)
      - Env: KILL_SWITCH_AUDIT_REGION  (optional; falls back to AWS_DEFAULT_REGION)
      - Env: KILL_SWITCH_AUDIT_WORM_MODE (GOVERNANCE|COMPLIANCE; required in REQUIRED mode)

    P5.8 Security:
      - ObjectLockMode and ObjectLockRetainUntilDate are ALWAYS sent as a paired set.
      - BypassGovernanceRetention is NEVER included in PutObject calls.
      - The service role must NOT hold s3:BypassGovernanceRetention permission.
    """

    # 7 years ≈ 2555 days (365.25 * 7)
    _RETENTION_DAYS = 2555

    def __init__(self, bucket: str, region: str | None = None, mode: str = _DEFAULT_WORM_MODE) -> None:
        import boto3

        self._bucket = bucket
        self._mode = mode
        self._client = boto3.client(
            "s3",
            region_name=region or os.getenv("AWS_DEFAULT_REGION", "us-east-1"),
        )

    def put_record(self, key: str, data: dict, *, content_type: str = "application/json") -> None:
        """Serialize data to JSON and PUT to S3 with Object Lock.

        Always sends ObjectLockMode + ObjectLockRetainUntilDate as a paired set.
        Never includes BypassGovernanceRetention or any bypass-related parameters.
        """
        from datetime import timedelta

        body = json.dumps(data, ensure_ascii=False, default=str).encode("utf-8")
        retain_until = datetime.now(timezone.utc) + timedelta(days=self._RETENTION_DAYS)

        try:
            self._client.put_object(
                Bucket=self._bucket,
                Key=key,
                Body=body,
                ContentType=content_type,
                ObjectLockMode=self._mode,
                ObjectLockRetainUntilDate=retain_until.isoformat(),
            )
            logger.info(
                "AUDIT_WORM_WRITTEN",
                extra={
                    "bucket": self._bucket,
                    "key": key,
                    "worm_mode": self._mode,
                    "retain_until": retain_until.isoformat(),
                },
            )
        except Exception as exc:
            logger.error("AUDIT_WORM_WRITE_FAILED", extra={"bucket": self._bucket, "key": key, "error": str(exc)})
            raise RuntimeError(f"S3 WORM audit write failed: {exc}") from exc


# ── File sink (CI / local dev) ────────────────────────────────────────────────

class FileAuditSink:
    """Write audit records as JSON files on the local filesystem.

    Intended for local development and CI only (no WORM guarantee).
    Each record is written as a separate file named by key.
    """

    def __init__(self, directory: str | None = None) -> None:
        if directory:
            self._dir = Path(directory)
        else:
            self._dir = Path(os.getenv("KILL_SWITCH_AUDIT_FILE_DIR", tempfile.gettempdir()))
        self._dir.mkdir(parents=True, exist_ok=True)

    def put_record(self, key: str, data: dict, *, content_type: str = "application/json") -> None:
        # Sanitize key → safe filename (replace slashes / colons)
        filename = key.replace("/", "_").replace(":", "_") + ".json"
        filepath = self._dir / filename
        body = json.dumps(data, ensure_ascii=False, indent=2, default=str)
        filepath.write_text(body, encoding="utf-8")
        logger.info("AUDIT_FILE_WRITTEN", extra={"path": str(filepath)})


# ── Failing sink (test helper) ────────────────────────────────────────────────

class FailingAuditSink:
    """Always raises RuntimeError.  Used in tests to simulate sink failure."""

    def put_record(self, key: str, data: dict, *, content_type: str = "application/json") -> None:
        raise RuntimeError("FailingAuditSink: intentional failure for testing")


# ── Factory ───────────────────────────────────────────────────────────────────

def get_default_audit_sink() -> AuditSink:
    """Return the appropriate sink based on environment configuration.

    Priority:
      1. KILL_SWITCH_AUDIT_BUCKET → S3WormAuditSink (mode from KILL_SWITCH_AUDIT_WORM_MODE)
      2. Otherwise               → FileAuditSink (only when REQUIRED=0)

    P5.6: If KILL_SWITCH_AUDIT_REQUIRED=1 and KILL_SWITCH_AUDIT_BUCKET is unset,
    raises AuditSinkConfigError immediately — FileAuditSink fallback is forbidden.

    P5.8: If KILL_SWITCH_AUDIT_REQUIRED=1 and KILL_SWITCH_AUDIT_WORM_MODE is absent
    or invalid, raises AuditSinkConfigError — silent defaults are forbidden in
    production-required mode.
    """
    # P5.6/P5.8: Fail-fast if REQUIRED=1 but config is incomplete
    validate_audit_required_config()

    bucket = os.getenv("KILL_SWITCH_AUDIT_BUCKET")
    if bucket:
        region = os.getenv("KILL_SWITCH_AUDIT_REGION")
        # P5.8: Use explicitly configured WORM mode; fall back to GOVERNANCE for non-required mode
        mode = os.getenv("KILL_SWITCH_AUDIT_WORM_MODE", _DEFAULT_WORM_MODE).strip()
        logger.info("AUDIT_SINK_S3_WORM", extra={"bucket": bucket, "worm_mode": mode})
        return S3WormAuditSink(bucket=bucket, region=region, mode=mode)

    file_dir = os.getenv("KILL_SWITCH_AUDIT_FILE_DIR")
    logger.info("AUDIT_SINK_FILE", extra={"directory": file_dir or tempfile.gettempdir()})
    return FileAuditSink(directory=file_dir)
