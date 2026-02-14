"""
Runtime Enforcement Engine
Enforces pricing policies: RPM, monthly DC quota, hard overage cap
"""

from datetime import datetime
from typing import Optional
from redis import Redis
from .models import PricingSSoTModel, TierModel
from .problem_details import ProblemDetails, ViolatedPolicy


class EnforcementEngine:
    """
    Runtime enforcement of pricing policies:
    1. RPM (Requests Per Minute) - Redis INCR-first
    2. Monthly DC Quota - Redis + Database
    3. Hard Overage Cap - Redis + Database
    """

    def __init__(self, redis: Redis, ssot: PricingSSoTModel):
        self.redis = redis
        self.ssot = ssot

    def check_rpm_limit(
        self,
        workspace_id: str,
        tier: TierModel
    ) -> Optional[ProblemDetails]:
        """
        Check RPM limit (INCR-first pattern)
        
        Returns:
            None if OK
            ProblemDetails if exceeded
        """

        rpm_limit = tier.limits.rate_limit_rpm

        # Zero means unlimited?
        if self.ssot.is_zero_unlimited(rpm_limit, "rate_limit_rpm"):
            return None

        # RPM key
        window_seconds = tier.limits.rate_limit_window_seconds
        now_window = int(datetime.utcnow().timestamp() / window_seconds)
        rpm_key = f"rpm:{workspace_id}:{now_window}"

        # INCR-first (atomic)
        new_count = self.redis.incr(rpm_key)

        # Set TTL (first request only)
        if new_count == 1:
            self.redis.expire(rpm_key, window_seconds)

        # Check limit
        if new_count > rpm_limit:
            # Exceeded - rollback
            self.redis.decr(rpm_key)

            # Get TTL for Retry-After
            ttl = self.redis.ttl(rpm_key)
            retry_after = max(1, ttl)

            return ProblemDetails(
                type=self.ssot.http.problem_details.type_uris["quota_exceeded"],
                title="Request cannot be satisfied as assigned quota has been exceeded",
                status=429,
                detail=f"RPM limit of {rpm_limit} requests per minute exceeded",
                violated_policies=[
                    ViolatedPolicy(
                        policy_name=tier.policies.rpm_policy_name,
                        limit=rpm_limit,
                        current=new_count - 1,  # After decr
                        window_seconds=window_seconds
                    )
                ]
            )

        return None

    def check_monthly_dc_quota(
        self,
        workspace_id: str,
        tier: TierModel,
        current_month: str  # "2026-02"
    ) -> Optional[ProblemDetails]:
        """
        Check monthly DC quota
        
        Returns:
            None if OK
            ProblemDetails if exceeded
        """

        monthly_quota = tier.limits.monthly_quota_dc

        # Zero means unlimited?
        if self.ssot.is_zero_unlimited(monthly_quota, "monthly_quota_dc"):
            return None

        # Get current usage from Redis
        usage_key = f"usage:{workspace_id}:{current_month}"
        current_usage = int(self.redis.get(usage_key) or 0)

        # Check limit
        if current_usage >= monthly_quota:
            return ProblemDetails(
                type=self.ssot.http.problem_details.type_uris["quota_exceeded"],
                title="Request cannot be satisfied as assigned quota has been exceeded",
                status=429,
                detail=f"Monthly DC quota of {monthly_quota} exceeded",
                violated_policies=[
                    ViolatedPolicy(
                        policy_name=tier.policies.monthly_dc_policy_name,
                        limit=monthly_quota,
                        current=current_usage,
                        window_seconds=None  # Monthly quota
                    )
                ]
            )

        return None

    def check_hard_overage_cap(
        self,
        workspace_id: str,
        tier: TierModel,
        current_month: str
    ) -> Optional[ProblemDetails]:
        """
        Check hard overage cap
        
        Returns:
            None if OK
            ProblemDetails if exceeded
        """

        hard_cap = tier.limits.hard_overage_dc_cap

        # Zero means unlimited?
        if self.ssot.is_zero_unlimited(hard_cap, "hard_overage_dc_cap"):
            return None

        # Get current usage
        usage_key = f"usage:{workspace_id}:{current_month}"
        current_usage = int(self.redis.get(usage_key) or 0)

        # Hard cap = monthly_quota + hard_overage_dc_cap
        total_cap = tier.limits.monthly_quota_dc + hard_cap

        # Check limit (with grace overage)
        grace_dc = self._calculate_grace_overage(total_cap)
        effective_cap = total_cap + grace_dc

        if current_usage >= effective_cap:
            return ProblemDetails(
                type=self.ssot.http.problem_details.type_uris["quota_exceeded"],
                title="Request cannot be satisfied as assigned quota has been exceeded",
                status=429,
                detail=f"Hard overage cap of {hard_cap} DC exceeded",
                violated_policies=[
                    ViolatedPolicy(
                        policy_name=tier.policies.hard_overage_cap_policy_name,
                        limit=total_cap,
                        current=current_usage,
                        window_seconds=None
                    )
                ]
            )

        return None

    def _calculate_grace_overage(self, hard_cap: int) -> int:
        """
        Calculate grace overage amount
        
        Returns:
            Grace DC amount (waived)
        """

        if not self.ssot.grace_overage.enabled:
            return 0

        # min(1% of cap, 100 DC)
        grace_percent = self.ssot.grace_overage.max_grace_percent / 100
        grace_from_percent = int(hard_cap * grace_percent)
        grace_from_dc = self.ssot.grace_overage.max_grace_dc

        return min(grace_from_percent, grace_from_dc)
