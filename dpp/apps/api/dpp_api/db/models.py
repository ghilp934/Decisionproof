"""SQLAlchemy ORM Models for DPP."""

from datetime import date, datetime, timezone
from typing import Optional

from sqlalchemy import BIGINT, DATE, FLOAT, JSON, TEXT, TIMESTAMP, UUID, Index, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Base class for all ORM models."""

    pass


class Tenant(Base):
    """Tenant model for multi-tenancy."""

    __tablename__ = "tenants"

    tenant_id: Mapped[str] = mapped_column(TEXT, primary_key=True)
    display_name: Mapped[str] = mapped_column(TEXT, nullable=False)
    status: Mapped[str] = mapped_column(TEXT, nullable=False, default="ACTIVE")
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )


class APIKey(Base):
    """API Key model for authentication."""

    __tablename__ = "api_keys"

    key_id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(
        TEXT, nullable=False
    )  # FK to tenants
    key_hash: Mapped[str] = mapped_column(TEXT, nullable=False)
    label: Mapped[Optional[str]] = mapped_column(TEXT, nullable=True)
    status: Mapped[str] = mapped_column(TEXT, nullable=False, default="ACTIVE")
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    last_used_at: Mapped[Optional[datetime]] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )

    __table_args__ = (Index("idx_api_keys_tenant", "tenant_id"),)


class Run(Base):
    """Run model - authoritative state for async executions."""

    __tablename__ = "runs"

    run_id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(
        TEXT, nullable=False
    )  # FK to tenants

    pack_type: Mapped[str] = mapped_column(TEXT, nullable=False)
    profile_version: Mapped[str] = mapped_column(TEXT, nullable=False, default="v0.4.2.2")

    # Execution state
    status: Mapped[str] = mapped_column(TEXT, nullable=False)  # QUEUED/PROCESSING/etc
    money_state: Mapped[str] = mapped_column(TEXT, nullable=False)  # NONE/RESERVED/etc

    # Idempotency
    idempotency_key: Mapped[Optional[str]] = mapped_column(TEXT, nullable=True)
    payload_hash: Mapped[str] = mapped_column(TEXT, nullable=False)

    # DEC-4210: Optimistic locking
    version: Mapped[int] = mapped_column(BIGINT, nullable=False, default=0)

    # DEC-4211: Money in USD_MICROS (BIGINT)
    reservation_max_cost_usd_micros: Mapped[int] = mapped_column(BIGINT, nullable=False)
    actual_cost_usd_micros: Mapped[Optional[int]] = mapped_column(BIGINT, nullable=True)
    minimum_fee_usd_micros: Mapped[int] = mapped_column(BIGINT, nullable=False, default=0)

    # P1-7: Reservation parameters and inputs
    timebox_sec: Mapped[Optional[int]] = mapped_column(BIGINT, nullable=True)
    min_reliability_score: Mapped[Optional[float]] = mapped_column(FLOAT, nullable=True)
    inputs_json: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)

    # Result persistence
    result_bucket: Mapped[Optional[str]] = mapped_column(TEXT, nullable=True)
    result_key: Mapped[Optional[str]] = mapped_column(TEXT, nullable=True)
    result_sha256: Mapped[Optional[str]] = mapped_column(TEXT, nullable=True)
    retention_until: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False
    )

    # Lease management (zombie protection)
    lease_token: Mapped[Optional[str]] = mapped_column(TEXT, nullable=True)
    lease_expires_at: Mapped[Optional[datetime]] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )

    # Finalize stage (2-phase commit)
    finalize_token: Mapped[Optional[str]] = mapped_column(TEXT, nullable=True)
    finalize_stage: Mapped[Optional[str]] = mapped_column(TEXT, nullable=True)
    finalize_claimed_at: Mapped[Optional[datetime]] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )

    # P1-10: Completion timestamp
    completed_at: Mapped[Optional[datetime]] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )

    # Error tracking
    last_error_reason_code: Mapped[Optional[str]] = mapped_column(TEXT, nullable=True)
    last_error_detail: Mapped[Optional[str]] = mapped_column(TEXT, nullable=True)

    # Observability
    trace_id: Mapped[Optional[str]] = mapped_column(TEXT, nullable=True)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (
        Index("idx_runs_tenant_created", "tenant_id", "created_at"),
        Index("idx_runs_status_lease", "status", "lease_expires_at"),
        # P0-B: Prevent duplicate idempotency_key per tenant (INT-01, DEC-4201)
        # Note: UniqueConstraint already creates an index, so idx_runs_idem is redundant
        UniqueConstraint("tenant_id", "idempotency_key", name="uq_runs_tenant_idempotency"),
    )


class Plan(Base):
    """Plan model for API monetization tiers/products.

    Defines rate limits, allowed pack types, and cost constraints per plan.
    """

    __tablename__ = "plans"

    plan_id: Mapped[str] = mapped_column(TEXT, primary_key=True)
    name: Mapped[str] = mapped_column(TEXT, nullable=False)
    status: Mapped[str] = mapped_column(TEXT, nullable=False, default="ACTIVE")

    # Default profile version for this plan
    default_profile_version: Mapped[str] = mapped_column(TEXT, nullable=False, default="v0.4.2.2")

    # Features and limits (JSON fields)
    # features_json: {"allowed_pack_types": ["decision", "url"], "max_concurrent_runs": 10}
    features_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)

    # limits_json: {
    #   "rate_limit_post_per_min": 60,
    #   "rate_limit_poll_per_min": 300,
    #   "pack_type_limits": {"decision": {"max_cost_usd_micros": 1000000}}
    # }
    limits_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)

    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )


class TenantPlan(Base):
    """TenantPlan model - maps tenants to their active plan.

    A tenant has exactly one active plan at any time.
    Audit trail for plan changes.
    """

    __tablename__ = "tenant_plans"

    # P0-A: Use BIGINT for autoincrement IDs (production scale)
    id: Mapped[int] = mapped_column(BIGINT, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(TEXT, nullable=False)
    plan_id: Mapped[str] = mapped_column(TEXT, nullable=False)

    status: Mapped[str] = mapped_column(TEXT, nullable=False, default="ACTIVE")

    effective_from: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    effective_to: Mapped[Optional[datetime]] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )

    # Audit fields
    changed_by: Mapped[Optional[str]] = mapped_column(TEXT, nullable=True)
    change_reason: Mapped[Optional[str]] = mapped_column(TEXT, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (
        Index("idx_tenant_plans_tenant_status", "tenant_id", "status"),
        Index("idx_tenant_plans_effective", "tenant_id", "effective_from", "effective_to"),
    )


class TenantUsageDaily(Base):
    """TenantUsageDaily model - daily rollup of usage metrics per tenant.

    Usage metering for monetization analytics.
    Source of truth: RunRecord (no PII, only metadata).
    """

    __tablename__ = "tenant_usage_daily"

    # P0-A: Use BIGINT for autoincrement IDs (production scale)
    id: Mapped[int] = mapped_column(BIGINT, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(TEXT, nullable=False)
    usage_date: Mapped[date] = mapped_column(DATE, nullable=False)

    # Counts
    runs_count: Mapped[int] = mapped_column(BIGINT, nullable=False, default=0)
    success_count: Mapped[int] = mapped_column(BIGINT, nullable=False, default=0)
    fail_count: Mapped[int] = mapped_column(BIGINT, nullable=False, default=0)

    # Costs (DEC-4211: USD_MICROS only)
    cost_usd_micros_sum: Mapped[int] = mapped_column(BIGINT, nullable=False, default=0)
    reserved_usd_micros_sum: Mapped[int] = mapped_column(BIGINT, nullable=False, default=0)

    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (
        Index("idx_tenant_usage_daily_tenant_date", "tenant_id", "usage_date", unique=True),
    )


# ============================================================================
# P0-2: Billing Models (PayPal + TossPayments)
# ============================================================================


class BillingOrder(Base):
    """BillingOrder model - payment orders from PayPal or TossPayments.

    DEC-P02-1: Provider 이원화 (PAYPAL, TOSS)
    DEC-P02-6: (provider, provider_order_id) unique constraint
    """

    __tablename__ = "billing_orders"

    id: Mapped[int] = mapped_column(BIGINT, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(TEXT, nullable=False)  # FK to tenants

    # Provider identification
    provider: Mapped[str] = mapped_column(TEXT, nullable=False)  # PAYPAL, TOSS
    provider_order_id: Mapped[str] = mapped_column(TEXT, nullable=False)  # External order ID
    provider_capture_id: Mapped[Optional[str]] = mapped_column(TEXT, nullable=True)  # PayPal capture ID
    provider_payment_key: Mapped[Optional[str]] = mapped_column(TEXT, nullable=True)  # Toss paymentKey

    # Order details
    plan_id: Mapped[str] = mapped_column(TEXT, nullable=False)
    currency: Mapped[str] = mapped_column(TEXT, nullable=False, default="USD")  # USD, KRW
    amount: Mapped[str] = mapped_column(TEXT, nullable=False)  # Decimal string for precision

    # Status tracking
    status: Mapped[str] = mapped_column(TEXT, nullable=False, default="PENDING")
    # PENDING, PAID, FAILED, REFUNDED, CANCELLED, PARTIAL_REFUNDED

    # Order metadata (renamed from 'metadata' to avoid SQLAlchemy reserved keyword)
    order_metadata: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (
        # DEC-P02-6: Idempotency - unique constraint per provider
        UniqueConstraint("provider", "provider_order_id", name="uq_billing_orders_provider_order"),
        Index("idx_billing_orders_tenant", "tenant_id"),
        Index("idx_billing_orders_status", "status"),
    )


class BillingEvent(Base):
    """BillingEvent model - webhook events from PayPal or TossPayments.

    DEC-P02-5: Webhook 검증 정책
    DEC-P02-6: (provider, event_id) unique constraint for idempotency
    """

    __tablename__ = "billing_events"

    id: Mapped[int] = mapped_column(BIGINT, primary_key=True, autoincrement=True)

    # Provider identification
    provider: Mapped[str] = mapped_column(TEXT, nullable=False)  # PAYPAL, TOSS
    event_id: Mapped[str] = mapped_column(TEXT, nullable=False)  # External event ID
    event_type: Mapped[str] = mapped_column(TEXT, nullable=False)  # PAYMENT.CAPTURE.COMPLETED, etc.

    # Related order (nullable - not all events have orders)
    order_id: Mapped[Optional[int]] = mapped_column(BIGINT, nullable=True)  # FK to billing_orders

    # Event payload
    raw_payload: Mapped[dict] = mapped_column(JSON, nullable=False)

    # Processing status
    received_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    processed_at: Mapped[Optional[datetime]] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )

    # Verification
    verification_status: Mapped[str] = mapped_column(TEXT, nullable=False)
    # SUCCESS, FAILED, PENDING, FRAUD
    verification_meta: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)

    __table_args__ = (
        # DEC-P02-6: Idempotency - unique constraint per provider
        UniqueConstraint("provider", "event_id", name="uq_billing_events_provider_event"),
        Index("idx_billing_events_order", "order_id"),
        Index("idx_billing_events_received", "received_at"),
    )


class WebhookDedupEvent(Base):
    """Webhook dedup gate table for concurrent idempotency (P6.3).

    Guarantees at most one business-processing per (provider, dedup_key) pair
    even under concurrent delivery (PayPal/Toss retry storms).

    Atomic gate: INSERT ON CONFLICT (provider, dedup_key) DO NOTHING RETURNING id
      → row returned  : first/re-processing handler → continue
      → no row        : duplicate/concurrent → 200 immediately (zero side effects)
    """

    __tablename__ = "webhook_dedup_events"

    id: Mapped[int] = mapped_column(BIGINT, primary_key=True, autoincrement=True)

    provider: Mapped[str] = mapped_column(TEXT, nullable=False)     # paypal | toss
    dedup_key: Mapped[str] = mapped_column(TEXT, nullable=False)    # ev_<event_id> | tx_<tid> | pkey_<key>

    first_seen_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    last_seen_at: Mapped[Optional[datetime]] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )
    status: Mapped[str] = mapped_column(
        TEXT, nullable=False, default="processing"
    )  # processing | done | failed

    # SHA-256 hex of request body (never raw payload — only hash for audit)
    request_hash: Mapped[Optional[str]] = mapped_column(TEXT, nullable=True)

    __table_args__ = (
        UniqueConstraint("provider", "dedup_key", name="uq_webhook_dedup_events"),
        Index("idx_webhook_dedup_provider_key", "provider", "dedup_key"),
        Index("idx_webhook_dedup_status", "status"),
        Index("idx_webhook_dedup_first_seen", "first_seen_at"),
    )


class Entitlement(Base):
    """Entitlement model - tenant's plan entitlements and status.

    DEC-P02-2: 권한 부여 타이밍
    DEC-P02-3: 환불/부분환불 처리
    DEC-P02-4: 분쟁 처리
    """

    __tablename__ = "entitlements"

    id: Mapped[int] = mapped_column(BIGINT, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(TEXT, nullable=False)  # FK to tenants
    plan_id: Mapped[str] = mapped_column(TEXT, nullable=False)  # FK to plans

    # Status tracking
    status: Mapped[str] = mapped_column(TEXT, nullable=False, default="FREE")
    # FREE, ACTIVE, SUSPENDED

    # Validity period
    valid_from: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    valid_until: Mapped[Optional[datetime]] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )

    # Audit
    order_id: Mapped[Optional[int]] = mapped_column(BIGINT, nullable=True)  # FK to billing_orders
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (
        Index("idx_entitlements_tenant", "tenant_id"),
        Index("idx_entitlements_status", "status"),
    )


class BillingAuditLog(Base):
    """BillingAuditLog model - audit trail for payment and entitlement changes.

    DEC-P02-4: 분쟁/환불 감사 로그
    """

    __tablename__ = "billing_audit_logs"

    id: Mapped[int] = mapped_column(BIGINT, primary_key=True, autoincrement=True)

    # Event identification
    event_type: Mapped[str] = mapped_column(TEXT, nullable=False)
    # PAYMENT_COMPLETED, PAYMENT_REFUNDED, ENTITLEMENT_ACTIVATED, ENTITLEMENT_SUSPENDED, etc.

    # Related entities
    tenant_id: Mapped[Optional[str]] = mapped_column(TEXT, nullable=True)
    related_entity_type: Mapped[Optional[str]] = mapped_column(TEXT, nullable=True)
    # ORDER, ENTITLEMENT, API_KEY
    related_entity_id: Mapped[Optional[str]] = mapped_column(TEXT, nullable=True)

    # Actor and details
    actor: Mapped[Optional[str]] = mapped_column(TEXT, nullable=True)  # SYSTEM, ADMIN, WEBHOOK
    details: Mapped[dict] = mapped_column(JSON, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (
        Index("idx_billing_audit_tenant", "tenant_id"),
        Index("idx_billing_audit_created", "created_at"),
    )


# ============================================================================
# P0-3: Token Lifecycle Models
# ============================================================================


class APIToken(Base):
    """APIToken model - opaque Bearer tokens with rotation and revocation.

    P0-3: Production-ready token management with HMAC-SHA256 hashing.
    """

    __tablename__ = "api_tokens"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(TEXT, nullable=False)  # FK to tenants

    # Token identification
    name: Mapped[str] = mapped_column(TEXT, nullable=False)
    token_hash: Mapped[str] = mapped_column(TEXT, nullable=False, unique=True)
    prefix: Mapped[str] = mapped_column(TEXT, nullable=False)  # dp_live, dp_test
    last4: Mapped[str] = mapped_column(TEXT, nullable=False)

    # Authorization
    scopes: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)

    # Lifecycle state
    status: Mapped[str] = mapped_column(TEXT, nullable=False)
    # active | rotating | revoked | expired

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    expires_at: Mapped[Optional[datetime]] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )
    revoked_at: Mapped[Optional[datetime]] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )
    last_used_at: Mapped[Optional[datetime]] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )

    # Security versioning
    pepper_version: Mapped[int] = mapped_column(BIGINT, nullable=False, default=1)

    # Metadata
    created_by_user_id: Mapped[Optional[str]] = mapped_column(TEXT, nullable=True)
    user_agent: Mapped[Optional[str]] = mapped_column(TEXT, nullable=True)
    ip_address: Mapped[Optional[str]] = mapped_column(TEXT, nullable=True)

    __table_args__ = (
        Index("idx_api_tokens_tenant_status", "tenant_id", "status"),
        Index("idx_api_tokens_token_hash", "token_hash"),
        Index("idx_api_tokens_expires_at", "expires_at"),
    )


class TokenEvent(Base):
    """TokenEvent model - audit trail for token lifecycle events.

    P0-3: Security and compliance logging.
    """

    __tablename__ = "token_events"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(TEXT, nullable=False)  # FK to tenants
    token_id: Mapped[Optional[str]] = mapped_column(
        UUID(as_uuid=False), nullable=True
    )  # NULL for revoke_all

    # Actor
    actor_user_id: Mapped[Optional[str]] = mapped_column(TEXT, nullable=True)

    # Event details
    event_type: Mapped[str] = mapped_column(TEXT, nullable=False)
    # issued | rotated | revoked | revoke_all | compromised_flagged | expired

    # Event metadata (minimal, no secrets)
    event_meta: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (
        Index("idx_token_events_tenant", "tenant_id"),
        Index("idx_token_events_token_id", "token_id"),
        Index("idx_token_events_created_at", "created_at"),
    )


class AuthRequestLog(Base):
    """AuthRequestLog model - security telemetry for API token authentication.

    P0-3: Privacy-preserving observability (hashed IP/UA).
    """

    __tablename__ = "auth_request_log"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True)
    token_id: Mapped[Optional[str]] = mapped_column(UUID(as_uuid=False), nullable=True)
    tenant_id: Mapped[Optional[str]] = mapped_column(TEXT, nullable=True)

    # Request details
    route: Mapped[str] = mapped_column(TEXT, nullable=False)
    method: Mapped[str] = mapped_column(TEXT, nullable=False)
    status_code: Mapped[int] = mapped_column(BIGINT, nullable=False)

    # Security hashes (privacy-preserving)
    ip_hash: Mapped[Optional[str]] = mapped_column(TEXT, nullable=True)
    ua_hash: Mapped[Optional[str]] = mapped_column(TEXT, nullable=True)

    # Observability
    trace_id: Mapped[Optional[str]] = mapped_column(TEXT, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (
        Index("idx_auth_request_log_token_id", "token_id"),
        Index("idx_auth_request_log_created_at", "created_at"),
        Index("idx_auth_request_log_status_code", "status_code"),
    )


class UserTenant(Base):
    """UserTenant model - maps Supabase auth.users to application tenants.

    Session auth integration: user_id (Supabase) -> tenant_id (application).
    """

    __tablename__ = "user_tenants"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True)
    user_id: Mapped[str] = mapped_column(UUID(as_uuid=False), nullable=False)  # Supabase auth.users
    tenant_id: Mapped[str] = mapped_column(TEXT, nullable=False)  # FK to tenants

    # Role within tenant (RBAC)
    role: Mapped[str] = mapped_column(TEXT, nullable=False, default="member")
    # owner | admin | member | viewer

    # Status
    status: Mapped[str] = mapped_column(TEXT, nullable=False, default="active")
    # active | inactive | suspended

    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (
        UniqueConstraint("user_id", "tenant_id", name="uq_user_tenants_user_tenant"),
        Index("idx_user_tenants_user_id", "user_id"),
        Index("idx_user_tenants_tenant_id", "tenant_id"),
        Index("idx_user_tenants_user_status", "user_id", "status"),
    )
