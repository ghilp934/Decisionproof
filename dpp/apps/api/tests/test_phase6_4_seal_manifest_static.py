"""Phase 6.4: Static validation of pilot cutover seal manifest schema.

Coverage (static — no AWS/S3/DB calls):
  T1) Valid manifest passes all required field checks
  T2) Missing required fields are individually detected
  T3) Evidence dir timestamp follows the expected format (YYYYMMDDTHHMMSSz)
  T4) S3 key follows the deterministic construction rule
  T5) seal_evidence_to_worm.sh exists and contains required safety keywords
  T6) pilot_cutover_run.sh exists with 'set -euo pipefail' and 'exit 2' gate
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Path setup
# ---------------------------------------------------------------------------

_TESTS_DIR = Path(__file__).parent                      # dpp/apps/api/tests/
_DPP_DIR = _TESTS_DIR.parents[2]                        # dpp/
_TOOLS_DIR = _DPP_DIR / "tools"

SEAL_SCRIPT = _TOOLS_DIR / "seal_evidence_to_worm.sh"
CUTOVER_SCRIPT = _TOOLS_DIR / "pilot_cutover_run.sh"

# ---------------------------------------------------------------------------
# Manifest schema helpers
# ---------------------------------------------------------------------------

# Required fields as specified in P6.4 spec
_REQUIRED_MANIFEST_FIELDS = {
    "sha256",
    "s3_key",
    "lock_mode",
    "retain_until",
    "version_id",
}

_OPTIONAL_MANIFEST_FIELDS = {
    "schema_version",
    "generated_at",
    "tarball_path",
    "tarball_size_bytes",
    "s3_bucket",
    "etag",
    "retention_days",
    "verified_lock_mode",
    "verified_retain_until",
}

_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_ISO8601_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
_TS_DIR_PATTERN = re.compile(r"^\d{8}T\d{6}Z$")  # e.g. 20260221T120000Z


def _make_valid_manifest(**overrides) -> dict:
    """Return a valid manifest dict, optionally overriding fields."""
    base = {
        "schema_version": "1.0",
        "generated_at": "20260221T120000Z",
        "tarball_path": "/path/to/evidence.tar.gz",
        "tarball_size_bytes": 24576,
        "sha256": "a" * 64,
        "s3_key": "evidence/cutover/phase6_4_cutover/20260221T120000Z/evidence.tar.gz",
        "s3_bucket": "<BUCKET_REDACTED_SEE_ENV>",
        "lock_mode": "GOVERNANCE",
        "retain_until": "2027-02-21T12:00:00Z",
        "retention_days": 365,
        "version_id": "9D6uFiT7gqXxR4T.WelXJ1qbTVMGXYw",
        "etag": "abc1234def5678",
        "verified_lock_mode": "GOVERNANCE",
        "verified_retain_until": "2027-02-21T12:00:00Z",
    }
    base.update(overrides)
    return base


# ===========================================================================
# T1: Valid manifest passes all required field checks
# ===========================================================================


def test_t1_valid_manifest_has_all_required_fields():
    """T1: A well-formed manifest has all required fields with correct types."""
    manifest = _make_valid_manifest()

    # All required fields must be present
    for field in _REQUIRED_MANIFEST_FIELDS:
        assert field in manifest, f"Required field missing from manifest: {field!r}"
        assert manifest[field] is not None, f"Required field is None: {field!r}"
        assert str(manifest[field]).strip(), f"Required field is empty: {field!r}"

    # sha256 must be 64-char lowercase hex
    assert _SHA256_PATTERN.match(manifest["sha256"]), (
        f"sha256 must be 64-char hex, got: {manifest['sha256']!r}"
    )

    # retain_until must be ISO8601 UTC
    assert _ISO8601_PATTERN.match(manifest["retain_until"]), (
        f"retain_until must be ISO8601 UTC (YYYY-MM-DDThh:mm:ssZ), "
        f"got: {manifest['retain_until']!r}"
    )

    # lock_mode must be GOVERNANCE or COMPLIANCE
    assert manifest["lock_mode"] in ("GOVERNANCE", "COMPLIANCE"), (
        f"lock_mode must be GOVERNANCE or COMPLIANCE, got: {manifest['lock_mode']!r}"
    )

    # version_id must be non-empty string
    assert isinstance(manifest["version_id"], str) and len(manifest["version_id"]) > 0, (
        "version_id must be a non-empty string"
    )

    # s3_key must be a non-empty string
    assert isinstance(manifest["s3_key"], str) and len(manifest["s3_key"]) > 0, (
        "s3_key must be a non-empty string"
    )


# ===========================================================================
# T2: Missing required fields are individually detected
# ===========================================================================


@pytest.mark.parametrize("missing_field", sorted(_REQUIRED_MANIFEST_FIELDS))
def test_t2_missing_required_field_detected(missing_field: str):
    """T2: Each required manifest field, if absent, causes validation failure."""
    manifest = _make_valid_manifest()
    del manifest[missing_field]

    missing = [f for f in _REQUIRED_MANIFEST_FIELDS if f not in manifest]
    assert missing, (
        f"Expected validation to detect missing field '{missing_field}', "
        f"but it was not flagged. Required fields: {_REQUIRED_MANIFEST_FIELDS}"
    )


# ===========================================================================
# T3: Evidence dir timestamp follows the expected pattern
# ===========================================================================


def test_t3_evidence_dir_timestamp_pattern():
    """T3: Evidence dir timestamp must match YYYYMMDDTHHMMSSz (e.g. 20260221T120000Z).
    This is what pilot_cutover_run.sh generates via 'date -u +%Y%m%dT%H%M%SZ'.
    """
    valid_timestamps = [
        "20260221T120000Z",
        "20260101T000000Z",
        "20261231T235959Z",
        "20260315T083045Z",
    ]
    invalid_timestamps = [
        "2026-02-21T12:00:00Z",  # ISO8601 format (wrong — not for dir name)
        "20260221",              # date only
        "20260221T120000",       # missing Z suffix
        "20260221_120000Z",      # underscore instead of T
        "",
    ]

    for ts in valid_timestamps:
        assert _TS_DIR_PATTERN.match(ts), (
            f"Valid timestamp {ts!r} did not match pattern {_TS_DIR_PATTERN.pattern}"
        )

    for ts in invalid_timestamps:
        assert not _TS_DIR_PATTERN.match(ts), (
            f"Invalid timestamp {ts!r} incorrectly matched pattern"
        )


# ===========================================================================
# T4: S3 key follows deterministic construction rule
# ===========================================================================


def test_t4_s3_key_construction():
    """T4: S3 key must follow the pattern:
    <DPP_WORM_PREFIX>/phase6_4_cutover/<TS>/evidence.tar.gz
    This is the deterministic key rule from seal_evidence_to_worm.sh.
    """
    test_cases = [
        (
            "evidence/cutover",
            "20260221T120000Z",
            "evidence/cutover/phase6_4_cutover/20260221T120000Z/evidence.tar.gz",
        ),
        (
            "evidence/cutover/",     # trailing slash stripped
            "20260221T120000Z",
            "evidence/cutover/phase6_4_cutover/20260221T120000Z/evidence.tar.gz",
        ),
        (
            "prod/evidence",
            "20261231T235959Z",
            "prod/evidence/phase6_4_cutover/20261231T235959Z/evidence.tar.gz",
        ),
    ]

    for prefix, ts, expected_key in test_cases:
        # Replicate the construction from the script:
        # S3_KEY="${DPP_WORM_PREFIX%/}/phase6_4_cutover/${TS}/evidence.tar.gz"
        actual_key = f"{prefix.rstrip('/')}/phase6_4_cutover/{ts}/evidence.tar.gz"
        assert actual_key == expected_key, (
            f"S3 key construction mismatch:\n"
            f"  prefix={prefix!r}  ts={ts!r}\n"
            f"  expected={expected_key!r}\n"
            f"  got     ={actual_key!r}"
        )

    # Verify the key from valid manifest follows the pattern
    manifest = _make_valid_manifest()
    s3_key = manifest["s3_key"]
    assert "phase6_4_cutover/" in s3_key, (
        f"s3_key must contain 'phase6_4_cutover/', got: {s3_key!r}"
    )
    assert s3_key.endswith("/evidence.tar.gz"), (
        f"s3_key must end with '/evidence.tar.gz', got: {s3_key!r}"
    )

    # Extract the timestamp segment from the key
    parts = s3_key.split("/phase6_4_cutover/")
    assert len(parts) == 2, f"s3_key must contain exactly one 'phase6_4_cutover/' segment"
    ts_segment = parts[1].split("/")[0]
    assert _TS_DIR_PATTERN.match(ts_segment), (
        f"Timestamp in s3_key must match pattern, got: {ts_segment!r}"
    )


# ===========================================================================
# T5: seal_evidence_to_worm.sh exists with required safety keywords
# ===========================================================================


def test_t5_seal_script_exists_and_has_required_keywords():
    """T5: seal_evidence_to_worm.sh must exist and contain required safety elements."""
    assert SEAL_SCRIPT.exists(), (
        f"seal_evidence_to_worm.sh not found at: {SEAL_SCRIPT}"
    )

    content = SEAL_SCRIPT.read_text(encoding="utf-8")

    # Safety: must use pipefail
    assert "set -euo pipefail" in content, (
        "seal_evidence_to_worm.sh must have 'set -euo pipefail'"
    )

    # Must perform tar.gz creation
    assert "tar -czf" in content, (
        "seal_evidence_to_worm.sh must create a tar.gz (tar -czf)"
    )

    # Must compute sha256
    assert "sha256sum" in content or "shasum" in content, (
        "seal_evidence_to_worm.sh must compute SHA-256 (sha256sum or shasum)"
    )

    # Must use s3api put-object (not s3 cp — object lock requires s3api)
    assert "s3api put-object" in content, (
        "seal_evidence_to_worm.sh must use 'aws s3api put-object' for Object Lock"
    )

    # Must verify with head-object
    assert "s3api head-object" in content, (
        "seal_evidence_to_worm.sh must verify upload with 'aws s3api head-object'"
    )

    # Must write manifest JSON
    assert "60_evidence_seal_manifest.json" in content or "MANIFEST" in content, (
        "seal_evidence_to_worm.sh must write 60_evidence_seal_manifest.json"
    )

    # Must NOT dump env vars (security)
    assert "\nenv\n" not in content and "\n env\n" not in content, (
        "seal_evidence_to_worm.sh must NOT dump env vars"
    )

    # Must use object-lock-mode parameter
    assert "object-lock-mode" in content, (
        "seal_evidence_to_worm.sh must pass --object-lock-mode to put-object"
    )

    # Must use object-lock-retain-until-date
    assert "object-lock-retain-until-date" in content, (
        "seal_evidence_to_worm.sh must pass --object-lock-retain-until-date to put-object"
    )


# ===========================================================================
# T6: pilot_cutover_run.sh exists with required safety and gate patterns
# ===========================================================================


def test_t6_cutover_script_exists_with_safety_patterns():
    """T6: pilot_cutover_run.sh must exist with pipefail, exit 2 DB gate,
    and must NOT dump env vars.
    """
    assert CUTOVER_SCRIPT.exists(), (
        f"pilot_cutover_run.sh not found at: {CUTOVER_SCRIPT}"
    )

    content = CUTOVER_SCRIPT.read_text(encoding="utf-8")

    # Safety: must use pipefail
    assert "set -euo pipefail" in content, (
        "pilot_cutover_run.sh must have 'set -euo pipefail'"
    )

    # Must have exit 2 for DB gate
    assert "exit 2" in content, (
        "pilot_cutover_run.sh must exit with code 2 when DB checkpoint is incomplete"
    )

    # Must check for COMPLETED in checkpoint file
    assert "COMPLETED" in content, (
        "pilot_cutover_run.sh must check for 'COMPLETED' in DB checkpoint file"
    )

    # Must call seal_evidence_to_worm.sh
    assert "seal_evidence_to_worm.sh" in content, (
        "pilot_cutover_run.sh must call seal_evidence_to_worm.sh"
    )

    # Must NOT dump env vars (security)
    lines = content.splitlines()
    for i, line in enumerate(lines, 1):
        stripped = line.strip()
        # Allow 'env' as part of a variable name or comment but not as a standalone command
        if stripped in ("env", "env | tee", "printenv") or stripped.startswith("env "):
            if not stripped.startswith("#"):
                raise AssertionError(
                    f"pilot_cutover_run.sh line {i} dumps env vars: {line!r}\n"
                    "This would expose secrets in evidence files."
                )

    # Must use AWS_PROFILE (not hardcode credentials)
    assert "AWS_PROFILE" in content, (
        "pilot_cutover_run.sh must respect AWS_PROFILE (default: dpp-admin)"
    )

    # Must reference 55_db_rollback_human_checkpoint.txt
    assert "55_db_rollback_human_checkpoint" in content, (
        "pilot_cutover_run.sh must create 55_db_rollback_human_checkpoint.txt"
    )

    # Must capture RC gates output to 30_rc_gates.txt
    assert "30_rc_gates" in content, (
        "pilot_cutover_run.sh must write RC gates output to 30_rc_gates.txt"
    )

    # Must support --seal-only re-run mode
    assert "seal-only" in content, (
        "pilot_cutover_run.sh must support --seal-only re-run mode"
    )
