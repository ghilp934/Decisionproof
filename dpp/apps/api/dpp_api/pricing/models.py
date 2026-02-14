"""
Pydantic models for Decisionwise Pricing SSoT v0.2.1
"""

from pydantic import BaseModel, Field
from typing import Literal, Optional, List
from datetime import datetime


class CurrencyModel(BaseModel):
    """Currency configuration"""
    code: str = "KRW"
    symbol: str = "₩"
    tax_behavior: Literal["exclusive", "inclusive"] = "exclusive"


class UnlimitedSemanticsModel(BaseModel):
    """Unlimited semantics (zero means unlimited)"""
    zero_means: Literal["custom_or_unlimited", "disabled"]
    applies_to_fields: List[str]


class MeterModel(BaseModel):
    """Metering configuration"""
    event_name: str = "decisionwise.dc"
    quantity_field: str = "dc_amount"
    idempotency_key_field: str = "run_id"
    aggregation: Literal["sum", "count", "max"]
    timestamp_source: str = "occurred_at"
    idempotency_scope: str = "workspace_id"
    idempotency_retention_days: int = 45


class GraceOverageModel(BaseModel):
    """Grace overage policy (company waiver)"""
    enabled: bool
    policy: Literal["waive_excess", "notify_only"]
    resolution: Literal["min_of_percent_or_dc", "percent_only", "dc_only"]
    max_grace_percent: float  # 1 = 1%
    max_grace_dc: int  # 100 DC
    applies_to: List[str]


class ProblemDetailsModel(BaseModel):
    """RFC 9457 Problem Details configuration"""
    rfc: str = "9457"
    content_type: str = "application/problem+json"
    type_uris: dict[str, str]
    extensions: dict[str, str]


class RateLimitHeadersModel(BaseModel):
    """IETF RateLimit headers configuration"""
    enabled: bool
    policy_header: str = "RateLimit-Policy"
    limit_header: str = "RateLimit"
    retry_after_precedence: bool
    policy_name_conventions: dict[str, str]
    rate_limit_window_seconds_default: int


class HTTPModel(BaseModel):
    """HTTP layer configuration"""
    problem_details: ProblemDetailsModel
    ratelimit_headers: RateLimitHeadersModel


class TierLimitsModel(BaseModel):
    """Tier limits configuration"""
    rate_limit_rpm: int
    rate_limit_window_seconds: int
    monthly_quota_dc: int
    hard_overage_dc_cap: int
    overage_behavior: Literal["block_on_breach", "notify_only"]
    max_execution_seconds: int
    max_input_tokens: int
    max_output_tokens: int


class TierPoliciesModel(BaseModel):
    """Tier policy names"""
    rpm_policy_name: str
    monthly_dc_policy_name: str
    hard_overage_cap_policy_name: str


class TierSafetyModel(BaseModel):
    """Tier safety features"""
    overage_alerts: bool
    hard_spending_limit: bool


class TierModel(BaseModel):
    """Pricing tier configuration"""
    tier: Literal["SANDBOX", "STARTER", "GROWTH", "ENTERPRISE"]
    monthly_base_price: int
    included_dc_per_month: int
    overage_price_per_dc: int
    features: dict[str, bool]
    limits: TierLimitsModel
    policies: TierPoliciesModel
    safety: TierSafetyModel


class BillingRulesModel(BaseModel):
    """Billing rules configuration"""
    rounding: str
    billable: dict[str, bool]
    non_billable: dict[str, bool]
    limit_exceeded_http_status: int
    limit_exceeded_problem: dict[str, str]
    payment_required_http_status_optional: Optional[int] = None


class PricingSSoTModel(BaseModel):
    """Pricing SSoT root model"""
    pricing_version: str
    effective_from: datetime
    effective_to: Optional[datetime] = None
    currency: CurrencyModel
    unlimited_semantics: UnlimitedSemanticsModel
    meter: MeterModel
    grace_overage: GraceOverageModel
    http: HTTPModel
    tiers: List[TierModel]
    billing_rules: BillingRulesModel

    def get_tier(self, tier_name: str) -> Optional[TierModel]:
        """Get tier configuration by name"""
        for tier in self.tiers:
            if tier.tier == tier_name:
                return tier
        return None

    def is_zero_unlimited(self, value: int, field_name: str) -> bool:
        """Check if zero means unlimited for given field"""
        if value != 0:
            return False
        return field_name in self.unlimited_semantics.applies_to_fields
