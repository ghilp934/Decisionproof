"""Phase 6.3: Webhook idempotency gate tests.

P6.3 Goal: Prove that the atomic INSERT ON CONFLICT dedup gate prevents duplicate
business processing even when identical webhooks arrive concurrently.

Coverage (no real DB/network calls):
  T1) dedup conflict → 200 already_processed; _process_paypal_event NOT called
  T2) dedup success  → _process_paypal_event called exactly once; status=processed
  T3) WEBHOOK_RECEIVED log has payload_hash+size; raw body content never in any log record
  T4) 5 concurrent identical events → _process_paypal_event called exactly once

Design:
  - All tests mock try_acquire_dedup, get_paypal_client, get_db
  - No real PostgreSQL, no real PayPal API calls
  - T4 uses a threading.Lock-based fake dedup store to prove atomicity
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
from unittest.mock import ANY, AsyncMock, MagicMock, patch

import httpx
import pytest
from fastapi.testclient import TestClient

from dpp_api.main import app


# ===========================================================================
# Shared helpers
# ===========================================================================

_PAYPAL_EVENT_ID = "WH-EVT-DEDUP-P63-TEST-001"

_PAYPAL_REQUIRED_HEADERS: dict[str, str] = {
    "X-PAYPAL-TRANSMISSION-ID": "txid-p63-test-001",
    "X-PAYPAL-TRANSMISSION-TIME": "2026-02-21T00:00:00Z",
    "X-PAYPAL-CERT-URL": "https://api.paypal.com/v1/notifications/certs/CERT-001",
    "X-PAYPAL-AUTH-ALGO": "SHA256withRSA",
    "X-PAYPAL-TRANSMISSION-SIG": "dummysig==",
    "Content-Type": "application/json",
}


def _paypal_body(event_id: str = _PAYPAL_EVENT_ID) -> bytes:
    """Minimal valid PayPal webhook payload."""
    return json.dumps({
        "id": event_id,
        "event_type": "PAYMENT.CAPTURE.COMPLETED",
        "resource": {"id": "CAPTURE-001"},
    }).encode()


def _make_mock_db() -> MagicMock:
    """Return a mock SQLAlchemy Session sufficient for webhook handlers.

    Provides:
      - add/commit/rollback/close → no-ops
      - query(...).filter_by(...).first() → mock BillingEvent with received_at=None
    """
    db = MagicMock()
    mock_event = MagicMock()
    mock_event.received_at = None
    db.query.return_value.filter_by.return_value.first.return_value = mock_event
    return db


def _mock_paypal_client() -> AsyncMock:
    """Mock PayPal client whose verify_webhook_signature always returns SUCCESS."""
    mock_client = AsyncMock()
    mock_client.verify_webhook_signature = AsyncMock(
        return_value={"verification_status": "SUCCESS"}
    )
    return mock_client


# ===========================================================================
# T1: dedup conflict → 200 already_processed, no business processing
# ===========================================================================


@pytest.mark.asyncio
async def test_t1_dedup_conflict_returns_already_processed_no_business_call():
    """T1: try_acquire_dedup returns False (conflict) → handler returns 200
    'already_processed' immediately; _process_paypal_event is NOT called.

    This proves: duplicate webhook → zero side effects, safe ACK.
    """
    mock_db = _make_mock_db()
    mock_client = _mock_paypal_client()

    with (
        patch("dpp_api.routers.webhooks.get_paypal_client", return_value=mock_client),
        patch("dpp_api.routers.webhooks.get_db", return_value=iter([mock_db])),
        patch("dpp_api.routers.webhooks.try_acquire_dedup", return_value=False) as mock_dedup,
        patch("dpp_api.routers.webhooks._process_paypal_event", new_callable=AsyncMock) as mock_process,
    ):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/webhooks/paypal",
                content=_paypal_body(),
                headers=_PAYPAL_REQUIRED_HEADERS,
            )

    # Response: 200 with already_processed
    assert resp.status_code == 200, (
        f"Expected 200, got {resp.status_code}: {resp.text}"
    )
    body = resp.json()
    assert body.get("status") == "already_processed", (
        f"Expected status='already_processed', got: {body}"
    )

    # Core: business processing was NOT triggered
    mock_process.assert_not_called()

    # Dedup gate WAS called with correct provider and key prefix
    mock_dedup.assert_called_once()
    call_args = mock_dedup.call_args
    assert call_args.args[1] == "paypal", (
        f"Expected provider='paypal', got {call_args.args[1]!r}"
    )
    assert call_args.args[2].startswith("ev_"), (
        f"dedup_key should start with 'ev_' for PayPal, got {call_args.args[2]!r}"
    )


# ===========================================================================
# T2: dedup success → business processing called exactly once
# ===========================================================================


@pytest.mark.asyncio
async def test_t2_dedup_success_calls_business_processing_exactly_once():
    """T2: try_acquire_dedup returns True (first handler) → _process_paypal_event
    called exactly once; response status is 'processed'.

    This proves: first delivery → normal processing path.
    """
    mock_db = _make_mock_db()
    mock_client = _mock_paypal_client()

    with (
        patch("dpp_api.routers.webhooks.get_paypal_client", return_value=mock_client),
        patch("dpp_api.routers.webhooks.get_db", return_value=iter([mock_db])),
        patch("dpp_api.routers.webhooks.try_acquire_dedup", return_value=True),
        patch("dpp_api.routers.webhooks.mark_dedup_done"),
        patch("dpp_api.routers.webhooks._process_paypal_event", new_callable=AsyncMock) as mock_process,
    ):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/webhooks/paypal",
                content=_paypal_body(),
                headers=_PAYPAL_REQUIRED_HEADERS,
            )

    # Response: 200 with processed
    assert resp.status_code == 200, (
        f"Expected 200, got {resp.status_code}: {resp.text}"
    )
    body = resp.json()
    assert body.get("status") == "processed", (
        f"Expected status='processed', got: {body}"
    )

    # Core: business processing WAS triggered exactly once
    mock_process.assert_called_once()


# ===========================================================================
# T3: logs contain only payload_hash/size, not raw body
# ===========================================================================


def test_t3_webhook_logs_only_hash_and_size_not_raw_body():
    """T3: WEBHOOK_RECEIVED log has payload_hash and payload_size extras.
    Raw webhook body content (sentinel) does NOT appear in any log record.

    This proves: P6.3 log hygiene — raw payload is never logged; only
    the SHA-256 hash and size are emitted for auditability.
    """
    # Sentinel is embedded in the payload but must NOT appear in any log output
    SENTINEL = "SENTINEL_RAW_BODY_MUST_NOT_APPEAR_IN_LOGS_8675309_P63"
    raw_body = json.dumps({
        "id": _PAYPAL_EVENT_ID,
        "event_type": "PAYMENT.CAPTURE.COMPLETED",
        "resource": {"id": "CAPTURE-001", "_test_sentinel": SENTINEL},
    }).encode()

    # Capture all log records from the dpp_api package hierarchy
    captured_records: list[logging.LogRecord] = []

    class _CapturingHandler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            captured_records.append(record)

    capture_handler = _CapturingHandler()
    capture_handler.setLevel(logging.DEBUG)

    root_dpp_logger = logging.getLogger("dpp_api")
    original_level = root_dpp_logger.level
    root_dpp_logger.setLevel(logging.DEBUG)
    root_dpp_logger.addHandler(capture_handler)

    mock_db = _make_mock_db()
    mock_client = _mock_paypal_client()

    try:
        with (
            patch("dpp_api.routers.webhooks.get_paypal_client", return_value=mock_client),
            patch("dpp_api.routers.webhooks.get_db", return_value=iter([mock_db])),
            patch("dpp_api.routers.webhooks.try_acquire_dedup", return_value=True),
            patch("dpp_api.routers.webhooks.mark_dedup_done"),
            patch("dpp_api.routers.webhooks._process_paypal_event", new_callable=AsyncMock),
        ):
            client = TestClient(app)
            resp = client.post(
                "/webhooks/paypal",
                content=raw_body,
                headers=_PAYPAL_REQUIRED_HEADERS,
            )
    finally:
        root_dpp_logger.removeHandler(capture_handler)
        root_dpp_logger.setLevel(original_level)

    assert resp.status_code == 200, f"Request failed: {resp.text}"

    # (a) WEBHOOK_RECEIVED record must have payload_hash and payload_size extras
    received_records = [
        r for r in captured_records
        if r.getMessage() == "WEBHOOK_RECEIVED"
    ]
    assert received_records, (
        "WEBHOOK_RECEIVED log record not emitted by PayPal webhook handler. "
        "Handler must log payload_hash and payload_size at INFO level."
    )
    rec = received_records[0]
    assert getattr(rec, "payload_hash", None) is not None, (
        "WEBHOOK_RECEIVED log record missing 'payload_hash' extra field. "
        "Add extra={'payload_hash': ..., 'payload_size': ...} to the log call."
    )
    assert getattr(rec, "payload_size", None) is not None, (
        "WEBHOOK_RECEIVED log record missing 'payload_size' extra field."
    )

    # (b) Sentinel raw body content must NOT appear in any log record
    _SKIP_ATTRS = frozenset({
        "msg", "args", "exc_text", "stack_info", "exc_info",
        # Internal Python logging attrs that don't carry payload
        "levelname", "levelno", "pathname", "filename", "module",
        "lineno", "funcName", "created", "msecs", "relativeCreated",
        "thread", "threadName", "processName", "process", "name",
        "taskName",
    })

    for log_rec in captured_records:
        # Check formatted message
        msg = log_rec.getMessage()
        assert SENTINEL not in msg, (
            f"Raw body sentinel found in log message!\n"
            f"  Logger: {log_rec.name}  Level: {log_rec.levelname}\n"
            f"  Message: {msg!r}"
        )
        # Check extra attributes attached to the record
        for attr_name, attr_val in vars(log_rec).items():
            if attr_name in _SKIP_ATTRS:
                continue
            if isinstance(attr_val, str) and SENTINEL in attr_val:
                raise AssertionError(
                    f"Raw body sentinel found in log record attribute '{attr_name}'!\n"
                    f"  Logger: {log_rec.name}  Level: {log_rec.levelname}\n"
                    f"  Value: {attr_val!r}"
                )
            if isinstance(attr_val, (dict, list)):
                serialized = json.dumps(attr_val, default=str)
                assert SENTINEL not in serialized, (
                    f"Raw body sentinel found in log record attribute '{attr_name}' (dict/list)!\n"
                    f"  Logger: {log_rec.name}  Level: {log_rec.levelname}"
                )


# ===========================================================================
# T4: 5 concurrent identical events → exactly 1 business processing call
# ===========================================================================


@pytest.mark.asyncio
async def test_t4_five_concurrent_identical_events_process_exactly_once():
    """T4: 5 simultaneous identical PayPal webhook events → _process_paypal_event
    called exactly once; all 5 requests return 200 with a valid status.

    This proves the atomic dedup gate works under concurrent load:
    - 1 request gets try_acquire_dedup=True  → processes → 'processed'
    - 4 requests get try_acquire_dedup=False → skip → 'already_processed'

    Concurrency model: asyncio.gather simulates concurrent in-flight requests.
    Atomicity model: threading.Lock in fake_try_acquire_dedup mirrors the
    PostgreSQL UNIQUE constraint atomicity guarantee.
    """
    # Thread-safe fake dedup store (mirrors PostgreSQL UNIQUE constraint)
    _dedup_store: set[str] = set()
    _dedup_lock = threading.Lock()

    # Thread-safe process call counter
    _process_call_count = 0
    _process_lock = threading.Lock()

    def fake_try_acquire_dedup(
        db: object, provider: str, dedup_key: str, request_hash: str | None = None
    ) -> bool:
        """Atomic gate: only first caller per dedup_key returns True."""
        with _dedup_lock:
            if dedup_key in _dedup_store:
                return False  # conflict → duplicate
            _dedup_store.add(dedup_key)
            return True     # first → proceed

    async def fake_process_paypal_event(
        db: object, billing_event: object, webhook_body: dict
    ) -> None:
        nonlocal _process_call_count
        with _process_lock:
            _process_call_count += 1

    raw_body = _paypal_body()
    mock_client = _mock_paypal_client()

    with (
        patch("dpp_api.routers.webhooks.get_paypal_client", return_value=mock_client),
        patch(
            "dpp_api.routers.webhooks.get_db",
            side_effect=lambda: iter([_make_mock_db()]),
        ),
        patch(
            "dpp_api.routers.webhooks.try_acquire_dedup",
            side_effect=fake_try_acquire_dedup,
        ),
        patch("dpp_api.routers.webhooks.mark_dedup_done"),
        patch(
            "dpp_api.routers.webhooks._process_paypal_event",
            new_callable=AsyncMock,
            side_effect=fake_process_paypal_event,
        ),
    ):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            # Fire 5 identical webhooks simultaneously
            tasks = [
                client.post(
                    "/webhooks/paypal",
                    content=raw_body,
                    headers=_PAYPAL_REQUIRED_HEADERS,
                )
                for _ in range(5)
            ]
            responses = await asyncio.gather(*tasks)

    # All 5 requests must return HTTP 200
    for i, resp in enumerate(responses):
        assert resp.status_code == 200, (
            f"Request #{i}: expected 200, got {resp.status_code}: {resp.text}"
        )

    statuses = [r.json().get("status") for r in responses]

    # Exactly 1 must be "processed" (first handler)
    processed_count = statuses.count("processed")
    already_processed_count = statuses.count("already_processed")

    assert processed_count == 1, (
        f"Expected exactly 1 'processed', got {processed_count}.\n"
        f"All response statuses: {statuses}\n"
        f"Dedup store: {_dedup_store}"
    )
    assert already_processed_count == 4, (
        f"Expected 4 'already_processed', got {already_processed_count}.\n"
        f"All response statuses: {statuses}"
    )

    # Core: business processing function called exactly once across 5 concurrent requests
    assert _process_call_count == 1, (
        f"Expected _process_paypal_event called exactly once under 5 concurrent "
        f"identical events, but got {_process_call_count} calls.\n"
        f"All response statuses: {statuses}"
    )
