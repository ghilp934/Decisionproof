#!/usr/bin/env bash
# =============================================================================
# p6_2_setup_breakglass_alerts.sh
# Phase 6.2: WORM Break-glass EventBridge + SNS alert setup (idempotent)
#
# Purpose:
#   Create or update the EventBridge rule + SNS topic that fires when
#   WORM GOVERNANCE retention is bypassed on the kill-switch audit bucket.
#   Alert body includes WHO/WHERE/WHAT via InputTransformer.
#
# Usage:
#   export AUDIT_BUCKET_NAME="dpp-kill-switch-audit-prod"
#   export ALERT_EMAIL="security-team@example.com"
#   export AWS_REGION="ap-northeast-2"
#   export AWS_PROFILE="dpp-admin"
#   bash dpp/infra/scripts/p6_2_setup_breakglass_alerts.sh
#
# Idempotent: Safe to re-run. Existing resources are verified; drift is updated.
# =============================================================================

set -euo pipefail

# --------------------------------------------------------------------------
# 0. Configuration — Required env vars
# --------------------------------------------------------------------------
AUDIT_BUCKET_NAME="${AUDIT_BUCKET_NAME:?Must set AUDIT_BUCKET_NAME (e.g. dpp-kill-switch-audit-prod)}"
ALERT_EMAIL="${ALERT_EMAIL:?Must set ALERT_EMAIL (e.g. security-team@example.com)}"
AWS_REGION="${AWS_REGION:-ap-northeast-2}"
AWS_PROFILE="${AWS_PROFILE:-dpp-admin}"

TOPIC_NAME="kill-switch-audit-break-glass-alerts"
RULE_NAME="KillSwitchAuditGovernanceBypass"
TRAIL_NAME="${TRAIL_NAME:-dpp-audit-trail}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INFRA_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

RULE_JSON_TEMPLATE="${INFRA_DIR}/eventbridge/kill_switch_audit_breakglass_rule_v1.json"
TARGET_JSON_TEMPLATE="${INFRA_DIR}/eventbridge/kill_switch_audit_breakglass_target_v1.json"
SAMPLE_EVENT_JSON="${INFRA_DIR}/samples/cloudtrail_s3_breakglass_sample_event_v1.json"

CLI="aws --region ${AWS_REGION} --profile ${AWS_PROFILE}"

echo "======================================================================"
echo "  DPP Phase 6.2: Break-glass Alert Setup"
echo "======================================================================"
echo "  Bucket  : ${AUDIT_BUCKET_NAME}"
echo "  Topic   : ${TOPIC_NAME}"
echo "  Rule    : ${RULE_NAME}"
echo "  Region  : ${AWS_REGION}"
echo "  Profile : ${AWS_PROFILE}"
echo "======================================================================"

# --------------------------------------------------------------------------
# 1. SNS Topic (idempotent: create-topic returns ARN if already exists)
# --------------------------------------------------------------------------
echo ""
echo "[1/6] Ensuring SNS topic exists..."

TOPIC_ARN=$(${CLI} sns create-topic \
  --name "${TOPIC_NAME}" \
  --query 'TopicArn' \
  --output text)

echo "      Topic ARN: ${TOPIC_ARN}"

# --------------------------------------------------------------------------
# 2. SNS Email Subscription
# --------------------------------------------------------------------------
echo ""
echo "[2/6] Checking email subscription for ${ALERT_EMAIL}..."

EXISTING_SUB=$(${CLI} sns list-subscriptions-by-topic \
  --topic-arn "${TOPIC_ARN}" \
  --query "Subscriptions[?Endpoint=='${ALERT_EMAIL}'].SubscriptionArn" \
  --output text)

if [[ -z "${EXISTING_SUB}" || "${EXISTING_SUB}" == "None" ]]; then
  echo "      Subscribing ${ALERT_EMAIL}..."
  ${CLI} sns subscribe \
    --topic-arn "${TOPIC_ARN}" \
    --protocol email \
    --notification-endpoint "${ALERT_EMAIL}" \
    --output text > /dev/null
  echo "      [ACTION REQUIRED] Confirm subscription in the email sent to ${ALERT_EMAIL}"
else
  echo "      Already subscribed (${EXISTING_SUB})"
fi

# --------------------------------------------------------------------------
# 3. SNS Topic Policy — allow EventBridge to publish
# --------------------------------------------------------------------------
echo ""
echo "[3/6] Setting SNS topic policy for EventBridge publish..."

ACCOUNT_ID=$(${CLI} sts get-caller-identity --query Account --output text)

TOPIC_POLICY=$(cat <<EOF
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "AllowEventBridgePublish",
      "Effect": "Allow",
      "Principal": {
        "Service": "events.amazonaws.com"
      },
      "Action": "sns:Publish",
      "Resource": "${TOPIC_ARN}",
      "Condition": {
        "ArnLike": {
          "aws:SourceArn": "arn:aws:events:${AWS_REGION}:${ACCOUNT_ID}:rule/${RULE_NAME}"
        }
      }
    }
  ]
}
EOF
)

${CLI} sns set-topic-attributes \
  --topic-arn "${TOPIC_ARN}" \
  --attribute-name Policy \
  --attribute-value "${TOPIC_POLICY}"

echo "      EventBridge publish policy applied."

# --------------------------------------------------------------------------
# 4. EventBridge Rule (idempotent: put-rule updates if exists)
# --------------------------------------------------------------------------
echo ""
echo "[4/6] Creating/updating EventBridge rule: ${RULE_NAME}..."

# Substitute AUDIT_BUCKET_NAME_PLACEHOLDER in rule JSON
RULE_PATTERN=$(jq \
  --arg bucket "${AUDIT_BUCKET_NAME}" \
  'walk(if type == "string" and . == "AUDIT_BUCKET_NAME_PLACEHOLDER" then $bucket else . end)
   | del(._comment, ._placeholders)' \
  "${RULE_JSON_TEMPLATE}")

${CLI} events put-rule \
  --name "${RULE_NAME}" \
  --event-pattern "${RULE_PATTERN}" \
  --state ENABLED \
  --description "WORM break-glass: governance bypass detected on ${AUDIT_BUCKET_NAME} (Phase 6.2)" \
  --output text > /dev/null

echo "      Rule created/updated: ${RULE_NAME}"

# --------------------------------------------------------------------------
# 5. EventBridge Target with InputTransformer (WHO/WHERE/WHAT)
# --------------------------------------------------------------------------
echo ""
echo "[5/6] Creating/updating EventBridge target (SNS + InputTransformer)..."

# Substitute TOPIC_ARN_PLACEHOLDER in target JSON
TARGETS_JSON=$(jq \
  --arg arn "${TOPIC_ARN}" \
  '[.[] | walk(if type == "string" and . == "TOPIC_ARN_PLACEHOLDER" then $arn else . end)
    | del(._comment, ._placeholders)]' \
  "${TARGET_JSON_TEMPLATE}")

FAILED_ENTRY_COUNT=$(${CLI} events put-targets \
  --rule "${RULE_NAME}" \
  --targets "${TARGETS_JSON}" \
  --query 'FailedEntryCount' \
  --output text)

if [[ "${FAILED_ENTRY_COUNT}" != "0" ]]; then
  echo "      ERROR: ${FAILED_ENTRY_COUNT} target(s) failed to register."
  ${CLI} events put-targets \
    --rule "${RULE_NAME}" \
    --targets "${TARGETS_JSON}" \
    --query 'FailedEntries'
  exit 1
fi

echo "      Target registered with InputTransformer (WHO/WHERE/WHAT)."

# --------------------------------------------------------------------------
# 6. CloudTrail Data Events (WriteOnly on audit bucket)
# --------------------------------------------------------------------------
echo ""
echo "[6/6] Verifying CloudTrail data events for ${AUDIT_BUCKET_NAME}..."

DATA_EVENTS_JSON=$(${CLI} cloudtrail get-event-selectors \
  --trail-name "${TRAIL_NAME}" \
  --query 'EventSelectors[?ReadWriteType==`WriteOnly`].DataResources[].Values[]' \
  --output text 2>/dev/null || echo "")

BUCKET_ARN="arn:aws:s3:::${AUDIT_BUCKET_NAME}/"

if echo "${DATA_EVENTS_JSON}" | grep -q "${BUCKET_ARN}"; then
  echo "      CloudTrail data events already enabled (WriteOnly) for ${AUDIT_BUCKET_NAME}"
else
  echo "      Enabling CloudTrail data events (WriteOnly) for ${AUDIT_BUCKET_NAME}..."
  ${CLI} cloudtrail put-event-selectors \
    --trail-name "${TRAIL_NAME}" \
    --event-selectors "[
      {
        \"ReadWriteType\": \"WriteOnly\",
        \"IncludeManagementEvents\": false,
        \"DataResources\": [
          {
            \"Type\": \"AWS::S3::Object\",
            \"Values\": [\"${BUCKET_ARN}\"]
          }
        ]
      }
    ]" \
    --output text > /dev/null
  echo "      CloudTrail data events enabled."
fi

# --------------------------------------------------------------------------
# Done
# --------------------------------------------------------------------------
echo ""
echo "======================================================================"
echo "  SETUP COMPLETE"
echo "======================================================================"
echo ""
echo "  SNS Topic ARN : ${TOPIC_ARN}"
echo "  EventBridge   : ${RULE_NAME} [ENABLED]"
echo "  CloudTrail    : WriteOnly data events on ${AUDIT_BUCKET_NAME}"
echo ""
echo "  SIMULATION TEST (run manually to verify email delivery):"
echo "  --------------------------------------------------------"
echo "  1. Edit infra/samples/cloudtrail_s3_breakglass_sample_event_v1.json:"
echo "     - ACCOUNT_ID_PLACEHOLDER  → ${ACCOUNT_ID}"
echo "     - AUDIT_BUCKET_NAME_PLACEHOLDER → ${AUDIT_BUCKET_NAME}"
echo ""
echo "  2. Send test event:"
echo "     aws events put-events \\"
echo "       --entries file://${SAMPLE_EVENT_JSON} \\"
echo "       --region ${AWS_REGION} \\"
echo "       --profile ${AWS_PROFILE}"
echo ""
echo "  3. Check email inbox for [WORM BREAK-GLASS DETECTED] with:"
echo "     actor_arn=arn:aws:sts::...assumed-role/dpp-break-glass-role/..."
echo "     source_ip=203.0.113.1"
echo "     request_parameters={\"bucketName\":\"...\",\"x-amz-bypass-governance-retention\":\"true\"}"
echo ""
echo "  NOTE: Confirm SNS subscription if this is the first run."
echo "======================================================================"
