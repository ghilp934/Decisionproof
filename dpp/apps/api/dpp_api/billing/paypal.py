"""PayPal Orders API client for P0-2 Paid Pilot.

DEC-P02-1: Provider 이원화 (PayPal)
DEC-P02-5: Webhook 검증 정책

PayPal API Reference:
- Orders API v2: https://developer.paypal.com/docs/api/orders/v2/
- Webhooks: https://developer.paypal.com/api/rest/webhooks/
"""

import base64
import logging
import os
from typing import Optional

import httpx

logger = logging.getLogger(__name__)


class PayPalClient:
    """PayPal Orders API client (CAPTURE flow).

    Environment Variables:
    - PAYPAL_ENV: sandbox or live
    - PAYPAL_CLIENT_ID: PayPal app client ID
    - PAYPAL_CLIENT_SECRET: PayPal app client secret
    - PAYPAL_WEBHOOK_ID: Webhook ID for signature verification
    """

    def __init__(self):
        self.env = os.getenv("PAYPAL_ENV", "sandbox")
        self.client_id = os.getenv("PAYPAL_CLIENT_ID")
        self.client_secret = os.getenv("PAYPAL_CLIENT_SECRET")
        self.webhook_id = os.getenv("PAYPAL_WEBHOOK_ID")

        if not self.client_id or not self.client_secret:
            raise ValueError(
                "PAYPAL_CLIENT_ID and PAYPAL_CLIENT_SECRET are required. "
                "Set them in environment configuration."
            )

        self.base_url = (
            "https://api-m.sandbox.paypal.com"
            if self.env == "sandbox"
            else "https://api-m.paypal.com"
        )

    async def get_access_token(self) -> str:
        """Get OAuth 2.0 access token from PayPal.

        Returns:
            Access token string

        Raises:
            httpx.HTTPStatusError: If token request fails
        """
        url = f"{self.base_url}/v1/oauth2/token"

        # Basic Auth: base64(client_id:client_secret)
        credentials = f"{self.client_id}:{self.client_secret}"
        encoded_credentials = base64.b64encode(credentials.encode()).decode()

        headers = {
            "Authorization": f"Basic {encoded_credentials}",
            "Content-Type": "application/x-www-form-urlencoded",
        }

        data = {"grant_type": "client_credentials"}

        async with httpx.AsyncClient() as client:
            response = await client.post(url, headers=headers, data=data, timeout=30.0)
            response.raise_for_status()

            result = response.json()
            return result["access_token"]

    async def create_order(
        self,
        *,
        amount: str,
        currency: str,
        internal_order_id: str,
        plan_id: str,
        return_url: Optional[str] = None,
        cancel_url: Optional[str] = None,
        request_id: Optional[str] = None,
    ) -> dict:
        """Create PayPal order with CAPTURE intent.

        Args:
            amount: Amount as decimal string (e.g., "10.00")
            currency: Currency code (USD, EUR, etc.)
            internal_order_id: Internal order ID for reference
            plan_id: Plan ID being purchased
            return_url: URL to redirect after approval (optional)
            cancel_url: URL to redirect on cancel (optional)
            request_id: PayPal-Request-Id for idempotency (optional)

        Returns:
            PayPal order response dict

        Raises:
            httpx.HTTPStatusError: If create order fails
        """
        access_token = await self.get_access_token()
        url = f"{self.base_url}/v2/checkout/orders"

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {access_token}",
        }

        # PayPal-Request-Id for idempotency
        if request_id:
            headers["PayPal-Request-Id"] = request_id

        # Build order request
        order_request = {
            "intent": "CAPTURE",
            "purchase_units": [
                {
                    "reference_id": internal_order_id,
                    "description": f"Decisionproof {plan_id} subscription",
                    "custom_id": internal_order_id,
                    "amount": {
                        "currency_code": currency,
                        "value": amount,
                    },
                }
            ],
            "application_context": {
                "brand_name": "Decisionproof",
                "landing_page": "NO_PREFERENCE",
                "user_action": "PAY_NOW",
                "return_url": return_url or "https://api.decisionproof.ai/billing/paypal/return",
                "cancel_url": cancel_url or "https://api.decisionproof.ai/billing/paypal/cancel",
            },
        }

        async with httpx.AsyncClient() as client:
            response = await client.post(
                url, headers=headers, json=order_request, timeout=30.0
            )
            response.raise_for_status()

            result = response.json()
            logger.info(
                "PayPal order created",
                extra={
                    "event": "paypal.order.created",
                    "order_id": result.get("id"),
                    "internal_order_id": internal_order_id,
                },
            )
            return result

    async def capture_order(self, paypal_order_id: str, request_id: Optional[str] = None) -> dict:
        """Capture payment for approved PayPal order.

        Args:
            paypal_order_id: PayPal order ID
            request_id: PayPal-Request-Id for idempotency (optional)

        Returns:
            Capture response dict

        Raises:
            httpx.HTTPStatusError: If capture fails
        """
        access_token = await self.get_access_token()
        url = f"{self.base_url}/v2/checkout/orders/{paypal_order_id}/capture"

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {access_token}",
        }

        if request_id:
            headers["PayPal-Request-Id"] = request_id

        async with httpx.AsyncClient() as client:
            response = await client.post(url, headers=headers, json={}, timeout=30.0)
            response.raise_for_status()

            result = response.json()
            logger.info(
                "PayPal order captured",
                extra={
                    "event": "paypal.order.captured",
                    "order_id": paypal_order_id,
                    "status": result.get("status"),
                },
            )
            return result

    async def show_order_details(self, paypal_order_id: str) -> dict:
        """Get order details from PayPal (for verification).

        DEC-P02-5: 재조회 검증 로직

        Args:
            paypal_order_id: PayPal order ID

        Returns:
            Order details dict

        Raises:
            httpx.HTTPStatusError: If show order fails
        """
        access_token = await self.get_access_token()
        url = f"{self.base_url}/v2/checkout/orders/{paypal_order_id}"

        headers = {
            "Authorization": f"Bearer {access_token}",
        }

        async with httpx.AsyncClient() as client:
            response = await client.get(url, headers=headers, timeout=30.0)
            response.raise_for_status()

            return response.json()

    async def verify_webhook_signature(
        self,
        *,
        webhook_id: Optional[str] = None,
        transmission_id: str,
        transmission_time: str,
        cert_url: str,
        auth_algo: str,
        transmission_sig: str,
        webhook_event: dict,
    ) -> dict:
        """Verify webhook signature using PayPal API.

        DEC-P02-5: Webhook 검증 정책

        Args:
            webhook_id: Webhook ID (optional, uses env var if not provided)
            transmission_id: X-PAYPAL-TRANSMISSION-ID header
            transmission_time: X-PAYPAL-TRANSMISSION-TIME header
            cert_url: X-PAYPAL-CERT-URL header
            auth_algo: X-PAYPAL-AUTH-ALGO header
            transmission_sig: X-PAYPAL-TRANSMISSION-SIG header
            webhook_event: Full webhook event payload

        Returns:
            Verification response dict with verification_status

        Raises:
            httpx.HTTPStatusError: If verification request fails
        """
        webhook_id = webhook_id or self.webhook_id
        if not webhook_id:
            raise ValueError(
                "PAYPAL_WEBHOOK_ID is required for signature verification. "
                "Set it in environment configuration."
            )

        access_token = await self.get_access_token()
        url = f"{self.base_url}/v1/notifications/verify-webhook-signature"

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {access_token}",
        }

        verification_request = {
            "webhook_id": webhook_id,
            "transmission_id": transmission_id,
            "transmission_time": transmission_time,
            "cert_url": cert_url,
            "auth_algo": auth_algo,
            "transmission_sig": transmission_sig,
            "webhook_event": webhook_event,
        }

        async with httpx.AsyncClient() as client:
            response = await client.post(
                url, headers=headers, json=verification_request, timeout=30.0
            )
            response.raise_for_status()

            result = response.json()
            logger.info(
                "PayPal webhook signature verified",
                extra={
                    "event": "paypal.webhook.verified",
                    "verification_status": result.get("verification_status"),
                    "transmission_id": transmission_id,
                },
            )
            return result


# Global client instance (singleton)
_paypal_client: Optional[PayPalClient] = None


def get_paypal_client() -> PayPalClient:
    """Get global PayPal client instance (singleton).

    Returns:
        PayPalClient instance
    """
    global _paypal_client
    if _paypal_client is None:
        _paypal_client = PayPalClient()
    return _paypal_client
