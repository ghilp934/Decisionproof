# First Paid Pilot Pack v0.2

## Overview

The First Paid Pilot Pack v0.2 introduces refined pricing mechanics, settlement logic, and incentive structures for early customers transitioning from proof-of-concept to production usage.

**Version**: 0.2
**Effective Date**: 2026-Q1
**Supersedes**: v0.1 (Free Trial DC Model)

---

## Key Changes from v0.1

| Aspect | v0.1 | v0.2 |
|--------|------|------|
| **Incentive Model** | Free trial DC (non-transferable) | $100 free credit (applies to all charges) |
| **Idempotency Retention** | 30 days | **D+7** (7 days from decision execution) |
| **Safety Buffer** | Not specified | **100 DC waived** at settlement |
| **Settlement Logic** | Gross charges only | **Net against prepay** (credits, incentives) |
| **Tier Enforcement** | S4-Standard | **S4-Alt** (simplified overage caps) |

---

## S4-Alt Tier (Simplified Overage Caps)

**Previous (S4-Standard)**:
- Overage cap: min(50% of monthly_minimum, 3× DC quota)
- Complex calculation requiring tier-specific logic

**Current (S4-Alt)**:
- **STARTER**: Overage cap = min($500, 3× DC quota)
- **SCALE**: Overage cap = min($2,500, 3× DC quota)
- Simpler, more predictable for billing logic

**Example (STARTER tier)**:
- Monthly minimum: $0
- DC quota: 10,000 DC
- Overage cap: min($500, 30,000 DC) = **$500** (equivalent to 5,000 DC at $0.10/DC)

---

## Incentive Change: $100 Free Credit

### Previous Model (v0.1)
- **Free Trial DC**: 5,000 DC granted upfront
- **Limitation**: Only applies to DC consumption (not monthly minimums, overages, or other fees)
- **Expiration**: 90 days
- **Problem**: Complex accounting (separate trial DC balance vs paid DC balance)

### New Model (v0.2)
- **$100 Free Credit**: Applied to **all charges** (monthly minimums, DC consumption, overages)
- **Flexibility**: Customers can use credits however they want (low-volume high-tier vs high-volume low-tier)
- **Expiration**: 90 days (same as before)
- **Settlement**: Credits are **netted against gross charges** at monthly settlement

**Example Usage**:
```
Gross Charges:
  - Monthly minimum (STARTER): $0
  - DC consumption: 12,000 DC × $0.10 = $1,200
  - Overage: 2,000 DC × $0.10 = $200 (within cap)
  Total Gross: $1,400

Credits Applied:
  - $100 free credit
  Total Credits: $100

Net Amount Due: $1,400 - $100 = $1,300
```

---

## Idempotency Retention: D+7

### Previous (v0.1)
- **Retention**: 30 days from first request
- **Problem**: Bloated Redis storage for long-tail idempotency keys
- **Cost**: Higher infrastructure cost for infrequent retries

### New (v0.2)
- **Retention**: **D+7** (7 days from decision execution timestamp)
- **Rationale**: Most retries occur within minutes/hours of original request. 7 days provides sufficient safety margin.
- **Implementation**: Redis TTL set to `retention_seconds = 7 * 24 * 60 * 60` (604,800 seconds)

**Redis Key Pattern**:
```python
idempotency_key = f"idempotency:{workspace_id}:{run_id}"
retention_seconds = 7 * 24 * 60 * 60  # D+7

redis.set(idempotency_key, "1", nx=True, ex=retention_seconds)
```

**Deduplication Status**:
- First request within D+7: `deduplication_status: "original"`
- Retry within D+7: `deduplication_status: "duplicate"` (non-billable)
- Request after D+7: `deduplication_status: "original"` (billable again, but same run_id reused)

---

## Safety Buffer: 100 DC Waived

### Purpose
Prevent accidental overage charges due to race conditions, timing issues, or minor miscalculations.

### Mechanism
At monthly settlement, the system **waives** the first 100 DC of overage charges (or 1% of monthly cap, whichever is smaller).

**Formula**:
```python
grace_overage_dc = min(
    100,  # Absolute grace (100 DC)
    monthly_cap_usd * 10 * 0.01  # 1% of cap in DC
)
```

**Example (STARTER tier, $1,000 monthly cap)**:
```
Gross Overage: 150 DC × $0.10 = $15
Grace Overage: min(100, 1000 × 10 × 0.01) = min(100, 100) = 100 DC = $10
Waived Amount: $10
Net Overage Charge: $15 - $10 = $5
```

**Example (SCALE tier, $5,000 monthly cap)**:
```
Gross Overage: 200 DC × $0.10 = $20
Grace Overage: min(100, 5000 × 10 × 0.01) = min(100, 500) = 100 DC = $10
Waived Amount: $10
Net Overage Charge: $20 - $10 = $10
```

**Note**: Grace overage is applied **once per month** at settlement, not per request.

---

## Settlement Logic: Net Against Prepay

### Settlement Flow

1. **Calculate Gross Charges**:
   - Monthly minimum (tier-based)
   - DC consumption (within quota: included, beyond quota: $0.10/DC)
   - Overage charges (capped per tier, after grace waiver)

2. **Apply Grace Overage**:
   - Waive first 100 DC (or 1% of cap) from overage

3. **Deduct Credits & Incentives**:
   - $100 free credit (if available, expires 90 days)
   - Any other promotional credits
   - Prepaid balances (if customer loaded funds)

4. **Calculate Net Amount Due**:
   ```
   Net Amount Due = Gross Charges - Grace Waiver - Credits - Prepay Balance
   ```

5. **Charge Payment Method**:
   - If Net Amount Due > 0: Charge credit card
   - If Net Amount Due ≤ 0: No charge (roll over remaining credits)

### Example Settlement (STARTER tier)

```
Month: January 2026
Tier: STARTER ($0–$1,000/mo, 10,000 DC quota, 600 RPM)

Usage:
  - DC consumed: 12,500 DC
  - DC quota: 10,000 DC (included)
  - Overage: 2,500 DC

Gross Charges:
  - Monthly minimum: $0 (STARTER has $0 minimum)
  - DC within quota: 10,000 DC × $0 = $0
  - DC overage: 2,500 DC × $0.10 = $250
  Total Gross: $250

Grace Waiver:
  - Grace overage: min(100 DC, 1% of $1,000 cap) = 100 DC = $10
  Total Gross after Grace: $250 - $10 = $240

Credits Applied:
  - $100 free credit (from Pilot Pack v0.2)
  Total Credits: $100

Net Amount Due: $240 - $100 = $140

Action: Charge credit card $140
Remaining Free Credit: $0 (fully consumed)
```

---

## Migration from v0.1 to v0.2

### For Existing Customers

**Customers with Free Trial DC (v0.1)**:
1. Remaining trial DC balance is **converted to USD credit** at $0.10/DC rate
2. Example: 3,000 trial DC remaining → $300 USD credit
3. Credits are added to account and expire 90 days from original grant date
4. New incentive ($100 free credit) is **not stacked** (existing credits take precedence)

**Customers without Trial DC**:
1. Automatically enrolled in v0.2
2. Receive $100 free credit (expires 90 days from grant)
3. Idempotency retention updated to D+7 (no action required)

### For New Customers

1. **Onboarding**: Automatically enrolled in v0.2
2. **Incentive**: $100 free credit granted upon first API key activation
3. **Expiration**: 90 days from grant date
4. **Settlement**: First invoice reflects net charges after credit application

---

## Compliance & Transparency

### Billability Rules (Unchanged)
- **Billable**: 2xx, 422 (successful decisions + unprocessable entities)
- **Non-billable**: 400, 401, 403, 404, 409, 412, 413, 415, 429, 5xx (client/server errors, rate limits)

### Idempotency (Updated)
- Retention: **D+7** (7 days from decision execution)
- Same (workspace_id, run_id) within D+7 → deduplication_status: "duplicate" (non-billable)
- Same (workspace_id, run_id) after D+7 → deduplication_status: "original" (billable again)

### Grace Overage (New)
- **100 DC waived** at monthly settlement (or 1% of cap, whichever is smaller)
- Applied once per month, not per request
- Transparent in invoice line items

### Settlement (Updated)
- Credits netted against gross charges
- Grace overage waived before credit application
- Detailed invoice breakdown provided

---

## FAQ

### Q: What happens to my free trial DC from v0.1?
**A**: Remaining trial DC is converted to USD credits at $0.10/DC and added to your account. Credits expire 90 days from original grant date.

### Q: Can I stack the $100 free credit with other promotions?
**A**: No. Only one promotional credit can be active at a time. Existing credits take precedence over new grants.

### Q: What happens if I reuse a run_id after D+7?
**A**: The request is treated as a new "original" request and is billable again. However, this is **not recommended** as run_id should be globally unique within your workspace.

### Q: Is the 100 DC grace overage per request or per month?
**A**: **Per month**. The grace waiver is applied once at monthly settlement, not per request.

### Q: Does the safety buffer apply to rate limit overages?
**A**: No. The safety buffer only applies to **DC consumption overages** (exceeding quota cap). Rate limit violations (429 errors) are always non-billable.

### Q: Can I opt out of the $100 free credit?
**A**: No. The credit is automatically applied to all new accounts. However, you can contact support if you prefer invoice-based billing without promotional credits.

---

## Reference Documentation

- [Pricing SSoT v0.2.1](/docs/pricing-ssot.md)
- [Metering & Billing](/docs/metering-billing.md)
- [Changelog](/docs/changelog.md)
- [Quickstart](/docs/quickstart.md)

---

**Last Updated**: 2026-Q1
**Version**: 0.2
**Status**: Active
