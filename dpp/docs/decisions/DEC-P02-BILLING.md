# DEC-P02: Paid Pilot Billing Provider Migration

**Date**: 2026-02-18
**Status**: LOCKED ✅
**Category**: Payment Processing, Billing

---

## Decision Summary

Migrate from Stripe to dual-provider billing system (PayPal + TossPayments) for Paid Pilot, with conservative entitlement policies to prevent unauthorized access.

---

## DEC-P02-1: Provider 이원화

**Decision**: Use PayPal (global) and TossPayments (Korea) as payment providers.

**Rationale**:
- PayPal: Widely accepted internationally, strong buyer protection
- TossPayments: Dominant in Korean market, supports local payment methods

**Implementation**:
- `provider` enum: `PAYPAL`, `TOSS`
- Unique constraint: `(provider, provider_order_id)` per order
- Separate webhook endpoints: `/webhooks/paypal`, `/webhooks/tosspayments`

**Status**: LOCKED ✅

---

## DEC-P02-2: 권한 부여 타이밍 (가장 중요)

**Decision**: Grant paid entitlements ONLY after payment confirmation via webhook + re-query verification.

**Rules**:
1. **PayPal**: Grant on `PAYMENT.CAPTURE.COMPLETED` webhook AFTER:
   - Signature verification SUCCESS
   - PayPal API re-query confirms `status=COMPLETED`
   - Amount/currency match

2. **TossPayments**: Grant on `status=DONE` AFTER:
   - TossPayments API re-query confirms `status=DONE`
   - Amount/orderId match

3. **All other events**: Log only, NO entitlement changes

**Rationale**: Conservative approach prevents granting access based on potentially forged webhooks.

**Status**: LOCKED ✅

---

## DEC-P02-3: 환불/부분환불 처리

**Decision**: Immediately revoke entitlements on refund, conservatively.

**Rules**:
1. **PayPal**: On `PAYMENT.CAPTURE.REFUNDED` webhook + re-query OK:
   - Set entitlement `status=FREE`
   - Revoke all active API keys
   - Set remaining credits to 0

2. **TossPayments**: On `status in {CANCELED, PARTIAL_CANCELED}` + re-query OK:
   - Same as PayPal (conservative downgrade)

**Rationale**: Immediate action prevents continued usage after refund.

**Status**: LOCKED ✅

---

## DEC-P02-4: 분쟁(Dispute) 처리 (보수적 잠금)

**Decision**: Suspend account immediately on dispute, manual resolution only.

**Rules**:
1. **PayPal**: On `CUSTOMER.DISPUTE.CREATED` or `CUSTOMER.DISPUTE.UPDATED`:
   - Set entitlement `status=SUSPENDED`
   - Disable all API keys immediately

2. **PayPal**: On `CUSTOMER.DISPUTE.RESOLVED`:
   - **NO automatic restoration**
   - Remains `status=SUSPENDED`
   - Requires admin manual review and unlock

3. **TossPayments**: No separate dispute webhooks - rely on refund/cancel status

**Rationale**: Protect business from chargebacks, prevent abuse during dispute investigation.

**Status**: LOCKED ✅

---

## DEC-P02-5: Webhook 검증 정책

**Decision**: All webhooks MUST be verified before processing.

**Rules**:
1. **PayPal**:
   - Call `/v1/notifications/verify-webhook-signature`
   - Require `verification_status=SUCCESS`
   - On verification FAIL: Return 401 (not 2xx), no state changes

2. **TossPayments**:
   - No signature verification available
   - **ALWAYS re-query** payment via `/v1/payments/{paymentKey}`
   - Compare `orderId`, `amount`, `status` with internal order
   - On mismatch: Flag as FRAUD, no state changes

3. **Verification failure**: Log as `INVALID_WEBHOOK` or `FRAUD`, return 4xx

**Rationale**: Prevent webhook forgery attacks, ensure data integrity.

**Status**: LOCKED ✅

---

## DEC-P02-6: Idempotency / 중복 방어

**Decision**: All webhooks are idempotent via database unique constraints.

**Implementation**:
1. `billing_events` table unique constraint: `(provider, event_id)`
2. `billing_orders` table unique constraint: `(provider, provider_order_id)`
3. Webhook processing order:
   - Upsert `billing_events` (fails if duplicate)
   - If duplicate detected: Return `{"status": "already_processed"}` immediately
   - Process only if new event

4. All state changes are transactional (commit only if all succeed)

**Rationale**: Webhooks may be retried/duplicated - ensure exactly-once processing.

**Status**: LOCKED ✅

---

## Open Issues

None. All decisions are LOCKED for Paid Pilot.

---

## References

- PayPal Orders API v2: https://developer.paypal.com/docs/api/orders/v2/
- PayPal Webhooks: https://developer.paypal.com/api/rest/webhooks/
- TossPayments API: https://docs.tosspayments.com/reference
- Migration SQL: `migrations/20260218_create_billing_p0_2.sql`
- Implementation: `apps/api/dpp_api/billing/`, `apps/api/dpp_api/routers/webhooks.py`

---

**Decision Owner**: Engineering Lead
**Review Date**: Post Paid Pilot (Q2 2026)
