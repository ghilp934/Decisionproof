"""Unit tests for PayPal-Request-Id mandatory enforcement.

Merge-blocker test:
  T-27: PayPal-Request-Id is MANDATORY on both create_order and capture_order.
        Omitting it must raise TypeError (not a silent no-op).

Locked decisions: DEC-V1-14 (create), DEC-V1-15 (capture)

Design intent:
  The PayPal-Request-Id values are generated ONCE at CheckoutSession creation
  and stored immutably. They must be passed explicitly to every PayPal call.
  Making request_id a required keyword argument in the Python signature ensures
  no code path can accidentally omit it.
"""

import inspect
import pytest

from dpp_api.billing.paypal import PayPalClient


class TestPayPalRequestIdMandatory:
    """T-27: PayPal-Request-Id must be a required argument (not Optional)."""

    def test_create_order_request_id_is_required_kwarg(self):
        """T-27a: create_order must require request_id — no default value."""
        sig = inspect.signature(PayPalClient.create_order)
        params = sig.parameters

        assert "request_id" in params, (
            "create_order must have a request_id parameter (DEC-V1-14)"
        )

        param = params["request_id"]
        # Required = no default AND not Optional (i.e., no default value)
        assert param.default is inspect.Parameter.empty, (
            "create_order.request_id must be required (no default). "
            "DEC-V1-14: PayPal-Request-Id is mandatory."
        )

    def test_capture_order_request_id_is_required_kwarg(self):
        """T-27b: capture_order must require request_id — no default value."""
        sig = inspect.signature(PayPalClient.capture_order)
        params = sig.parameters

        assert "request_id" in params, (
            "capture_order must have a request_id parameter (DEC-V1-15)"
        )

        param = params["request_id"]
        assert param.default is inspect.Parameter.empty, (
            "capture_order.request_id must be required (no default). "
            "DEC-V1-15: PayPal-Request-Id is mandatory."
        )

    def test_create_order_type_annotation_is_str_not_optional(self):
        """T-27c: create_order.request_id type annotation must be str, not Optional[str]."""
        sig = inspect.signature(PayPalClient.create_order)
        param = sig.parameters.get("request_id")

        assert param is not None
        annotation = param.annotation

        # Must be str (or unset), must NOT be Optional[str]
        # Optional[str] would be Union[str, None] or str | None
        if annotation is not inspect.Parameter.empty:
            import types
            import typing

            # Check it's not Optional (Union with None)
            origin = getattr(annotation, "__origin__", None)
            is_optional = (
                origin is typing.Union
                or (isinstance(annotation, types.UnionType) and type(None) in annotation.__args__)
            )
            assert not is_optional, (
                f"create_order.request_id type is {annotation!r} — must be 'str', not Optional. "
                "DEC-V1-14: request_id is mandatory, never optional."
            )

    def test_capture_order_type_annotation_is_str_not_optional(self):
        """T-27d: capture_order.request_id type annotation must be str, not Optional[str]."""
        sig = inspect.signature(PayPalClient.capture_order)
        param = sig.parameters.get("request_id")

        assert param is not None
        annotation = param.annotation

        if annotation is not inspect.Parameter.empty:
            import types
            import typing

            origin = getattr(annotation, "__origin__", None)
            is_optional = (
                origin is typing.Union
                or (isinstance(annotation, types.UnionType) and type(None) in annotation.__args__)
            )
            assert not is_optional, (
                f"capture_order.request_id type is {annotation!r} — must be 'str', not Optional. "
                "DEC-V1-15: request_id is mandatory, never optional."
            )

    def test_paypal_request_id_header_always_set_in_create_order(self):
        """T-27e: PayPal-Request-Id header must always be set in create_order.

        Verify that the header is set unconditionally (no 'if request_id:' guard).
        """
        import ast
        import pathlib

        paypal_py = (
            pathlib.Path(__file__).parent.parent.parent
            / "dpp_api" / "billing" / "paypal.py"
        )
        source = paypal_py.read_text(encoding="utf-8")

        # The old code had: if request_id: headers["PayPal-Request-Id"] = request_id
        # The new code must NOT have this conditional guard
        assert 'if request_id:' not in source or (
            # Allow the pattern only in verify_webhook_signature (different context)
            source.count('if request_id:') == 0
        ), (
            "paypal.py must NOT have 'if request_id:' guard for PayPal-Request-Id. "
            "The header must be set unconditionally (DEC-V1-14, DEC-V1-15)."
        )

    def test_create_order_docstring_mentions_mandatory(self):
        """T-27f: create_order docstring must state request_id is MANDATORY."""
        doc = PayPalClient.create_order.__doc__ or ""
        assert "MANDATORY" in doc or "mandatory" in doc.lower(), (
            "create_order docstring must state that request_id is MANDATORY (DEC-V1-14)."
        )

    def test_capture_order_docstring_mentions_mandatory(self):
        """T-27g: capture_order docstring must state request_id is MANDATORY."""
        doc = PayPalClient.capture_order.__doc__ or ""
        assert "MANDATORY" in doc or "mandatory" in doc.lower(), (
            "capture_order docstring must state that request_id is MANDATORY (DEC-V1-15)."
        )
