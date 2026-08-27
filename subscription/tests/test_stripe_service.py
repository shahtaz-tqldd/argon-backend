import hashlib
import hmac
import json
import time
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import Mock, patch
from uuid import uuid4

from django.test import SimpleTestCase, override_settings

from subscription.choices import BillingInterval
from subscription.services.stripe import (
    StripeBillingService,
    StripeConfigurationError,
    stripe_minor_unit_amount,
)
from subscription.services.webhooks import _minor_units_to_decimal


@override_settings(
    STRIPE_SECRET_KEY="sk_test_example",
    STRIPE_CHECKOUT_SUCCESS_URL="https://app.example/success",
    STRIPE_CHECKOUT_CANCEL_URL="https://app.example/cancel",
    STRIPE_BILLING_PORTAL_RETURN_URL="https://app.example/billing",
)
class StripeBillingServiceTests(SimpleTestCase):
    def subscription(self):
        plan_id = uuid4()
        plan_price_id = uuid4()
        return SimpleNamespace(
            id=uuid4(),
            chatbot_id=uuid4(),
            plan_price_id=plan_price_id,
            snapshot={
                "plan": {
                    "id": str(plan_id),
                    "name": "Growth",
                },
                "pricing": {
                    "plan_price_id": str(plan_price_id),
                    "amount": "19.00",
                    "currency": "USD",
                    "billing_interval": BillingInterval.MONTHLY,
                },
            },
        )

    @patch("subscription.services.stripe.stripe.StripeClient")
    def test_checkout_uses_local_price_data_metadata_and_idempotency(
        self,
        client_class,
    ):
        session = Mock()
        session.to_dict.return_value = {
            "id": "cs_test_123",
            "client_secret": "cs_test_123_secret_example",
        }
        create = client_class.return_value.v1.checkout.sessions.create
        create.return_value = session
        subscription = self.subscription()
        user = SimpleNamespace(id=uuid4(), email="buyer@example.com")

        result = StripeBillingService().create_checkout_session(
            subscription=subscription,
            user=user,
            idempotency_key="checkout-key",
        )

        self.assertEqual(result["id"], "cs_test_123")
        params = create.call_args.args[0]
        options = create.call_args.kwargs["options"]
        self.assertEqual(params["mode"], "subscription")
        self.assertEqual(params["ui_mode"], "embedded_page")
        self.assertEqual(params["redirect_on_completion"], "if_required")
        self.assertEqual(
            params["return_url"],
            "https://app.example/success",
        )
        self.assertNotIn("success_url", params)
        self.assertNotIn("cancel_url", params)
        self.assertEqual(
            params["line_items"],
            [
                {
                    "price_data": {
                        "currency": "usd",
                        "product_data": {
                            "name": "Growth",
                            "metadata": {
                                "argon_plan_id": subscription.snapshot["plan"]["id"],
                                "argon_plan_price_id": subscription.snapshot["pricing"][
                                    "plan_price_id"
                                ],
                            },
                        },
                        "recurring": {"interval": "month"},
                        "unit_amount": 1900,
                    },
                    "quantity": 1,
                },
            ],
        )
        self.assertEqual(
            params["subscription_data"]["metadata"]["argon_subscription_id"],
            str(subscription.id),
        )
        self.assertEqual(options, {"idempotency_key": "checkout-key"})

    @patch("subscription.services.stripe.stripe.StripeClient")
    def test_checkout_session_can_be_retrieved_for_reuse_checks(
        self,
        client_class,
    ):
        session = Mock()
        session.to_dict.return_value = {
            "id": "cs_test_123",
            "status": "expired",
        }
        retrieve = client_class.return_value.v1.checkout.sessions.retrieve
        retrieve.return_value = session

        result = StripeBillingService().retrieve_checkout_session(
            session_id="cs_test_123"
        )

        self.assertEqual(result["status"], "expired")
        retrieve.assert_called_once_with("cs_test_123")

    @patch("subscription.services.stripe.stripe.StripeClient")
    def test_active_subscription_plan_change_uses_inline_price_data(
        self,
        client_class,
    ):
        current = Mock()
        current.to_dict.return_value = {
            "id": "sub_123",
            "metadata": {"argon_subscription_id": "old-local-id"},
            "items": {
                "data": [
                    {
                        "id": "si_123",
                        "price": {"product": {"id": "prod_123"}},
                    }
                ]
            },
        }
        updated = Mock()
        updated.to_dict.return_value = {"id": "sub_123", "status": "active"}
        client = client_class.return_value.v1
        client.subscriptions.retrieve.return_value = current
        client.subscriptions.update.return_value = updated
        replacement = self.subscription()

        result = StripeBillingService().change_subscription_plan(
            provider_subscription_id="sub_123",
            replacement_subscription=replacement,
            idempotency_key="plan-change-key",
        )

        self.assertEqual(result["status"], "active")
        params = client.subscriptions.update.call_args.args[1]
        self.assertEqual(
            params["items"],
            [
                {
                    "id": "si_123",
                    "price_data": {
                        "currency": "usd",
                        "product": "prod_123",
                        "recurring": {"interval": "month"},
                        "unit_amount": 1900,
                    },
                    "quantity": 1,
                }
            ],
        )
        self.assertEqual(params["payment_behavior"], "error_if_incomplete")
        self.assertEqual(params["proration_behavior"], "always_invoice")
        self.assertEqual(
            params["metadata"]["argon_subscription_id"],
            str(replacement.id),
        )
        self.assertEqual(
            client.subscriptions.update.call_args.kwargs["options"],
            {"idempotency_key": "plan-change-key"},
        )

    @override_settings(STRIPE_SECRET_KEY="")
    def test_missing_secret_key_is_reported_as_configuration_error(self):
        with self.assertRaises(StripeConfigurationError):
            StripeBillingService()._client()

    @patch("subscription.services.stripe.stripe.StripeClient")
    def test_portal_and_cancellation_use_stripe_billing(self, client_class):
        portal = Mock()
        portal.to_dict.return_value = {"url": "https://billing.stripe.test"}
        client_class.return_value.v1.billing_portal.sessions.create.return_value = portal
        updated = Mock()
        updated.to_dict.return_value = {"cancel_at_period_end": True}
        client_class.return_value.v1.subscriptions.update.return_value = updated
        service = StripeBillingService()

        service.create_portal_session(customer_id="cus_123")
        service.set_cancel_at_period_end(subscription_id="sub_123", cancel=True)

        client_class.return_value.v1.billing_portal.sessions.create.assert_called_with(
            {
                "customer": "cus_123",
                "return_url": "https://app.example/billing",
            }
        )
        client_class.return_value.v1.subscriptions.update.assert_called_with(
            "sub_123",
            {"cancel_at_period_end": True},
        )

    def test_webhook_signature_is_verified_against_raw_body(self):
        payload = json.dumps(
            {
                "id": "evt_123",
                "object": "event",
                "type": "customer.subscription.updated",
                "data": {"object": {}},
            },
            separators=(",", ":"),
        ).encode()
        timestamp = int(time.time())
        secret = "whsec_test"
        digest = hmac.new(
            secret.encode(),
            f"{timestamp}.{payload.decode()}".encode(),
            hashlib.sha256,
        ).hexdigest()

        event = StripeBillingService(
            webhook_secret=secret
        ).construct_webhook_event(
            payload=payload,
            signature=f"t={timestamp},v1={digest}",
        )

        self.assertEqual(event["id"], "evt_123")

    def test_publishable_key_is_rejected_as_a_webhook_secret(self):
        service = StripeBillingService(webhook_secret="pk_test_not_a_webhook_secret")

        with self.assertRaisesRegex(
            StripeConfigurationError,
            "must be a Stripe endpoint signing secret",
        ):
            service.construct_webhook_event(
                payload=b"{}",
                signature="t=123,v1=invalid",
            )

    def test_stripe_minor_units_support_decimal_and_zero_decimal_currencies(self):
        self.assertEqual(stripe_minor_unit_amount(Decimal("19.00"), "USD"), 1900)
        self.assertEqual(stripe_minor_unit_amount(Decimal("1900"), "JPY"), 1900)
        self.assertEqual(_minor_units_to_decimal(1900, "USD"), 19)
        self.assertEqual(_minor_units_to_decimal(1900, "JPY"), 1900)
