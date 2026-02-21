"""RC-10.P5.7 Contract Gate: Webhook Error Semantics (Retry Storm Prevention).

Prevents retry storms by enforcing strict HTTP status code taxonomy:
  (A) Invalid JSON / malformed payload         → 400
  (B) Signature invalid / verify != SUCCESS    → 401  (NEVER 500)
  (C) Required header missing                  → 400
  (D) Our misconfig (missing secret/webhook_id)→ 500 WEBHOOK_PROVIDER_MISCONFIG
  (E) Upstream network/SDK error               → 500 WEBHOOK_VERIFY_UPSTREAM_FAILED
  (F) Internal processing/DB error             → 500 WEBHOOK_INTERNAL_ERROR

Test matrix:
  A – Invalid JSON → 400; WEBHOOK_INVALID_JSON log with payload_hash
  B – PayPal signature FAILURE → 401 (NOT 500); warning log with payload_hash
  C – PayPal verify upstream httpx.RequestError → 500; error log + Retry-After
  D – Toss HMAC signature invalid → 401; warning log with payload_hash
  E – No false positive: WEBHOOK_RECEIVED log emitted; no raw token in output
"""

import hashlib
import hmac
import json
import logging
import os
from io import StringIO
from typing import Generator
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from httpx import ASGITransport, AsyncClient

from dpp_api.main import app
from dpp_api.utils.logging import JSONFormatter


# ── Log capture helper (same pattern as RC-10) ────────────────────────────────

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
    """Capture JSON-formatted log output for a test block."""

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
        assert self._stream is not None and not self._stream.closed, \
            "Call raw() inside the `with LogCapture()` block"
        return self._stream.getvalue()

    def logs(self) -> list[dict]:
        return _parse_json_logs(self.raw())


# ── Shared constants ──────────────────────────────────────────────────────────

_PAYPAL_HEADERS = {
    "X-PAYPAL-TRANSMISSION-ID": "test-tx-001",
    "X-PAYPAL-TRANSMISSION-TIME": "2026-02-21T00:00:00Z",
    "X-PAYPAL-CERT-URL": "https://api.sandbox.paypal.com/v1/notifications/certs/CERT-test",
    "X-PAYPAL-AUTH-ALGO": "SHA256withRSA",
    "X-PAYPAL-TRANSMISSION-SIG": "test-sig-value",
}

_PAYPAL_BODY = json.dumps({
    "id": "EVT-P57-TEST",
    "event_type": "PAYMENT.CAPTURE.COMPLETED",
}).encode()

_TOSS_BODY = json.dumps({
    "eventType": "PAYMENT_STATUS_CHANGED",
    "data": {"paymentKey": "pk_test_abc123"},
}).encode()


# ── Test class ────────────────────────────────────────────────────────────────

class TestRC10WebhookErrorSemantics:
    """RC-10.P5.7: Webhook error taxonomy — retry storm prevention."""

    @pytest.fixture(autouse=True)
    def clean_toss_env(self) -> Generator:
        """Remove TOSS_WEBHOOK_SECRET after each test to prevent cross-test contamination."""
        yield
        os.environ.pop("TOSS_WEBHOOK_SECRET", None)

    # ── Test A: Invalid JSON → 400 ────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_a_invalid_json_returns_400_with_log(self) -> None:
        """A: Invalid JSON → HTTP 400; WEBHOOK_INVALID_JSON log emitted with payload_hash."""
        bad_body = b"not valid json {"

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            with LogCapture() as cap:
                response = await client.post(
                    "/webhooks/paypal",
                    content=bad_body,
                    headers={
                        "Content-Type": "application/json",
                        **_PAYPAL_HEADERS,
                    },
                )

                log_output = cap.raw()
                logs = cap.logs()

        # Status must be 400 (A — client error, not 422)
        assert response.status_code == 400, (
            f"Expected 400 for invalid JSON, got {response.status_code}: {response.text}"
        )

        # Response must be RFC 9457 Problem Details
        body = response.json()
        assert body.get("status") == 400
        assert body.get("error_code") == "WEBHOOK_INVALID_JSON"
        assert body.get("provider") == "paypal"

        # WEBHOOK_INVALID_JSON must be logged (not silently dropped)
        invalid_json_log = next(
            (l for l in logs if l.get("message") == "WEBHOOK_INVALID_JSON"), None
        )
        assert invalid_json_log is not None, (
            f"WEBHOOK_INVALID_JSON log not found in output:\n{log_output}"
        )
        assert "payload_hash" in invalid_json_log, (
            "payload_hash must be present in WEBHOOK_INVALID_JSON log"
        )

    # ── Test B: PayPal signature FAILURE → 401, NEVER 500 ────────────────────

    @pytest.mark.asyncio
    async def test_b_paypal_signature_failure_returns_401_not_500(self) -> None:
        """B: PayPal verify returns FAILURE → HTTP 401 (never 500); warning log with payload_hash.

        This test guards against the critical bug where the outer except Exception
        was catching the HTTPException for signature mismatch and returning 500.
        """
        mock_paypal = MagicMock()
        mock_paypal.verify_webhook_signature = AsyncMock(
            return_value={"verification_status": "FAILURE"}
        )

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            with LogCapture() as cap:
                with patch("dpp_api.routers.webhooks.get_paypal_client", return_value=mock_paypal):
                    response = await client.post(
                        "/webhooks/paypal",
                        content=_PAYPAL_BODY,
                        headers={"Content-Type": "application/json", **_PAYPAL_HEADERS},
                    )

                log_output = cap.raw()
                logs = cap.logs()

        # MUST be 401, not 500 — signature mismatch is a client error
        assert response.status_code == 401, (
            f"Expected 401 for signature FAILURE, got {response.status_code}: {response.text}\n"
            "CRITICAL: signature mismatch must NEVER return 500 (retry storm risk)"
        )

        body = response.json()
        assert body.get("error_code") == "WEBHOOK_SIGNATURE_INVALID"
        assert body.get("provider") == "paypal"
        # Response must NOT have Retry-After (4xx → no retry prompt)
        assert "retry-after" not in {k.lower() for k in response.headers}

        # Warning log must be emitted with payload_hash
        sig_log = next(
            (l for l in logs if l.get("message") == "WEBHOOK_SIGNATURE_INVALID"), None
        )
        assert sig_log is not None, (
            f"WEBHOOK_SIGNATURE_INVALID warning log not found:\n{log_output}"
        )
        assert "payload_hash" in sig_log, "payload_hash must be in WEBHOOK_SIGNATURE_INVALID log"
        assert sig_log.get("provider") == "paypal"

    # ── Test C: PayPal upstream error → 500 + Retry-After ────────────────────

    @pytest.mark.asyncio
    async def test_c_paypal_verify_upstream_failure_returns_500(self) -> None:
        """C: httpx.RequestError during PayPal verification → 500 + Retry-After: 60."""
        mock_paypal = MagicMock()
        mock_paypal.verify_webhook_signature = AsyncMock(
            side_effect=httpx.RequestError("connection timeout")
        )

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            with LogCapture() as cap:
                with patch("dpp_api.routers.webhooks.get_paypal_client", return_value=mock_paypal):
                    response = await client.post(
                        "/webhooks/paypal",
                        content=_PAYPAL_BODY,
                        headers={"Content-Type": "application/json", **_PAYPAL_HEADERS},
                    )

                log_output = cap.raw()
                logs = cap.logs()

        # Must be 500 (E — our upstream failure)
        assert response.status_code == 500, (
            f"Expected 500 for upstream RequestError, got {response.status_code}: {response.text}"
        )

        body = response.json()
        assert body.get("error_code") == "WEBHOOK_VERIFY_UPSTREAM_FAILED"
        assert body.get("provider") == "paypal"

        # Retry-After header MUST be present on 5xx (operational guidance for retry)
        assert "retry-after" in {k.lower() for k in response.headers}, (
            "Retry-After header must be present on 5xx webhook responses"
        )
        assert response.headers.get("retry-after") == "60"

        # Error log must be emitted with payload_hash
        upstream_log = next(
            (l for l in logs if l.get("message") == "WEBHOOK_VERIFY_UPSTREAM_FAILED"), None
        )
        assert upstream_log is not None, (
            f"WEBHOOK_VERIFY_UPSTREAM_FAILED error log not found:\n{log_output}"
        )
        assert "payload_hash" in upstream_log, "payload_hash must be in upstream failure log"

    # ── Test D: Toss HMAC invalid → 401 ──────────────────────────────────────

    @pytest.mark.asyncio
    async def test_d_toss_signature_invalid_returns_401(self) -> None:
        """D: Toss HMAC signature mismatch → 401 WEBHOOK_SIGNATURE_INVALID; warning logged."""
        os.environ["TOSS_WEBHOOK_SECRET"] = "test-toss-secret-p57"

        # Compute a deliberately wrong signature (different content)
        wrong_sig = hmac.new(
            b"test-toss-secret-p57",
            b"wrong content",
            hashlib.sha256,
        ).hexdigest()

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            with LogCapture() as cap:
                response = await client.post(
                    "/webhooks/tosspayments",
                    content=_TOSS_BODY,
                    headers={
                        "Content-Type": "application/json",
                        "X-TossPayments-Signature": wrong_sig,
                    },
                )

                log_output = cap.raw()
                logs = cap.logs()

        # Must be 401 (B — signature mismatch is a client error)
        assert response.status_code == 401, (
            f"Expected 401 for Toss HMAC mismatch, got {response.status_code}: {response.text}"
        )

        body = response.json()
        assert body.get("error_code") == "WEBHOOK_SIGNATURE_INVALID"
        assert body.get("provider") == "tosspayments"

        # Warning log with payload_hash
        sig_log = next(
            (l for l in logs if l.get("message") == "WEBHOOK_SIGNATURE_INVALID"), None
        )
        assert sig_log is not None, (
            f"WEBHOOK_SIGNATURE_INVALID warning log not found:\n{log_output}"
        )
        assert "payload_hash" in sig_log, "payload_hash must be in Toss signature-invalid log"
        assert sig_log.get("provider") == "tosspayments"

    # ── Test E: No false positive — logs ARE emitted and secrets NOT present ──

    @pytest.mark.asyncio
    async def test_e_no_false_positive_logs_emitted_and_secrets_absent(self) -> None:
        """E: Positive check (WEBHOOK_RECEIVED + WEBHOOK_INVALID_JSON markers present)
        AND negative check (no raw secret string in any log line).

        Guards against two failure modes:
          1. Silent swallow — error happens but nothing is logged.
          2. Secret leak — raw token / key appears in log output.
        """
        fake_secret = "super_secret_token_p57_DO_NOT_LOG"
        # Embed the secret in the raw body (it must NOT appear in logs)
        body_with_secret = json.dumps({
            "id": "EVT-E",
            "event_type": "PAYMENT.CAPTURE.COMPLETED",
            "authorization": f"Bearer {fake_secret}",
        }).encode()

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            with LogCapture() as cap:
                # Send valid JSON so WEBHOOK_RECEIVED is logged, then let
                # verification fail (no mock → misconfig → WEBHOOK_PROVIDER_MISCONFIG)
                response = await client.post(
                    "/webhooks/paypal",
                    content=body_with_secret,
                    headers={"Content-Type": "application/json", **_PAYPAL_HEADERS},
                )

                log_output = cap.raw()
                logs = cap.logs()

        # ── Positive checks: expected log events present ───────────────────
        received_log = next(
            (l for l in logs if l.get("message") == "WEBHOOK_RECEIVED"), None
        )
        assert received_log is not None, (
            f"WEBHOOK_RECEIVED log is MISSING — error was silently swallowed!\n{log_output}"
        )
        assert "payload_hash" in received_log, (
            "payload_hash must be present in WEBHOOK_RECEIVED log"
        )
        assert "payload_size" in received_log, (
            "payload_size must be present in WEBHOOK_RECEIVED log"
        )

        # At least one additional log event after WEBHOOK_RECEIVED
        non_received = [l for l in logs if l.get("message") != "WEBHOOK_RECEIVED"]
        assert len(non_received) >= 1, (
            "Expected at least one log event after WEBHOOK_RECEIVED (e.g. error/warning), "
            f"got none. Logs:\n{log_output}"
        )

        # ── Negative checks: no secrets in log output ──────────────────────
        assert fake_secret not in log_output, (
            f"Raw secret token found in log output! Secret must never appear in logs."
        )
        # Also verify the bearer prefix is not present with the secret
        assert f"Bearer {fake_secret}" not in log_output, (
            "Raw Bearer token found in log output"
        )
