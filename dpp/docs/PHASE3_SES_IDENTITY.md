# Phase 3: SES Identity Configuration

**Purpose**: Document SES sending identity (domain) for feedback notifications

---

## 📋 Current SES Identity

**Primary Sending Domain**: `<YOUR_DOMAIN_HERE>`

**Region**: `ap-northeast-2` (Seoul)

**Verification Status**: Must be verified before running Phase 3 scripts

---

## ✅ Prerequisites

### 1. Verify Your Domain in SES

```bash
# Check if domain is verified
aws ses get-identity-verification-attributes \
  --identities <YOUR_DOMAIN> \
  --region ap-northeast-2

# If not verified, verify it:
aws ses verify-domain-identity \
  --domain <YOUR_DOMAIN> \
  --region ap-northeast-2

# Add the returned TXT record to your DNS
```

### 2. Move Out of SES Sandbox (Production)

If in sandbox mode, you can only send to verified email addresses. For production:

```bash
# Request production access (via AWS Support)
# https://console.aws.amazon.com/support/home#/case/create?issueType=service-limit-increase
```

---

## 🔧 Configuration

Export the domain before running infrastructure scripts:

```bash
export SES_DOMAIN="your-domain.com"

# Then run:
bash scripts/phase3_ses_feedback_infra.sh
```

---

## 📊 Current Configuration

Once `phase3_ses_feedback_infra.sh` has run, check current setup:

```bash
# View notification configuration
aws ses get-identity-notification-attributes \
  --identities <YOUR_DOMAIN> \
  --region ap-northeast-2

# View sending statistics
aws ses get-send-statistics \
  --region ap-northeast-2
```

---

## 🔍 Troubleshooting

### Issue: "Identity not found"
**Solution**: Domain must be verified in SES first.

### Issue: "Sandbox mode"
**Solution**: Request production access via AWS Support.

### Issue: "Notifications not arriving"
**Solution**: Check SNS topic subscriptions and SQS queue policy.

---

**Last Updated**: 2026-02-17
**Phase**: 3 (SES Feedback Handling)
