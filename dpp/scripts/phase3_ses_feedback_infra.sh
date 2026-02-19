#!/bin/bash
# Phase 3: SES Feedback Infrastructure Setup
# Creates SNS topics, SQS queue, and wires SES notifications
# IDEMPOTENT: Safe to re-run

set -euo pipefail

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

log_info() { echo -e "${GREEN}[INFO]${NC} $1" >&2; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $1" >&2; }
log_error() { echo -e "${RED}[ERROR]${NC} $1" >&2; }

# Configuration
AWS_REGION="${AWS_REGION:-ap-northeast-2}"
AWS_ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
SES_DOMAIN="${SES_DOMAIN:-}"  # Must be set by user

# Resource names
SNS_BOUNCE="decisionproof-ses-bounce"
SNS_COMPLAINT="decisionproof-ses-complaint"
SNS_DELIVERY="decisionproof-ses-delivery"
SQS_QUEUE="decisionproof-ses-feedback-queue"
SQS_DLQ="decisionproof-ses-feedback-dlq"

log_info "Phase 3: SES Feedback Infrastructure Setup"
log_info "Region: $AWS_REGION"
log_info "Account: $AWS_ACCOUNT_ID"

# Validate SES_DOMAIN
if [ -z "$SES_DOMAIN" ]; then
    log_error "SES_DOMAIN environment variable is not set"
    log_error "Export SES_DOMAIN=<your-verified-domain> and re-run"
    log_error "See docs/PHASE3_SES_IDENTITY.md for details"
    exit 1
fi

log_info "SES Domain: $SES_DOMAIN"

# ============================================================================
# Step 1: Create SNS Topics
# ============================================================================
log_info "Step 1: Creating SNS topics..."

create_sns_topic() {
    local topic_name=$1
    local topic_arn

    if topic_arn=$(aws sns create-topic \
        --name "$topic_name" \
        --region "$AWS_REGION" \
        --query 'TopicArn' \
        --output text 2>/dev/null); then
        log_info "  Created/verified SNS topic: $topic_name"
        echo "$topic_arn"
    else
        log_error "  Failed to create SNS topic: $topic_name"
        exit 1
    fi
}

SNS_BOUNCE_ARN=$(create_sns_topic "$SNS_BOUNCE")
SNS_COMPLAINT_ARN=$(create_sns_topic "$SNS_COMPLAINT")
SNS_DELIVERY_ARN=$(create_sns_topic "$SNS_DELIVERY")

log_info "SNS Topics created:"
log_info "  Bounce:    $SNS_BOUNCE_ARN"
log_info "  Complaint: $SNS_COMPLAINT_ARN"
log_info "  Delivery:  $SNS_DELIVERY_ARN"

# ============================================================================
# Step 2: Create SQS DLQ
# ============================================================================
log_info "Step 2: Creating SQS Dead Letter Queue..."

DLQ_URL=$(aws sqs create-queue \
    --queue-name "$SQS_DLQ" \
    --region "$AWS_REGION" \
    --attributes MessageRetentionPeriod=1209600 \
    --query 'QueueUrl' \
    --output text)

DLQ_ARN=$(aws sqs get-queue-attributes \
    --queue-url "$DLQ_URL" \
    --attribute-names QueueArn \
    --region "$AWS_REGION" \
    --query 'Attributes.QueueArn' \
    --output text)

log_info "DLQ created: $DLQ_ARN"

# ============================================================================
# Step 3: Create SQS Main Queue with DLQ redrive
# ============================================================================
log_info "Step 3: Creating SQS main queue..."

REDRIVE_POLICY=$(cat <<EOF
{
  "deadLetterTargetArn": "$DLQ_ARN",
  "maxReceiveCount": 3
}
EOF
)

QUEUE_URL=$(aws sqs create-queue \
    --queue-name "$SQS_QUEUE" \
    --region "$AWS_REGION" \
    --attributes "{\"RedrivePolicy\":\"$(echo $REDRIVE_POLICY | sed 's/"/\\"/g')\"}" \
    --query 'QueueUrl' \
    --output text)

QUEUE_ARN=$(aws sqs get-queue-attributes \
    --queue-url "$QUEUE_URL" \
    --attribute-names QueueArn \
    --region "$AWS_REGION" \
    --query 'Attributes.QueueArn' \
    --output text)

log_info "Queue created: $QUEUE_ARN"

# ============================================================================
# Step 4: Set SQS Queue Policy (allow SNS topics to send)
# ============================================================================
log_info "Step 4: Setting SQS queue policy..."

QUEUE_POLICY=$(cat <<EOF
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": {
        "Service": "sns.amazonaws.com"
      },
      "Action": "sqs:SendMessage",
      "Resource": "$QUEUE_ARN",
      "Condition": {
        "ArnEquals": {
          "aws:SourceArn": [
            "$SNS_BOUNCE_ARN",
            "$SNS_COMPLAINT_ARN",
            "$SNS_DELIVERY_ARN"
          ]
        }
      }
    }
  ]
}
EOF
)

aws sqs set-queue-attributes \
    --queue-url "$QUEUE_URL" \
    --attributes "{\"Policy\":\"$(echo $QUEUE_POLICY | sed 's/"/\\"/g')\"}" \
    --region "$AWS_REGION"

log_info "Queue policy set"

# ============================================================================
# Step 5: Subscribe SQS to SNS topics
# ============================================================================
log_info "Step 5: Subscribing SQS to SNS topics..."

subscribe_sns_to_sqs() {
    local topic_arn=$1
    local topic_name=$2

    local subscription_arn
    subscription_arn=$(aws sns subscribe \
        --topic-arn "$topic_arn" \
        --protocol sqs \
        --notification-endpoint "$QUEUE_ARN" \
        --region "$AWS_REGION" \
        --attributes RawMessageDelivery=false \
        --query 'SubscriptionArn' \
        --output text)

    log_info "  Subscribed $topic_name -> SQS: $subscription_arn"
}

subscribe_sns_to_sqs "$SNS_BOUNCE_ARN" "Bounce"
subscribe_sns_to_sqs "$SNS_COMPLAINT_ARN" "Complaint"
subscribe_sns_to_sqs "$SNS_DELIVERY_ARN" "Delivery"

# ============================================================================
# Step 6: Configure SES Identity Notifications
# ============================================================================
log_info "Step 6: Configuring SES identity notifications..."

configure_ses_notification() {
    local notification_type=$1
    local topic_arn=$2

    aws ses set-identity-notification-topic \
        --identity "$SES_DOMAIN" \
        --notification-type "$notification_type" \
        --sns-topic "$topic_arn" \
        --region "$AWS_REGION"

    log_info "  Configured SES $notification_type -> SNS"
}

configure_ses_notification "Bounce" "$SNS_BOUNCE_ARN"
configure_ses_notification "Complaint" "$SNS_COMPLAINT_ARN"
configure_ses_notification "Delivery" "$SNS_DELIVERY_ARN"

# Enable forwarding (optional, for debugging)
aws ses set-identity-feedback-forwarding-enabled \
    --identity "$SES_DOMAIN" \
    --forwarding-enabled \
    --region "$AWS_REGION" || log_warn "Failed to enable feedback forwarding (non-critical)"

# ============================================================================
# Step 7: Verification
# ============================================================================
log_info "Step 7: Verifying configuration..."

echo ""
log_info "=== SES Identity Notification Attributes ==="
aws ses get-identity-notification-attributes \
    --identities "$SES_DOMAIN" \
    --region "$AWS_REGION" \
    --output table

echo ""
log_info "=== SNS Subscriptions ==="
aws sns list-subscriptions-by-topic \
    --topic-arn "$SNS_BOUNCE_ARN" \
    --region "$AWS_REGION" \
    --query 'Subscriptions[*].[Endpoint,Protocol]' \
    --output table

echo ""
log_info "=== SQS Queue Attributes ==="
aws sqs get-queue-attributes \
    --queue-url "$QUEUE_URL" \
    --attribute-names All \
    --region "$AWS_REGION" \
    --query 'Attributes' \
    --output table

# ============================================================================
# Output Summary
# ============================================================================
echo ""
log_info "=============================================="
log_info "Phase 3 Infrastructure Setup Complete!"
log_info "=============================================="
echo ""
log_info "Resource ARNs (save these for K8s ConfigMap):"
echo "SNS_BOUNCE_ARN=$SNS_BOUNCE_ARN"
echo "SNS_COMPLAINT_ARN=$SNS_COMPLAINT_ARN"
echo "SNS_DELIVERY_ARN=$SNS_DELIVERY_ARN"
echo "SQS_QUEUE_URL=$QUEUE_URL"
echo "SQS_QUEUE_ARN=$QUEUE_ARN"
echo "SQS_DLQ_URL=$DLQ_URL"
echo "SQS_DLQ_ARN=$DLQ_ARN"
echo ""
log_info "Next steps:"
log_info "1. Run scripts/phase3_irsa.sh to set up IRSA"
log_info "2. Update k8s/configmap-ses-feedback.yaml with above ARNs"
log_info "3. Deploy k8s/deploy-ses-feedback-worker.yaml"
