# MTS-2 Implementation Spec: Decisionwise Monetization System
## SSoT v0.2.1 Enforcement + RFC 9457 Problem Details + RateLimit Headers

**Project**: Decisionwise API Platform
**Version**: v0.2.1
**Date**: 2026-02-14
**Status**: Implementation Ready

---

## 📋 Overview

**Goal**: Implement complete monetization system based on Decisionwise_Pricing_SSoT_v0_2_1.md

**Key Components**:
1. SSoT Loader + Validator (JSON Schema validation)
2. Runtime Enforcement Engine (RPM, monthly DC quota, hard overage cap)
3. RFC 9457 Problem Details responses
4. IETF RateLimit headers (draft-ietf-httpapi-ratelimit-headers)
5. Idempotent Metering (workspace_id + run_id uniqueness)

---

## 🏗️ Architecture

### Component Diagram

```
┌─────────────────────────────────────────────────────────┐
│ FastAPI Application                                      │
│                                                          │
│  ┌────────────────┐                                     │
│  │ SSoT Loader    │ ← pricing_ssot.json                 │
│  │ + Validator    │   (JSON Schema validation)          │
│  └────────┬───────┘                                     │
│           ↓                                              │
│  ┌────────────────┐                                     │
│  │ Pricing Engine │ ← In-memory pricing config          │
│  └────────┬───────┘                                     │
│           ↓                                              │
│  ┌─────────────────────────────────────────────┐       │
│  │ Runtime Enforcement Middleware              │       │
│  │                                              │       │
│  │  1. RPM Limiter (Redis INCR-first)          │       │
│  │  2. Monthly DC Quota Checker (Redis)        │       │
│  │  3. Hard Overage Cap Enforcer               │       │
│  └───────────────┬─────────────────────────────┘       │
│                  ↓                                       │
│  ┌─────────────────────────────────────────────┐       │
│  │ Metering Service (Idempotent)               │       │
│  │                                              │       │
│  │  - Key: (workspace_id, run_id)              │       │
│  │  - Deduplication: 45 days retention         │       │
│  │  - Billable vs Non-billable logic           │       │
│  └───────────────┬─────────────────────────────┘       │
│                  ↓                                       │
│  ┌─────────────────────────────────────────────┐       │
│  │ RFC 9457 Problem Details Generator          │       │
│  │                                              │       │
│  │  - Type: quota-exceeded                     │       │
│  │  - Extension: violated-policies array       │       │
│  │  - Content-Type: application/problem+json   │       │
│  └───────────────┬─────────────────────────────┘       │
│                  ↓                                       │
│  ┌─────────────────────────────────────────────┐       │
│  │ RateLimit Headers Generator                 │       │
│  │                                              │       │
│  │  - RateLimit-Policy                         │       │
│  │  - RateLimit                                │       │
│  │  - Retry-After (takes precedence)           │       │
│  └─────────────────────────────────────────────┘       │
└─────────────────────────────────────────────────────────┘
```

---

## 📦 Data Models

### 1. Pricing SSoT Model

```python
# apps/api/dpp_api/pricing/models.py

from pydantic import BaseModel, Field
from typing import Literal, Optional, List
from datetime import datetime

class CurrencyModel(BaseModel):
    code: str = "KRW"
    symbol: str = "₩"
    tax_behavior: Literal["exclusive", "inclusive"] = "exclusive"

class UnlimitedSemanticsModel(BaseModel):
    zero_means: Literal["custom_or_unlimited", "disabled"]
    applies_to_fields: List[str]

class MeterModel(BaseModel):
    event_name: str = "decisionwise.dc"
    quantity_field: str = "dc_amount"
    idempotency_key_field: str = "run_id"
    aggregation: Literal["sum", "count", "max"]
    timestamp_source: str = "occurred_at"
    idempotency_scope: str = "workspace_id"
    idempotency_retention_days: int = 45

class GraceOverageModel(BaseModel):
    enabled: bool
    policy: Literal["waive_excess", "notify_only"]
    resolution: Literal["min_of_percent_or_dc", "percent_only", "dc_only"]
    max_grace_percent: float  # 1 = 1%
    max_grace_dc: int  # 100 DC
    applies_to: List[str]

class ProblemDetailsModel(BaseModel):
    rfc: str = "9457"
    content_type: str = "application/problem+json"
    type_uris: dict[str, str]
    extensions: dict[str, str]

class RateLimitHeadersModel(BaseModel):
    enabled: bool
    policy_header: str = "RateLimit-Policy"
    limit_header: str = "RateLimit"
    retry_after_precedence: bool
    policy_name_conventions: dict[str, str]
    rate_limit_window_seconds_default: int

class HTTPModel(BaseModel):
    problem_details: ProblemDetailsModel
    ratelimit_headers: RateLimitHeadersModel

class TierLimitsModel(BaseModel):
    rate_limit_rpm: int
    rate_limit_window_seconds: int
    monthly_quota_dc: int
    hard_overage_dc_cap: int
    overage_behavior: Literal["block_on_breach", "notify_only"]
    max_execution_seconds: int
    max_input_tokens: int
    max_output_tokens: int

class TierPoliciesModel(BaseModel):
    rpm_policy_name: str
    monthly_dc_policy_name: str
    hard_overage_cap_policy_name: str

class TierSafetyModel(BaseModel):
    overage_alerts: bool
    hard_spending_limit: bool

class TierModel(BaseModel):
    tier: Literal["SANDBOX", "STARTER", "GROWTH", "ENTERPRISE"]
    monthly_base_price: int
    included_dc_per_month: int
    overage_price_per_dc: int
    features: dict[str, bool]
    limits: TierLimitsModel
    policies: TierPoliciesModel
    safety: TierSafetyModel

class BillingRulesModel(BaseModel):
    rounding: str
    billable: dict[str, bool]
    non_billable: dict[str, bool]
    limit_exceeded_http_status: int
    limit_exceeded_problem: dict[str, str]
    payment_required_http_status_optional: Optional[int]

class PricingSSoTModel(BaseModel):
    pricing_version: str
    effective_from: datetime
    effective_to: Optional[datetime]
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
```

### 2. Metering Event Model

```python
# apps/api/dpp_api/pricing/metering.py

from pydantic import BaseModel
from datetime import datetime

class MeteringEvent(BaseModel):
    event_name: str = "decisionwise.dc"
    workspace_id: str
    run_id: str  # Idempotency key
    dc_amount: int
    occurred_at: datetime
    http_status: int
    billable: bool
    metadata: dict = {}

class MeteringResult(BaseModel):
    event_id: str
    deduplication_status: Literal["new", "duplicate"]
    dc_charged: int
    workspace_remaining_dc: int
```

### 3. Problem Details Model

```python
# apps/api/dpp_api/pricing/problem_details.py

from pydantic import BaseModel
from typing import List, Optional

class ViolatedPolicy(BaseModel):
    policy_name: str
    limit: int
    current: int
    window_seconds: Optional[int] = None

class ProblemDetails(BaseModel):
    type: str
    title: str
    status: int
    detail: str
    instance: Optional[str] = None
    violated_policies: List[ViolatedPolicy] = []  # Extension field
```

---

## 🔧 Implementation Components

### Component 1: SSoT Loader + Validator

```python
# apps/api/dpp_api/pricing/ssot_loader.py

import json
from pathlib import Path
from jsonschema import validate, ValidationError
from .models import PricingSSoTModel

class SSOTLoader:
    """
    Load and validate Pricing SSoT JSON against JSON Schema
    """

    def __init__(self, ssot_path: Path, schema_path: Path):
        self.ssot_path = ssot_path
        self.schema_path = schema_path
        self._ssot: Optional[PricingSSoTModel] = None

    def load(self) -> PricingSSoTModel:
        """
        Load SSoT JSON and validate against JSON Schema

        Raises:
            FileNotFoundError: SSoT file not found
            ValidationError: JSON Schema validation failed
            ValueError: Pydantic validation failed
        """

        # 1. Load JSON Schema
        with open(self.schema_path, 'r', encoding='utf-8') as f:
            schema = json.load(f)

        # 2. Load SSoT JSON
        with open(self.ssot_path, 'r', encoding='utf-8') as f:
            ssot_json = json.load(f)

        # 3. Validate against JSON Schema
        validate(instance=ssot_json, schema=schema)

        # 4. Parse into Pydantic model
        ssot = PricingSSoTModel(**ssot_json)

        self._ssot = ssot
        return ssot

    def get_ssot(self) -> PricingSSoTModel:
        """Get loaded SSoT (cached)"""
        if self._ssot is None:
            raise RuntimeError("SSoT not loaded. Call load() first.")
        return self._ssot
```

### Component 2: Runtime Enforcement Engine

```python
# apps/api/dpp_api/pricing/enforcement.py

from redis import Redis
from datetime import datetime, timedelta
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
```

### Component 3: Idempotent Metering Service

```python
# apps/api/dpp_api/pricing/metering.py

from sqlalchemy.orm import Session
from redis import Redis
from .models import PricingSSoTModel
import hashlib

class MeteringService:
    """
    Idempotent metering service

    Key: (workspace_id, run_id)
    Retention: 45 days
    """

    def __init__(self, db: Session, redis: Redis, ssot: PricingSSoTModel):
        self.db = db
        self.redis = redis
        self.ssot = ssot

    def record_usage(
        self,
        workspace_id: str,
        run_id: str,
        dc_amount: int,
        http_status: int,
        current_month: str
    ) -> MeteringResult:
        """
        Record usage with idempotency

        Args:
            workspace_id: Workspace ID
            run_id: Run ID (idempotency key)
            dc_amount: DC amount consumed
            http_status: HTTP response status
            current_month: "2026-02"

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
                workspace_remaining_dc=self._get_remaining_dc(workspace_id, current_month)
            )

        # 3. Record idempotency key (45 days TTL)
        retention_seconds = self.ssot.meter.idempotency_retention_days * 86400
        self.redis.setex(idempotency_key, retention_seconds, "1")

        # 4. Charge DC (if billable)
        if billable:
            usage_key = f"usage:{workspace_id}:{current_month}"
            self.redis.incrby(usage_key, dc_amount)

            # Set TTL to end of next month
            # (implementation detail)

        # 5. Log metering event to Database (immutable log)
        # (implementation detail)

        return MeteringResult(
            event_id=run_id,
            deduplication_status="new",
            dc_charged=dc_amount if billable else 0,
            workspace_remaining_dc=self._get_remaining_dc(workspace_id, current_month)
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

    def _get_remaining_dc(self, workspace_id: str, current_month: str) -> int:
        """Get remaining DC for workspace"""
        # (implementation detail - query from workspace tier + usage)
        pass
```

### Component 4: RateLimit Headers Generator

```python
# apps/api/dpp_api/pricing/ratelimit_headers.py

from .models import TierModel, PricingSSoTModel
from redis import Redis

class RateLimitHeadersGenerator:
    """
    Generate RateLimit headers per IETF draft-ietf-httpapi-ratelimit-headers
    """

    def __init__(self, redis: Redis, ssot: PricingSSoTModel):
        self.redis = redis
        self.ssot = ssot

    def generate_rpm_headers(
        self,
        workspace_id: str,
        tier: TierModel
    ) -> dict[str, str]:
        """
        Generate RateLimit-Policy and RateLimit headers for RPM

        Returns:
            {
                "RateLimit-Policy": '"rpm";q=600;w=60',
                "RateLimit": '"rpm";r=123;t=17'
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

        return {
            self.ssot.http.ratelimit_headers.policy_header: policy_header,
            self.ssot.http.ratelimit_headers.limit_header: limit_header
        }
```

---

## 🔌 API Endpoints

### Endpoint 1: POST /v1/runs (with enforcement)

```python
# apps/api/dpp_api/routers/runs.py

from fastapi import APIRouter, Depends, Header, HTTPException, Response
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
from ..pricing.enforcement import EnforcementEngine
from ..pricing.metering import MeteringService
from ..pricing.ratelimit_headers import RateLimitHeadersGenerator
from ..pricing.problem_details import ProblemDetails

router = APIRouter()

@router.post("/v1/runs", status_code=201)
async def create_run(
    request: RunCreateRequest,
    workspace_id: str = Header(..., alias="X-Workspace-ID"),
    db: Session = Depends(get_db),
    redis: Redis = Depends(get_redis),
    ssot: PricingSSoTModel = Depends(get_ssot)
):
    """
    Create new run with enforcement

    Headers:
        X-Workspace-ID: Workspace ID

    Returns:
        201 Created: Run created
        429 Too Many Requests: Quota exceeded (application/problem+json)
    """

    # 1. Get workspace tier
    workspace = get_workspace(db, workspace_id)
    tier = ssot.get_tier(workspace.tier)

    # 2. Runtime Enforcement
    enforcement = EnforcementEngine(redis, ssot)

    # 2a. Check RPM limit
    problem = enforcement.check_rpm_limit(workspace_id, tier)
    if problem:
        return problem_details_response(problem, workspace_id, tier)

    # 2b. Check monthly DC quota
    current_month = datetime.utcnow().strftime("%Y-%m")
    problem = enforcement.check_monthly_dc_quota(workspace_id, tier, current_month)
    if problem:
        return problem_details_response(problem, workspace_id, tier)

    # 2c. Check hard overage cap
    problem = enforcement.check_hard_overage_cap(workspace_id, tier, current_month)
    if problem:
        return problem_details_response(problem, workspace_id, tier)

    # 3. Create run (business logic)
    run = create_run_logic(db, workspace_id, request)

    # 4. Metering (idempotent)
    metering = MeteringService(db, redis, ssot)
    metering_result = metering.record_usage(
        workspace_id=workspace_id,
        run_id=run.run_id,
        dc_amount=request.estimated_dc,  # Or actual DC after execution
        http_status=201,
        current_month=current_month
    )

    # 5. RateLimit headers
    headers_gen = RateLimitHeadersGenerator(redis, ssot)
    rate_headers = headers_gen.generate_rpm_headers(workspace_id, tier)

    # 6. Return response with RateLimit headers
    return JSONResponse(
        status_code=201,
        content={"run_id": run.run_id, "status": "queued"},
        headers=rate_headers
    )


def problem_details_response(
    problem: ProblemDetails,
    workspace_id: str,
    tier: TierModel
) -> JSONResponse:
    """
    Generate RFC 9457 Problem Details response

    Content-Type: application/problem+json
    Status: 429
    Headers: RateLimit-Policy, RateLimit, Retry-After
    """

    # Serialize Problem Details
    content = problem.dict(exclude_none=True)

    # RateLimit headers
    headers_gen = RateLimitHeadersGenerator(redis, ssot)
    rate_headers = headers_gen.generate_rpm_headers(workspace_id, tier)

    # Retry-After (takes precedence)
    # Calculate from violated_policies
    if problem.violated_policies:
        policy = problem.violated_policies[0]
        if policy.window_seconds:
            # RPM policy
            headers["Retry-After"] = str(policy.window_seconds)

    return JSONResponse(
        status_code=problem.status,
        content=content,
        headers={**rate_headers, "Content-Type": "application/problem+json"}
    )
```

---

## 🧪 Testing Strategy

### Test 1: Idempotency

```python
# apps/api/tests/test_metering_idempotency.py

def test_idempotent_metering_same_run_id():
    """
    Same run_id twice => billed/usage counted once
    """

    workspace_id = "ws_123"
    run_id = "run_abc"
    dc_amount = 10

    # First request
    result1 = metering_service.record_usage(
        workspace_id=workspace_id,
        run_id=run_id,
        dc_amount=dc_amount,
        http_status=200,
        current_month="2026-02"
    )

    assert result1.deduplication_status == "new"
    assert result1.dc_charged == 10

    # Second request (duplicate)
    result2 = metering_service.record_usage(
        workspace_id=workspace_id,
        run_id=run_id,
        dc_amount=dc_amount,
        http_status=200,
        current_month="2026-02"
    )

    assert result2.deduplication_status == "duplicate"
    assert result2.dc_charged == 0  # Not charged again

    # Verify total usage
    usage_key = f"usage:{workspace_id}:2026-02"
    total_usage = int(redis.get(usage_key))
    assert total_usage == 10  # Counted once
```

### Test 2: 429 violated-policies

```python
# apps/api/tests/test_problem_details.py

def test_429_rpm_exceeded_violated_policies():
    """
    429 response includes violated-policies for RPM
    """

    # Setup: RPM limit = 10
    workspace = create_workspace(tier="SANDBOX")  # RPM = 60

    # Exhaust RPM limit
    for _ in range(60):
        response = client.post("/v1/runs", headers={"X-Workspace-ID": workspace.id})
        assert response.status_code == 201

    # 61st request should fail
    response = client.post("/v1/runs", headers={"X-Workspace-ID": workspace.id})

    assert response.status_code == 429
    assert response.headers["Content-Type"] == "application/problem+json"

    problem = response.json()
    assert problem["type"] == "https://iana.org/assignments/http-problem-types#quota-exceeded"
    assert problem["title"] == "Request cannot be satisfied as assigned quota has been exceeded"
    assert "violated-policies" in problem
    assert len(problem["violated-policies"]) == 1

    violated = problem["violated-policies"][0]
    assert violated["policy_name"] == "rpm"
    assert violated["limit"] == 60
    assert violated["current"] >= 60
    assert violated["window_seconds"] == 60
```

### Test 3: Billability Rules

```python
# apps/api/tests/test_billability.py

def test_422_is_billable():
    """422 Unprocessable Entity is billable"""

    workspace_id = "ws_123"
    run_id = "run_422"

    result = metering_service.record_usage(
        workspace_id=workspace_id,
        run_id=run_id,
        dc_amount=10,
        http_status=422,
        current_month="2026-02"
    )

    assert result.dc_charged == 10  # Billable


def test_404_is_non_billable():
    """404 Not Found is non-billable"""

    workspace_id = "ws_123"
    run_id = "run_404"

    result = metering_service.record_usage(
        workspace_id=workspace_id,
        run_id=run_id,
        dc_amount=10,
        http_status=404,
        current_month="2026-02"
    )

    assert result.dc_charged == 0  # Non-billable


def test_429_is_non_billable():
    """429 Too Many Requests is non-billable"""

    result = metering_service.record_usage(
        workspace_id="ws_123",
        run_id="run_429",
        dc_amount=10,
        http_status=429,
        current_month="2026-02"
    )

    assert result.dc_charged == 0  # Non-billable
```

### Test 4: Grace Overage

```python
# apps/api/tests/test_grace_overage.py

def test_grace_overage_waived_at_settlement():
    """
    Small overage beyond cap is waived at settlement
    """

    # STARTER tier:
    # - monthly_quota_dc = 2000
    # - hard_overage_dc_cap = 1000
    # - Total cap = 3000
    # - Grace = min(1% of 3000, 100) = min(30, 100) = 30 DC

    workspace = create_workspace(tier="STARTER")

    # Use 3020 DC (20 DC over total cap, within grace)
    for i in range(302):
        metering_service.record_usage(
            workspace_id=workspace.id,
            run_id=f"run_{i}",
            dc_amount=10,
            http_status=200,
            current_month="2026-02"
        )

    # Should NOT be blocked (within grace)
    usage = get_usage(workspace.id, "2026-02")
    assert usage == 3020

    # Settlement at month end
    settlement = settle_workspace(workspace.id, "2026-02")

    # Grace waived: 3020 - 3000 = 20 DC waived
    assert settlement.grace_waived_dc == 20
    assert settlement.billable_dc == 3000
```

---

## 📁 File Structure

```
apps/api/dpp_api/
├── pricing/
│   ├── __init__.py
│   ├── models.py                  # Pydantic models
│   ├── ssot_loader.py            # SSoT Loader + Validator
│   ├── enforcement.py            # Runtime Enforcement Engine
│   ├── metering.py               # Idempotent Metering Service
│   ├── problem_details.py        # RFC 9457 Problem Details
│   ├── ratelimit_headers.py      # RateLimit headers generator
│   └── fixtures/
│       ├── pricing_ssot.json     # SSoT v0.2.1
│       ├── pricing_ssot_schema.json  # JSON Schema
│       └── problem_details_examples.json
│
├── routers/
│   └── runs.py                   # Modified with enforcement
│
└── tests/
    ├── test_ssot_loader.py
    ├── test_enforcement.py
    ├── test_metering_idempotency.py
    ├── test_problem_details.py
    ├── test_billability.py
    └── test_grace_overage.py
```

---

## 📋 Checklist

### Implementation

- [ ] JSON Schema for SSoT v0.2.1
- [ ] SSoT Loader + Validator
- [ ] Pydantic models
- [ ] Enforcement Engine (RPM, monthly_dc, hard_overage_cap)
- [ ] Metering Service (idempotent)
- [ ] Problem Details generator (RFC 9457)
- [ ] RateLimit headers generator
- [ ] API endpoint modifications

### Testing

- [ ] Idempotency test (same run_id twice)
- [ ] 429 violated-policies test
- [ ] Billability rules test (422, 404, 409, 429, 5xx)
- [ ] Grace overage test
- [ ] RPM limit test
- [ ] Monthly DC quota test
- [ ] Hard overage cap test

### Documentation

- [ ] Reference fixtures (ssot.json, examples)
- [ ] API documentation update
- [ ] CI validation step for SSoT JSON

---

## 🚀 Deployment Plan

1. **Create pricing/ module**
2. **Implement SSoT Loader + Validator**
3. **Implement Enforcement Engine**
4. **Implement Metering Service**
5. **Implement Problem Details + RateLimit headers**
6. **Modify API endpoints**
7. **Write tests**
8. **Create fixtures**
9. **CI/CD integration** (JSON Schema validation)
10. **Commit + Push to GitHub**

---

**Status**: Ready for Implementation
**Next Steps**: Create JSON Schema → Implement SSoT Loader → Tests

