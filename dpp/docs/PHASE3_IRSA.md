# Phase 3: IRSA (IAM Roles for Service Accounts)

**Purpose**: Enable EKS pods to access AWS services WITHOUT static AWS keys

**CRITICAL**: NO `AWS_ACCESS_KEY_ID` or `AWS_SECRET_ACCESS_KEY` anywhere in repo, CI, or K8s

---

## 🎯 Architecture

```
EKS Pod (with ServiceAccount)
  → IRSA automatically injects AWS credentials via webhook
  → Pod assumes IAM Role
  → IAM Role has SQS permissions
  → Pod can access SQS (no keys needed)
```

---

## ✅ Prerequisites

### 1. OIDC Provider must exist

Check if OIDC provider is associated with your EKS cluster:

```bash
aws eks describe-cluster \
  --name dpp-production \
  --region ap-northeast-2 \
  --query "cluster.identity.oidc.issuer"
```

If empty, create it:

```bash
eksctl utils associate-iam-oidc-provider \
  --cluster dpp-production \
  --region ap-northeast-2 \
  --approve
```

---

## 🚀 Setup

### Step 1: Run IRSA Script

```bash
export AWS_REGION=ap-northeast-2
export EKS_CLUSTER_NAME=dpp-production
export K8S_NAMESPACE=dpp-production

bash scripts/phase3_irsa.sh
```

**Output**: `ROLE_ARN` (copy this for next step)

### Step 2: Update ServiceAccount with ROLE_ARN

Edit `k8s/sa-decisionproof-ses-feedback.yaml`:

```yaml
annotations:
  eks.amazonaws.com/role-arn: "arn:aws:iam::783268398937:role/DecisionproofSESFeedbackRole"
```

### Step 3: Apply ServiceAccount

```bash
kubectl apply -f k8s/sa-decisionproof-ses-feedback.yaml
```

### Step 4: Verify

```bash
kubectl get sa decisionproof-ses-feedback-sa -n dpp-production -o yaml
```

Should show annotation:
```yaml
annotations:
  eks.amazonaws.com/role-arn: arn:aws:iam::...
```

---

## 🔍 Verification

### Test AWS Identity from Pod

Deploy a test pod with the ServiceAccount:

```bash
kubectl run test-irsa \
  --image=amazon/aws-cli:latest \
  --serviceaccount=decisionproof-ses-feedback-sa \
  --namespace=dpp-production \
  --command -- sleep 3600

# Exec into pod and test
kubectl exec -it test-irsa -n dpp-production -- /bin/bash

# Inside pod:
aws sts get-caller-identity
# Should show: "Arn": "arn:aws:sts::...:assumed-role/DecisionproofSESFeedbackRole/..."

# Test SQS access
aws sqs get-queue-attributes \
  --queue-url https://sqs.ap-northeast-2.amazonaws.com/783268398937/decisionproof-ses-feedback-queue \
  --attribute-names All \
  --region ap-northeast-2

# Cleanup
kubectl delete pod test-irsa -n dpp-production
```

---

## ❌ Common Mistakes

### ❌ DO NOT DO THIS

```yaml
# WRONG: Setting AWS keys in env
env:
- name: AWS_ACCESS_KEY_ID
  value: "AKIA..."
- name: AWS_SECRET_ACCESS_KEY
  valueFrom:
    secretKeyRef:
      name: aws-creds
      key: secret-key
```

### ✅ DO THIS INSTEAD

```yaml
# CORRECT: Use ServiceAccount with IRSA
serviceAccountName: decisionproof-ses-feedback-sa
# AWS SDK automatically picks up credentials from IRSA
```

---

## 🔧 Troubleshooting

### Issue: "AccessDenied" in pod logs

**Cause**: IAM Role not attached or policy insufficient

**Solution**:
1. Check ServiceAccount annotation: `kubectl get sa ... -o yaml`
2. Verify IAM Role trust policy includes correct OIDC + ServiceAccount
3. Verify IAM Policy allows required actions

### Issue: "Unable to locate credentials"

**Cause**: IRSA webhook not injecting credentials

**Solution**:
1. Verify OIDC provider exists: `eksctl utils associate-iam-oidc-provider ...`
2. Verify pod is using correct ServiceAccount
3. Check pod environment: `kubectl exec ... -- env | grep AWS`

### Issue: "AssumeRoleWithWebIdentity failed"

**Cause**: Trust policy mismatch

**Solution**:
1. Re-run `scripts/phase3_irsa.sh` to update trust policy
2. Verify namespace and ServiceAccount name match exactly

---

## 📊 IAM Policy Details

**Policy Name**: `DecisionproofSESFeedbackPolicy`

**Permissions**:
- `sqs:ReceiveMessage` on `decisionproof-ses-feedback-queue`
- `sqs:DeleteMessage` on `decisionproof-ses-feedback-queue`
- `sqs:GetQueueAttributes` on `decisionproof-ses-feedback-queue`
- `sqs:ChangeMessageVisibility` on `decisionproof-ses-feedback-queue`
- `sqs:ReceiveMessage` on `decisionproof-ses-feedback-dlq` (read-only)
- `cloudwatch:PutMetricData` (optional, namespace-scoped)

**Least Privilege**: Only grants access to specific SQS queues, no wildcard

---

## 🔐 Security

### ✅ Secure
- IRSA uses temporary credentials (auto-rotated every 15 minutes)
- Scoped to specific K8s namespace + ServiceAccount
- No long-term credentials stored anywhere
- IAM Role trust policy enforces OIDC audience + subject

### ❌ Insecure (what we're avoiding)
- Static AWS keys in K8s Secrets
- Static AWS keys in environment variables
- Overly broad IAM policies (`*` resources)
- Shared credentials across multiple services

---

**Last Updated**: 2026-02-17
**Phase**: 3 (SES Feedback Handling)
