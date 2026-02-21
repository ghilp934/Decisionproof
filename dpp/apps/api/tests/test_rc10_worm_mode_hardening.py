"""RC-10.P5.8 Contract Gate: WORM Mode Hardening.

Ensures the S3 WORM audit sink is truly immutable:
  - ObjectLockMode + ObjectLockRetainUntilDate are ALWAYS paired in every PutObject call.
  - GOVERNANCE and COMPLIANCE modes are explicitly supported and correctly passed.
  - In production-required mode (KILL_SWITCH_AUDIT_REQUIRED=1), WORM mode must be
    explicitly set; missing or invalid WORM_MODE is a hard config error.
  - No bypass-related parameters (BypassGovernanceRetention) are ever passed to S3.

Test matrix:
  A – put_record always passes ObjectLockMode + ObjectLockRetainUntilDate as a paired set
  B – GOVERNANCE / COMPLIANCE modes correctly passed to S3 PutObject (parameterized)
  C – REQUIRED=1 + BUCKET set but WORM_MODE unset → AuditSinkConfigError (fail-closed)
  D – put_record never includes BypassGovernanceRetention or any bypass-related params

Facts (documented in runbook + DEC):
  - Governance mode: can be overridden ONLY with s3:BypassGovernanceRetention IAM permission
    AND explicit bypass header on delete/retention-change calls (never silently).
  - Compliance mode: CANNOT be overridden by any user; retention cannot be shortened.
  - CloudTrail data events (not management events) required for object-level audit trail.
"""

import os
from typing import Generator
from unittest.mock import MagicMock, call, patch

import pytest

from dpp_api.audit.sinks import (
    AuditSinkConfigError,
    S3WormAuditSink,
    validate_audit_required_config,
)


# ── Env restore fixture ───────────────────────────────────────────────────────

_WORM_ENV_KEYS = [
    "KILL_SWITCH_AUDIT_REQUIRED",
    "KILL_SWITCH_AUDIT_BUCKET",
    "KILL_SWITCH_AUDIT_WORM_MODE",
    "KILL_SWITCH_AUDIT_REGION",
]


class TestRC10WormModeHardening:
    """RC-10.P5.8: S3 Object Lock WORM mode hardening."""

    @pytest.fixture(autouse=True)
    def restore_env(self) -> Generator:
        """Snapshot and restore relevant env vars after each test."""
        snapshot = {k: os.environ.get(k) for k in _WORM_ENV_KEYS}
        yield
        for k, v in snapshot.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    # ── Test A: PutObject pairing ─────────────────────────────────────────────

    def test_a_putobject_sets_objectlock_pairing(self) -> None:
        """A: put_record always passes BOTH ObjectLockMode AND ObjectLockRetainUntilDate.

        AWS S3 Object Lock requires these to be provided as a paired set.
        Providing only one (e.g., mode without retention date) is an API error.
        Our sink must always send both — never one without the other.
        """
        mock_s3 = MagicMock()

        with patch("boto3.client", return_value=mock_s3):
            sink = S3WormAuditSink(bucket="test-bucket", mode="GOVERNANCE")
            sink.put_record("kill-switch/2026-test.json", {"action": "test"})

        assert mock_s3.put_object.call_count == 1, "put_object must be called exactly once"
        kwargs = mock_s3.put_object.call_args.kwargs

        # Both must be present
        assert "ObjectLockMode" in kwargs, (
            "ObjectLockMode MISSING from put_object call — "
            "S3 Object Lock requires this parameter"
        )
        assert "ObjectLockRetainUntilDate" in kwargs, (
            "ObjectLockRetainUntilDate MISSING from put_object call — "
            "S3 Object Lock requires this parameter"
        )

        # Verify they are non-empty / non-None
        assert kwargs["ObjectLockMode"], "ObjectLockMode must not be empty"
        assert kwargs["ObjectLockRetainUntilDate"], "ObjectLockRetainUntilDate must not be empty"

        # Verify RetainUntilDate is a UTC-aware ISO string (contains 'Z' or '+')
        rdate = kwargs["ObjectLockRetainUntilDate"]
        assert "+" in rdate or rdate.endswith("Z") or "+00:00" in rdate, (
            f"ObjectLockRetainUntilDate must be a UTC-aware timestamp, got: {rdate!r}"
        )

    # ── Test B: GOVERNANCE vs COMPLIANCE (parameterized) ─────────────────────

    @pytest.mark.parametrize("worm_mode", ["GOVERNANCE", "COMPLIANCE"])
    def test_b_worm_mode_governance_vs_compliance(self, worm_mode: str) -> None:
        """B: Sink correctly passes the configured WORM mode to S3 PutObject.

        GOVERNANCE: overridable only with special IAM permission + explicit bypass header.
        COMPLIANCE: immutable; no user can shorten retention or delete before expiry.
        Both must be supported; the active mode must exactly match what is passed to S3.
        """
        mock_s3 = MagicMock()

        with patch("boto3.client", return_value=mock_s3):
            sink = S3WormAuditSink(bucket="test-bucket", mode=worm_mode)
            sink.put_record("test-key", {"event": "kill_switch_change"})

        kwargs = mock_s3.put_object.call_args.kwargs

        assert kwargs["ObjectLockMode"] == worm_mode, (
            f"Expected ObjectLockMode={worm_mode!r}, "
            f"got {kwargs.get('ObjectLockMode')!r}"
        )

        # Both params still paired
        assert "ObjectLockRetainUntilDate" in kwargs, (
            "ObjectLockRetainUntilDate must be present regardless of mode"
        )

    # ── Test C: REQUIRED=1 + WORM_MODE missing → hard fail ───────────────────

    def test_c_prod_required_mode_missing_worm_mode_raises_config_error(self) -> None:
        """C: REQUIRED=1 + bucket set + WORM_MODE absent → AuditSinkConfigError.

        In production-required mode, the WORM retention mode must be explicitly
        declared. Relying on a silent default is not allowed — operators must
        consciously choose GOVERNANCE (pilot) or COMPLIANCE (locked).

        Error message must contain 'WORM_MODE_REQUIRED_BUT_NOT_SET' to provide
        actionable diagnostic information.
        """
        os.environ["KILL_SWITCH_AUDIT_REQUIRED"] = "1"
        os.environ["KILL_SWITCH_AUDIT_BUCKET"] = "test-audit-bucket-p58"
        os.environ.pop("KILL_SWITCH_AUDIT_WORM_MODE", None)  # explicitly absent

        with pytest.raises(AuditSinkConfigError) as exc_info:
            validate_audit_required_config()

        assert "WORM_MODE_REQUIRED_BUT_NOT_SET" in str(exc_info.value), (
            f"AuditSinkConfigError must contain 'WORM_MODE_REQUIRED_BUT_NOT_SET', "
            f"got: {exc_info.value}"
        )

    def test_c2_prod_required_mode_invalid_worm_mode_raises_config_error(self) -> None:
        """C2: REQUIRED=1 + invalid WORM_MODE value → AuditSinkConfigError.

        Prevents typos (e.g., 'governance' lowercase, 'GOVT', 'COMPLIANT') from
        silently being ignored or causing runtime errors at write time.
        """
        os.environ["KILL_SWITCH_AUDIT_REQUIRED"] = "1"
        os.environ["KILL_SWITCH_AUDIT_BUCKET"] = "test-audit-bucket-p58"
        os.environ["KILL_SWITCH_AUDIT_WORM_MODE"] = "INVALID_MODE"

        with pytest.raises(AuditSinkConfigError) as exc_info:
            validate_audit_required_config()

        assert "INVALID_WORM_MODE" in str(exc_info.value), (
            f"AuditSinkConfigError must contain 'INVALID_WORM_MODE', got: {exc_info.value}"
        )

    # ── Test D: No bypass behavior ────────────────────────────────────────────

    def test_d_no_bypass_behavior_in_sink(self) -> None:
        """D: put_record NEVER passes BypassGovernanceRetention or any bypass-related
        parameters to S3 PutObject.

        Rationale:
        - Governance bypass requires special IAM permission (s3:BypassGovernanceRetention)
          and an explicit request header. Our service role must NOT be granted this.
        - Our sink's purpose is to WRITE immutable records; it never needs to delete or
          override retention. Any bypass capability in PutObject would be a security risk.
        - Compliance mode objects cannot be bypassed by any user regardless; including
          bypass params for those would be an error.

        Note: BypassGovernanceRetention is only valid for delete/retention-change operations
        (DeleteObject, PutObjectRetention). Including it in PutObject is undefined behavior.
        We assert it is never attempted.
        """
        mock_s3 = MagicMock()

        with patch("boto3.client", return_value=mock_s3):
            sink = S3WormAuditSink(bucket="test-bucket", mode="GOVERNANCE")
            sink.put_record("test-key", {"action": "kill_switch_change"})

        kwargs = mock_s3.put_object.call_args.kwargs

        # Explicit check for the primary bypass parameter
        assert "BypassGovernanceRetention" not in kwargs, (
            "Sink must NEVER pass BypassGovernanceRetention to put_object. "
            "This would indicate the service role is attempting to bypass WORM."
        )

        # Check that no kwarg key contains 'bypass' (case-insensitive)
        bypass_keys = [k for k in kwargs if "bypass" in k.lower()]
        assert not bypass_keys, (
            f"Bypass-related parameter(s) found in put_object kwargs: {bypass_keys}. "
            "Service sink must not include any bypass semantics."
        )

        # Also verify no extra headers are being passed that could contain bypass directives
        # (boto3 allows custom headers via RequestPayer or ChecksumAlgorithm but not bypass)
        allowed_param_prefixes = {"Bucket", "Key", "Body", "ContentType", "ObjectLock",
                                   "Metadata", "ServerSideEncryption", "StorageClass",
                                   "Tagging", "ChecksumAlgorithm"}
        unexpected = [
            k for k in kwargs
            if not any(k.startswith(p) for p in allowed_param_prefixes)
        ]
        assert not unexpected, (
            f"Unexpected put_object parameters found: {unexpected}. "
            "Review to ensure no unintended capabilities are being invoked."
        )
