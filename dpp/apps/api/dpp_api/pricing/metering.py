"""
Idempotent Metering Service
Key: (workspace_id, run_id)
Retention: 45 days
"""

from pydantic import BaseModel, Field
from datetime import datetime
from typing import Literal, Optional
from redis import Redis
from .models import PricingSSoTModel


class MeteringEvent(BaseModel):
    """Metering event payload"""
    event_name: str = "decisionproof.dc"
    workspace_id: str
    run_id: str  # Idempotency key
    dc_amount: int
    occurred_at: datetime
    http_status: int
    billable: bool
    metadata: dict = Field(default_factory=dict)  # P0 Hotfix: mutable default fix


class MeteringResult(BaseModel):
    """Metering operation result"""
    event_id: str
    deduplication_status: Literal["new", "duplicate"]
    dc_charged: int
    workspace_remaining_dc: int


class MeteringService:
    """
    Idempotent metering service
    
    Key: (workspace_id, run_id)
    Retention: 45 days
    """

    def __init__(self, ssot: PricingSSoTModel, redis: Redis):
        """Constructor with ssot-first argument order"""
        self.ssot = ssot
        self.redis = redis

    def record_usage(
        self,
        workspace_id: str,
        run_id: str,
        dc_amount: int,
        http_status: int,
        occurred_at: datetime,
        tier_monthly_quota: int = 0
    ) -> MeteringResult:
        """
        Record usage with idempotency (P0-4: Atomic SET NX EX)

        Args:
            workspace_id: Workspace ID
            run_id: Run ID (idempotency key)
            dc_amount: DC amount consumed
            http_status: HTTP response status
            occurred_at: Timestamp of the request (UTC)
            tier_monthly_quota: Workspace tier monthly quota

        Returns:
            MeteringResult with deduplication status
        """

        # Derive current_month from occurred_at (UTC)
        current_month = occurred_at.strftime("%Y-%m")

        # 1. Check billability
        billable = self._is_billable(http_status)

        # 2. Atomic idempotency check (P0-4: SET NX EX pattern, TOCTOU-safe)
        idempotency_key = self._generate_idempotency_key(workspace_id, run_id)
        retention_seconds = self.ssot.meter.idempotency_retention_days * 86400

        # SET NX EX: atomic "set if not exists" with expiration
        was_set = self.redis.set(idempotency_key, "1", nx=True, ex=retention_seconds)

        if not was_set:
            # Duplicate - key already exists, do NOT charge
            return MeteringResult(
                event_id=run_id,
                deduplication_status="duplicate",
                dc_charged=0,
                workspace_remaining_dc=self._get_remaining_dc(
                    workspace_id, current_month, tier_monthly_quota
                )
            )

        # 4. Charge DC (if billable)
        dc_charged = 0
        if billable:
            usage_key = f"usage:{workspace_id}:{current_month}"
            self.redis.incrby(usage_key, dc_amount)
            dc_charged = dc_amount

            # Set TTL to end of next month (90 days)
            self.redis.expire(usage_key, 90 * 86400)

        # 5. Log metering event to Database (immutable log)
        # TODO: Implement database logging
        # self._log_metering_event(workspace_id, run_id, dc_amount, http_status, billable)

        return MeteringResult(
            event_id=run_id,
            deduplication_status="new",
            dc_charged=dc_charged,
            workspace_remaining_dc=self._get_remaining_dc(
                workspace_id, current_month, tier_monthly_quota
            )
        )

    def _is_billable(self, http_status: int) -> bool:
        """
        Check if HTTP status is billable

        Billable: 2xx, 422
        Non-billable: 400/401/403/404/409/412/413/415/429, 5xx

        P0 Hotfix: Safe defaults - 2xx and 422 default to billable (True) to prevent revenue loss
        """

        # Success (2xx) - P0 Hotfix: default True (billable if config missing)
        if 200 <= http_status < 300:
            return self.ssot.billing_rules.billable.get("success", True)

        # 422 Unprocessable Entity - P0 Hotfix: default True (billable if config missing)
        if http_status == 422:
            return self.ssot.billing_rules.billable.get("http_422", True)

        # Non-billable statuses
        non_billable_map = {
            400: "http_400",
            401: "http_401",
            403: "http_403",
            404: "http_404",
            409: "http_409",
            412: "http_412",
            413: "http_413",
            415: "http_415",
            429: "http_429",
        }

        if http_status in non_billable_map:
            return not self.ssot.billing_rules.non_billable.get(
                non_billable_map[http_status], True  # P0 Hotfix: default True (non-billable if config missing, safe default)
            )

        # 5xx
        if 500 <= http_status < 600:
            return not self.ssot.billing_rules.non_billable.get("http_5xx", True)  # P0 Hotfix: default True (non-billable if config missing, safe default)

        # Default: non-billable
        return False

    def _generate_idempotency_key(self, workspace_id: str, run_id: str) -> str:
        """Generate Redis idempotency key"""
        return f"idempotency:{workspace_id}:{run_id}"

    def _get_remaining_dc(
        self,
        workspace_id: str,
        current_month: str,
        tier_monthly_quota: int
    ) -> int:
        """Get remaining DC for workspace"""
        usage_key = f"usage:{workspace_id}:{current_month}"
        current_usage = int(self.redis.get(usage_key) or 0)
        remaining = max(0, tier_monthly_quota - current_usage)
        return remaining
