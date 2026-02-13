"""Chaos Testing Helpers (MS-6).

Utilities for simulating production failures:
- Worker process kills
- Redis disconnections
- DB transaction failures
"""

import time
from typing import Any, Callable

import redis
from sqlalchemy.orm import Session

from dpp_api.budget.redis_scripts import BudgetScripts
from dpp_api.db.models import Run


def simulate_redis_disconnect(redis_client: redis.Redis) -> Callable[[], None]:
    """Simulate Redis disconnection by closing the connection pool.

    Returns:
        Callable to restore the connection
    """
    # Close all connections in the pool
    redis_client.connection_pool.disconnect()

    def restore() -> None:
        """Restore Redis connection."""
        # Connection will be re-established on next operation
        redis_client.ping()

    return restore


def verify_money_conservation(
    budget_scripts: BudgetScripts,
    db_session: Session,
    tenant_id: str,
    initial_balance: int,
    expected_spent: int,
) -> tuple[bool, str]:
    """Verify that money is conserved (MS-6: zero tolerance for leaks).

    Args:
        budget_scripts: BudgetScripts instance
        db_session: DB session
        tenant_id: Tenant ID to check
        initial_balance: Initial balance set for tenant
        expected_spent: Expected amount spent (settled runs)

    Returns:
        Tuple of (is_valid, error_message)
        - (True, "") if money is conserved
        - (False, "explanation") if money leaked
    """
    from sqlalchemy import func, select

    # Get current balance
    current_balance = budget_scripts.get_balance(tenant_id)

    # Get all reservations for this tenant's runs
    # (Scan all reserve keys and filter by tenant_id)
    reserved_total = 0
    cursor = 0
    while True:
        cursor, keys = budget_scripts.redis.scan(cursor, match="reserve:*", count=1000)
        for key in keys:
            if isinstance(key, bytes):
                key_str = key.decode("utf-8")
            else:
                key_str = key

            run_id = key_str.replace("reserve:", "")
            reservation = budget_scripts.get_reservation(run_id)
            if reservation and reservation["tenant_id"] == tenant_id:
                reserved_total += reservation["reserved_usd_micros"]

        if cursor == 0:
            break

    # Get settled amount from DB for this tenant
    stmt = select(func.sum(Run.actual_cost_usd_micros)).where(
        Run.money_state == "SETTLED",
        Run.tenant_id == tenant_id,
    )
    settled_total = int(db_session.execute(stmt).scalar() or 0)

    # MS-6: Money conservation law
    # initial_balance = current_balance + reserved + settled
    expected_current = initial_balance - reserved_total - settled_total
    discrepancy = current_balance - expected_current

    if discrepancy != 0:
        return (
            False,
            f"Money leaked! Discrepancy: {discrepancy} micros. "
            f"Initial: {initial_balance}, Current: {current_balance}, "
            f"Reserved: {reserved_total}, Settled: {settled_total}, "
            f"Expected current: {expected_current}",
        )

    # Also verify expected_spent matches settled_total
    if expected_spent != settled_total:
        return (
            False,
            f"Settled amount mismatch! Expected: {expected_spent}, Actual: {settled_total}",
        )

    return (True, "")


def simulate_stuck_run_scenario(
    db_session: Session,
    tenant_id: str,
    run_id: str,
    reserved_amount: int,
) -> None:
    """Simulate a run stuck in PROCESSING state (worker killed).

    Creates a run that looks like it was processing but worker died.
    This should be recovered by Reaper's Reconcile Loop.

    Args:
        db_session: DB session
        tenant_id: Tenant ID
        run_id: Run ID
        reserved_amount: Amount reserved for this run
    """
    from datetime import datetime, timedelta, timezone

    # Create a run stuck in PROCESSING with RESERVED money_state
    # Simulate that it's been stuck for >5 minutes (Reconcile Loop threshold)
    stuck_run = Run(
        run_id=run_id,
        tenant_id=tenant_id,
        pack_type="decision",
        profile_version="v0.4.2.2",
        status="PROCESSING",
        money_state="RESERVED",
        payload_hash=f"chaos_test_{run_id}",
        reservation_max_cost_usd_micros=reserved_amount,
        actual_cost_usd_micros=None,
        minimum_fee_usd_micros=100_000,  # $0.10
        retention_until=datetime.now(timezone.utc) + timedelta(days=30),
        # Simulate worker started processing but died
        lease_token="dead_worker_token",
        lease_expires_at=datetime.now(timezone.utc) - timedelta(minutes=10),  # Expired
    )

    db_session.add(stuck_run)
    db_session.commit()


def simulate_stuck_claimed_run(
    db_session: Session,
    tenant_id: str,
    run_id: str,
    reserved_amount: int,
    has_s3_result: bool = False,
) -> None:
    """Simulate a run stuck in CLAIMED finalize stage (Reconcile Loop target).

    This is the DEC-4206 scenario: Worker completed and uploaded to S3,
    but died before DB commit. Reconcile Loop should detect and recover.

    Args:
        db_session: DB session
        tenant_id: Tenant ID
        run_id: Run ID
        reserved_amount: Amount reserved
        has_s3_result: If True, simulates S3 upload success (roll-forward)
                       If False, simulates S3 missing (roll-back)
    """
    from datetime import datetime, timedelta, timezone

    # Create run stuck in CLAIMED stage for >5 minutes
    stuck_run = Run(
        run_id=run_id,
        tenant_id=tenant_id,
        pack_type="decision",
        profile_version="v0.4.2.2",
        status="PROCESSING",
        money_state="RESERVED",
        payload_hash=f"chaos_claimed_{run_id}",
        reservation_max_cost_usd_micros=reserved_amount,
        actual_cost_usd_micros=None,
        minimum_fee_usd_micros=100_000,
        retention_until=datetime.now(timezone.utc) + timedelta(days=30),
        # CLAIMED stage - stuck before DB commit
        finalize_stage="CLAIMED",
        finalize_token="dead_finalize_token",
        finalize_claimed_at=datetime.now(timezone.utc) - timedelta(minutes=10),  # Stuck >5min
    )

    # If has_s3_result, set result_* fields (Reconcile Loop will roll-forward)
    if has_s3_result:
        stuck_run.result_bucket = "test-results-bucket"
        stuck_run.result_key = f"results/{run_id}/output.json"
        stuck_run.result_sha256 = "abc123def456"

    db_session.add(stuck_run)
    db_session.commit()


def wait_for_reaper_cycle(seconds: float = 2.0) -> None:
    """Wait for Reaper to complete a cycle.

    Args:
        seconds: Time to wait (default: 2 seconds for test Reaper)
    """
    time.sleep(seconds)
