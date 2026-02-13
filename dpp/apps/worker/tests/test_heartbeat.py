"""Tests for P0-D: Lease Heartbeat + SQS Visibility Heartbeat.

Verifies that:
- Heartbeat extends DB lease_expires_at
- Heartbeat extends SQS visibility timeout
- Heartbeat stops on completion
- Heartbeat uses optimistic locking
"""

import time
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy.orm import Session

from dpp_api.db.models import Run
from dpp_api.db.repo_runs import RunRepository
from dpp_worker.heartbeat import HeartbeatThread


def test_heartbeat_extends_db_lease(db_session: Session):
    """Test that heartbeat extends DB lease_expires_at."""
    # Setup: Create a PROCESSING run
    run_id = str(uuid.uuid4())
    tenant_id = "test-tenant-heartbeat"
    lease_token = str(uuid.uuid4())
    initial_lease_expires_at = datetime.now(timezone.utc) + timedelta(seconds=120)

    run = Run(
        run_id=run_id,
        tenant_id=tenant_id,
        pack_type="decision",
        profile_version="v0.4.2.2",
        status="PROCESSING",
        money_state="RESERVED",
        payload_hash=f"sha256:{run_id}",
        reservation_max_cost_usd_micros=1_000_000,
        minimum_fee_usd_micros=100_000,
        lease_token=lease_token,
        lease_expires_at=initial_lease_expires_at,
        version=1,
        retention_until=datetime.now(timezone.utc) + timedelta(days=30),
    )
    db_session.add(run)
    db_session.commit()

    # Mock SQS client
    mock_sqs = MagicMock()

    # Create heartbeat thread (but don't start automatic loop)
    heartbeat = HeartbeatThread(
        run_id=run_id,
        tenant_id=tenant_id,
        lease_token=lease_token,
        current_version=1,
        db_session=db_session,
        sqs_client=mock_sqs,
        queue_url="http://test-queue",
        receipt_handle="test-receipt-handle",
        heartbeat_interval_sec=30,
        lease_extension_sec=120,
    )

    # Manually trigger one heartbeat
    heartbeat._send_heartbeat()

    # Verify: DB lease_expires_at was extended
    db_session.refresh(run)

    # Ensure both datetimes are timezone-aware for comparison
    updated_lease = run.lease_expires_at
    if updated_lease.tzinfo is None:
        updated_lease = updated_lease.replace(tzinfo=timezone.utc)

    assert updated_lease > initial_lease_expires_at
    assert run.version == 2  # Version incremented

    # Verify: SQS change_message_visibility was called
    mock_sqs.change_message_visibility.assert_called_once_with(
        QueueUrl="http://test-queue",
        ReceiptHandle="test-receipt-handle",
        VisibilityTimeout=120,
    )


def test_heartbeat_stops_on_completion(db_session: Session):
    """Test that heartbeat thread stops cleanly."""
    run_id = str(uuid.uuid4())
    tenant_id = "test-tenant-stop"
    lease_token = str(uuid.uuid4())

    run = Run(
        run_id=run_id,
        tenant_id=tenant_id,
        pack_type="decision",
        profile_version="v0.4.2.2",
        status="PROCESSING",
        money_state="RESERVED",
        payload_hash=f"sha256:{run_id}",
        reservation_max_cost_usd_micros=1_000_000,
        minimum_fee_usd_micros=100_000,
        lease_token=lease_token,
        lease_expires_at=datetime.now(timezone.utc) + timedelta(seconds=120),
        version=1,
        retention_until=datetime.now(timezone.utc) + timedelta(days=30),
    )
    db_session.add(run)
    db_session.commit()

    mock_sqs = MagicMock()

    # Create and start heartbeat thread
    heartbeat = HeartbeatThread(
        run_id=run_id,
        tenant_id=tenant_id,
        lease_token=lease_token,
        current_version=1,
        db_session=db_session,
        sqs_client=mock_sqs,
        queue_url="http://test-queue",
        receipt_handle="test-receipt-handle",
        heartbeat_interval_sec=1,  # Fast heartbeat for testing
        lease_extension_sec=120,
    )
    heartbeat.start()

    # Wait a bit for thread to start
    time.sleep(0.5)

    # Verify thread is alive
    assert heartbeat.is_alive()

    # Stop heartbeat
    heartbeat.stop()

    # Verify thread stopped
    assert not heartbeat.is_alive()


def test_heartbeat_fails_on_lease_token_mismatch(db_session: Session):
    """Test that heartbeat fails if lease_token doesn't match."""
    run_id = str(uuid.uuid4())
    tenant_id = "test-tenant-mismatch"
    correct_lease_token = str(uuid.uuid4())
    wrong_lease_token = str(uuid.uuid4())

    run = Run(
        run_id=run_id,
        tenant_id=tenant_id,
        pack_type="decision",
        profile_version="v0.4.2.2",
        status="PROCESSING",
        money_state="RESERVED",
        payload_hash=f"sha256:{run_id}",
        reservation_max_cost_usd_micros=1_000_000,
        minimum_fee_usd_micros=100_000,
        lease_token=correct_lease_token,  # Different from heartbeat's token
        lease_expires_at=datetime.now(timezone.utc) + timedelta(seconds=120),
        version=1,
        retention_until=datetime.now(timezone.utc) + timedelta(days=30),
    )
    db_session.add(run)
    db_session.commit()

    initial_lease_expires_at = run.lease_expires_at
    initial_version = run.version

    mock_sqs = MagicMock()

    # Create heartbeat with WRONG lease_token
    heartbeat = HeartbeatThread(
        run_id=run_id,
        tenant_id=tenant_id,
        lease_token=wrong_lease_token,  # Wrong token!
        current_version=1,
        db_session=db_session,
        sqs_client=mock_sqs,
        queue_url="http://test-queue",
        receipt_handle="test-receipt-handle",
        heartbeat_interval_sec=30,
        lease_extension_sec=120,
    )

    # Manually trigger heartbeat
    heartbeat._send_heartbeat()

    # Verify: DB was NOT updated (lease_token mismatch)
    db_session.refresh(run)
    assert run.lease_expires_at == initial_lease_expires_at  # Not extended
    assert run.version == initial_version  # Not incremented


def test_heartbeat_fails_on_status_change(db_session: Session):
    """Test that heartbeat fails if run status changes to COMPLETED."""
    run_id = str(uuid.uuid4())
    tenant_id = "test-tenant-status"
    lease_token = str(uuid.uuid4())

    run = Run(
        run_id=run_id,
        tenant_id=tenant_id,
        pack_type="decision",
        profile_version="v0.4.2.2",
        status="PROCESSING",
        money_state="RESERVED",
        payload_hash=f"sha256:{run_id}",
        reservation_max_cost_usd_micros=1_000_000,
        minimum_fee_usd_micros=100_000,
        lease_token=lease_token,
        lease_expires_at=datetime.now(timezone.utc) + timedelta(seconds=120),
        version=1,
        retention_until=datetime.now(timezone.utc) + timedelta(days=30),
    )
    db_session.add(run)
    db_session.commit()

    mock_sqs = MagicMock()

    heartbeat = HeartbeatThread(
        run_id=run_id,
        tenant_id=tenant_id,
        lease_token=lease_token,
        current_version=1,
        db_session=db_session,
        sqs_client=mock_sqs,
        queue_url="http://test-queue",
        receipt_handle="test-receipt-handle",
        heartbeat_interval_sec=30,
        lease_extension_sec=120,
    )

    # Change run status to COMPLETED
    run.status = "COMPLETED"
    db_session.commit()

    initial_lease_expires_at = run.lease_expires_at
    initial_version = run.version

    # Manually trigger heartbeat
    heartbeat._send_heartbeat()

    # Verify: DB was NOT updated (status changed)
    db_session.refresh(run)
    assert run.lease_expires_at == initial_lease_expires_at  # Not extended
    assert run.version == initial_version  # Not incremented
