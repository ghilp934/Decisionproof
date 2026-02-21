"""Observability metrics helpers for P0-1 Paid Pilot.

P0-1: Scorecard Metrics Instrumentation

Usage:
    from dpp_api.observability.metrics import log_payment_attempt, log_payment_success

    # Payment metrics
    log_payment_attempt(tenant_id="t_123", amount_usd="10.00", status="pending")
    log_payment_success(tenant_id="t_123", amount_usd="10.00")

    # Rate limit metrics
    log_rate_limit_exceeded(tenant_id="t_123", key_id="k_abc123def456")

    # Security metrics
    log_key_leak_suspected(key_id="k_abc123def456", unique_ip_count=25)

Security:
- API key values are NEVER logged
- key_id is hashed or truncated (first 8 chars only)
- Full tenant_id is logged (internal identifier, not PII)
"""

import hashlib
import logging
from typing import Optional

logger = logging.getLogger(__name__)


# ============================================================================
# Security Helpers
# ============================================================================


def sanitize_key_id(key_id: str) -> str:
    """Sanitize key_id for logging (prevent full key exposure).

    Uses first 8 characters as prefix for readability.

    Args:
        key_id: Full key_id (e.g., "k_abc123def456ghi789")

    Returns:
        Sanitized key_id prefix (e.g., "k_abc123")
    """
    if not key_id:
        return "unknown"

    # Return first 8 chars (prefix for readability)
    return key_id[:8] if len(key_id) >= 8 else key_id


def hash_key_id(key_id: str) -> str:
    """Hash key_id for secure logging.

    Args:
        key_id: Full key_id

    Returns:
        SHA256 hash (first 16 chars)
    """
    if not key_id:
        return "unknown"

    # SHA256 hash, truncated to 16 chars
    return hashlib.sha256(key_id.encode()).hexdigest()[:16]


# ============================================================================
# Payment Metrics (SC-01, SC-02, SC-03)
# ============================================================================


def log_payment_attempt(
    tenant_id: str,
    amount_usd: str,
    status: str,
    payment_method: Optional[str] = None,
) -> None:
    """Log payment attempt for SC-01 (Payment Success Rate).

    Args:
        tenant_id: Tenant identifier
        amount_usd: Payment amount in USD (string for precision)
        status: Payment status (pending, processing, etc.)
        payment_method: Payment method (optional)
    """
    logger.info(
        "payment.attempt",
        extra={
            "event": "payment.attempt",
            "tenant_id": tenant_id,
            "amount_usd": amount_usd,
            "status": status,
            "payment_method": payment_method,
        },
    )


def log_payment_success(
    tenant_id: str,
    amount_usd: str,
    payment_method: Optional[str] = None,
) -> None:
    """Log successful payment for SC-01 (Payment Success Rate).

    Args:
        tenant_id: Tenant identifier
        amount_usd: Payment amount in USD (string for precision)
        payment_method: Payment method (optional)
    """
    logger.info(
        "payment.success",
        extra={
            "event": "payment.success",
            "tenant_id": tenant_id,
            "amount_usd": amount_usd,
            "payment_method": payment_method,
        },
    )


def log_payment_dispute(
    tenant_id: str,
    amount_usd: str,
    dispute_type: str,
    reason: Optional[str] = None,
) -> None:
    """Log payment dispute/chargeback for SC-02.

    SC-02 Trigger: ≥1 dispute → HARD_STOP

    Args:
        tenant_id: Tenant identifier
        amount_usd: Disputed amount in USD
        dispute_type: "dispute" or "chargeback"
        reason: Dispute reason (optional)
    """
    logger.warning(
        "payment.dispute",
        extra={
            "event": "payment.dispute",
            "tenant_id": tenant_id,
            "amount_usd": amount_usd,
            "dispute_type": dispute_type,
            "reason": reason,
        },
    )


def log_payment_refund(
    tenant_id: str,
    amount_usd: str,
    reason: Optional[str] = None,
) -> None:
    """Log payment refund for SC-03 (Refund Rate).

    Args:
        tenant_id: Tenant identifier
        amount_usd: Refund amount in USD
        reason: Refund reason (optional)
    """
    logger.info(
        "payment.refund",
        extra={
            "event": "payment.refund",
            "tenant_id": tenant_id,
            "amount_usd": amount_usd,
            "reason": reason,
        },
    )


# ============================================================================
# Request Metrics (SC-04, SC-05)
# ============================================================================

# Note: SC-04 (5xx rate) and SC-05 (p95 latency) are automatically logged
# by the existing http_completion_logging_middleware in main.py.
#
# Metrics are available via:
#   - event: "http.request.completed"
#   - fields: status_code, duration_ms, path, method
#
# No additional instrumentation needed.


# ============================================================================
# Rate Limit Metrics (SC-06)
# ============================================================================


def log_rate_limit_exceeded(
    tenant_id: str,
    key_id: str,
    path: Optional[str] = None,
) -> None:
    """Log rate limit exceeded (429) for SC-06.

    Args:
        tenant_id: Tenant identifier
        key_id: API key_id (will be sanitized)
        path: Request path (optional)
    """
    key_id_safe = sanitize_key_id(key_id)

    logger.warning(
        "rate_limit.exceeded",
        extra={
            "event": "rate_limit.exceeded",
            "tenant_id": tenant_id,
            "key_id_prefix": key_id_safe,
            "path": path,
        },
    )


# ============================================================================
# Security Metrics (SC-07)
# ============================================================================


def log_key_leak_suspected(
    key_id: str,
    unique_ip_count: int,
    sample_ips: Optional[list[str]] = None,
) -> None:
    """Log suspected API key leak for SC-07.

    SC-07 Trigger: ≥20 unique IPs per key_id in 24h → Revoke key + HARD_STOP

    Args:
        key_id: API key_id (will be hashed)
        unique_ip_count: Number of unique IPs in time window
        sample_ips: Sample IPs for investigation (optional, first 5)
    """
    key_id_hash = hash_key_id(key_id)

    logger.warning(
        "security.key_leak_suspected",
        extra={
            "event": "security.key_leak_suspected",
            "key_id_hash": key_id_hash,
            "unique_ip_count": unique_ip_count,
            "sample_ips": sample_ips[:5] if sample_ips else None,
        },
    )


def log_key_revoked(
    key_id: str,
    tenant_id: str,
    reason: str,
    revoked_by: str,
) -> None:
    """Log API key revocation.

    Args:
        key_id: API key_id (will be sanitized)
        tenant_id: Tenant identifier
        reason: Revocation reason
        revoked_by: Actor who revoked the key
    """
    key_id_safe = sanitize_key_id(key_id)

    logger.warning(
        "security.key_revoked",
        extra={
            "event": "security.key_revoked",
            "key_id_prefix": key_id_safe,
            "tenant_id": tenant_id,
            "reason": reason,
            "revoked_by": revoked_by,
        },
    )


# ============================================================================
# Support Ticket Metrics (SC-08)
# ============================================================================


def log_support_ticket_created(
    tenant_id: str,
    ticket_id: str,
    category: Optional[str] = None,
    priority: Optional[str] = None,
) -> None:
    """Log support ticket creation for SC-08.

    SC-08: Support Tickets / Paid Accounts ratio
    Target: ≤1.0, Redline: ≥3.0 → Stop new onboarding

    Note: Mark as OPEN if no ticketing system exists yet.

    Args:
        tenant_id: Tenant identifier
        ticket_id: Support ticket ID
        category: Ticket category (optional)
        priority: Ticket priority (optional)
    """
    logger.info(
        "support.ticket.created",
        extra={
            "event": "support.ticket.created",
            "tenant_id": tenant_id,
            "ticket_id": ticket_id,
            "category": category,
            "priority": priority,
        },
    )


# ============================================================================
# Utility: Metric Aggregation Helpers
# ============================================================================


def get_metric_status() -> dict:
    """Get current metric collection status.

    Returns:
        Dictionary with metric implementation status
    """
    return {
        "SC-01_payment_success_rate": "implemented",
        "SC-02_dispute_chargeback": "implemented",
        "SC-03_refund_rate": "implemented",
        "SC-04_5xx_rate": "auto_collected",  # Via http_completion_logging_middleware
        "SC-05_p95_latency": "auto_collected",  # Via http_completion_logging_middleware
        "SC-06_rate_limit_rate": "implemented",
        "SC-07_key_leak": "implemented",
        "SC-08_support_tickets": "placeholder",  # Mark as OPEN
        "log_format": "structured_json",
        "retention_days": 90,
    }
