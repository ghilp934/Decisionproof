"""SES Feedback Worker

Polls SQS for SES bounce/complaint/delivery notifications and stores in Supabase.

Phase 3: NO AWS KEYS - uses IRSA (IAM Roles for Service Accounts)
"""

__version__ = "0.4.2.2"
