"""Unit tests for SES notification parsing

Tests both raw SES JSON and SNS envelope JSON formats.
"""

import json
import unittest
from worker import SESFeedbackWorker


class TestSESNotificationParsing(unittest.TestCase):
    """Test SES notification parsing logic."""

    def setUp(self):
        """Set up test worker (skip init)."""
        # Mock environment for testing
        import os
        os.environ['SQS_QUEUE_URL'] = 'https://sqs.ap-northeast-2.amazonaws.com/123/test'
        os.environ['DATABASE_URL'] = 'postgres://test'

    def test_parse_raw_bounce_notification(self):
        """Test parsing raw SES bounce notification."""
        raw_bounce = {
            "notificationType": "Bounce",
            "mail": {
                "messageId": "0000014a8a7c0000-aaaa-bbbb-cccc-000000000000",
                "timestamp": "2026-02-17T10:00:00.000Z",
                "source": "sender@example.com",
                "destination": ["bounced@example.com"]
            },
            "bounce": {
                "bounceType": "Permanent",
                "bounceSubType": "General",
                "bouncedRecipients": [
                    {"emailAddress": "bounced@example.com"}
                ]
            }
        }

        worker = SESFeedbackWorker()
        result = worker._parse_ses_notification(json.dumps(raw_bounce))

        self.assertIsNotNone(result)
        self.assertEqual(result['notification_type'], 'Bounce')
        self.assertEqual(result['message_id'], '0000014a8a7c0000-aaaa-bbbb-cccc-000000000000')
        self.assertEqual(result['source'], 'sender@example.com')
        self.assertEqual(result['destinations'], ['bounced@example.com'])
        self.assertEqual(result['primary_recipient'], 'bounced@example.com')
        self.assertEqual(result['type_data']['bounce_type'], 'Permanent')
        self.assertEqual(result['type_data']['bounce_sub_type'], 'General')

    def test_parse_sns_envelope_complaint(self):
        """Test parsing SES complaint notification wrapped in SNS envelope."""
        sns_envelope = {
            "Type": "Notification",
            "MessageId": "sns-message-id",
            "TopicArn": "arn:aws:sns:ap-northeast-2:123:test-topic",
            "Message": json.dumps({
                "notificationType": "Complaint",
                "mail": {
                    "messageId": "0000014a8a7c0001-xxxx-yyyy-zzzz-111111111111",
                    "timestamp": "2026-02-17T11:00:00.000Z",
                    "source": "sender@example.com",
                    "destination": ["complained@example.com"]
                },
                "complaint": {
                    "complaintFeedbackType": "abuse",
                    "complainedRecipients": [
                        {"emailAddress": "complained@example.com"}
                    ]
                }
            })
        }

        worker = SESFeedbackWorker()
        result = worker._parse_ses_notification(json.dumps(sns_envelope))

        self.assertIsNotNone(result)
        self.assertEqual(result['notification_type'], 'Complaint')
        self.assertEqual(result['message_id'], '0000014a8a7c0001-xxxx-yyyy-zzzz-111111111111')
        self.assertEqual(result['type_data']['complaint_feedback_type'], 'abuse')
        self.assertIn('complained@example.com', result['type_data']['complained_recipients'])

    def test_parse_delivery_notification(self):
        """Test parsing SES delivery notification."""
        delivery = {
            "notificationType": "Delivery",
            "mail": {
                "messageId": "0000014a8a7c0002-pppp-qqqq-rrrr-222222222222",
                "timestamp": "2026-02-17T12:00:00.000Z",
                "source": "sender@example.com",
                "destination": ["delivered@example.com"]
            },
            "delivery": {
                "processingTimeMillis": 1234,
                "smtpResponse": "250 2.0.0 OK"
            }
        }

        worker = SESFeedbackWorker()
        result = worker._parse_ses_notification(json.dumps(delivery))

        self.assertIsNotNone(result)
        self.assertEqual(result['notification_type'], 'Delivery')
        self.assertEqual(result['type_data']['processing_time_millis'], 1234)
        self.assertEqual(result['type_data']['smtp_response'], '250 2.0.0 OK')

    def test_parse_invalid_json(self):
        """Test handling of invalid JSON."""
        worker = SESFeedbackWorker()
        result = worker._parse_ses_notification("not valid json{}")

        self.assertIsNone(result)

    def test_parse_unknown_notification_type(self):
        """Test handling of unknown notification type."""
        unknown = {
            "notificationType": "UnknownType",
            "mail": {
                "messageId": "test-id",
                "timestamp": "2026-02-17T13:00:00.000Z",
                "source": "sender@example.com",
                "destination": ["test@example.com"]
            }
        }

        worker = SESFeedbackWorker()
        result = worker._parse_ses_notification(json.dumps(unknown))

        self.assertIsNone(result)

    def test_extract_message_id(self):
        """Test that messageId is correctly extracted."""
        notification = {
            "notificationType": "Bounce",
            "mail": {
                "messageId": "extracted-message-id",
                "timestamp": "2026-02-17T14:00:00.000Z",
                "source": "test@example.com",
                "destination": ["test@example.com"]
            },
            "bounce": {
                "bounceType": "Transient",
                "bounceSubType": "General",
                "bouncedRecipients": []
            }
        }

        worker = SESFeedbackWorker()
        result = worker._parse_ses_notification(json.dumps(notification))

        self.assertEqual(result['message_id'], 'extracted-message-id')


if __name__ == '__main__':
    unittest.main()
