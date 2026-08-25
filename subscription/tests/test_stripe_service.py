import hashlib
import hmac
import json
import time
from types import SimpleNamespace
from unittest.mock import Mock, patch
from uuid import uuid4

from django.test import SimpleTestCase, override_settings

from subscription.services.stripe import (
    StripeBillingService,
    StripeConfigurationError,
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
        plan_price = SimpleNamespace(
            provider_price_id="price_base",
            provider_overage_price_id="price_overage",
        )
        return SimpleNamespace(
            id=uuid4(),
            chatbot_id=uuid4(),
            plan_price_id=uuid4(),
            plan_price=plan_price,
        )

    @patch("subscription.services.stripe.stripe.StripeClient")
    def test_checkout_uses_catalog_prices_metadata_and_idempotency(self, client_class):
        session = Mock()
        session.to_dict.return_value = {
            "id": "cs_test_123",
            "url": "https://checkout.stripe.test/session",
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
        self.assertEqual(
            params["line_items"],
            [
                {"price": "price_base", "quantity": 1},
                {"price": "price_overage"},
            ],
        )
        self.assertEqual(
            params["subscription_data"]["metadata"]["argon_subscription_id"],
            str(subscription.id),
        )
        self.assertEqual(options, {"idempotency_key": "checkout-key"})

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

    def test_stripe_minor_units_support_decimal_and_zero_decimal_currencies(self):
        self.assertEqual(_minor_units_to_decimal(1900, "USD"), 19)
        self.assertEqual(_minor_units_to_decimal(1900, "JPY"), 1900)
