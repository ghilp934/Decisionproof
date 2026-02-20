# Phase 3: SES Feedback Handling - Runbook

**Purpose**: Step-by-step guide for deploying and verifying SES feedback handling system

**Architecture**: SES → SNS → SQS → EKS Worker (IRSA) → Supabase DB

**NO AWS KEYS**: Uses IRSA (IAM Roles for Service Accounts) exclusively

---

## 📋 Prerequisites Checklist

Before starting, ensure:

- [ ] EKS cluster exists (`dpp-production` in `ap-northeast-2`)
- [ ] OIDC provider is associated with EKS cluster
- [ ] SES domain is verified and out of sandbox mode
- [ ] kubectl is configured for the cluster
- [ ] AWS CLI is installed and authenticated
- [ ] Supabase database is accessible from EKS

---

## 🚀 Deployment Steps

### Step 1: Set Environment Variables

```bash
export AWS_REGION=ap-northeast-2
export AWS_ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
export EKS_CLUSTER_NAME=dpp-production
export K8S_NAMESPACE=dpp-production
export SES_DOMAIN="your-domain.com"  # REPLACE with your verified domain
```

### Step 2: Run Infrastructure Script

Creates SNS topics, SQS queue, and wires SES notifications:

```bash
cd scripts
bash phase3_ses_feedback_infra.sh
```

**Expected output**:
- 3 SNS topics created (bounce/complaint/delivery)
- 1 SQS queue + DLQ created
- SNS subscriptions to SQS configured
- SES identity notifications configured

**Save the ARNs** from output for next steps:
```
SNS_BOUNCE_ARN=arn:aws:sns:...
SNS_COMPLAINT_ARN=arn:aws:sns:...
SNS_DELIVERY_ARN=arn:aws:sns:...
SQS_QUEUE_URL=https://sqs.ap-northeast-2.amazonaws.com/.../decisionproof-ses-feedback-queue
SQS_QUEUE_ARN=arn:aws:sqs:...
SQS_DLQ_URL=https://sqs.ap-northeast-2.amazonaws.com/.../decisionproof-ses-feedback-dlq
```

### Step 3: Set Up IRSA (NO AWS KEYS)

Creates IAM Role and Policy for worker:

```bash
cd scripts
bash phase3_irsa.sh
```

**Expected output**:
```
ROLE_ARN=arn:aws:iam::783268398937:role/DecisionproofSESFeedbackRole
```

**Save this ROLE_ARN** for Step 4.

### Step 4: Update K8s ServiceAccount

Edit `k8s/sa-decisionproof-ses-feedback.yaml`:

```yaml
annotations:
  eks.amazonaws.com/role-arn: "arn:aws:iam::783268398937:role/DecisionproofSESFeedbackRole"
```

Apply:
```bash
kubectl apply -f k8s/sa-decisionproof-ses-feedback.yaml
```

Verify:
```bash
kubectl get sa decisionproof-ses-feedback-sa -n dpp-production -o yaml
```

### Step 5: Update ConfigMap with Queue URLs

Edit `k8s/configmap-ses-feedback.yaml` with ARNs from Step 2:

```yaml
data:
  SQS_QUEUE_URL: "https://sqs.ap-northeast-2.amazonaws.com/783268398937/decisionproof-ses-feedback-queue"
  SNS_BOUNCE_ARN: "arn:aws:sns:ap-northeast-2:783268398937:decisionproof-ses-bounce"
  SNS_COMPLAINT_ARN: "arn:aws:sns:ap-northeast-2:783268398937:decisionproof-ses-complaint"
  SNS_DELIVERY_ARN: "arn:aws:sns:ap-northeast-2:783268398937:decisionproof-ses-delivery"
  SQS_DLQ_URL: "https://sqs.ap-northeast-2.amazonaws.com/783268398937/decisionproof-ses-feedback-dlq"
```

Apply:
```bash
kubectl apply -f k8s/configmap-ses-feedback.yaml
```

### Step 6: Run Database Migration

Apply the migrations to create `public.ses_feedback_events` table with RLS:

**Option A: Using Supabase SQL Editor**
1. Open Supabase Dashboard → SQL Editor
2. Run `migrations/20260217_create_ses_feedback_events.sql` (creates table + enables RLS)
3. Run `migrations/20260220_fix_ses_feedback_events_rls_and_guard.sql` (RLS + P1-B event trigger)

**Option B: Using psql**
```bash
psql "$DATABASE_URL" -f migrations/20260217_create_ses_feedback_events.sql
psql "$DATABASE_URL" -f migrations/20260220_fix_ses_feedback_events_rls_and_guard.sql
```

Verify table and RLS state:
```bash
psql "$DATABASE_URL" -f docs/p0_rls_ses_feedback_verify.sql
```

### Step 7: Build and Push Docker Image

```bash
cd apps/worker_ses_feedback

# Build image
docker build -t dpp-ses-feedback-worker:0.4.2.2 .

# Tag for ECR
docker tag dpp-ses-feedback-worker:0.4.2.2 \
  ${AWS_ACCOUNT_ID}.dkr.ecr.ap-northeast-2.amazonaws.com/dpp-ses-feedback-worker:0.4.2.2

# Login to ECR
aws ecr get-login-password --region ap-northeast-2 | \
  docker login --username AWS --password-stdin \
  ${AWS_ACCOUNT_ID}.dkr.ecr.ap-northeast-2.amazonaws.com

# Create ECR repository (if doesn't exist)
aws ecr create-repository \
  --repository-name dpp-ses-feedback-worker \
  --region ap-northeast-2 || echo "Repository already exists"

# Push image
docker push ${AWS_ACCOUNT_ID}.dkr.ecr.ap-northeast-2.amazonaws.com/dpp-ses-feedback-worker:0.4.2.2
```

### Step 8: Deploy Worker

```bash
kubectl apply -f k8s/deploy-ses-feedback-worker.yaml
```

Verify deployment:
```bash
kubectl get pods -n dpp-production -l app=dpp-ses-feedback
kubectl logs -f -n dpp-production -l app=dpp-ses-feedback
```

**Expected logs**:
```
INFO - AWS Identity: arn:aws:sts::783268398937:assumed-role/DecisionproofSESFeedbackRole/...
INFO - Worker initialized: queue=https://sqs.ap-northeast-2.amazonaws.com/.../decisionproof-ses-feedback-queue
INFO - Starting SES feedback worker...
DEBUG - Polling SQS (max 10 messages)...
```

---

## ✅ Verification & Testing

### Test 1: Verify AWS Identity (IRSA)

Exec into worker pod:
```bash
POD_NAME=$(kubectl get pods -n dpp-production -l app=dpp-ses-feedback -o jsonpath='{.items[0].metadata.name}')
kubectl exec -it $POD_NAME -n dpp-production -- /bin/bash
```

Inside pod:
```bash
# Test AWS identity (should show assumed role)
python -c "import boto3; print(boto3.client('sts').get_caller_identity())"

# Should output:
# {'UserId': '...', 'Account': '783268398937', 'Arn': 'arn:aws:sts::...:assumed-role/DecisionproofSESFeedbackRole/...'}

# Verify SQS access
python -c "import boto3; import os; print(boto3.client('sqs').get_queue_attributes(QueueUrl=os.getenv('SQS_QUEUE_URL'), AttributeNames=['All']))"
```

### Test 2: Trigger Bounce Notification

Send a test email to SES mailbox simulator:

```bash
# Using AWS CLI
aws ses send-email \
  --from "sender@${SES_DOMAIN}" \
  --destination "ToAddresses=bounce@simulator.amazonses.com" \
  --message "Subject={Data=Test Bounce},Body={Text={Data=Test message}}" \
  --region ap-northeast-2
```

**Expected flow**:
1. SES sends email to bounce simulator
2. Bounce notification sent to SNS topic
3. SNS delivers to SQS queue
4. Worker polls SQS and receives message
5. Worker stores in `ses_feedback_events` table
6. Worker deletes message from SQS

### Test 3: Verify Database Record

```bash
psql "$DATABASE_URL" -c "SELECT id, notification_type, source, primary_recipient, type_data->'bounce_type' AS bounce_type FROM public.ses_feedback_events ORDER BY received_at DESC LIMIT 5;"
```

Expected output:
```
                  id                  | notification_type |        source         | primary_recipient | bounce_type
--------------------------------------+-------------------+-----------------------+-------------------+-------------
 xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx | Bounce            | sender@your-domain... | bounce@simulator...| "Permanent"
```

### Test 4: Check SQS Queue Metrics

```bash
aws sqs get-queue-attributes \
  --queue-url $SQS_QUEUE_URL \
  --attribute-names ApproximateNumberOfMessages,ApproximateNumberOfMessagesNotVisible \
  --region ap-northeast-2
```

Should show:
- `ApproximateNumberOfMessages`: 0 (all processed)
- `ApproximateNumberOfMessagesNotVisible`: 0 (none in-flight)

### Test 5: Check DLQ (Dead Letter Queue)

```bash
aws sqs get-queue-attributes \
  --queue-url $SQS_DLQ_URL \
  --attribute-names ApproximateNumberOfMessages \
  --region ap-northeast-2
```

Should show:
- `ApproximateNumberOfMessages`: 0 (no failures)

If messages in DLQ, inspect:
```bash
aws sqs receive-message \
  --queue-url $SQS_DLQ_URL \
  --max-number-of-messages 1 \
  --region ap-northeast-2
```

---

## 🔍 Monitoring

### Key Metrics to Monitor

**SQS Metrics** (CloudWatch):
- `ApproximateNumberOfMessagesVisible` (should stay near 0)
- `ApproximateAgeOfOldestMessage` (should be low)
- `NumberOfMessagesSent` (SNS -> SQS delivery)
- `NumberOfMessagesReceived` (worker consumption)

**Worker Metrics** (Logs):
- "Processed [type] notification" count
- "Failed to process message" errors
- AWS identity verification on startup

**Database Metrics** (Supabase):
```sql
-- Count by notification type
SELECT notification_type, COUNT(*)
FROM public.ses_feedback_events
WHERE received_at > NOW() - INTERVAL '1 day'
GROUP BY notification_type;

-- Recent bounces
SELECT * FROM public.ses_feedback_events
WHERE notification_type = 'Bounce'
ORDER BY received_at DESC
LIMIT 10;
```

### Alerts to Set Up

1. **DLQ has messages**: Critical
2. **Worker pod not running**: Critical
3. **SQS messages aging >5 minutes**: Warning
4. **High bounce rate**: Warning (business logic)

---

## ❌ Troubleshooting

### Issue: "AccessDenied" in worker logs

**Symptoms**:
```
botocore.exceptions.ClientError: An error occurred (AccessDenied) when calling the ReceiveMessage operation
```

**Solution**:
1. Verify IRSA setup: `kubectl get sa decisionproof-ses-feedback-sa -n dpp-production -o yaml`
2. Check IAM Role trust policy includes correct OIDC + ServiceAccount
3. Verify IAM Policy allows SQS actions
4. Re-run `scripts/phase3_irsa.sh`

### Issue: "Unable to locate credentials"

**Symptoms**:
```
botocore.exceptions.NoCredentialsError: Unable to locate credentials
```

**Solution**:
1. Verify pod is using correct ServiceAccount: `kubectl get pod $POD_NAME -n dpp-production -o yaml | grep serviceAccountName`
2. Verify OIDC provider exists: `eksctl utils associate-iam-oidc-provider --cluster=$EKS_CLUSTER_NAME --approve`
3. Check AWS SDK can find credentials: `kubectl exec ... -- env | grep AWS`

### Issue: No messages in SQS

**Symptoms**: Worker logs show "No messages received" continuously

**Solution**:
1. Verify SES notifications are configured: `aws ses get-identity-notification-attributes --identities $SES_DOMAIN --region ap-northeast-2`
2. Check SNS subscriptions: `aws sns list-subscriptions-by-topic --topic-arn $SNS_BOUNCE_ARN --region ap-northeast-2`
3. Send test email to bounce@simulator.amazonses.com
4. Check SNS topic metrics in CloudWatch

### Issue: Messages in DLQ

**Symptoms**: `ApproximateNumberOfMessages` in DLQ > 0

**Solution**:
1. Retrieve failed message: `aws sqs receive-message --queue-url $SQS_DLQ_URL ...`
2. Check worker logs for parsing errors
3. Verify database connectivity
4. Check database schema matches migration
5. Fix issue and redrive from DLQ or manually reprocess

### Issue: Database connection errors

**Symptoms**:
```
psycopg2.OperationalError: could not connect to server
```

**Solution**:
1. Verify DATABASE_URL secret is set: `kubectl get secret dpp-secrets -n dpp-production -o yaml`
2. Test connectivity from worker pod: `kubectl exec ... -- psql "$DATABASE_URL" -c "SELECT 1"`
3. Check Supabase network restrictions (IP allowlist)
4. Verify VPC/security group settings allow EKS -> Supabase

---

## ✅ Success Criteria Checklist

- [ ] NO AWS keys exist in repo, CI, or K8s secrets/env
- [ ] IRSA role is attached to K8s ServiceAccount
- [ ] Worker pod can call `aws sts get-caller-identity` (shows assumed role)
- [ ] SES bounce/complaint/delivery notifications wired to SNS topics
- [ ] SNS topics deliver to SQS queue
- [ ] Worker consumes SQS messages and writes to Supabase DB
- [ ] Test bounce creates row in `ses_feedback_events` table
- [ ] Failed processing goes to DLQ (or retries deterministically)
- [ ] Documentation is copy-paste runnable
- [ ] Zero messages in DLQ after test

---

## 🔐 Security Verification

**CRITICAL**: Verify NO static AWS keys anywhere:

```bash
# Search repo for AWS keys (should return nothing)
grep -r "AWS_ACCESS_KEY_ID" k8s/
grep -r "AWS_SECRET_ACCESS_KEY" k8s/
grep -r "AKIA" .  # AWS Access Key pattern

# Verify worker env (should NOT have AWS_ACCESS_KEY_ID)
kubectl exec $POD_NAME -n dpp-production -- env | grep -i aws_access

# Verify Secrets don't contain AWS keys
kubectl get secret dpp-secrets -n dpp-production -o yaml | grep -i aws_access
```

**All checks should return EMPTY or "not found".**

---

## 📊 Operational Commands

```bash
# View worker logs
kubectl logs -f -n dpp-production -l app=dpp-ses-feedback

# Restart worker
kubectl rollout restart deployment dpp-ses-feedback-worker -n dpp-production

# Scale worker (if needed)
kubectl scale deployment dpp-ses-feedback-worker -n dpp-production --replicas=2

# View SQS metrics
aws cloudwatch get-metric-statistics \
  --namespace AWS/SQS \
  --metric-name ApproximateNumberOfMessagesVisible \
  --dimensions Name=QueueName,Value=decisionproof-ses-feedback-queue \
  --start-time $(date -u -d '1 hour ago' +%Y-%m-%dT%H:%M:%S) \
  --end-time $(date -u +%Y-%m-%dT%H:%M:%S) \
  --period 300 \
  --statistics Average \
  --region ap-northeast-2

# Query recent feedback
psql "$DATABASE_URL" -c "SELECT notification_type, COUNT(*) FROM public.ses_feedback_events WHERE received_at > NOW() - INTERVAL '1 hour' GROUP BY notification_type;"
```

---

**Last Updated**: 2026-02-17
**Phase**: 3 (SES Feedback Handling)
**Status**: Production Ready
