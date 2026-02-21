"""RC-10 Contract Gate: Log Masking + Kill-Switch WORM Audit.

Test matrix:
  A – Webhook log: PII / Bearer token → [REDACTED], payload_hash present
  B – exc_info traceback: no raw sensitive value leaks
  C – sanitize_str performance: 50 000-char string < 1 s, returns [TRUNCATED:...]
  D – STRICT=1 + FailingAuditSink → HTTP 500, state unchanged, no raw token in logs
  E – STRICT=0 + FailingAuditSink → HTTP 200, audit_write_ok=false
"""

import json
import logging
import os
import time
from io import StringIO
from typing import Generator

import pytest
from httpx import ASGITransport, AsyncClient

from dpp_api.main import app
from dpp_api.utils.logging import JSONFormatter
from dpp_api.utils.sanitize import sanitize_str


# ── Helpers ────────────────────────────────────────────────────────────────────

def _parse_json_logs(raw: str) -> list[dict]:
    logs = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            logs.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    return logs


class LogCapture:
    """Context manager that captures JSON-formatted log output.

    Usage: read logs INSIDE the `with` block to avoid closed-stream errors.
    """

    def __init__(self) -> None:
        self._root = logging.getLogger()
        self._saved: list[logging.Handler] = []
        self._stream: StringIO | None = None
        self._handler: logging.StreamHandler | None = None

    def __enter__(self) -> "LogCapture":
        self._saved = self._root.handlers[:]
        for h in self._saved:
            self._root.removeHandler(h)
        self._stream = StringIO()
        self._handler = logging.StreamHandler(self._stream)
        self._handler.setFormatter(JSONFormatter())
        self._root.addHandler(self._handler)
        return self

    def __exit__(self, *_) -> None:
        if self._handler:
            self._root.removeHandler(self._handler)
        if self._stream:
            self._stream.close()
        for h in self._saved:
            self._root.addHandler(h)

    def raw(self) -> str:
        """Return captured raw log text (call INSIDE the `with` block)."""
        assert self._stream is not None and not self._stream.closed, \
            "Call raw() inside the `with LogCapture()` block"
        return self._stream.getvalue()

    def logs(self) -> list[dict]:
        """Return parsed JSON log entries (call INSIDE the `with` block)."""
        return _parse_json_logs(self.raw())


# ── Test class ─────────────────────────────────────────────────────────────────

class TestRC10LogMaskingAndWormAudit:
    """RC-10: Log Masking (P5.2) + Kill-Switch WORM Audit (P5.3)."""

    @pytest.fixture(autouse=True)
    def reset_test_state(self) -> Generator:
        """Restore admin module sink and kill-switch mode after each test."""
        import dpp_api.routers.admin as admin_module
        from dpp_api.config.kill_switch import get_kill_switch_config

        original_sink = admin_module._audit_sink
        config = get_kill_switch_config()
        original_mode = config._state.mode

        yield

        admin_module._audit_sink = original_sink
        config._state.mode = original_mode

    # ── Test A ──────────────────────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_a_webhook_log_redacts_pii_and_bearer(self) -> None:
        """A: Webhook receives payload with PII/Bearer → WEBHOOK_RECEIVED log has hash/size,
        raw PII is NOT in any log line.

        Design note: the handler logs only (provider, payload_hash, payload_size) — the body
        content is deliberately NOT logged. So [REDACTED] won't appear (nothing to redact in
        the extras), but raw sensitive values must be absent from all log output.

        PayPal client is not configured in CI; the request raises a ValueError after
        WEBHOOK_RECEIVED is already logged. We catch the exception and verify the logs.
        """
        sensitive_email = "user@example.com"
        sensitive_bearer = "Bearer supersecrettoken123"
        payload = json.dumps({
            "event_type": "PAYMENT.CAPTURE.COMPLETED",
            "id": "EVT-001",
            "payer": {"email": sensitive_email},
            "authorization": sensitive_bearer,
        }).encode()

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            with LogCapture() as cap:
                try:
                    await client.post(
                        "/webhooks/paypal",
                        content=payload,
                        headers={
                            "Content-Type": "application/json",
                            "X-PAYPAL-TRANSMISSION-ID": "tid-001",
                            "X-PAYPAL-TRANSMISSION-TIME": "2026-01-01T00:00:00Z",
                            "X-PAYPAL-CERT-URL": "https://example.com/cert",
                            "X-PAYPAL-AUTH-ALGO": "SHA256withRSA",
                            "X-PAYPAL-TRANSMISSION-SIG": "fakesig",
                        },
                    )
                except Exception:
                    # PayPal client not configured in CI → ValueError expected after
                    # WEBHOOK_RECEIVED is already logged. Ignore exception; check logs.
                    pass

                log_output = cap.raw()
                logs = cap.logs()

        # WEBHOOK_RECEIVED must be present with hash and size
        webhook_log = next(
            (l for l in logs if l.get("message") == "WEBHOOK_RECEIVED"), None
        )
        assert webhook_log is not None, "WEBHOOK_RECEIVED log not found"
        assert "payload_hash" in webhook_log, "payload_hash missing from WEBHOOK_RECEIVED"
        assert "payload_size" in webhook_log, "payload_size missing from WEBHOOK_RECEIVED"
        assert webhook_log["payload_size"] == len(payload), "payload_size value incorrect"

        # Raw sensitive values must NOT appear anywhere in log output
        assert sensitive_email not in log_output, \
            f"Raw email found in logs: {sensitive_email}"
        assert "supersecrettoken123" not in log_output, \
            "Raw Bearer token value found in logs"

    # ── Test B ──────────────────────────────────────────────────────────────────

    def test_b_exc_info_traceback_redacted(self) -> None:
        """B: logger.error(exc_info=True) → exc_info field in log has no raw sensitive value."""
        import sys

        test_logger = logging.getLogger("test_rc10.exc_info")
        secret_value = "Bearer sk_live_abc123_SUPERSECRET"

        try:
            raise ValueError(f"Payment failed for token {secret_value}")
        except ValueError:
            exc = sys.exc_info()
            with LogCapture() as cap:
                test_logger.error("TEST_EXCEPTION", exc_info=exc)
                logs = cap.logs()

        assert len(logs) > 0, "No logs captured"

        exc_log = next((l for l in logs if l.get("message") == "TEST_EXCEPTION"), None)
        assert exc_log is not None, "TEST_EXCEPTION log not found"

        exc_info_field = exc_log.get("exc_info", "")
        assert isinstance(exc_info_field, str), "exc_info should be a string"

        # The raw secret must not appear
        assert "sk_live_abc123_SUPERSECRET" not in exc_info_field, \
            "Raw secret found in exc_info traceback"

        # [REDACTED] should appear because sanitize_str replaces the Bearer token
        assert "[REDACTED]" in exc_info_field, \
            "[REDACTED] not found in sanitized traceback"

    # ── Test C ──────────────────────────────────────────────────────────────────

    def test_c_sanitize_str_large_input_performance(self) -> None:
        """C: sanitize_str on 50 000-char string completes < 1 second and returns [TRUNCATED...]."""
        large_input = "a" * 50_000
        start = time.monotonic()
        result = sanitize_str(large_input)
        elapsed = time.monotonic() - start

        assert elapsed < 1.0, f"sanitize_str too slow: {elapsed:.3f}s"
        assert result.startswith("[TRUNCATED"), \
            f"Expected [TRUNCATED...], got: {result[:50]}"
        assert "sha256=" in result, "sha256 fingerprint not found in truncated output"

    # ── Test D ──────────────────────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_d_strict_mode_audit_failure_returns_500(self) -> None:
        """D: STRICT=1 + FailingAuditSink → HTTP 500, kill-switch state unchanged, no raw token in logs."""
        from dpp_api.audit.sinks import FailingAuditSink
        from dpp_api.config.kill_switch import get_kill_switch_config
        import dpp_api.routers.admin as admin_module

        admin_module._audit_sink = FailingAuditSink()
        config = get_kill_switch_config()
        original_mode = config._state.mode

        admin_token = os.getenv("ADMIN_TOKEN", "test-admin-token-for-rc10")
        os.environ.setdefault("ADMIN_TOKEN", admin_token)
        os.environ["KILL_SWITCH_AUDIT_STRICT"] = "1"

        transport = ASGITransport(app=app)
        try:
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                with LogCapture() as cap:
                    response = await client.post(
                        "/admin/kill-switch",
                        json={"mode": "SAFE_MODE", "reason": "RC-10 Test D", "ttl_minutes": 0},
                        headers={"X-Admin-Token": admin_token},
                    )
                    log_output = cap.raw()

            # STRICT=1 must block the change → 500
            assert response.status_code == 500, \
                f"Expected 500, got {response.status_code}: {response.text}"

            # State must NOT have changed
            assert config._state.mode == original_mode, \
                f"Kill-switch state changed despite audit failure! mode={config._state.mode}"

            # Raw token must not appear in logs
            assert admin_token not in log_output, "Raw admin token found in logs"

        finally:
            os.environ["KILL_SWITCH_AUDIT_STRICT"] = "0"

    # ── Test E ──────────────────────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_e_non_strict_mode_audit_failure_returns_200(self) -> None:
        """E: STRICT=0 + FailingAuditSink → HTTP 200, audit_write_ok=false in response."""
        from dpp_api.audit.sinks import FailingAuditSink
        import dpp_api.routers.admin as admin_module

        admin_module._audit_sink = FailingAuditSink()

        admin_token = os.getenv("ADMIN_TOKEN", "test-admin-token-for-rc10")
        os.environ.setdefault("ADMIN_TOKEN", admin_token)
        os.environ["KILL_SWITCH_AUDIT_STRICT"] = "0"

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/admin/kill-switch",
                json={"mode": "NORMAL", "reason": "RC-10 Test E", "ttl_minutes": 0},
                headers={"X-Admin-Token": admin_token},
            )

        assert response.status_code == 200, \
            f"Expected 200, got {response.status_code}: {response.text}"

        body = response.json()
        assert body.get("audit_write_ok") is False, \
            f"Expected audit_write_ok=false, got: {body.get('audit_write_ok')}"
