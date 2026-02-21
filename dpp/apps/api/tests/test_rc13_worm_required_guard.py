"""RC-13 Contract Gate: WORM "Must-be-WORM" Hardening.

Prevents silent FileAuditSink fallback in production by enforcing
KILL_SWITCH_AUDIT_REQUIRED=1 configuration guard.

Test matrix:
  T1 – required_without_bucket_raises_config_error
       REQUIRED=1 + missing KILL_SWITCH_AUDIT_BUCKET
       → validate_audit_required_config raises AuditSinkConfigError

  T2 – required_without_bucket_blocks_kill_switch_change
       REQUIRED=1 + missing bucket
       → POST /admin/kill-switch returns HTTP 500 AND mode unchanged (fail-closed)

  T3 – required_with_bucket_returns_s3_sink_not_file_sink
       REQUIRED=1 + KILL_SWITCH_AUDIT_BUCKET set
       → get_default_audit_sink returns S3WormAuditSink, never FileAuditSink

  T4 – break_glass_runbook_exists_and_has_required_headings
       ops/runbooks/kill_switch_audit_break_glass_alerts.md exists
       and contains all required sections.
"""

import json
import os
from pathlib import Path
from typing import Generator

import pytest
from httpx import ASGITransport, AsyncClient

from dpp_api.main import app

# ── Paths ─────────────────────────────────────────────────────────────────────

# parents[0] = tests/   parents[3] = dpp/
DPP_ROOT = Path(__file__).resolve().parents[3]
_BREAK_GLASS_RUNBOOK = (
    DPP_ROOT / "ops" / "runbooks" / "kill_switch_audit_break_glass_alerts.md"
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

class TestRC13WormRequiredGuard:
    """RC-13: WORM Required Guard + Break-glass Alert Plan."""

    @pytest.fixture(autouse=True)
    def restore_state(self) -> Generator:
        """Restore admin sink, kill-switch mode, and env vars after each test."""
        import dpp_api.routers.admin as admin_module
        from dpp_api.config.kill_switch import get_kill_switch_config

        original_sink = admin_module._audit_sink
        config = get_kill_switch_config()
        original_mode = config._state.mode

        # Snapshot relevant env vars
        env_keys = ["KILL_SWITCH_AUDIT_REQUIRED", "KILL_SWITCH_AUDIT_BUCKET"]
        env_snapshot = {k: os.environ.get(k) for k in env_keys}

        yield

        # Restore
        admin_module._audit_sink = original_sink
        config._state.mode = original_mode
        for k, v in env_snapshot.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    # ── T1 ────────────────────────────────────────────────────────────────────

    def test_t1_required_without_bucket_raises_config_error(self) -> None:
        """T1: REQUIRED=1 + missing bucket → AuditSinkConfigError raised."""
        from dpp_api.audit.sinks import AuditSinkConfigError, validate_audit_required_config

        os.environ["KILL_SWITCH_AUDIT_REQUIRED"] = "1"
        os.environ.pop("KILL_SWITCH_AUDIT_BUCKET", None)

        with pytest.raises(AuditSinkConfigError) as exc_info:
            validate_audit_required_config()

        assert "AUDIT_SINK_REQUIRED_BUT_NOT_CONFIGURED" in str(exc_info.value), (
            f"AuditSinkConfigError message must contain 'AUDIT_SINK_REQUIRED_BUT_NOT_CONFIGURED', "
            f"got: {exc_info.value}"
        )

    # ── T2 ────────────────────────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_t2_required_without_bucket_blocks_kill_switch_change(self) -> None:
        """T2: REQUIRED=1 + no bucket → /admin/kill-switch returns 500, mode unchanged."""
        import dpp_api.routers.admin as admin_module
        from dpp_api.config.kill_switch import get_kill_switch_config

        config = get_kill_switch_config()
        original_mode = config._state.mode

        # Reset singleton so it calls get_default_audit_sink again
        admin_module._audit_sink = None
        os.environ["KILL_SWITCH_AUDIT_REQUIRED"] = "1"
        os.environ.pop("KILL_SWITCH_AUDIT_BUCKET", None)

        admin_token = os.getenv("ADMIN_TOKEN", "test-admin-token-rc13")
        os.environ.setdefault("ADMIN_TOKEN", admin_token)

        transport = ASGITransport(app=app)
        try:
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                response = await client.post(
                    "/admin/kill-switch",
                    json={"mode": "SAFE_MODE", "reason": "RC-13 T2 test", "ttl_minutes": 0},
                    headers={"X-Admin-Token": admin_token},
                )

            assert response.status_code == 500, (
                f"Expected HTTP 500 when audit is REQUIRED but not configured, "
                f"got {response.status_code}: {response.text}"
            )

            # Mode must NOT have changed
            assert config._state.mode == original_mode, (
                f"Kill-switch state changed despite audit misconfiguration! "
                f"mode={config._state.mode!r} (expected {original_mode!r})"
            )

            # Response must not contain raw tokens or credentials
            body = response.text
            assert admin_token not in body, "Raw admin token must not appear in error response"

        finally:
            os.environ.pop("KILL_SWITCH_AUDIT_REQUIRED", None)

    # ── T3 ────────────────────────────────────────────────────────────────────

    def test_t3_required_with_bucket_returns_s3_sink_not_file_sink(self) -> None:
        """T3: REQUIRED=1 + bucket set → S3WormAuditSink returned; FileAuditSink never."""
        from dpp_api.audit.sinks import (
            FileAuditSink,
            S3WormAuditSink,
            get_default_audit_sink,
        )

        os.environ["KILL_SWITCH_AUDIT_REQUIRED"] = "1"
        os.environ["KILL_SWITCH_AUDIT_BUCKET"] = "test-audit-bucket-rc13"
        os.environ["KILL_SWITCH_AUDIT_WORM_MODE"] = "GOVERNANCE"  # P5.8: required when REQUIRED=1
        os.environ["AWS_DEFAULT_REGION"] = "ap-northeast-2"

        try:
            sink = get_default_audit_sink()
            assert isinstance(sink, S3WormAuditSink), (
                f"Expected S3WormAuditSink when REQUIRED=1 and bucket is set, "
                f"got {type(sink).__name__}"
            )
            assert not isinstance(sink, FileAuditSink), (
                "FileAuditSink must NEVER be returned when KILL_SWITCH_AUDIT_REQUIRED=1"
            )
        finally:
            os.environ.pop("KILL_SWITCH_AUDIT_REQUIRED", None)
            os.environ.pop("KILL_SWITCH_AUDIT_BUCKET", None)
            os.environ.pop("KILL_SWITCH_AUDIT_WORM_MODE", None)

    # ── T4 ────────────────────────────────────────────────────────────────────

    def test_t4_break_glass_runbook_exists_and_has_required_headings(self) -> None:
        """T4: Break-glass runbook exists and contains all required sections."""
        assert _BREAK_GLASS_RUNBOOK.exists(), (
            f"Break-glass alert runbook not found: {_BREAK_GLASS_RUNBOOK}\n"
            "Create ops/runbooks/kill_switch_audit_break_glass_alerts.md"
        )

        text = _BREAK_GLASS_RUNBOOK.read_text(encoding="utf-8")

        required_terms = [
            "Break-glass",
            "CloudTrail",
            "EventBridge",
            "SNS",
            "bypassGovernanceRetention",
        ]
        for term in required_terms:
            assert term in text, (
                f"Required term '{term}' missing from break-glass runbook.\n"
                f"File: {_BREAK_GLASS_RUNBOOK}"
            )
