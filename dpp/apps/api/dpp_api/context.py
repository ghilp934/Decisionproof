"""Request context management for observability.

P1-9: Context variables for request tracking across async boundaries.
MS-6: Add run_id and tenant_id for complete observability.
RC-6: Add plan_key and budget_decision for observability.
"""

from contextvars import ContextVar

# Request ID - unique per HTTP request
request_id_var: ContextVar[str] = ContextVar("request_id", default="")

# MS-6: Run ID - current run being processed
run_id_var: ContextVar[str] = ContextVar("run_id", default="")

# MS-6: Tenant ID - current tenant context
tenant_id_var: ContextVar[str] = ContextVar("tenant_id", default="")

# RC-6: Plan key - format: "{plan_id}:{profile_version}"
plan_key_var: ContextVar[str] = ContextVar("plan_key", default="")

# RC-6: Budget decision - "reserve.ok" or "reserve.deny"
budget_decision_var: ContextVar[str] = ContextVar("budget_decision", default="")
