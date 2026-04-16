"""Checkout session repository — Phase 2 payment front door.

DP-V1-P1-SOW §5 / §7 / §8 / §9

All economic side effect points are marked [EC] inline.
All concurrency-sensitive operations use conditional UPDATEs or DB constraints.

Key design decisions (locked):
- First-writer guard: DB partial unique index uq_cs_tenant_plan_active
  On IntegrityError → fetch and return existing session (OI-03 LOCKED)
- State transitions: conditional UPDATE WHERE status NOT IN (terminal)
  prevents status downgrade in capture-vs-webhook race (SOW §8.3)
- paypal_request_id_* are generated ONCE at session creation and never
  regenerated on retry (DEC-V1-15)
"""

import logging
import os
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Optional

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from dpp_api.db.models import (
    BillingOrder,
    CheckoutSession,
    CheckoutSessionEvent,
    CHECKOUT_SESSION_TERMINAL_STATUSES,
    Plan,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SESSION_TTL_MINUTES: int = int(os.getenv("CHECKOUT_SESSION_TTL_MINUTES", "30"))


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _format_amount(amount_usd_cents: int) -> str:
    """Convert BIGINT USD cents to decimal string for PayPal API.

    e.g. 2900 → '29.00'
    """
    return str(Decimal(amount_usd_cents) / Decimal(100))


# ---------------------------------------------------------------------------
# CheckoutRepository
# ---------------------------------------------------------------------------


class CheckoutRepository:
    """Data access for checkout_sessions and checkout_session_events.

    All methods that write state are concurrency-safe by design:
    - create_session(): relies on DB unique partial index for first-writer safety
    - transition_status(): uses conditional UPDATE WHERE status NOT IN (terminal)
    """

    def __init__(self, db: Session) -> None:
        self.db = db

    # ------------------------------------------------------------------ #
    # Read operations
    # ------------------------------------------------------------------ #

    def get_by_id(self, session_id: str) -> Optional[CheckoutSession]:
        """Fetch session by primary key. Returns None if not found."""
        stmt = select(CheckoutSession).where(CheckoutSession.id == session_id)
        return self.db.execute(stmt).scalar_one_or_none()

    def get_by_id_for_tenant(
        self, session_id: str, tenant_id: str
    ) -> Optional[CheckoutSession]:
        """Fetch session with ownership check.

        Returns None on both "not found" and "wrong tenant" — stealth 404
        semantics per SOW §11 (security requirements).
        """
        stmt = select(CheckoutSession).where(
            CheckoutSession.id == session_id,
            CheckoutSession.tenant_id == tenant_id,
        )
        return self.db.execute(stmt).scalar_one_or_none()

    def get_active_session_for_tenant_plan(
        self, tenant_id: str, plan_id: str
    ) -> Optional[CheckoutSession]:
        """Return existing non-terminal, non-expired session for (tenant, plan).

        Used on session creation to implement idempotent behavior.
        """
        stmt = select(CheckoutSession).where(
            CheckoutSession.tenant_id == tenant_id,
            CheckoutSession.plan_id == plan_id,
            CheckoutSession.status.not_in(CHECKOUT_SESSION_TERMINAL_STATUSES),
            CheckoutSession.expires_at > _now(),
        )
        return self.db.execute(stmt).scalar_one_or_none()

    def get_latest_for_tenant(self, tenant_id: str) -> Optional[CheckoutSession]:
        """Return the most recently created session for a tenant (any status)."""
        stmt = (
            select(CheckoutSession)
            .where(CheckoutSession.tenant_id == tenant_id)
            .order_by(CheckoutSession.created_at.desc())
            .limit(1)
        )
        return self.db.execute(stmt).scalar_one_or_none()

    def get_plan(self, plan_id: str) -> Optional[Plan]:
        """Fetch plan by ID."""
        stmt = select(Plan).where(Plan.plan_id == plan_id)
        return self.db.execute(stmt).scalar_one_or_none()

    # ------------------------------------------------------------------ #
    # Session creation
    # ------------------------------------------------------------------ #

    def create_session(
        self,
        *,
        user_id: str,
        tenant_id: str,
        plan_id: str,
        amount_usd_cents: int,
        currency: str = "USD",
    ) -> tuple[CheckoutSession, bool]:
        """Create a new checkout session.

        Returns (session, created_new: bool).

        First-writer safety (OI-03 LOCKED):
          The DB partial unique index uq_cs_tenant_plan_active ensures only one
          active session exists per (tenant_id, plan_id). On IntegrityError,
          this method falls back to fetching the existing session — the DB decides
          the winner, not the application.

        The PayPal-Request-Id values are generated here and NEVER regenerated.
        """
        session_id = str(uuid.uuid4())
        paypal_request_id_create = str(uuid.uuid4())
        paypal_request_id_capture = str(uuid.uuid4())
        nonce = secrets.token_hex(32)
        expires_at = _now() + timedelta(minutes=SESSION_TTL_MINUTES)

        session = CheckoutSession(
            id=session_id,
            user_id=user_id,
            tenant_id=tenant_id,
            plan_id=plan_id,
            amount_usd_cents=amount_usd_cents,
            currency=currency,
            status="CHECKOUT_SESSION_CREATED",
            paypal_request_id_create=paypal_request_id_create,
            paypal_request_id_capture=paypal_request_id_capture,
            nonce=nonce,
            expires_at=expires_at,
        )

        try:
            self.db.add(session)
            self.db.flush()  # raise IntegrityError now if constraint violated
            self.add_event(session_id, "CS_CREATED", actor="SYSTEM")
            logger.info(
                "checkout_session.created",
                extra={"session_id": session_id, "tenant_id": tenant_id, "plan_id": plan_id},
            )
            return session, True

        except IntegrityError:
            self.db.rollback()
            # First-writer guard fired: return the existing valid session
            existing = self.get_active_session_for_tenant_plan(tenant_id, plan_id)
            if existing:
                logger.info(
                    "checkout_session.create.idempotent_existing",
                    extra={"session_id": existing.id, "tenant_id": tenant_id},
                )
                return existing, False
            # Extremely rare: constraint fired but no active session found
            # (e.g. the winner expired between INSERT and SELECT).
            # Retry from scratch by re-raising to let the router handle it.
            raise

    # ------------------------------------------------------------------ #
    # BillingOrder creation
    # ------------------------------------------------------------------ #

    def create_billing_order(
        self,
        *,
        session: CheckoutSession,
        paypal_order_id: str,
    ) -> BillingOrder:
        """[EC] Create BillingOrder row bound to checkout session.

        Economic side effect: creates the authoritative payment record.
        Called only after PayPal create-order succeeds.
        Protected by UNIQUE (provider, provider_order_id) on billing_orders.
        """
        amount_str = _format_amount(session.amount_usd_cents)
        order = BillingOrder(
            tenant_id=session.tenant_id,
            provider="PAYPAL",
            provider_order_id=paypal_order_id,
            plan_id=session.plan_id,
            currency=session.currency,
            amount=amount_str,
            status="PENDING",
            checkout_session_id=session.id,
        )
        self.db.add(order)
        self.db.flush()
        logger.info(
            "billing_order.created",
            extra={
                "order_id": order.id,
                "session_id": session.id,
                "tenant_id": session.tenant_id,
                "paypal_order_id": paypal_order_id,
            },
        )
        return order

    def get_billing_order_by_session(
        self, session_id: str
    ) -> Optional[BillingOrder]:
        """Fetch BillingOrder linked to a checkout session."""
        stmt = select(BillingOrder).where(
            BillingOrder.checkout_session_id == session_id
        )
        return self.db.execute(stmt).scalar_one_or_none()

    # ------------------------------------------------------------------ #
    # State transitions
    # ------------------------------------------------------------------ #

    def transition_status(
        self,
        session: CheckoutSession,
        new_status: str,
        *,
        extra_updates: Optional[dict] = None,
    ) -> bool:
        """Conditionally transition session status.

        Uses WHERE status NOT IN (terminal) to prevent status downgrade
        in capture-vs-webhook race (SOW §8.3).

        Returns True if the row was updated, False if already terminal.
        """
        updates: dict = {"status": new_status, "updated_at": _now()}
        if extra_updates:
            updates.update(extra_updates)

        stmt = (
            update(CheckoutSession)
            .where(
                CheckoutSession.id == session.id,
                CheckoutSession.status.not_in(CHECKOUT_SESSION_TERMINAL_STATUSES),
            )
            .values(**updates)
        )
        result = self.db.execute(stmt)
        updated = result.rowcount > 0
        if updated:
            # Refresh in-memory object
            self.db.refresh(session)
        else:
            logger.warning(
                "checkout_session.transition.skipped",
                extra={
                    "session_id": session.id,
                    "attempted_status": new_status,
                    "current_status": session.status,
                    "reason": "session_already_terminal_or_not_found",
                },
            )
        return updated

    def mark_expired(self, session: CheckoutSession) -> bool:
        """Mark session as EXPIRED (on-access expiry check)."""
        updated = self.transition_status(session, "EXPIRED")
        if updated:
            self.add_event(session.id, "EXPIRED", actor="SYSTEM")
        return updated

    def mark_order_created(
        self,
        session: CheckoutSession,
        paypal_order_id: str,
    ) -> bool:
        """[EC] Transition to PAYPAL_ORDER_CREATED and store paypal_order_id."""
        return self.transition_status(
            session,
            "PAYPAL_ORDER_CREATED",
            extra_updates={"paypal_order_id": paypal_order_id},
        )

    def mark_capture_submitted(
        self, session: CheckoutSession
    ) -> bool:
        """[EC] Transition to CAPTURE_SUBMITTED.

        This is a NON-AUTHORITATIVE state — money may have moved but
        entitlement must NOT be activated here. Webhook is authoritative.
        """
        return self.transition_status(session, "CAPTURE_SUBMITTED")

    def mark_paid_verified(self, session: CheckoutSession) -> bool:
        """[EC-AUTH] Transition to PAID_VERIFIED.

        MUST only be called from webhook handler after verified
        PAYMENT.CAPTURE.COMPLETED event. This is the gateway to
        entitlement activation.
        """
        updated = self.transition_status(session, "PAID_VERIFIED")
        if updated:
            self.add_event(
                session.id, "PAID_VERIFIED", actor="PAYPAL_WEBHOOK"
            )
        return updated

    def mark_failed(
        self, session: CheckoutSession, reason: str
    ) -> bool:
        """Transition to FAILED and record reason."""
        updated = self.transition_status(
            session,
            "FAILED",
            extra_updates={"failed_reason": reason},
        )
        if updated:
            self.add_event(
                session.id, "FAILED", actor="SYSTEM",
                details={"reason": reason},
            )
        return updated

    # ------------------------------------------------------------------ #
    # BillingOrder status transitions
    # ------------------------------------------------------------------ #

    def set_order_capture_submitted(
        self,
        order: BillingOrder,
        capture_id: str,
    ) -> None:
        """[EC] Mark BillingOrder as CAPTURE_SUBMITTED.

        Non-authoritative — entitlement NOT activated.
        """
        order.status = "CAPTURE_SUBMITTED"
        order.provider_capture_id = capture_id
        order.updated_at = _now()
        self.db.flush()

    # ------------------------------------------------------------------ #
    # Event log
    # ------------------------------------------------------------------ #

    def add_event(
        self,
        session_id: str,
        event_type: str,
        *,
        actor: str = "SYSTEM",
        details: Optional[dict] = None,
    ) -> None:
        """Append an immutable audit event for the session.

        details must NOT contain paypal_request_id_*, nonce, or secrets.
        """
        event = CheckoutSessionEvent(
            session_id=session_id,
            event_type=event_type,
            actor=actor,
            details=details or {},
        )
        self.db.add(event)
        # Intentionally no flush — caller controls transaction boundary
