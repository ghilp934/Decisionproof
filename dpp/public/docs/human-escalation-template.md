# Human Escalation Templates

## Overview

When AI agents or automated systems encounter decisions requiring human approval, use these templates to structure escalation requests. Templates include cost estimates, expected value, security considerations, and next-step URLs.

## Template A: Request API Key / Workspace Approval

**Subject**: 🔑 API Key & Workspace Access Approval Required

**Context**:
An automated agent or integration requires access to the Decisionwise API to execute decision workflows on your behalf.

**Estimated Cost**:
- **Setup**: Free (API keys are generated at no charge)
- **Monthly Operating Cost**: Depends on usage
  - STARTER tier: $0–$1,000/month (600 requests/min, 10,000 DC included)
  - SCALE tier: $1,000+ (1,200 requests/min, 50,000 DC included)
  - See [Pricing SSoT](/pricing/ssot.json) for full details

**Expected Value**:
- Automated decision execution for [AGENT_USE_CASE]
- Estimated [X] decisions per day
- Expected time savings: [Y] hours/week
- Projected decision quality improvement: [Z]%

**Security Notes**:
- API keys grant full access to your workspace resources
- Keys use format: `dw_live_*` (production) or `dw_test_*` (sandbox)
- Recommended: Start with sandbox environment for testing
- Keys can be rotated or revoked at any time via dashboard

**Action Required**:
1. Review the request details above
2. If approved, visit: [Decisionwise Dashboard - API Keys](https://app.decisionwise.ai/settings/api-keys)
3. Click "Generate New API Key"
4. Select workspace: `[WORKSPACE_NAME]`
5. Copy the key and provide it to the requesting agent/integration (store securely)
6. Monitor usage at: [Usage Dashboard](https://app.decisionwise.ai/usage)

**Next Steps**:
- Approve: Generate key and proceed → [API Keys Dashboard](https://app.decisionwise.ai/settings/api-keys)
- Reject: No action needed
- Questions: Contact support → [Support](https://decisionwise.ai/support)

**Reference Documentation**:
- [Authentication Guide](/docs/auth.md)
- [Delegated Authentication](/docs/auth-delegated.md)
- [Quickstart](/docs/quickstart.md)

---

## Template B: Request Monthly Budget Cap Approval

**Subject**: 💰 Monthly Budget Cap Increase Approval Required

**Context**:
Current usage patterns indicate the workspace will exceed the current monthly budget cap. Agent requests approval to increase the cap to prevent service interruption.

**Current State**:
- **Current Monthly Cap**: $[CURRENT_CAP]
- **Current Month Usage**: $[CURRENT_USAGE] ([PERCENTAGE]% of cap)
- **Projected Month-End Usage**: $[PROJECTED_USAGE] (would exceed cap by $[OVERAGE])
- **Rate Limit Status**: [CURRENT_TIER] tier ([REQUESTS_PER_MIN] requests/min)

**Requested Change**:
- **New Monthly Cap**: $[REQUESTED_CAP]
- **Reason**: [REASON_FOR_INCREASE]
  - Example: Increased decision volume due to seasonal demand
  - Example: New integration launched requiring higher throughput
  - Example: Projected overage from pilot program success

**Estimated Cost**:
- **Additional Monthly Spend**: $[REQUESTED_CAP - CURRENT_CAP]
- **Included Decision Credits**: [DC_AMOUNT] DC (based on tier)
- **Overage Rate**: $0.10 per DC beyond included amount
- **Grace Overage**: min(1% of cap, 100 DC) waived at settlement

**Expected Value**:
- Prevents service interruption for [CRITICAL_WORKFLOWS]
- Supports [X] additional decisions per day
- Expected ROI: [Y]% (based on decision value vs API cost)
- Business impact if not approved: [IMPACT_DESCRIPTION]

**Security Notes**:
- Budget caps are hard limits enforced at the workspace level
- Once cap is reached, new requests return 429 (rate limit exceeded)
- Cap increases take effect immediately
- Can be reduced at any time (takes effect next billing cycle)

**Action Required**:
1. Review the request details above
2. If approved, visit: [Billing Settings](https://app.decisionwise.ai/settings/billing)
3. Navigate to "Monthly Budget Cap" section
4. Update cap to: $[REQUESTED_CAP]
5. Confirm change and save

**Next Steps**:
- Approve: Update cap immediately → [Billing Settings](https://app.decisionwise.ai/settings/billing)
- Approve with conditions: Adjust to different amount → [Billing Settings](https://app.decisionwise.ai/settings/billing)
- Reject: Current cap remains; agent will receive 429 errors when exceeded
- Questions: Contact support → [Support](https://decisionwise.ai/support)

**Monitoring**:
- Real-time usage: [Usage Dashboard](https://app.decisionwise.ai/usage)
- Alerts: Configure notifications at [Alert Settings](https://app.decisionwise.ai/settings/alerts)
  - Recommended: Set alert at 80% of cap
  - Recommended: Set alert at 95% of cap

**Reference Documentation**:
- [Metering & Billing](/docs/metering-billing.md)
- [Pricing SSoT](/docs/pricing-ssot.md)
- [Rate Limits](/docs/rate-limits.md)

---

## Template C: Request Tier Upgrade (Rate Limit Increase)

**Subject**: ⚡ Rate Limit Tier Upgrade Approval Required

**Context**:
Current rate limit tier is insufficient for projected request volume. Agent requests tier upgrade to prevent throttling.

**Current State**:
- **Current Tier**: [CURRENT_TIER]
- **Current Rate Limit**: [CURRENT_RPM] requests/min
- **Recent Throttling Events**: [THROTTLE_COUNT] in past 7 days
- **Peak Request Rate**: [PEAK_RPM] requests/min (would require [REQUIRED_TIER] tier)

**Requested Change**:
- **New Tier**: [REQUESTED_TIER]
- **New Rate Limit**: [NEW_RPM] requests/min
- **Reason**: [REASON_FOR_UPGRADE]

**Estimated Cost**:
- **STARTER** ($0–$1,000/mo): 600 requests/min, 10,000 DC included
- **SCALE** ($1,000+/mo): 1,200 requests/min, 50,000 DC included
- **Tier upgrade cost**: Difference in monthly minimums

**Expected Value**:
- Eliminates throttling for [CRITICAL_WORKFLOWS]
- Supports peak loads of [X] requests/min
- Expected reduction in 429 errors: [Y]%

**Action Required**:
1. Review the request details above
2. If approved, visit: [Billing Settings](https://app.decisionwise.ai/settings/billing)
3. Navigate to "Tier & Rate Limits" section
4. Select new tier: [REQUESTED_TIER]
5. Confirm change and save

**Next Steps**:
- Approve: Upgrade tier → [Billing Settings](https://app.decisionwise.ai/settings/billing)
- Reject: Current tier remains; agent will receive 429 errors during peaks
- Questions: Contact support → [Support](https://decisionwise.ai/support)

**Reference Documentation**:
- [Rate Limits](/docs/rate-limits.md)
- [Pricing SSoT](/docs/pricing-ssot.md)

---

## Integration Notes for AI Agents

When implementing escalation logic, consider:

1. **Detection Triggers**:
   - API returns 401/403 → Escalate with Template A
   - Projected monthly usage > 90% of cap → Escalate with Template B (proactive)
   - 429 errors > 5% of requests → Escalate with Template C
   - Actual usage exceeds cap → Escalate with Template B (reactive)

2. **Escalation Channels**:
   - Email: Send template to workspace admin
   - Slack: Post template to designated channel
   - Dashboard: Create notification in web UI
   - Webhook: POST template to configured endpoint

3. **Response Handling**:
   - Approved: Resume operation with new credentials/limits
   - Rejected: Graceful degradation (pause non-critical workflows)
   - No response within [X] hours: Send reminder
   - No response within [Y] hours: Emergency contact (phone/SMS)

4. **Audit Trail**:
   - Log all escalation requests with timestamp
   - Record approval/rejection decisions
   - Track time-to-resolution
   - Measure impact on service availability
