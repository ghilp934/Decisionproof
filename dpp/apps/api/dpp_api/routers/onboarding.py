"""Onboarding status endpoint — Phase 2.

DP-V1-P1-SOW §9

GET /v1/onboarding/status

Returns a step-by-step completion checklist for the authenticated user's tenant,
enabling the frontend to display onboarding progress and guide the user to the
next action.

Step order (fail-fast — first incomplete step = current_step):
  1. signup_complete     — user exists + JWT valid (always True if authenticated)
  2. email_confirmed     — email_confirmed_at set (Admin API, NOT JWT claim — locked)
  3. tenant_resolved     — user has an active tenant
  4. payment_complete    — entitlement granted (ACTIVE)
  5. entitlement_active  — entitlement ACTIVE and within validity window
  6. token_issued        — at least one active API token
  7. first_run_complete  — at least one successful run
"""

import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from dpp_api.auth.session_auth import SessionAuthContext, get_session_auth_context
from dpp_api.db.models import APIToken, Entitlement, Run
from dpp_api.db.repo_checkout import CheckoutRepository
from dpp_api.db.session import get_db
from dpp_api.routers.billing import _is_email_confirmed
from dpp_api.schemas import OnboardingStatusResponse, OnboardingSteps

router = APIRouter(prefix="/v1/onboarding", tags=["onboarding"])
logger = logging.getLogger(__name__)

# Ordered list of step keys for current_step resolution
_STEP_ORDER = [
    "signup_complete",
    "email_confirmed",
    "tenant_resolved",
    "payment_complete",
    "entitlement_active",
    "token_issued",
    "first_run_complete",
]


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# GET /v1/onboarding/status
# ---------------------------------------------------------------------------


@router.get(
    "/status",
    response_model=OnboardingStatusResponse,
)
async def get_onboarding_status(
    auth: SessionAuthContext = Depends(get_session_auth_context),
    db: Session = Depends(get_db),
) -> OnboardingStatusResponse:
    """Return per-step onboarding completion flags for the authenticated tenant.

    Uses Supabase Admin API (with 60s cache) to check email confirmation —
    the JWT claim MUST NOT be trusted for this (locked decision).
    """
    tenant_id = auth.tenant_id
    user_id = auth.user_id

    # Step 1: signup_complete — always True if authenticated
    signup_complete = True

    # Step 2: email_confirmed — Admin API + 60s TTL cache (locked)
    email_confirmed = _is_email_confirmed(user_id)

    # Step 3: tenant_resolved — always True if get_session_auth_context succeeded
    tenant_resolved = True

    # Step 4/5: payment_complete + entitlement_active
    entitlement_row: Optional[Entitlement] = db.execute(
        select(Entitlement)
        .where(Entitlement.tenant_id == tenant_id)
        .order_by(Entitlement.id.desc())
        .limit(1)
    ).scalar_one_or_none()

    entitlement_status = "FREE"
    payment_complete = False
    entitlement_active = False

    if entitlement_row:
        entitlement_status = entitlement_row.status
        payment_complete = entitlement_row.status in ("ACTIVE", "SUSPENDED")
        if entitlement_row.status == "ACTIVE":
            now = _now()
            # Check validity window if valid_until is set
            within_window = (
                entitlement_row.valid_until is None
                or entitlement_row.valid_until > now
            )
            entitlement_active = within_window

    # Step 6: token_issued — at least one active API token
    token_issued: bool = db.execute(
        select(APIToken)
        .where(
            APIToken.tenant_id == tenant_id,
            APIToken.status == "active",
        )
        .limit(1)
    ).first() is not None

    # Step 7: first_run_complete — at least one completed run
    run_exists = db.execute(
        select(Run)
        .where(
            Run.tenant_id == tenant_id,
            Run.status == "COMPLETED",
        )
        .limit(1)
    ).first() is not None
    first_run_complete = run_exists

    steps = OnboardingSteps(
        signup_complete=signup_complete,
        email_confirmed=email_confirmed,
        tenant_resolved=tenant_resolved,
        payment_complete=payment_complete,
        entitlement_active=entitlement_active,
        token_issued=token_issued,
        first_run_complete=first_run_complete,
    )

    # current_step = first incomplete step
    steps_dict = steps.model_dump()
    current_step = "complete"
    for step_key in _STEP_ORDER:
        if not steps_dict.get(step_key, False):
            current_step = step_key
            break

    # Latest checkout session status (for UI context)
    repo = CheckoutRepository(db)
    latest_session = repo.get_latest_for_tenant(tenant_id)
    checkout_session_status: Optional[str] = (
        latest_session.status if latest_session else None
    )

    return OnboardingStatusResponse(
        steps=steps,
        current_step=current_step,
        entitlement_status=entitlement_status,
        checkout_session_status=checkout_session_status,
    )
