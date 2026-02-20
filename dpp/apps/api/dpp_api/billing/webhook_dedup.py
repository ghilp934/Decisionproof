"""Webhook dedup gate: atomic INSERT ON CONFLICT for concurrent idempotency.

P6.3: Replaces the SELECT-then-INSERT pattern with a two-step atomic operation that
guarantees at most one successful processing per (provider, dedup_key) pair, even
under concurrent webhook delivery from PayPal/Toss retry storms.

Design (DEC-P02-6):
  1. INSERT ON CONFLICT (provider, dedup_key) DO NOTHING RETURNING id
       → row returned  : this request is the FIRST processor → continue
       → no row        : conflict exists → check if it's a re-processable failure
  2. If no row (conflict): UPDATE ... WHERE status='failed' RETURNING id
       → row returned  : previous attempt failed; re-claim for re-processing
       → no row        : status is 'done' or 'processing' (true duplicate) → 200 immediately

Thread/process safety: PostgreSQL UNIQUE constraint guarantees exactly one INSERT wins
under concurrent load. The UPDATE in step 2 is also atomic (row-level lock).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Dedup key extraction (deterministic per provider)
# ---------------------------------------------------------------------------


def get_paypal_dedup_key(payload: dict, headers: dict) -> str:
    """Extract deterministic dedup key for a PayPal webhook event.

    Primary  : payload['id'] — PayPal event ID (globally unique per PayPal docs)
    Fallback : X-PAYPAL-TRANSMISSION-ID header (per-delivery unique)

    Raises ValueError if neither field is present.
    """
    event_id = payload.get("id")
    if event_id:
        return f"ev_{event_id}"

    # Fallback: delivery-level header (covers edge cases with missing event id)
    for header_name in ("x-paypal-transmission-id", "X-PAYPAL-TRANSMISSION-ID"):
        tid = headers.get(header_name)
        if tid:
            return f"tx_{tid}"

    raise ValueError("Cannot derive PayPal dedup_key: 'id' missing and no transmission-id header")


def get_toss_dedup_key(payload: dict, headers: dict) -> str:
    """Extract deterministic dedup key for a Toss webhook event.

    Primary  : Tosspayments-Webhook-Transmission-Id header (per Toss docs: unique per delivery)
    Fallback : data.paymentKey (unique per payment; stable across retries for same payment)

    Raises ValueError if neither field is present.
    """
    # Toss documents Tosspayments-Webhook-Transmission-Id as the delivery-unique ID.
    # The current codebase also accepts X-Transmission-ID as an alias.
    for header_name in (
        "tosspayments-webhook-transmission-id",
        "Tosspayments-Webhook-Transmission-Id",
        "x-transmission-id",
        "X-Transmission-ID",
    ):
        tid = headers.get(header_name)
        if tid:
            return f"tx_{tid}"

    # Fallback: paymentKey is stable across PG retries for the same payment
    data = payload.get("data", {})
    payment_key = data.get("paymentKey") or data.get("transactionKey")
    if payment_key:
        return f"pkey_{payment_key}"

    raise ValueError("Cannot derive Toss dedup_key: no transmission-id header or paymentKey in payload")


# ---------------------------------------------------------------------------
# Atomic dedup gate
# ---------------------------------------------------------------------------


def try_acquire_dedup(
    db: Session,
    provider: str,
    dedup_key: str,
    request_hash: Optional[str] = None,
) -> bool:
    """Attempt to atomically claim processing rights for (provider, dedup_key).

    Returns:
        True  — INSERT succeeded OR a previous 'failed' record was reclaimed.
                The caller is the FIRST (or re-processing) handler → proceed.
        False — A 'done' or concurrent 'processing' record already exists.
                The caller is a duplicate → ACK with 200, zero side effects.

    Step 1: INSERT ON CONFLICT DO NOTHING RETURNING id
      - UNIQUE constraint on (provider, dedup_key) ensures exactly one wins.
    Step 2: If conflict: UPDATE ... WHERE status='failed' RETURNING id
      - Allows PG retry of genuinely failed events without manual intervention.
    """
    now = datetime.now(timezone.utc)

    # Step 1: atomic insert
    insert_sql = text("""
        INSERT INTO webhook_dedup_events
            (provider, dedup_key, first_seen_at, status, request_hash)
        VALUES
            (:provider, :dedup_key, :now, 'processing', :request_hash)
        ON CONFLICT (provider, dedup_key) DO NOTHING
        RETURNING id
    """)
    result = db.execute(insert_sql, {
        "provider": provider,
        "dedup_key": dedup_key,
        "now": now,
        "request_hash": request_hash,
    })
    row = result.fetchone()

    if row is not None:
        db.commit()
        logger.debug(
            "WEBHOOK_DEDUP_ACQUIRED",
            extra={"provider": provider, "dedup_key_prefix": dedup_key[:16]},
        )
        return True

    # Step 2: check if it's a re-processable failure
    retry_sql = text("""
        UPDATE webhook_dedup_events
        SET status = 'processing', last_seen_at = :now
        WHERE provider = :provider AND dedup_key = :dedup_key AND status = 'failed'
        RETURNING id
    """)
    retry_result = db.execute(retry_sql, {
        "provider": provider,
        "dedup_key": dedup_key,
        "now": now,
    })
    retry_row = retry_result.fetchone()

    if retry_row is not None:
        db.commit()
        logger.info(
            "WEBHOOK_DEDUP_RETRY_RECLAIMED",
            extra={"provider": provider, "dedup_key_prefix": dedup_key[:16]},
        )
        return True

    # True duplicate (status = 'done' or concurrent 'processing')
    db.commit()
    logger.info(
        "WEBHOOK_DEDUP_DUPLICATE",
        extra={"provider": provider, "dedup_key_prefix": dedup_key[:16]},
    )
    return False


def mark_dedup_done(db: Session, provider: str, dedup_key: str) -> None:
    """Mark dedup record as 'done' after successful business processing."""
    sql = text("""
        UPDATE webhook_dedup_events
        SET status = 'done', last_seen_at = :now
        WHERE provider = :provider AND dedup_key = :dedup_key
    """)
    db.execute(sql, {
        "provider": provider,
        "dedup_key": dedup_key,
        "now": datetime.now(timezone.utc),
    })
    db.commit()


def mark_dedup_failed(db: Session, provider: str, dedup_key: str) -> None:
    """Mark dedup record as 'failed' on processing error (allows PG retry)."""
    sql = text("""
        UPDATE webhook_dedup_events
        SET status = 'failed', last_seen_at = :now
        WHERE provider = :provider AND dedup_key = :dedup_key
    """)
    db.execute(sql, {
        "provider": provider,
        "dedup_key": dedup_key,
        "now": datetime.now(timezone.utc),
    })
    db.commit()
