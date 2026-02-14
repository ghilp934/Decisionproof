"""
IETF RateLimit Headers Generator
draft-ietf-httpapi-ratelimit-headers
"""

from datetime import datetime
from typing import Optional
from redis import Redis
from .models import TierModel, PricingSSoTModel


class RateLimitHeadersGenerator:
    """
    Generate RateLimit headers per IETF draft-ietf-httpapi-ratelimit-headers
    
    Headers:
    - RateLimit-Policy: Policy name and quota parameters
    - RateLimit: Current quota status
    - Retry-After: Seconds until quota reset (takes precedence over RateLimit)
    """

    def __init__(self, redis: Redis, ssot: PricingSSoTModel):
        self.redis = redis
        self.ssot = ssot

    def generate_rpm_headers(
        self,
        workspace_id: str,
        tier: TierModel,
        include_retry_after: bool = False
    ) -> dict[str, str]:
        """
        Generate RateLimit-Policy and RateLimit headers for RPM
        
        Args:
            workspace_id: Workspace ID
            tier: Tier model
            include_retry_after: Include Retry-After header if quota exceeded
        
        Returns:
            {
                "RateLimit-Policy": '"rpm";q=600;w=60',
                "RateLimit": '"rpm";r=123;t=17',
                "Retry-After": "17"  # Optional
            }
        """

        if not self.ssot.http.ratelimit_headers.enabled:
            return {}

        rpm_limit = tier.limits.rate_limit_rpm
        window_seconds = tier.limits.rate_limit_window_seconds

        # Zero means unlimited
        if self.ssot.is_zero_unlimited(rpm_limit, "rate_limit_rpm"):
            return {}

        # Get current usage
        now_window = int(datetime.utcnow().timestamp() / window_seconds)
        rpm_key = f"rpm:{workspace_id}:{now_window}"
        current_count = int(self.redis.get(rpm_key) or 0)
        remaining = max(0, rpm_limit - current_count)

        # TTL
        ttl = self.redis.ttl(rpm_key)
        if ttl < 0:
            ttl = window_seconds

        # Policy name
        policy_name = tier.policies.rpm_policy_name

        # RateLimit-Policy: "rpm";q=600;w=60
        policy_header = f'"{policy_name}";q={rpm_limit};w={window_seconds}'

        # RateLimit: "rpm";r=123;t=17
        limit_header = f'"{policy_name}";r={remaining};t={ttl}'

        headers = {
            self.ssot.http.ratelimit_headers.policy_header: policy_header,
            self.ssot.http.ratelimit_headers.limit_header: limit_header
        }

        # Retry-After (optional, takes precedence)
        if include_retry_after and self.ssot.http.ratelimit_headers.retry_after_precedence:
            headers["Retry-After"] = str(ttl)

        return headers

    def generate_monthly_dc_headers(
        self,
        workspace_id: str,
        tier: TierModel,
        current_month: str
    ) -> dict[str, str]:
        """
        Generate RateLimit headers for monthly DC quota
        
        Args:
            workspace_id: Workspace ID
            tier: Tier model
            current_month: Current month string (e.g., "2026-02")
        
        Returns:
            RateLimit headers dict
        """

        if not self.ssot.http.ratelimit_headers.enabled:
            return {}

        monthly_quota = tier.limits.monthly_quota_dc

        # Zero means unlimited
        if self.ssot.is_zero_unlimited(monthly_quota, "monthly_quota_dc"):
            return {}

        # Get current usage
        usage_key = f"usage:{workspace_id}:{current_month}"
        current_usage = int(self.redis.get(usage_key) or 0)
        remaining = max(0, monthly_quota - current_usage)

        # Policy name
        policy_name = tier.policies.monthly_dc_policy_name

        # For monthly quota, window is approximately 30 days
        # This is informational only
        window_seconds = 30 * 24 * 3600

        # RateLimit-Policy: "monthly_dc";q=2000;w=2592000
        policy_header = f'"{policy_name}";q={monthly_quota};w={window_seconds}'

        # RateLimit: "monthly_dc";r=1000
        # Note: No "t" parameter for monthly quotas (resets at month boundary)
        limit_header = f'"{policy_name}";r={remaining}'

        return {
            self.ssot.http.ratelimit_headers.policy_header: policy_header,
            self.ssot.http.ratelimit_headers.limit_header: limit_header
        }
