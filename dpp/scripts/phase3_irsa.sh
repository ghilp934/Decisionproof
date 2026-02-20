#!/bin/bash
# Phase 3: IRSA (IAM Roles for Service Accounts) Setup
# Creates IAM Role + Policy for SES feedback worker (NO AWS KEYS)
# IDEMPOTENT: Safe to re-run

set -euo pipefail

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

log_info() { echo -e "${GREEN}[INFO]${NC} $1"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }

# Configuration
AWS_REGION="${AWS_REGION:-ap-northeast-2}"
AWS_ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
EKS_CLUSTER_NAME="${EKS_CLUSTER_NAME:-dpp-production}"
K8S_NAMESPACE="${K8S_NAMESPACE:-dpp-production}"
SERVICE_ACCOUNT_NAME="decisionproof-ses-feedback-sa"

IAM_POLICY_NAME="DecisionproofSESFeedbackPolicy"
IAM_ROLE_NAME="DecisionproofSESFeedbackRole"

POLICY_FILE="infra/iam/decisionproof-ses-feedback-policy.json"

log_info "Phase 3: IRSA Setup (NO AWS KEYS)"
log_info "Region: $AWS_REGION"
log_info "Account: $AWS_ACCOUNT_ID"
log_info "EKS Cluster: $EKS_CLUSTER_NAME"
log_info "K8s Namespace: $K8S_NAMESPACE"
log_info "ServiceAccount: $SERVICE_ACCOUNT_NAME"

# ============================================================================
# Step 1: Verify OIDC Provider
# ============================================================================
log_info "Step 1: Verifying OIDC provider..."

OIDC_PROVIDER=$(aws eks describe-cluster \
    --name "$EKS_CLUSTER_NAME" \
    --region "$AWS_REGION" \
    --query "cluster.identity.oidc.issuer" \
    --output text | sed -e "s/^https:\/\///")

if [ -z "$OIDC_PROVIDER" ]; then
    log_error "OIDC provider not found for cluster $EKS_CLUSTER_NAME"
    log_error "Create OIDC provider:"
    log_error "  eksctl utils associate-iam-oidc-provider --cluster=$EKS_CLUSTER_NAME --region=$AWS_REGION --approve"
    exit 1
fi

log_info "OIDC Provider: $OIDC_PROVIDER"

# Verify OIDC provider exists in IAM
if aws iam get-open-id-connect-provider \
    --open-id-connect-provider-arn "arn:aws:iam::${AWS_ACCOUNT_ID}:oidc-provider/${OIDC_PROVIDER}" \
    --region "$AWS_REGION" &>/dev/null; then
    log_info "OIDC provider exists in IAM"
else
    log_warn "OIDC provider not found in IAM. Creating..."
    log_error "Run: eksctl utils associate-iam-oidc-provider --cluster=$EKS_CLUSTER_NAME --region=$AWS_REGION --approve"
    exit 1
fi

# ============================================================================
# Step 2: Create/Update IAM Policy
# ============================================================================
log_info "Step 2: Creating IAM policy..."

if [ ! -f "$POLICY_FILE" ]; then
    log_error "Policy file not found: $POLICY_FILE"
    exit 1
fi

# Try to create policy (idempotent)
POLICY_ARN="arn:aws:iam::${AWS_ACCOUNT_ID}:policy/${IAM_POLICY_NAME}"

if aws iam get-policy --policy-arn "$POLICY_ARN" &>/dev/null; then
    log_info "Policy already exists: $POLICY_ARN"

    # Update policy to latest version
    log_info "Updating policy to latest version..."
    aws iam create-policy-version \
        --policy-arn "$POLICY_ARN" \
        --policy-document "file://$POLICY_FILE" \
        --set-as-default || log_warn "Policy update failed (non-critical if identical)"
else
    log_info "Creating new policy..."
    aws iam create-policy \
        --policy-name "$IAM_POLICY_NAME" \
        --policy-document "file://$POLICY_FILE" \
        --description "SES feedback worker SQS access (Phase 3)"

    log_info "Policy created: $POLICY_ARN"
fi

# ============================================================================
# Step 3: Create IAM Role with Trust Policy
# ============================================================================
log_info "Step 3: Creating IAM role..."

TRUST_POLICY=$(cat <<EOF
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": {
        "Federated": "arn:aws:iam::${AWS_ACCOUNT_ID}:oidc-provider/${OIDC_PROVIDER}"
      },
      "Action": "sts:AssumeRoleWithWebIdentity",
      "Condition": {
        "StringEquals": {
          "${OIDC_PROVIDER}:sub": "system:serviceaccount:${K8S_NAMESPACE}:${SERVICE_ACCOUNT_NAME}",
          "${OIDC_PROVIDER}:aud": "sts.amazonaws.com"
        }
      }
    }
  ]
}
EOF
)

ROLE_ARN="arn:aws:iam::${AWS_ACCOUNT_ID}:role/${IAM_ROLE_NAME}"

if aws iam get-role --role-name "$IAM_ROLE_NAME" &>/dev/null; then
    log_info "Role already exists: $ROLE_ARN"

    # Update trust policy
    log_info "Updating trust policy..."
    echo "$TRUST_POLICY" > ./trust-policy-temp.json
    aws iam update-assume-role-policy \
        --role-name "$IAM_ROLE_NAME" \
        --policy-document "file://trust-policy-temp.json"
    rm -f ./trust-policy-temp.json
else
    log_info "Creating new role..."
    echo "$TRUST_POLICY" > ./trust-policy-temp.json
    aws iam create-role \
        --role-name "$IAM_ROLE_NAME" \
        --assume-role-policy-document "file://trust-policy-temp.json" \
        --description "SES feedback worker IRSA role (Phase 3)"
    rm -f ./trust-policy-temp.json

    log_info "Role created: $ROLE_ARN"
fi

# ============================================================================
# Step 4: Attach Policy to Role
# ============================================================================
log_info "Step 4: Attaching policy to role..."

aws iam attach-role-policy \
    --role-name "$IAM_ROLE_NAME" \
    --policy-arn "$POLICY_ARN" || log_info "Policy already attached"

log_info "Policy attached"

# ============================================================================
# Step 5: Verification
# ============================================================================
log_info "Step 5: Verifying configuration..."

echo ""
log_info "=== IAM Role Details ==="
aws iam get-role --role-name "$IAM_ROLE_NAME" --query 'Role.{RoleName:RoleName,Arn:Arn,CreateDate:CreateDate}' --output table

echo ""
log_info "=== Attached Policies ==="
aws iam list-attached-role-policies --role-name "$IAM_ROLE_NAME" --output table

# ============================================================================
# Output Summary
# ============================================================================
echo ""
log_info "=============================================="
log_info "Phase 3 IRSA Setup Complete!"
log_info "=============================================="
echo ""
log_info "ROLE_ARN (use in K8s ServiceAccount annotation):"
echo "ROLE_ARN=$ROLE_ARN"
echo ""
log_info "Next steps:"
log_info "1. Update k8s/sa-decisionproof-ses-feedback.yaml with ROLE_ARN"
log_info "2. kubectl apply -f k8s/sa-decisionproof-ses-feedback.yaml"
log_info "3. Deploy worker: kubectl apply -f k8s/deploy-ses-feedback-worker.yaml"
echo ""
log_info "CRITICAL: Do NOT set AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY in any K8s resource"
log_info "The worker will use IRSA to assume the IAM role automatically"
