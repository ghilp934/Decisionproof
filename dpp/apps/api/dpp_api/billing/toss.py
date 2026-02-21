"""TossPayments API client for P0-2 Paid Pilot.

DEC-P02-1: Provider 이원화 (TossPayments)
DEC-P02-5: Webhook 검증 정책 (결제 조회 API로 재조회)

TossPayments API Reference:
- Payments API: https://docs.tosspayments.com/reference#%EA%B2%B0%EC%A0%9C
- Webhooks: https://docs.tosspayments.com/guides/webhook
"""

import base64
import logging
import os
from typing import Optional

import httpx

logger = logging.getLogger(__name__)


class TossPaymentsClient:
    """TossPayments API client.

    Environment Variables:
    - TOSS_SECRET_KEY: TossPayments secret key (test_sk_* or live_sk_*)
    """

    def __init__(self):
        self.secret_key = os.getenv("TOSS_SECRET_KEY")

        if not self.secret_key:
            raise ValueError(
                "TOSS_SECRET_KEY is required. Set it in environment configuration."
            )

        # Determine environment from key prefix
        self.env = "sandbox" if self.secret_key.startswith("test_") else "live"
        self.base_url = "https://api.tosspayments.com"

    def _get_auth_header(self) -> str:
        """Get Basic Auth header for TossPayments.

        TossPayments uses Basic Auth with secret_key as username and empty password.

        Returns:
            Authorization header value
        """
        # Basic Auth: base64(secret_key:)
        credentials = f"{self.secret_key}:"
        encoded_credentials = base64.b64encode(credentials.encode()).decode()
        return f"Basic {encoded_credentials}"

    async def get_payment(self, payment_key: str) -> dict:
        """Get payment details from TossPayments.

        DEC-P02-5: 재조회 검증 로직

        Args:
            payment_key: TossPayments paymentKey

        Returns:
            Payment details dict

        Raises:
            httpx.HTTPStatusError: If get payment fails
        """
        url = f"{self.base_url}/v1/payments/{payment_key}"

        headers = {
            "Authorization": self._get_auth_header(),
        }

        async with httpx.AsyncClient() as client:
            response = await client.get(url, headers=headers, timeout=30.0)
            response.raise_for_status()

            result = response.json()
            logger.info(
                "TossPayments payment retrieved",
                extra={
                    "event": "toss.payment.retrieved",
                    "payment_key": payment_key,
                    "status": result.get("status"),
                },
            )
            return result

    async def get_payment_by_order_id(self, order_id: str) -> dict:
        """Get payment details by orderId.

        Args:
            order_id: Merchant's order ID

        Returns:
            Payment details dict

        Raises:
            httpx.HTTPStatusError: If get payment fails
        """
        url = f"{self.base_url}/v1/payments/orders/{order_id}"

        headers = {
            "Authorization": self._get_auth_header(),
        }

        async with httpx.AsyncClient() as client:
            response = await client.get(url, headers=headers, timeout=30.0)
            response.raise_for_status()

            return response.json()

    async def confirm_payment(
        self,
        *,
        payment_key: str,
        order_id: str,
        amount: int,
    ) -> dict:
        """Confirm payment (server-side confirmation).

        Note: For webhook-based flow, confirmation is typically done by client.
        This method is for server-initiated confirmation.

        Args:
            payment_key: TossPayments paymentKey
            order_id: Merchant's order ID
            amount: Payment amount (integer, e.g., 10000 for 10,000 KRW)

        Returns:
            Confirmation response dict

        Raises:
            httpx.HTTPStatusError: If confirmation fails
        """
        url = f"{self.base_url}/v1/payments/confirm"

        headers = {
            "Authorization": self._get_auth_header(),
            "Content-Type": "application/json",
        }

        confirm_request = {
            "paymentKey": payment_key,
            "orderId": order_id,
            "amount": amount,
        }

        async with httpx.AsyncClient() as client:
            response = await client.post(
                url, headers=headers, json=confirm_request, timeout=30.0
            )
            response.raise_for_status()

            result = response.json()
            logger.info(
                "TossPayments payment confirmed",
                extra={
                    "event": "toss.payment.confirmed",
                    "payment_key": payment_key,
                    "order_id": order_id,
                },
            )
            return result

    async def cancel_payment(
        self,
        *,
        payment_key: str,
        cancel_reason: str,
        cancel_amount: Optional[int] = None,
    ) -> dict:
        """Cancel payment (full or partial).

        Args:
            payment_key: TossPayments paymentKey
            cancel_reason: Reason for cancellation
            cancel_amount: Amount to cancel (optional, full if not provided)

        Returns:
            Cancellation response dict

        Raises:
            httpx.HTTPStatusError: If cancellation fails
        """
        url = f"{self.base_url}/v1/payments/{payment_key}/cancel"

        headers = {
            "Authorization": self._get_auth_header(),
            "Content-Type": "application/json",
        }

        cancel_request = {
            "cancelReason": cancel_reason,
        }

        if cancel_amount is not None:
            cancel_request["cancelAmount"] = cancel_amount

        async with httpx.AsyncClient() as client:
            response = await client.post(
                url, headers=headers, json=cancel_request, timeout=30.0
            )
            response.raise_for_status()

            return response.json()


# Global client instance (singleton)
_toss_client: Optional[TossPaymentsClient] = None


def get_toss_client() -> TossPaymentsClient:
    """Get global TossPayments client instance (singleton).

    Returns:
        TossPaymentsClient instance
    """
    global _toss_client
    if _toss_client is None:
        _toss_client = TossPaymentsClient()
    return _toss_client
