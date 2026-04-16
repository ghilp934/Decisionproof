# Pricing SSoT (Single Source of Truth)

## Canonical Pricing Configuration

Decisionproof pricing is defined in a **machine-readable JSON** file:

**Endpoint**: [/pricing/ssot.json](/pricing/ssot.json)

This file is the **single source of truth** for all pricing, limits, and billing rules.

## Version Information

```json
{
  "pricing_version": "2026-02-14.v0.2.1",
  "effective_from": "2026-03-01T00:00:00Z",
  "effective_to": null
}
```

- **pricing_version**: Semantic versioning (YYYY-MM-DD.vMAJOR.MINOR.PATCH)
- **effective_from**: ISO 8601 UTC timestamp when this pricing takes effect
- **effective_to**: When pricing expires (null = indefinite)

## Currency

```json
{
  "currency": {
    "code": "KRW",
    "symbol": "₩",
    "tax_behavior": "exclusive"
  }
}
```

All prices are in Korean Won (KRW) with tax exclusive.

## Tiers

Decisionproof offers 4 pricing tiers: **SANDBOX**, **STARTER**, **GROWTH**, **ENTERPRISE**.

### Tier Structure

Each tier includes:

- **monthly_base_price**: Monthly subscription fee (KRW)
- **included_dc_per_month**: Included Decision Credits (DC) per month
- **overage_price_per_dc**: Price per DC beyond included amount (KRW)
- **limits**: Rate limits and quotas
  - `rate_limit_rpm`: Requests per minute (0 = unlimited)
  - `monthly_quota_dc`: Total DC quota per month (0 = unlimited)
  - `hard_overage_dc_cap`: Maximum overage DC allowed (0 = unlimited)
  - `max_execution_seconds`: Max decision execution time
  - `max_input_tokens`: Max input tokens per request
  - `max_output_tokens`: Max output tokens per request

### Example (STARTER Tier)

```json
{
  "tier": "STARTER",
  "monthly_base_price": 29000,
  "included_dc_per_month": 1000,
  "overage_price_per_dc": 39,
  "limits": {
    "rate_limit_rpm": 600,
    "monthly_quota_dc": 2000,
    "hard_overage_dc_cap": 1000
  }
}
```

**STARTER**: ₩29,000/month, 1,000 included DC, up to 2,000 DC/month total (1,000 overage cap), 600 RPM.

## Unlimited Semantics

Fields with value `0` mean **unlimited** (custom or unlimited):

```json
{
  "unlimited_semantics": {
    "zero_means": "custom_or_unlimited",
    "applies_to_fields": [
      "included_dc_per_month",
      "monthly_quota_dc",
      "rate_limit_rpm",
      "hard_overage_dc_cap"
    ]
  }
}
```

Example: ENTERPRISE tier has `rate_limit_rpm: 0` = unlimited requests per minute.

## Grace Overage

Decisionproof allows a small **grace overage** to prevent hard stops:

```json
{
  "grace_overage": {
    "enabled": true,
    "policy": "waive_excess",
    "resolution": "min_of_percent_or_dc",
    "max_grace_percent": 1,
    "max_grace_dc": 100
  }
}
```

**Grace amount**: min(1% of overage cap, 100 DC) waived at settlement.

Example: STARTER tier (hard_overage_dc_cap=1000) → grace = min(10 DC, 100 DC) = 10 DC.

## Metering Configuration

```json
{
  "meter": {
    "event_name": "decisionproof.dc",
    "quantity_field": "dc_amount",
    "idempotency_key_field": "run_id",
    "idempotency_retention_days": 45
  }
}
```

- **Idempotency scope**: (workspace_id, run_id)
- **Retention**: 45 days

## Billing Rules

```json
{
  "billing_rules": {
    "billable": {
      "success": true,
      "http_422": true
    },
    "non_billable": {
      "http_400": true,
      "http_401": true,
      "http_403": true,
      "http_404": true,
      "http_409": true,
      "http_412": true,
      "http_413": true,
      "http_415": true,
      "http_429": true,
      "http_5xx": true
    }
  }
}
```

**Billable**: 2xx + 422
**Non-billable**: 400/401/403/404/409/412/413/415/429 + 5xx

## Tier Comparison

For complete tier comparison, refer to the canonical JSON:
**[GET /pricing/ssot.json](/pricing/ssot.json)**

Do **not** rely on manually-maintained tables. Always fetch the latest SSoT JSON.
