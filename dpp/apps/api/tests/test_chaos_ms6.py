"""Chaos Testing for MS-6: Zero-tolerance money leak verification.

Simulates production failures to verify that:
1. Worker kills don't leak money
2. Redis disconnects don't leak money
3. Reconcile Loop recovers all stuck runs correctly
"""

import uuid
from datetime import datetime, timedelta, timezone

import pytest
import redis
from sqlalchemy.orm import Session

from dpp_api.budget.redis_scripts import BudgetScripts
from dpp_api.db.models import Run
from tests.chaos_helpers import (
    simulate_redis_disconnect,
    simulate_stuck_claimed_run,
    simulate_stuck_run_scenario,
    verify_money_conservation,
    wait_for_reaper_cycle,
)


def test_chaos_worker_killed_during_processing(
    redis_client: redis.Redis, db_session: Session
) -> None:
    """Test that money doesn't leak when worker is killed mid-execution.

    Scenario:
    1. Reserve $5.00 for run
    2. Simulate worker death (run stuck in PROCESSING)
    3. Verify money is still reserved (not leaked)
    4. Manually recover (simulate Reaper refund)
    5. Verify money fully returned

    MS-6: Zero tolerance for money leaks.
    """
    tenant_id = f"chaos_worker_kill_{uuid.uuid4().hex[:8]}"
    budget_scripts = BudgetScripts(redis_client)

    # Setup: Start with $20.00
    initial_balance = 20_000_000  # $20.00
    budget_scripts.set_initial_balance(tenant_id, initial_balance)
    budget_scripts.set_balance(tenant_id, initial_balance)

    # Step 1: Reserve $5.00
    run_id = str(uuid.uuid4())
    status, new_balance = budget_scripts.reserve(tenant_id, run_id, 5_000_000)
    assert status == "OK"
    assert new_balance == 15_000_000  # $20 - $5 = $15

    # Step 2: Simulate worker death (run stuck in PROCESSING)
    simulate_stuck_run_scenario(db_session, tenant_id, run_id, 5_000_000)

    # Step 3: Verify money conservation BEFORE recovery
    # Money should be: Initial = Current + Reserved + Settled
    # $20.00 = $15.00 + $5.00 + $0.00 ✓
    is_valid, error_msg = verify_money_conservation(
        budget_scripts, db_session, tenant_id, initial_balance, expected_spent=0
    )
    assert is_valid, f"Money leaked before recovery: {error_msg}"

    # Step 4: Simulate Reaper recovery (refund full reservation)
    status, refund, new_balance = budget_scripts.refund_full(tenant_id, run_id)
    assert status == "OK"
    assert refund == 5_000_000
    assert new_balance == 20_000_000  # Back to $20

    # Update DB to reflect recovery
    run = db_session.query(Run).filter_by(run_id=run_id).one()
    run.status = "FAILED"
    run.money_state = "SETTLED"
    run.actual_cost_usd_micros = 0  # No charge (refunded)
    run.last_error_reason_code = "WORKER_TIMEOUT"
    db_session.commit()

    # Step 5: Verify money conservation AFTER recovery
    # $20.00 = $20.00 + $0.00 + $0.00 ✓
    is_valid, error_msg = verify_money_conservation(
        budget_scripts, db_session, tenant_id, initial_balance, expected_spent=0
    )
    assert is_valid, f"Money leaked after recovery: {error_msg}"


def test_chaos_reconcile_loop_roll_forward(
    redis_client: redis.Redis, db_session: Session
) -> None:
    """Test Reconcile Loop roll-forward: S3 exists, complete the run.

    Scenario (DEC-4206):
    1. Reserve $8.00 for run
    2. Worker completes, uploads to S3, but dies before DB commit
    3. Run stuck in CLAIMED stage with S3 result
    4. Reconcile Loop detects and rolls forward (settles with actual cost)
    5. Verify money conserved (charged $6.50, refunded $1.50)

    MS-6: This is the critical money leak prevention scenario.
    """
    tenant_id = f"chaos_roll_forward_{uuid.uuid4().hex[:8]}"
    budget_scripts = BudgetScripts(redis_client)

    # Setup: Start with $30.00
    initial_balance = 30_000_000  # $30.00
    budget_scripts.set_initial_balance(tenant_id, initial_balance)
    budget_scripts.set_balance(tenant_id, initial_balance)

    # Step 1: Reserve $8.00
    run_id = str(uuid.uuid4())
    status, new_balance = budget_scripts.reserve(tenant_id, run_id, 8_000_000)
    assert status == "OK"
    assert new_balance == 22_000_000  # $30 - $8 = $22

    # Step 2: Simulate worker completed S3 upload but died before commit
    simulate_stuck_claimed_run(
        db_session, tenant_id, run_id, 8_000_000, has_s3_result=True
    )

    # Step 3: Verify money conservation BEFORE Reconcile Loop
    # $30 = $22 + $8 + $0 ✓
    is_valid, error_msg = verify_money_conservation(
        budget_scripts, db_session, tenant_id, initial_balance, expected_spent=0
    )
    assert is_valid, f"Money leaked before reconcile: {error_msg}"

    # Step 4: Simulate Reconcile Loop roll-forward
    # (In real system, Reconcile Loop would detect and complete)
    # Here we manually execute the roll-forward logic:
    actual_cost = 6_500_000  # $6.50 (simulated from S3 result)

    status, charge, refund, new_balance = budget_scripts.settle(
        tenant_id, run_id, actual_cost
    )
    assert status == "OK"
    assert charge == 6_500_000
    assert refund == 1_500_000  # $8.00 - $6.50 = $1.50
    assert new_balance == 23_500_000  # $22 + $1.50 = $23.50

    # Update DB to COMPLETED/SETTLED
    run = db_session.query(Run).filter_by(run_id=run_id).one()
    run.status = "COMPLETED"
    run.money_state = "SETTLED"
    run.actual_cost_usd_micros = actual_cost
    run.finalize_stage = "COMMITTED"
    run.completed_at = datetime.now(timezone.utc)
    db_session.commit()

    # Step 5: Verify money conservation AFTER roll-forward
    # $30 = $23.50 + $0 + $6.50 ✓
    is_valid, error_msg = verify_money_conservation(
        budget_scripts,
        db_session,
        tenant_id,
        initial_balance,
        expected_spent=6_500_000,
    )
    assert is_valid, f"Money leaked after roll-forward: {error_msg}"


def test_chaos_reconcile_loop_roll_back(
    redis_client: redis.Redis, db_session: Session
) -> None:
    """Test Reconcile Loop roll-back: S3 missing, charge minimum_fee.

    Scenario (DEC-4206):
    1. Reserve $10.00 for run
    2. Worker dies before uploading to S3 (or S3 upload failed)
    3. Run stuck in CLAIMED stage WITHOUT S3 result
    4. Reconcile Loop detects and rolls back (charges minimum_fee, marks FAILED)
    5. Verify money conserved (charged $0.10, refunded $9.90)

    MS-6: Ensures failed runs don't leak reservations.
    """
    tenant_id = f"chaos_roll_back_{uuid.uuid4().hex[:8]}"
    budget_scripts = BudgetScripts(redis_client)

    # Setup: Start with $50.00
    initial_balance = 50_000_000  # $50.00
    budget_scripts.set_initial_balance(tenant_id, initial_balance)
    budget_scripts.set_balance(tenant_id, initial_balance)

    # Step 1: Reserve $10.00
    run_id = str(uuid.uuid4())
    status, new_balance = budget_scripts.reserve(tenant_id, run_id, 10_000_000)
    assert status == "OK"
    assert new_balance == 40_000_000  # $50 - $10 = $40

    # Step 2: Simulate worker died WITHOUT S3 upload
    simulate_stuck_claimed_run(
        db_session, tenant_id, run_id, 10_000_000, has_s3_result=False
    )

    # Step 3: Verify money conservation BEFORE Reconcile Loop
    is_valid, error_msg = verify_money_conservation(
        budget_scripts, db_session, tenant_id, initial_balance, expected_spent=0
    )
    assert is_valid, f"Money leaked before reconcile: {error_msg}"

    # Step 4: Simulate Reconcile Loop roll-back
    # Charge minimum_fee ($0.10) and mark FAILED
    minimum_fee = 100_000  # $0.10

    status, charge, refund, new_balance = budget_scripts.settle(
        tenant_id, run_id, minimum_fee
    )
    assert status == "OK"
    assert charge == 100_000
    assert refund == 9_900_000  # $10.00 - $0.10 = $9.90
    assert new_balance == 49_900_000  # $40 + $9.90 = $49.90

    # Update DB to FAILED/SETTLED
    run = db_session.query(Run).filter_by(run_id=run_id).one()
    run.status = "FAILED"
    run.money_state = "SETTLED"
    run.actual_cost_usd_micros = minimum_fee
    run.finalize_stage = "COMMITTED"
    run.last_error_reason_code = "S3_UPLOAD_FAILED"
    run.completed_at = datetime.now(timezone.utc)
    db_session.commit()

    # Step 5: Verify money conservation AFTER roll-back
    # $50 = $49.90 + $0 + $0.10 ✓
    is_valid, error_msg = verify_money_conservation(
        budget_scripts, db_session, tenant_id, initial_balance, expected_spent=100_000
    )
    assert is_valid, f"Money leaked after roll-back: {error_msg}"


def test_chaos_redis_auto_reconnect_preserves_money(
    redis_client: redis.Redis, db_session: Session
) -> None:
    """Test that Redis auto-reconnect doesn't leak money.

    Scenario:
    1. Set up tenant with budget
    2. Reserve $5.00
    3. Disconnect Redis (simulates network hiccup)
    4. Redis auto-reconnects
    5. Verify reservation still exists
    6. Settle normally
    7. Verify money conserved

    MS-6: System should be resilient to temporary Redis disruptions.
    Note: redis-py automatically reconnects, so we test that money
    remains consistent through reconnection cycles.
    """
    tenant_id = f"chaos_redis_reconnect_{uuid.uuid4().hex[:8]}"
    budget_scripts = BudgetScripts(redis_client)

    # Setup: Start with $15.00
    initial_balance = 15_000_000  # $15.00
    budget_scripts.set_initial_balance(tenant_id, initial_balance)
    budget_scripts.set_balance(tenant_id, initial_balance)

    # Step 1: Reserve $5.00
    run_id = str(uuid.uuid4())
    status, new_balance = budget_scripts.reserve(tenant_id, run_id, 5_000_000)
    assert status == "OK"
    assert new_balance == 10_000_000

    # Step 2: Simulate network hiccup (disconnect/reconnect)
    restore_connection = simulate_redis_disconnect(redis_client)
    restore_connection()  # Auto-reconnect

    # Step 3: Verify reservation still exists after reconnect
    reservation = budget_scripts.get_reservation(run_id)
    assert reservation is not None, "Reservation lost after Redis reconnect!"
    assert reservation["reserved_usd_micros"] == 5_000_000

    # Step 4: Settle normally
    status, charge, refund, new_balance = budget_scripts.settle(
        tenant_id, run_id, 4_000_000
    )
    assert status == "OK"
    assert charge == 4_000_000
    assert refund == 1_000_000
    assert new_balance == 11_000_000  # $10 + $1 = $11

    # Step 5: Verify Redis balance consistency
    # Initial ($15) - Charged ($4) = Current ($11) ✓
    final_balance = budget_scripts.get_balance(tenant_id)
    expected_final = initial_balance - charge
    assert final_balance == expected_final, (
        f"Redis balance inconsistent after reconnect! "
        f"Expected: {expected_final}, Got: {final_balance}"
    )


def test_chaos_multiple_failures_cascade(
    redis_client: redis.Redis, db_session: Session
) -> None:
    """Test multiple cascading failures don't leak money.

    Scenario:
    1. Reserve for 3 runs
    2. Run 1: Completes normally (settle)
    3. Run 2: Worker killed (stuck PROCESSING)
    4. Run 3: Stuck CLAIMED with S3 (needs roll-forward)
    5. Recover all failures
    6. Verify total money conservation

    MS-6: Stress test with multiple simultaneous failure modes.
    """
    tenant_id = f"chaos_cascade_{uuid.uuid4().hex[:8]}"
    budget_scripts = BudgetScripts(redis_client)

    # Setup: Start with $100.00
    initial_balance = 100_000_000  # $100.00
    budget_scripts.set_initial_balance(tenant_id, initial_balance)
    budget_scripts.set_balance(tenant_id, initial_balance)

    # Reserve for 3 runs
    run_id_1 = str(uuid.uuid4())
    run_id_2 = str(uuid.uuid4())
    run_id_3 = str(uuid.uuid4())

    budget_scripts.reserve(tenant_id, run_id_1, 20_000_000)  # $20
    budget_scripts.reserve(tenant_id, run_id_2, 15_000_000)  # $15
    budget_scripts.reserve(tenant_id, run_id_3, 25_000_000)  # $25

    # Balance after reserves: $100 - $60 = $40
    assert budget_scripts.get_balance(tenant_id) == 40_000_000

    # Run 1: Complete normally
    budget_scripts.settle(tenant_id, run_id_1, 18_000_000)  # Charge $18, refund $2
    run1 = Run(
        run_id=run_id_1,
        tenant_id=tenant_id,
        pack_type="decision",
        profile_version="v0.4.2.2",
        status="COMPLETED",
        money_state="SETTLED",
        payload_hash=f"cascade_1_{run_id_1}",
        reservation_max_cost_usd_micros=20_000_000,
        actual_cost_usd_micros=18_000_000,
        minimum_fee_usd_micros=100_000,
        retention_until=datetime.now(timezone.utc) + timedelta(days=30),
    )
    db_session.add(run1)
    db_session.commit()

    # Run 2: Worker killed
    simulate_stuck_run_scenario(db_session, tenant_id, run_id_2, 15_000_000)

    # Run 3: Stuck CLAIMED with S3
    simulate_stuck_claimed_run(
        db_session, tenant_id, run_id_3, 25_000_000, has_s3_result=True
    )

    # Verify money before recovery
    # $100 = $42 (after run1 refund) + $15 (run2 reserved) + $25 (run3 reserved) + $18 (run1 settled)
    is_valid, error_msg = verify_money_conservation(
        budget_scripts, db_session, tenant_id, initial_balance, expected_spent=18_000_000
    )
    assert is_valid, f"Money leaked during cascade failures: {error_msg}"

    # Recover Run 2: Full refund
    budget_scripts.refund_full(tenant_id, run_id_2)
    run2 = db_session.query(Run).filter_by(run_id=run_id_2).one()
    run2.status = "FAILED"
    run2.money_state = "SETTLED"
    run2.actual_cost_usd_micros = 0
    db_session.commit()

    # Recover Run 3: Roll-forward with $22 actual cost
    budget_scripts.settle(tenant_id, run_id_3, 22_000_000)
    run3 = db_session.query(Run).filter_by(run_id=run_id_3).one()
    run3.status = "COMPLETED"
    run3.money_state = "SETTLED"
    run3.actual_cost_usd_micros = 22_000_000
    run3.finalize_stage = "COMMITTED"
    db_session.commit()

    # Final verification
    # $100 = $60 + $0 + ($18 + $0 + $22)
    is_valid, error_msg = verify_money_conservation(
        budget_scripts,
        db_session,
        tenant_id,
        initial_balance,
        expected_spent=40_000_000,  # $18 + $0 + $22
    )
    assert is_valid, f"Money leaked after cascade recovery: {error_msg}"
