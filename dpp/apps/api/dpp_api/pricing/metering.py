"""
Idempotent Metering Service
Key: (workspace_id, run_id)
Retention: 45 days
"""

from pydantic import BaseModel
from datetime import datetime
from typing import Literal, Optional
from redis import Redis
from .models import PricingSSoTModel


class MeteringEvent(BaseModel):
    """Metering event payload"""
    event_name: str = "decisionwise.dc"
    workspace_id: str
    run_id: str  # Idempotency key
    dc_amount: int
    occurred_at: datetime
    http_status: int
    billable: bool
    metadata: dict = {}


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

    def __init__(self, redis: Redis, ssot: PricingSSoTModel):
        self.redis = redis
        self.ssot = ssot

    def record_usage(
        self,
        workspace_id: str,
        run_id: str,
        dc_amount: int,
        http_status: int,
        current_month: str,
        tier_monthly_quota: int = 0
    ) -> MeteringResult:
        """
        Record usage with idempotency
        
        Args:
            workspace_id: Workspace ID
            run_id: Run ID (idempotency key)
            dc_amount: DC amount consumed
            http_status: HTTP response status
            current_month: "2026-02"
            tier_monthly_quota: Workspace tier monthly quota
        
        Returns:
            MeteringResult with deduplication status
        """

        # 1. Check billability
        billable = self._is_billable(http_status)

        # 2. Idempotency check
        idempotency_key = self._generate_idempotency_key(workspace_id, run_id)
        is_duplicate = self.redis.exists(idempotency_key)

        if is_duplicate:
            # Duplicate - do NOT charge
            return MeteringResult(
                event_id=run_id,
                deduplication_status="duplicate",
                dc_charged=0,
                workspace_remaining_dc=self._get_remaining_dc(
                    workspace_id, current_month, tier_monthly_quota
                )
            )

        # 3. Record idempotency key (45 days TTL)
        retention_seconds = self.ssot.meter.idempotency_retention_days * 86400
        self.redis.setex(idempotency_key, retention_seconds, "1")

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
        """

        # Success (2xx)
        if 200 <= http_status < 300:
            return self.ssot.billing_rules.billable.get("success", False)

        # 422 Unprocessable Entity
        if http_status == 422:
            return self.ssot.billing_rules.billable.get("http_422", False)

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
                non_billable_map[http_status], False
            )

        # 5xx
        if 500 <= http_status < 600:
            return not self.ssot.billing_rules.non_billable.get("http_5xx", False)

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
