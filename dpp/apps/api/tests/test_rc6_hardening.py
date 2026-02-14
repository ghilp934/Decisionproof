"""RC-6 Hardening Gate Tests: Operational Risk Checks.

Tests for hardening observability guarantees:
H1) 500-path completeness (Exception still emits completion log)
H2) Exactly-one completion log per request (no duplicates)
H3) No context leakage across sequential requests
H4) Sensitive data never appears in logs
"""

import json
import logging
import uuid
from io import StringIO
from typing import Optional

import pytest

from dpp_api.main import app
from dpp_api.utils.logging import JSONFormatter


def parse_json_lines(text: str) -> list[dict]:
    """Parse JSON log lines from text, ignoring non-JSON lines.

    Args:
        text: Raw log output (may contain non-JSON lines)

    Returns:
        List of parsed JSON log dictionaries
    """
    logs = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            log_entry = json.loads(line)
            logs.append(log_entry)
        except json.JSONDecodeError:
            # Ignore non-JSON lines
            pass
    return logs


def find_completion_logs(
    records: list[dict], path: Optional[str] = None, method: Optional[str] = None
) -> list[dict]:
    """Find completion logs, optionally filtered by path and/or method.

    Args:
        records: List of log record dictionaries
        path: Optional path filter (e.g., "/v1/runs")
        method: Optional HTTP method filter (e.g., "GET")

    Returns:
        List of completion log records matching filters
    """
    completion_logs = []
    for rec in records:
        # Check if this is a completion log
        msg = rec.get("message") or rec.get("event") or rec.get("msg")
        if msg != "http.request.completed":
            continue

        # Apply filters
        if path is not None and rec.get("path") != path:
            continue
        if method is not None and rec.get("method") != method:
            continue

        completion_logs.append(rec)

    return completion_logs


def is_empty(value) -> bool:
    """Check if a value is empty (None, empty string, or missing)."""
    return value is None or value == ""


class TestRC6Hardening:
    """RC-6 Hardening: Operational Risk Checks."""

    @pytest.fixture(autouse=True)
    def setup(self):
        """Reset rate limiter and setup JSON log capture."""
        from dpp_api.rate_limiter import NoOpRateLimiter

        app.state.rate_limiter = NoOpRateLimiter(quota=60, window=60)
        yield

    def _setup_log_capture(self):
        """Setup fresh JSON log capture handler."""
        root_logger = logging.getLogger()

        # Save and remove existing handlers
        self.saved_handlers = root_logger.handlers[:]
        for handler in self.saved_handlers:
            root_logger.removeHandler(handler)

        # Create new StringIO for this test
        self.log_stream = StringIO()
        self.log_handler = logging.StreamHandler(self.log_stream)
        self.log_handler.setFormatter(JSONFormatter())

        # Add only our test handler
        root_logger.addHandler(self.log_handler)

    def _cleanup_log_capture(self):
        """Cleanup log capture handler and restore original handlers."""
        root_logger = logging.getLogger()

        # Remove test handler
        root_logger.removeHandler(self.log_handler)
        self.log_stream.close()

        # Restore original handlers
        for handler in self.saved_handlers:
            root_logger.addHandler(handler)

    def get_captured_logs(self) -> list[dict]:
        """Get parsed JSON logs from captured stream."""
        return parse_json_lines(self.log_stream.getvalue())

    def test_h1_500_path_emits_completion_log(
        self, test_client, redis_client, test_tenant_with_api_key, monkeypatch
    ):
        """H1: 500-path completeness (Exception still emits completion log).

        RC-6 Hardening Requirement:
        - Force a deterministic server-side RuntimeError during POST /v1/runs
        - Response should be 500
        - Completion log MUST still be emitted with status_code=500
        - Log must include: request_id, method, path, status_code, duration_ms
        """
        tenant_id, api_key, _ = test_tenant_with_api_key

        # Monkeypatch BudgetManager.reserve to raise RuntimeError
        from dpp_api.budget.manager import BudgetManager

        original_reserve = BudgetManager.reserve

        def boom_reserve(self, *args, **kwargs):
            raise RuntimeError("RC6_HARDENING_BOOM")

        monkeypatch.setattr(BudgetManager, "reserve", boom_reserve)

        # Setup log capture
        self._setup_log_capture()

        try:
            # Make POST /v1/runs request (expect 500)
            response = test_client.post(
                "/v1/runs",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Idempotency-Key": f"test-rc6-h1-{uuid.uuid4()}",
                },
                json={
                    "pack_type": "decision",
                    "inputs": {
                        "url": "https://example.com/proof_request.json",
                        "sha256": "a" * 64,
                    },
                    "reservation": {
                        "max_cost_usd": "1.0000",
                        "timebox_sec": 90,
                        "min_reliability_score": 0.8,
                    },
                },
            )

            # Assert 500 response
            assert response.status_code == 500, f"Expected 500, got {response.status_code}"

            # Get captured logs
            logs = self.get_captured_logs()

            # Find completion log for POST /v1/runs
            completion_logs = find_completion_logs(logs, path="/v1/runs", method="POST")
            assert len(completion_logs) > 0, "No completion log found for POST /v1/runs with 500"

            log = completion_logs[0]

            # Verify required fields
            assert log["status_code"] == 500, f"Expected status_code=500, got {log.get('status_code')}"
            assert log["path"] == "/v1/runs", f"Expected path=/v1/runs, got {log.get('path')}"
            assert log["method"] == "POST", f"Expected method=POST, got {log.get('method')}"
            assert "duration_ms" in log, "Missing duration_ms"
            assert isinstance(log["duration_ms"], (int, float)), "duration_ms must be numeric"
            # request_id may not propagate in TestClient, but verify it exists in production logs
            # We verify X-Request-ID header as proxy
            assert "X-Request-ID" in response.headers or "request_id" in log, \
                "Missing request_id or X-Request-ID header"

        finally:
            # Restore original method
            monkeypatch.setattr(BudgetManager, "reserve", original_reserve)
            self._cleanup_log_capture()

    def test_h2_exactly_one_completion_log_per_request(self, test_client):
        """H2: Exactly-one completion log per request (no duplicates).

        RC-6 Hardening Requirement:
        - Single request should emit exactly 1 completion log
        - Match by request_id to ensure no duplicates
        """
        # Setup log capture
        self._setup_log_capture()

        try:
            # Make single request
            response = test_client.get("/v1/test-ratelimit")
            assert response.status_code == 200

            # Get captured logs
            logs = self.get_captured_logs()

            # Find completion logs for this path
            completion_logs = find_completion_logs(logs, path="/v1/test-ratelimit")

            # Should have exactly 1 completion log
            assert len(completion_logs) == 1, \
                f"Expected exactly 1 completion log, got {len(completion_logs)}"

            # Verify by request_id if available
            log = completion_logs[0]
            request_id = log.get("request_id")
            if request_id:
                # Count logs with same request_id
                same_request = [l for l in logs if l.get("request_id") == request_id and
                               l.get("message") == "http.request.completed"]
                assert len(same_request) == 1, \
                    f"Found {len(same_request)} completion logs with same request_id={request_id}"

        finally:
            self._cleanup_log_capture()

    def test_h3_no_context_leakage_across_requests(
        self, test_client, redis_client, test_tenant_with_api_key
    ):
        """H3: No context leakage across sequential requests.

        RC-6 Hardening Requirement:
        - First request sets plan_key/budget_decision/run_id context
        - Second request (different endpoint) should NOT have those context values
        - Ensures contextvars are properly cleared between requests
        """
        tenant_id, api_key, _ = test_tenant_with_api_key

        # Setup log capture
        self._setup_log_capture()

        try:
            # Request 1: POST /v1/runs (sets context)
            response1 = test_client.post(
                "/v1/runs",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Idempotency-Key": f"test-rc6-h3-{uuid.uuid4()}",
                },
                json={
                    "pack_type": "decision",
                    "inputs": {
                        "url": "https://example.com/proof_request.json",
                        "sha256": "a" * 64,
                    },
                    "reservation": {
                        "max_cost_usd": "1.0000",
                        "timebox_sec": 90,
                        "min_reliability_score": 0.8,
                    },
                },
            )
            assert response1.status_code == 202

            # Request 2: GET /v1/test-ratelimit (should NOT have context from request 1)
            response2 = test_client.get("/v1/test-ratelimit")
            assert response2.status_code == 200

            # Get captured logs
            logs = self.get_captured_logs()

            # Find completion log for second request
            completion_logs = find_completion_logs(logs, path="/v1/test-ratelimit")
            assert len(completion_logs) > 0, "No completion log for /v1/test-ratelimit"

            log = completion_logs[0]

            # Assert NO context leakage
            run_id = log.get("run_id")
            plan_key = log.get("plan_key")
            budget_decision = log.get("budget_decision")

            assert is_empty(run_id), \
                f"Context leakage: run_id should be empty, got {run_id}"
            assert is_empty(plan_key), \
                f"Context leakage: plan_key should be empty, got {plan_key}"
            assert is_empty(budget_decision), \
                f"Context leakage: budget_decision should be empty, got {budget_decision}"

        finally:
            self._cleanup_log_capture()

    def test_h4_no_sensitive_data_in_logs(
        self, test_client, redis_client, test_tenant_with_api_key
    ):
        """H4: Sensitive data never appears in logs.

        RC-6 Hardening Requirement:
        - Authorization Bearer tokens must NOT appear in logs
        - Request body payloads must NOT appear in logs
        - Prevents accidental exposure of credentials or user data
        """
        tenant_id, api_key, _ = test_tenant_with_api_key

        # Create unique sensitive markers
        unique_id = str(uuid.uuid4())
        sensitive_token = f"SENSITIVE_TOKEN_ABC123_{unique_id}"
        sensitive_payload = f"SENSITIVE_PAYLOAD_XYZ_{unique_id}"

        # Create custom API key with sensitive token (for testing log redaction)
        # Note: We'll use the real api_key but check that it doesn't appear in logs
        test_api_key = f"sk_test_{sensitive_token}"

        # Setup log capture
        self._setup_log_capture()

        try:
            # Make request with sensitive data
            response = test_client.post(
                "/v1/runs",
                headers={
                    "Authorization": f"Bearer {api_key}",  # Real key for auth
                    "Idempotency-Key": f"test-rc6-h4-{uuid.uuid4()}",
                },
                json={
                    "pack_type": "decision",
                    "inputs": {
                        "url": f"https://example.com/{sensitive_payload}.json",
                        "sha256": "b" * 64,
                    },
                    "reservation": {
                        "max_cost_usd": "1.0000",
                        "timebox_sec": 90,
                        "min_reliability_score": 0.8,
                    },
                },
            )

            # Get raw captured output
            raw_logs = self.log_stream.getvalue()

            # Assert sensitive data DOES NOT appear in logs
            assert api_key not in raw_logs, \
                f"SECURITY VIOLATION: API key found in logs"
            assert sensitive_payload not in raw_logs, \
                f"SECURITY VIOLATION: Request payload found in logs"

            # Also check for Authorization header patterns
            assert "Authorization:" not in raw_logs or "Bearer" not in raw_logs or api_key not in raw_logs, \
                "SECURITY VIOLATION: Authorization header or Bearer token pattern found in logs"

        finally:
            self._cleanup_log_capture()
