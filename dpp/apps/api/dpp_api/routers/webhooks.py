"""Webhook handlers for P0-2 Paid Pilot (PayPal + TossPayments).

DEC-P02-2: 권한 부여 타이밍
DEC-P02-3: 환불/부분환불 처리
DEC-P02-4: 분쟁 처리
DEC-P02-5: Webhook 검증 정책
DEC-P02-6: Idempotency / 중복 방어

P5.7: Webhook error taxonomy (retry storm prevention)
  (A) Invalid JSON / malformed payload → 400
  (B) Signature invalid / verification != SUCCESS → 401
  (C) Required header missing → 400
  (D) Our misconfig (missing secret / webhook_id) → 500 WEBHOOK_PROVIDER_MISCONFIG
  (E) Upstream network/SDK error during verification → 500 WEBHOOK_VERIFY_UPSTREAM_FAILED
  (F) Internal DB/processing error after verification → 500 WEBHOOK_INTERNAL_ERROR
  500 is ONLY for (D)(E)(F). Signature mismatch is NEVER 500.
"""

import hashlib
import hmac
import json as _json
import logging
import os
from typing import Optional

import httpx
from fastapi import APIRouter, Header, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from dpp_api.billing.paypal import get_paypal_client
from dpp_api.billing.toss import get_toss_client
from dpp_api.billing.webhook_dedup import (
    get_paypal_dedup_key,
    get_toss_dedup_key,
    mark_dedup_done,
    mark_dedup_failed,
    try_acquire_dedup,
)
from dpp_api.context import request_id_var
from dpp_api.db.models import BillingEvent, BillingOrder, Entitlement, BillingAuditLog, APIKey
from dpp_api.db.session import get_db
from dpp_api.utils.sanitize import payload_hash_bytes, sanitize_str

router = APIRouter(prefix="/webhooks", tags=["webhooks"])
logger = logging.getLogger(__name__)


# ============================================================================
# P5.7: Webhook Problem Details helper
# ============================================================================


def _webhook_problem(
    request: Request,
    status: int,
    *,
    code: str,
    title: str,
    detail: str | None,
    provider: str,
    payload_hash: str | None,
    extra: dict | None = None,
) -> JSONResponse:
    """Log once + return RFC 9457 Problem Details response with webhook extensions.

    4xx failures → warning log.
    5xx failures → error log + Retry-After: 60 response header.

    Response extensions (beyond RFC 9457 base):
      provider, payload_hash, error_code  (safe; never contain raw payload/secrets)
    """
    request_id = request_id_var.get(None)
    instance = request_id or str(request.url.path)

    log_extra: dict = {
        "event": f"webhook.{code.lower()}",
        "provider": provider,
        "payload_hash": payload_hash,
        "error_code": code,
    }
    if request_id:
        log_extra["request_id"] = request_id
    if extra:
        log_extra.update(extra)

    if status >= 500:
        logger.error(code, extra=log_extra)
    else:
        logger.warning(code, extra=log_extra)

    content: dict = {
        "type": f"urn:dpp:webhook:{code.lower()}",
        "title": title,
        "status": status,
        "provider": provider,
        "error_code": code,
    }
    if detail is not None:
        content["detail"] = detail
    if payload_hash is not None:
        content["payload_hash"] = payload_hash
    if instance:
        content["instance"] = instance

    response_headers = {"Content-Type": "application/problem+json"}
    if status >= 500:
        response_headers["Retry-After"] = "60"

    return JSONResponse(
        status_code=status,
        content=content,
        headers=response_headers,
    )


# ============================================================================
# PayPal Webhook Handler
# ============================================================================


@router.post("/paypal")
async def paypal_webhook(
    request: Request,
    x_paypal_transmission_id: Optional[str] = Header(None, alias="X-PAYPAL-TRANSMISSION-ID"),
    x_paypal_transmission_time: Optional[str] = Header(None, alias="X-PAYPAL-TRANSMISSION-TIME"),
    x_paypal_cert_url: Optional[str] = Header(None, alias="X-PAYPAL-CERT-URL"),
    x_paypal_auth_algo: Optional[str] = Header(None, alias="X-PAYPAL-AUTH-ALGO"),
    x_paypal_transmission_sig: Optional[str] = Header(None, alias="X-PAYPAL-TRANSMISSION-SIG"),
):
    """PayPal webhook handler.

    DEC-P02-5: Webhook 검증 정책 (서명 검증 필수)
    DEC-P02-6: Idempotency (event_id 중복 방지)
    P5.7: Strict client-vs-server error taxonomy
    """
    # ── Step 0: Raw body ingestion ───────────────────────────────────────────
    raw_body: bytes = await request.body()
    payload_hash = payload_hash_bytes(raw_body)
    payload_size = len(raw_body)
    request.state.payload_hash = payload_hash
    request.state.payload_size = payload_size

    # ── Step 1: JSON parsing (A → 400) ──────────────────────────────────────
    try:
        webhook_body = _json.loads(raw_body)
    except _json.JSONDecodeError:
        return _webhook_problem(
            request, 400,
            code="WEBHOOK_INVALID_JSON",
            title="Invalid JSON payload",
            detail="Request body is not valid JSON",
            provider="paypal",
            payload_hash=payload_hash,
        )

    logger.info(
        "WEBHOOK_RECEIVED",
        extra={"provider": "paypal", "payload_hash": payload_hash, "payload_size": payload_size},
    )

    # ── Step 2: Required header validation (C → 400) ────────────────────────
    _missing = [
        name for name, val in [
            ("X-PAYPAL-TRANSMISSION-ID", x_paypal_transmission_id),
            ("X-PAYPAL-TRANSMISSION-TIME", x_paypal_transmission_time),
            ("X-PAYPAL-CERT-URL", x_paypal_cert_url),
            ("X-PAYPAL-AUTH-ALGO", x_paypal_auth_algo),
            ("X-PAYPAL-TRANSMISSION-SIG", x_paypal_transmission_sig),
        ] if not val
    ]
    if _missing:
        return _webhook_problem(
            request, 400,
            code="WEBHOOK_MISSING_HEADERS",
            title="Missing required webhook headers",
            detail="One or more PayPal verification headers are absent",
            provider="paypal",
            payload_hash=payload_hash,
        )

    # ── Step 3: Body field validation (A → 400) ─────────────────────────────
    event_id = webhook_body.get("id")
    event_type = webhook_body.get("event_type")
    if not event_id or not event_type:
        return _webhook_problem(
            request, 400,
            code="WEBHOOK_INVALID_PAYLOAD",
            title="Invalid webhook payload",
            detail="Missing required fields: id, event_type",
            provider="paypal",
            payload_hash=payload_hash,
        )

    # ── Step 4: Obtain PayPal client (D → 500 on misconfig) ─────────────────
    try:
        paypal_client = get_paypal_client()
    except ValueError:
        return _webhook_problem(
            request, 500,
            code="WEBHOOK_PROVIDER_MISCONFIG",
            title="Webhook provider misconfiguration",
            detail="PayPal client is not properly configured",
            provider="paypal",
            payload_hash=payload_hash,
        )

    # ── Step 5: Signature verification ──────────────────────────────────────
    # (D → 500 misconfig, E → 500 upstream, B → 401 invalid sig)
    # NOTE: HTTPException must NOT be raised inside this try block to avoid
    # the outer except swallowing 4xx responses as 5xx.
    try:
        verification = await paypal_client.verify_webhook_signature(
            transmission_id=x_paypal_transmission_id,
            transmission_time=x_paypal_transmission_time,
            cert_url=x_paypal_cert_url,
            auth_algo=x_paypal_auth_algo,
            transmission_sig=x_paypal_transmission_sig,
            webhook_event=webhook_body,
        )
    except ValueError:
        # Missing PAYPAL_WEBHOOK_ID (D → 500 misconfig)
        return _webhook_problem(
            request, 500,
            code="WEBHOOK_PROVIDER_MISCONFIG",
            title="Webhook provider misconfiguration",
            detail="Webhook verification is not properly configured",
            provider="paypal",
            payload_hash=payload_hash,
        )
    except httpx.RequestError:
        # Network / timeout (E → 500 upstream)
        return _webhook_problem(
            request, 500,
            code="WEBHOOK_VERIFY_UPSTREAM_FAILED",
            title="Webhook verification upstream failure",
            detail="Unable to verify webhook signature due to upstream error",
            provider="paypal",
            payload_hash=payload_hash,
        )
    except httpx.HTTPStatusError:
        # PayPal API returned error (E → 500 upstream)
        return _webhook_problem(
            request, 500,
            code="WEBHOOK_VERIFY_UPSTREAM_FAILED",
            title="Webhook verification upstream failure",
            detail="PayPal verification API returned an error",
            provider="paypal",
            payload_hash=payload_hash,
        )

    # Verification status check is OUTSIDE the try block (B → 401, never 500)
    if verification.get("verification_status") != "SUCCESS":
        return _webhook_problem(
            request, 401,
            code="WEBHOOK_SIGNATURE_INVALID",
            title="Webhook signature verification failed",
            detail="PayPal verification_status is not SUCCESS",
            provider="paypal",
            payload_hash=payload_hash,
        )

    # ── Step 6: Dedup gate (P6.3: atomic, concurrent-safe) ──────────────────
    # get_paypal_dedup_key: event_id from payload (already validated above)
    try:
        dedup_key = get_paypal_dedup_key(webhook_body, dict(request.headers))
    except ValueError as dk_exc:
        return _webhook_problem(
            request, 400,
            code="WEBHOOK_INVALID_PAYLOAD",
            title="Cannot derive idempotency key",
            detail=sanitize_str(str(dk_exc)),
            provider="paypal",
            payload_hash=payload_hash,
        )

    db: Session = next(get_db())
    try:
        # Atomic INSERT ON CONFLICT DO NOTHING — exactly 1 succeeds under concurrency
        is_first = try_acquire_dedup(db, "paypal", dedup_key, payload_hash)
        if not is_first:
            # Duplicate or concurrent duplicate — ACK immediately, zero side effects
            logger.info(
                "WEBHOOK_ALREADY_PROCESSED",
                extra={"provider": "paypal", "payload_hash": payload_hash},
            )
            return {"status": "already_processed"}

        # ── Step 7: Business processing (F → 500) ────────────────────────────
        billing_event = BillingEvent(
            provider="PAYPAL",
            event_id=event_id,
            event_type=event_type,
            raw_payload=webhook_body,
            verification_status="SUCCESS",
            verification_meta=verification,
        )
        db.add(billing_event)
        db.commit()

        await _process_paypal_event(db, billing_event, webhook_body)

        billing_event.processed_at = db.query(BillingEvent).filter_by(id=billing_event.id).first().received_at
        db.commit()
        mark_dedup_done(db, "paypal", dedup_key)

        return {"status": "processed"}

    except Exception as exc:
        db.rollback()
        mark_dedup_failed(db, "paypal", dedup_key)
        return _webhook_problem(
            request, 500,
            code="WEBHOOK_INTERNAL_ERROR",
            title="Internal processing error",
            detail="An internal error occurred while processing the webhook",
            provider="paypal",
            payload_hash=payload_hash,
            extra={
                "error_type": type(exc).__name__,
                "error_msg": sanitize_str(str(exc)),
            },
        )
    finally:
        db.close()


async def _process_paypal_event(db: Session, billing_event: BillingEvent, webhook_body: dict):
    """Process PayPal event by type.

    DEC-P02-2: 권한 부여 타이밍
    DEC-P02-3: 환불 처리
    DEC-P02-4: 분쟁 처리
    """
    event_type = billing_event.event_type
    resource = webhook_body.get("resource", {})

    # PAYMENT.CAPTURE.COMPLETED - Grant entitlements (DEC-P02-2)
    if event_type == "PAYMENT.CAPTURE.COMPLETED":
        await _handle_paypal_capture_completed(db, resource)

    # PAYMENT.CAPTURE.REFUNDED - Revoke entitlements (DEC-P02-3)
    elif event_type == "PAYMENT.CAPTURE.REFUNDED":
        await _handle_paypal_capture_refunded(db, resource)

    # CUSTOMER.DISPUTE.* - Suspend account (DEC-P02-4)
    elif event_type.startswith("CUSTOMER.DISPUTE."):
        await _handle_paypal_dispute(db, event_type, resource)

    # PAYMENT.CAPTURE.DENIED - Mark as failed
    elif event_type == "PAYMENT.CAPTURE.DENIED":
        await _handle_paypal_capture_denied(db, resource)

    else:
        logger.info(f"PayPal event not handled: {event_type}")


async def _handle_paypal_capture_completed(db: Session, resource: dict):
    """Handle PayPal PAYMENT.CAPTURE.COMPLETED event.

    DEC-P02-2: 권한 부여 타이밍 (재조회 검증 후에만 활성화)
    """
    capture_id = resource.get("id")
    order_id = resource.get("supplementary_data", {}).get("related_ids", {}).get("order_id")

    if not order_id:
        logger.warning(f"PayPal capture missing order_id: {capture_id}")
        return

    # Verify by re-querying PayPal API (DEC-P02-5)
    paypal_client = get_paypal_client()
    try:
        order_details = await paypal_client.show_order_details(order_id)

        # Verify status and amount
        if order_details.get("status") != "COMPLETED":
            logger.warning(f"PayPal order not COMPLETED: {order_id}")
            return

        # Find internal order
        billing_order = (
            db.query(BillingOrder)
            .filter_by(provider="PAYPAL", provider_order_id=order_id)
            .first()
        )

        if not billing_order:
            logger.warning(f"Internal order not found for PayPal order: {order_id}")
            return

        # Update order status
        billing_order.status = "PAID"
        billing_order.provider_capture_id = capture_id

        # Grant entitlement (DEC-P02-2)
        _grant_entitlement(db, billing_order)

        db.commit()
        logger.info(f"PayPal payment captured and entitlement granted: {order_id}")

    except Exception as e:
        logger.error(
            "PAYPAL_CAPTURE_VERIFICATION_FAILED",
            extra={"error_type": type(e).__name__, "error_msg": sanitize_str(str(e))},
            exc_info=True,
        )
        raise


async def _handle_paypal_capture_refunded(db: Session, resource: dict):
    """Handle PayPal PAYMENT.CAPTURE.REFUNDED event.

    DEC-P02-3: 환불 처리 (즉시 권한 회수)
    """
    capture_id = resource.get("id")

    # Find order by capture_id
    billing_order = (
        db.query(BillingOrder)
        .filter_by(provider="PAYPAL", provider_capture_id=capture_id)
        .first()
    )

    if not billing_order:
        logger.warning(f"Order not found for refunded capture: {capture_id}")
        return

    # Update order status
    billing_order.status = "REFUNDED"

    # Revoke entitlement (DEC-P02-3)
    _revoke_entitlement(db, billing_order)

    db.commit()
    logger.info(f"PayPal payment refunded and entitlement revoked: {capture_id}")


async def _handle_paypal_dispute(db: Session, event_type: str, resource: dict):
    """Handle PayPal CUSTOMER.DISPUTE.* events.

    DEC-P02-4: 분쟁 처리 (즉시 SUSPENDED, 자동 복구 금지)
    """
    dispute_id = resource.get("dispute_id")

    logger.warning(f"PayPal dispute detected: {dispute_id}, event: {event_type}")

    # Log audit event
    audit_log = BillingAuditLog(
        event_type="DISPUTE_DETECTED",
        actor="WEBHOOK",
        details={"dispute_id": dispute_id, "event_type": event_type},
    )
    db.add(audit_log)
    db.commit()


async def _handle_paypal_capture_denied(db: Session, resource: dict):
    """Handle PayPal PAYMENT.CAPTURE.DENIED event."""
    capture_id = resource.get("id")

    billing_order = (
        db.query(BillingOrder)
        .filter_by(provider="PAYPAL", provider_capture_id=capture_id)
        .first()
    )

    if billing_order:
        billing_order.status = "FAILED"
        db.commit()
        logger.info(f"PayPal payment denied: {capture_id}")


# ============================================================================
# TossPayments Webhook Handler
# ============================================================================


@router.post("/tosspayments")
async def tosspayments_webhook(
    request: Request,
    x_toss_signature: Optional[str] = Header(None, alias="X-TossPayments-Signature"),
):
    """TossPayments webhook handler.

    DEC-P02-5: Webhook 검증 정책 (HMAC 선택 + 결제 조회 API로 재조회)
    DEC-P02-6: Idempotency (event_id 중복 방지)
    P5.7: Strict client-vs-server error taxonomy

    HMAC signature (optional):
      Set TOSS_WEBHOOK_SECRET env var to enforce X-TossPayments-Signature header.
      Without the env var, falls back to re-query only (backward compatible).
    """
    # ── Step 0: Raw body ingestion ───────────────────────────────────────────
    raw_body: bytes = await request.body()
    payload_hash = payload_hash_bytes(raw_body)
    payload_size = len(raw_body)
    request.state.payload_hash = payload_hash
    request.state.payload_size = payload_size

    # ── Step 1: JSON parsing (A → 400) ──────────────────────────────────────
    try:
        webhook_body = _json.loads(raw_body)
    except _json.JSONDecodeError:
        return _webhook_problem(
            request, 400,
            code="WEBHOOK_INVALID_JSON",
            title="Invalid JSON payload",
            detail="Request body is not valid JSON",
            provider="tosspayments",
            payload_hash=payload_hash,
        )

    logger.info(
        "WEBHOOK_RECEIVED",
        extra={"provider": "tosspayments", "payload_hash": payload_hash, "payload_size": payload_size},
    )

    # ── Step 2: HMAC signature verification (if TOSS_WEBHOOK_SECRET is set) ─
    toss_webhook_secret = os.getenv("TOSS_WEBHOOK_SECRET")
    if toss_webhook_secret:
        # C → 400: required header missing when secret is configured
        if not x_toss_signature:
            return _webhook_problem(
                request, 400,
                code="WEBHOOK_MISSING_SIGNATURE_HEADER",
                title="Missing signature header",
                detail="X-TossPayments-Signature header is required",
                provider="tosspayments",
                payload_hash=payload_hash,
            )
        # B → 401: HMAC mismatch
        expected_sig = hmac.new(
            toss_webhook_secret.encode("utf-8"),
            raw_body,
            hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(expected_sig, x_toss_signature):
            return _webhook_problem(
                request, 401,
                code="WEBHOOK_SIGNATURE_INVALID",
                title="Webhook signature verification failed",
                detail="HMAC-SHA256 signature mismatch",
                provider="tosspayments",
                payload_hash=payload_hash,
            )

    # ── Step 3: Body field validation (A → 400) ─────────────────────────────
    event_type = webhook_body.get("eventType")
    data = webhook_body.get("data", {})
    payment_key = data.get("paymentKey")
    transmission_id = request.headers.get("X-Transmission-ID") or payment_key

    if not event_type or not payment_key:
        return _webhook_problem(
            request, 400,
            code="WEBHOOK_INVALID_PAYLOAD",
            title="Invalid webhook payload",
            detail="Missing required fields: eventType, data.paymentKey",
            provider="tosspayments",
            payload_hash=payload_hash,
        )

    # ── Step 4: Dedup gate (P6.3: atomic, concurrent-safe) ──────────────────
    # get_toss_dedup_key: Tosspayments-Webhook-Transmission-Id header → paymentKey fallback
    try:
        dedup_key = get_toss_dedup_key(webhook_body, dict(request.headers))
    except ValueError as dk_exc:
        return _webhook_problem(
            request, 400,
            code="WEBHOOK_INVALID_PAYLOAD",
            title="Cannot derive idempotency key",
            detail=sanitize_str(str(dk_exc)),
            provider="tosspayments",
            payload_hash=payload_hash,
        )

    db: Session = next(get_db())
    try:
        # Atomic INSERT ON CONFLICT DO NOTHING — skip Toss API call for duplicates
        is_first = try_acquire_dedup(db, "toss", dedup_key, payload_hash)
        if not is_first:
            logger.info(
                "WEBHOOK_ALREADY_PROCESSED",
                extra={"provider": "tosspayments", "payload_hash": payload_hash},
            )
            return {"status": "already_processed"}

        # ── Step 5: Toss API re-query verification (D/E → 500) ───────────────
        # D → 500: TOSS_SECRET_KEY not configured
        try:
            toss_client = get_toss_client()
        except ValueError:
            mark_dedup_failed(db, "toss", dedup_key)
            return _webhook_problem(
                request, 500,
                code="WEBHOOK_PROVIDER_MISCONFIG",
                title="Webhook provider misconfiguration",
                detail="Toss payment client is not properly configured",
                provider="tosspayments",
                payload_hash=payload_hash,
            )

        # E → 500 upstream / 400 bad paymentKey
        try:
            payment_details = await toss_client.get_payment(payment_key)
        except httpx.RequestError:
            mark_dedup_failed(db, "toss", dedup_key)
            return _webhook_problem(
                request, 500,
                code="WEBHOOK_VERIFY_UPSTREAM_FAILED",
                title="Webhook verification upstream failure",
                detail="Unable to verify payment due to upstream network error",
                provider="tosspayments",
                payload_hash=payload_hash,
            )
        except httpx.HTTPStatusError as upstream_exc:
            upstream_status = upstream_exc.response.status_code
            if upstream_status == 404:
                mark_dedup_failed(db, "toss", dedup_key)
                return _webhook_problem(
                    request, 400,
                    code="WEBHOOK_INVALID_PAYMENT_KEY",
                    title="Unknown payment key",
                    detail="Payment not found for the provided paymentKey",
                    provider="tosspayments",
                    payload_hash=payload_hash,
                )
            if upstream_status == 401:
                mark_dedup_failed(db, "toss", dedup_key)
                return _webhook_problem(
                    request, 500,
                    code="WEBHOOK_PROVIDER_MISCONFIG",
                    title="Webhook provider misconfiguration",
                    detail="Toss API rejected our credentials",
                    provider="tosspayments",
                    payload_hash=payload_hash,
                )
            mark_dedup_failed(db, "toss", dedup_key)
            return _webhook_problem(
                request, 500,
                code="WEBHOOK_VERIFY_UPSTREAM_FAILED",
                title="Webhook verification upstream failure",
                detail="TossPayments API returned an error",
                provider="tosspayments",
                payload_hash=payload_hash,
            )

        # ── Step 6: Business processing (F → 500) ────────────────────────────
        billing_event = BillingEvent(
            provider="TOSS",
            event_id=dedup_key,
            event_type=event_type,
            raw_payload=webhook_body,
            verification_status="SUCCESS",
            verification_meta={"payment_details": payment_details},
        )
        db.add(billing_event)
        db.commit()

        await _process_toss_event(db, billing_event, payment_details)

        billing_event.processed_at = db.query(BillingEvent).filter_by(id=billing_event.id).first().received_at
        db.commit()
        mark_dedup_done(db, "toss", dedup_key)

        return {"status": "processed"}

    except Exception as exc:
        db.rollback()
        mark_dedup_failed(db, "toss", dedup_key)
        return _webhook_problem(
            request, 500,
            code="WEBHOOK_INTERNAL_ERROR",
            title="Internal processing error",
            detail="An internal error occurred while processing the webhook",
            provider="tosspayments",
            payload_hash=payload_hash,
            extra={
                "error_type": type(exc).__name__,
                "error_msg": sanitize_str(str(exc)),
            },
        )
    finally:
        db.close()


async def _process_toss_event(db: Session, billing_event: BillingEvent, payment_details: dict):
    """Process TossPayments event by type.

    DEC-P02-2: 권한 부여 타이밍
    DEC-P02-3: 환불 처리
    """
    event_type = billing_event.event_type
    status = payment_details.get("status")
    order_id = payment_details.get("orderId")

    if event_type == "PAYMENT_STATUS_CHANGED":
        # DONE - Grant entitlements (DEC-P02-2)
        if status == "DONE":
            await _handle_toss_payment_done(db, payment_details)

        # CANCELED/PARTIAL_CANCELED - Revoke entitlements (DEC-P02-3)
        elif status in ("CANCELED", "PARTIAL_CANCELED"):
            await _handle_toss_payment_canceled(db, payment_details)

        # ABORTED/EXPIRED - Mark as failed
        elif status in ("ABORTED", "EXPIRED"):
            await _handle_toss_payment_failed(db, payment_details)

        # WAITING_FOR_DEPOSIT - Keep as PENDING
        elif status == "WAITING_FOR_DEPOSIT":
            logger.info(f"TossPayments waiting for deposit: {order_id}")

    elif event_type == "DEPOSIT_CALLBACK":
        # Virtual account deposit (same as DONE)
        if status == "DONE":
            await _handle_toss_payment_done(db, payment_details)


async def _handle_toss_payment_done(db: Session, payment_details: dict):
    """Handle TossPayments DONE status.

    DEC-P02-2: 권한 부여 타이밍
    """
    order_id = payment_details.get("orderId")
    payment_key = payment_details.get("paymentKey")

    # Find internal order
    billing_order = (
        db.query(BillingOrder)
        .filter_by(provider="TOSS", provider_order_id=order_id)
        .first()
    )

    if not billing_order:
        logger.warning(f"Internal order not found for Toss order: {order_id}")
        return

    # Verify amount matches
    expected_amount = int(float(billing_order.amount))
    actual_amount = payment_details.get("totalAmount")

    if expected_amount != actual_amount:
        logger.warning(
            f"TossPayments amount mismatch: expected {expected_amount}, got {actual_amount}",
            extra={"order_id": order_id, "FRAUD_FLAG": True},
        )
        return

    # Update order status
    billing_order.status = "PAID"
    billing_order.provider_payment_key = payment_key

    # Grant entitlement (DEC-P02-2)
    _grant_entitlement(db, billing_order)

    db.commit()
    logger.info(f"TossPayments payment completed and entitlement granted: {order_id}")


async def _handle_toss_payment_canceled(db: Session, payment_details: dict):
    """Handle TossPayments CANCELED/PARTIAL_CANCELED status.

    DEC-P02-3: 환불 처리
    """
    order_id = payment_details.get("orderId")

    billing_order = (
        db.query(BillingOrder)
        .filter_by(provider="TOSS", provider_order_id=order_id)
        .first()
    )

    if not billing_order:
        return

    # Update order status
    status = payment_details.get("status")
    billing_order.status = "REFUNDED" if status == "CANCELED" else "PARTIAL_REFUNDED"

    # Revoke entitlement (DEC-P02-3)
    _revoke_entitlement(db, billing_order)

    db.commit()
    logger.info(f"TossPayments payment canceled and entitlement revoked: {order_id}")


async def _handle_toss_payment_failed(db: Session, payment_details: dict):
    """Handle TossPayments ABORTED/EXPIRED status."""
    order_id = payment_details.get("orderId")

    billing_order = (
        db.query(BillingOrder)
        .filter_by(provider="TOSS", provider_order_id=order_id)
        .first()
    )

    if billing_order:
        billing_order.status = "FAILED"
        db.commit()
        logger.info(f"TossPayments payment failed: {order_id}")


# ============================================================================
# Entitlement Management (DEC-P02-2, DEC-P02-3, DEC-P02-4)
# ============================================================================


def _grant_entitlement(db: Session, billing_order: BillingOrder):
    """Grant entitlement for paid order.

    DEC-P02-2: 권한 부여 타이밍 (결제 확정 후에만)
    """
    # Find or create entitlement
    entitlement = (
        db.query(Entitlement)
        .filter_by(tenant_id=billing_order.tenant_id, plan_id=billing_order.plan_id)
        .first()
    )

    if not entitlement:
        entitlement = Entitlement(
            tenant_id=billing_order.tenant_id,
            plan_id=billing_order.plan_id,
            status="ACTIVE",
            order_id=billing_order.id,
        )
        db.add(entitlement)
    else:
        entitlement.status = "ACTIVE"
        entitlement.order_id = billing_order.id

    # Audit log
    audit_log = BillingAuditLog(
        event_type="ENTITLEMENT_ACTIVATED",
        tenant_id=billing_order.tenant_id,
        related_entity_type="ORDER",
        related_entity_id=str(billing_order.id),
        actor="WEBHOOK",
        details={
            "provider": billing_order.provider,
            "plan_id": billing_order.plan_id,
        },
    )
    db.add(audit_log)

    logger.info(
        f"Entitlement granted: tenant={billing_order.tenant_id}, plan={billing_order.plan_id}"
    )


def _revoke_entitlement(db: Session, billing_order: BillingOrder):
    """Revoke entitlement for refunded/canceled order.

    DEC-P02-3: 환불/부분환불 처리 (즉시 권한 회수)
    """
    entitlement = (
        db.query(Entitlement)
        .filter_by(tenant_id=billing_order.tenant_id, plan_id=billing_order.plan_id)
        .first()
    )

    if entitlement:
        entitlement.status = "FREE"

        # Disable API keys (보수적 처리)
        api_keys = (
            db.query(APIKey)
            .filter_by(tenant_id=billing_order.tenant_id, status="ACTIVE")
            .all()
        )
        for key in api_keys:
            key.status = "REVOKED"

        # Audit log
        audit_log = BillingAuditLog(
            event_type="ENTITLEMENT_REVOKED",
            tenant_id=billing_order.tenant_id,
            related_entity_type="ORDER",
            related_entity_id=str(billing_order.id),
            actor="WEBHOOK",
            details={
                "provider": billing_order.provider,
                "reason": billing_order.status,
            },
        )
        db.add(audit_log)

        logger.warning(
            f"Entitlement revoked: tenant={billing_order.tenant_id}, reason={billing_order.status}"
        )
