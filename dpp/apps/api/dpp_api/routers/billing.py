"""Billing endpoints — Phase 2 payment front door.

DP-V1-P1-SOW §6 / §7 / §8

Endpoints:
  POST /v1/billing/checkout-sessions   — create / idempotent-fetch session
  POST /v1/billing/paypal/orders       — [EC] create PayPal order
  POST /v1/billing/paypal/capture      — [EC, NON-AUTHORITATIVE] capture PayPal order
  GET  /v1/billing/me                  — entitlement + session status

All economic side-effect points are marked [EC] inline.

CRITICAL INVARIANTS (never violate):
- Entitlement MUST NOT be activated from any endpoint in this file.
  Entitlement activation is WEBHOOK-ONLY (DEC-V1-07, DEC-V1-08).
- CAPTURE_SUBMITTED is non-authoritative — "submitted, awaiting webhook".
- PayPal-Request-Id values come from the DB-stored immutable fields generated
  at session creation; they are NEVER regenerated here (DEC-V1-14, DEC-V1-15).
- return_url/cancel_url domain is locked to CHECKOUT_SITE_BASE_URL (DEC-V1-16).
"""

import logging
import time
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from dpp_api.auth.session_auth import SessionAuthContext, get_session_auth_context
from dpp_api.billing.paypal import get_paypal_client
from dpp_api.db.models import CHECKOUT_SESSION_TERMINAL_STATUSES, Entitlement
from dpp_api.db.repo_checkout import CheckoutRepository
from dpp_api.db.session import get_db
from dpp_api.schemas import (
    BillingMeResponse,
    CheckoutSessionCreateRequest,
    CheckoutSessionResponse,
    EntitlementInfo,
    LatestCheckoutSessionInfo,
    PayPalCaptureRequest,
    PayPalCaptureResponse,
    PayPalOrderCreateRequest,
    PayPalOrderCreateResponse,
    ProblemDetail,
)
from dpp_api.supabase_client import get_supabase_admin_client

router = APIRouter(prefix="/v1/billing", tags=["billing"])
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Email confirmation cache
# DEC: email_confirmed_at via Admin API + 60s TTL cache, NOT JWT claim (locked)
# ---------------------------------------------------------------------------

_email_confirmed_cache: dict[str, tuple[bool, float]] = {}
_EMAIL_CACHE_TTL_SEC = 60


def _is_email_confirmed(user_id: str) -> bool:
    """Check email confirmation status via Supabase Admin API with 60s TTL cache.

    The JWT claim MUST NOT be trusted for this check (locked decision).
    """
    now = time.monotonic()
    cached = _email_confirmed_cache.get(user_id)
    if cached is not None:
        confirmed, expires_at = cached
        if now < expires_at:
            return confirmed

    try:
        admin = get_supabase_admin_client()
        user_data = admin.auth.admin.get_user_by_id(user_id)
        confirmed = (
            user_data is not None
            and user_data.user is not None
            and user_data.user.email_confirmed_at is not None
        )
    except Exception:
        logger.exception(
            "email_confirmed_check.failed",
            extra={"user_id": user_id},
        )
        confirmed = False  # fail-safe: treat as unconfirmed

    _email_confirmed_cache[user_id] = (confirmed, now + _EMAIL_CACHE_TTL_SEC)
    return confirmed


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _format_amount(amount_usd_cents: int) -> str:
    """Convert BIGINT USD cents to decimal string ('2900' → '29.00')."""
    return f"{Decimal(amount_usd_cents) / Decimal(100):.2f}"


def _problem(
    request: Request,
    status_code: int,
    title: str,
    detail: str,
    problem_type: str,
) -> HTTPException:
    """Build RFC 9457 Problem Detail HTTPException."""
    body = ProblemDetail(
        type=f"https://api.decisionproof.ai/problems/{problem_type}",
        title=title,
        status=status_code,
        detail=detail,
        instance=str(request.url.path),
    )
    return HTTPException(
        status_code=status_code,
        detail=body.model_dump(exclude_none=True),
    )


def _session_to_response(session) -> CheckoutSessionResponse:
    return CheckoutSessionResponse(
        session_id=session.id,
        status=session.status,
        plan_id=session.plan_id,
        amount=_format_amount(session.amount_usd_cents),
        currency=session.currency,
        expires_at=session.expires_at,
        created_at=session.created_at,
        updated_at=session.updated_at,
    )


def _extract_approval_url(paypal_response: dict) -> Optional[str]:
    """Extract the PayPal buyer-approval URL from the order response links array."""
    for link in paypal_response.get("links", []):
        if link.get("rel") == "approve":
            return link.get("href")
    return None


# ---------------------------------------------------------------------------
# POST /v1/billing/checkout-sessions
# ---------------------------------------------------------------------------


@router.post(
    "/checkout-sessions",
    response_model=CheckoutSessionResponse,
)
async def create_checkout_session(
    body: CheckoutSessionCreateRequest,
    request: Request,
    response: Response,
    auth: SessionAuthContext = Depends(get_session_auth_context),
    db: Session = Depends(get_db),
) -> CheckoutSessionResponse:
    """Create (or idempotently return) a checkout session.

    - Login required (get_session_auth_context enforces JWT — DEC-V1-05)
    - Email confirmed required (Admin API check — DEC-V1-06)
    - One active session per (tenant, plan) — DB first-writer guard (OI-03)
    - Returns existing active session with 200; new session with 201.
    """
    # Email confirmation guard — Admin API, NOT JWT claim (locked)
    if not _is_email_confirmed(auth.user_id):
        raise _problem(
            request, 403,
            "Email Not Confirmed",
            "Email address must be confirmed before starting checkout.",
            "email-not-confirmed",
        )

    repo = CheckoutRepository(db)

    # Validate plan
    plan = repo.get_plan(body.plan_id)
    if not plan or plan.status != "ACTIVE":
        raise _problem(
            request, 404,
            "Plan Not Found",
            f"Plan '{body.plan_id}' not found or not available.",
            "plan-not-found",
        )

    # Price is stored in plan.features_json["price_usd_cents"] (set by plan seed migration)
    # e.g. {"price_usd_cents": 2900} for $29.00
    amount_usd_cents: Optional[int] = plan.features_json.get("price_usd_cents")
    if not isinstance(amount_usd_cents, int) or amount_usd_cents <= 0:
        logger.error(
            "plan.price_missing",
            extra={"plan_id": plan.plan_id, "features_json": plan.features_json},
        )
        raise _problem(
            request, 500,
            "Internal Error",
            "Plan pricing configuration error.",
            "plan-pricing-error",
        )

    session, created = repo.create_session(
        user_id=auth.user_id,
        tenant_id=auth.tenant_id,
        plan_id=body.plan_id,
        amount_usd_cents=amount_usd_cents,
    )

    if created:
        db.commit()
        response.status_code = status.HTTP_201_CREATED
    else:
        response.status_code = status.HTTP_200_OK

    return _session_to_response(session)


# ---------------------------------------------------------------------------
# POST /v1/billing/paypal/orders
# ---------------------------------------------------------------------------


@router.post(
    "/paypal/orders",
    response_model=PayPalOrderCreateResponse,
)
async def create_paypal_order(
    body: PayPalOrderCreateRequest,
    request: Request,
    response: Response,
    auth: SessionAuthContext = Depends(get_session_auth_context),
    db: Session = Depends(get_db),
) -> PayPalOrderCreateResponse:
    """[EC] Create a PayPal order for an existing checkout session.

    Economic side effect: PayPal order is created (real money can follow on capture).
    Idempotent: if session already has a paypal_order_id, returns stored data immediately.
    PayPal-Request-Id is the immutable paypal_request_id_create from the session (DEC-V1-14).
    """
    repo = CheckoutRepository(db)

    # Stealth 404 on both not-found and wrong-tenant (SOW §11)
    session = repo.get_by_id_for_tenant(body.session_id, auth.tenant_id)
    if not session:
        raise _problem(
            request, 404,
            "Session Not Found",
            "Checkout session not found.",
            "session-not-found",
        )

    # Expiry guard (on-access expiry check)
    if session.expires_at <= _now():
        repo.mark_expired(session)
        db.commit()
        raise _problem(
            request, 410,
            "Session Expired",
            "Checkout session has expired. Please start a new checkout.",
            "session-expired",
        )

    # Terminal guard
    if session.status in CHECKOUT_SESSION_TERMINAL_STATUSES:
        raise _problem(
            request, 409,
            "Session Terminal",
            f"Session cannot be used: status is {session.status}.",
            "session-terminal",
        )

    # Idempotent: PayPal order already created — return stored data
    if session.paypal_order_id:
        response.status_code = status.HTTP_200_OK
        return PayPalOrderCreateResponse(
            session_id=session.id,
            paypal_order_id=session.paypal_order_id,
            # Reconstruct approval URL from stored order ID
            approval_url=f"https://www.paypal.com/checkoutnow?token={session.paypal_order_id}",
            status=session.status,
            expires_at=session.expires_at,
        )

    # Must be in CHECKOUT_SESSION_CREATED to proceed
    if session.status != "CHECKOUT_SESSION_CREATED":
        raise _problem(
            request, 409,
            "Invalid Session State",
            f"Expected CHECKOUT_SESSION_CREATED, got {session.status}.",
            "session-invalid-state",
        )

    # [EC] Create PayPal order — PayPal-Request-Id is immutable (DEC-V1-14)
    paypal = get_paypal_client()
    amount_str = _format_amount(session.amount_usd_cents)

    try:
        paypal_response = await paypal.create_order(
            amount=amount_str,
            currency=session.currency,
            internal_order_id=session.id,
            plan_id=session.plan_id,
            request_id=session.paypal_request_id_create,  # Immutable — DEC-V1-14
        )
    except httpx.HTTPStatusError as exc:
        logger.error(
            "paypal.create_order.failed",
            extra={
                "session_id": session.id,
                "http_status": exc.response.status_code,
            },
        )
        raise _problem(
            request, 502,
            "PayPal Error",
            "Failed to create PayPal order. Please try again later.",
            "paypal-create-order-failed",
        )

    paypal_order_id: Optional[str] = paypal_response.get("id")
    if not paypal_order_id:
        logger.error("paypal.create_order.no_id", extra={"session_id": session.id})
        raise _problem(
            request, 502,
            "PayPal Error",
            "PayPal returned no order ID.",
            "paypal-no-order-id",
        )

    approval_url = _extract_approval_url(paypal_response)
    if not approval_url:
        logger.error(
            "paypal.create_order.no_approval_url",
            extra={"session_id": session.id, "paypal_order_id": paypal_order_id},
        )
        raise _problem(
            request, 502,
            "PayPal Error",
            "PayPal returned no buyer approval URL.",
            "paypal-no-approval-url",
        )

    # [EC] Persist BillingOrder row (authoritative payment record)
    repo.create_billing_order(session=session, paypal_order_id=paypal_order_id)

    # [EC] Transition session → PAYPAL_ORDER_CREATED
    repo.mark_order_created(session, paypal_order_id)

    db.commit()
    response.status_code = status.HTTP_201_CREATED

    return PayPalOrderCreateResponse(
        session_id=session.id,
        paypal_order_id=paypal_order_id,
        approval_url=approval_url,
        status=session.status,
        expires_at=session.expires_at,
    )


# ---------------------------------------------------------------------------
# POST /v1/billing/paypal/capture
# ---------------------------------------------------------------------------


@router.post(
    "/paypal/capture",
    response_model=PayPalCaptureResponse,
)
async def capture_paypal_order(
    body: PayPalCaptureRequest,
    request: Request,
    response: Response,
    auth: SessionAuthContext = Depends(get_session_auth_context),
    db: Session = Depends(get_db),
) -> PayPalCaptureResponse:
    """[EC, NON-AUTHORITATIVE] Submit capture for an approved PayPal order.

    Returns 202 on first submission, 200 on idempotent replay.

    CRITICAL: This endpoint MUST NOT activate entitlement (DEC-V1-07, DEC-V1-08).
    Entitlement is activated ONLY by POST /webhooks/paypal on PAYMENT.CAPTURE.COMPLETED.
    CAPTURE_SUBMITTED means "capture accepted by PayPal, money may have moved,
    but entitlement awaits webhook confirmation".

    PayPal-Request-Id is the immutable paypal_request_id_capture from the session (DEC-V1-15).
    """
    repo = CheckoutRepository(db)

    # Stealth 404 (SOW §11)
    session = repo.get_by_id_for_tenant(body.session_id, auth.tenant_id)
    if not session:
        raise _problem(
            request, 404,
            "Session Not Found",
            "Checkout session not found.",
            "session-not-found",
        )

    # Expiry guard
    if session.expires_at <= _now():
        repo.mark_expired(session)
        db.commit()
        raise _problem(
            request, 410,
            "Session Expired",
            "Checkout session has expired.",
            "session-expired",
        )

    # Idempotent replay: already submitted (SOW §8.3)
    if session.status == "CAPTURE_SUBMITTED":
        order = repo.get_billing_order_by_session(session.id)
        response.status_code = status.HTTP_200_OK
        return PayPalCaptureResponse(
            session_id=session.id,
            status=session.status,
            paypal_order_id=session.paypal_order_id or "",
            paypal_capture_id=order.provider_capture_id if order else None,
            message="Capture already submitted. Awaiting webhook confirmation.",
        )

    # Terminal guard
    if session.status in CHECKOUT_SESSION_TERMINAL_STATUSES:
        raise _problem(
            request, 409,
            "Session Terminal",
            f"Session cannot be used: status is {session.status}.",
            "session-terminal",
        )

    # Must have a PayPal order to capture
    if not session.paypal_order_id:
        raise _problem(
            request, 409,
            "No PayPal Order",
            "No PayPal order has been created for this session. "
            "Call POST /v1/billing/paypal/orders first.",
            "no-paypal-order",
        )

    # Must be in PAYPAL_ORDER_CREATED (buyer has approved on PayPal)
    if session.status != "PAYPAL_ORDER_CREATED":
        raise _problem(
            request, 409,
            "Invalid Session State",
            f"Expected PAYPAL_ORDER_CREATED, got {session.status}.",
            "session-invalid-state",
        )

    # [EC] Submit capture — PayPal-Request-Id is immutable (DEC-V1-15)
    paypal = get_paypal_client()
    try:
        capture_response = await paypal.capture_order(
            paypal_order_id=session.paypal_order_id,
            request_id=session.paypal_request_id_capture,  # Immutable — DEC-V1-15
        )
    except httpx.HTTPStatusError as exc:
        http_status = exc.response.status_code
        logger.error(
            "paypal.capture_order.failed",
            extra={
                "session_id": session.id,
                "paypal_order_id": session.paypal_order_id,
                "http_status": http_status,
            },
        )
        # Mark session FAILED on definitive client-error responses
        if http_status in (400, 422):
            repo.mark_failed(session, reason=f"paypal_capture_http_{http_status}")
            db.commit()
        raise _problem(
            request, 502,
            "PayPal Error",
            "Failed to capture PayPal order. Please try again.",
            "paypal-capture-failed",
        )

    # Extract capture ID from PayPal response
    capture_id: Optional[str] = None
    purchase_units = capture_response.get("purchase_units", [])
    if purchase_units:
        captures = purchase_units[0].get("payments", {}).get("captures", [])
        if captures:
            capture_id = captures[0].get("id")

    # [EC] Update BillingOrder — non-authoritative, no entitlement change
    order = repo.get_billing_order_by_session(session.id)
    if order and capture_id:
        repo.set_order_capture_submitted(order, capture_id)

    # [EC] Transition session → CAPTURE_SUBMITTED (non-authoritative)
    repo.mark_capture_submitted(session)

    db.commit()

    response.status_code = status.HTTP_202_ACCEPTED
    return PayPalCaptureResponse(
        session_id=session.id,
        status="CAPTURE_SUBMITTED",
        paypal_order_id=session.paypal_order_id,
        paypal_capture_id=capture_id,
        message="Capture submitted. Awaiting payment confirmation from PayPal.",
    )


# ---------------------------------------------------------------------------
# GET /v1/billing/me
# ---------------------------------------------------------------------------


@router.get(
    "/me",
    response_model=BillingMeResponse,
)
async def get_billing_me(
    auth: SessionAuthContext = Depends(get_session_auth_context),
    db: Session = Depends(get_db),
) -> BillingMeResponse:
    """Return entitlement status and latest checkout session for the authenticated tenant."""
    tenant_id = auth.tenant_id
    repo = CheckoutRepository(db)

    # Latest entitlement row for this tenant
    entitlement_row = db.execute(
        select(Entitlement)
        .where(Entitlement.tenant_id == tenant_id)
        .order_by(Entitlement.id.desc())
        .limit(1)
    ).scalar_one_or_none()

    if entitlement_row:
        entitlement = EntitlementInfo(
            status=entitlement_row.status,
            plan_id=entitlement_row.plan_id,
            valid_from=entitlement_row.valid_from,
            valid_until=entitlement_row.valid_until,
        )
    else:
        entitlement = EntitlementInfo(status="FREE")

    # Latest checkout session (any status) for this tenant
    latest_session = repo.get_latest_for_tenant(tenant_id)
    latest_session_info: Optional[LatestCheckoutSessionInfo] = None
    if latest_session:
        latest_session_info = LatestCheckoutSessionInfo(
            session_id=latest_session.id,
            status=latest_session.status,
            plan_id=latest_session.plan_id,
            amount=_format_amount(latest_session.amount_usd_cents),
            currency=latest_session.currency,
            created_at=latest_session.created_at,
            updated_at=latest_session.updated_at,
        )

    return BillingMeResponse(
        tenant_id=tenant_id,
        entitlement=entitlement,
        latest_checkout_session=latest_session_info,
    )
