from decimal import Decimal
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.test import TestCase

from chatbot.models import Chatbot
from subscription.choices import (
    BillingInterval,
    PaymentProvider,
    RenewalMode,
    SubscriptionStatus,
    WebhookProcessingStatus,
)
from subscription.models import (
    BillingPaymentMethod,
    ChatbotSubscription,
    Payment,
    PlanPrice,
    SubscriptionPlan,
)
from subscription.services.webhooks import StripeWebhookProcessor
from workspace.models import Workspace


class FakeStripeService:
    def __init__(self, event, payment_method=None, stripe_subscription=None):
        self.event = event
        self.payment_method = payment_method
        self.stripe_subscription = stripe_subscription

    def construct_webhook_event(self, **kwargs):
        return self.event

    def retrieve_payment_method(self, *, payment_method_id):
        return self.payment_method

    def retrieve_subscription(
        self, *, subscription_id, expand_latest_invoice=False
    ):
        if self.stripe_subscription is not None:
            return self.stripe_subscription
        checkout = self.event["data"]["object"]
        metadata = checkout.get("metadata") or {}
        return {
            "id": subscription_id,
            "customer": checkout.get("customer", "cus_123"),
            "status": "active",
            "start_date": 1_800_000_000,
            "cancel_at_period_end": False,
            "metadata": metadata,
            "items": {
                "data": [
                    {
                        "current_period_start": 1_800_000_000,
                        "current_period_end": 1_802_592_000,
                    }
                ]
            },
            "latest_invoice": {
                "id": "in_checkout_123",
                "status": "paid",
                "customer": checkout.get("customer", "cus_123"),
                "currency": "usd",
                "amount_paid": 1900,
                "status_transitions": {"paid_at": 1_800_000_000},
                "parent": {
                    "subscription_details": {
                        "subscription": subscription_id,
                        "metadata": metadata,
                    }
                },
            },
        }


class StripeWebhookProcessorTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            email="buyer@example.com",
            password="strong-password",
        )
        workspace = Workspace.objects.create(
            name="Example Workspace",
            owner=self.user,
        )
        chatbot = Chatbot.objects.create(
            workspace=workspace,
            chatbot_name="Billing Bot",
        )
        plan = SubscriptionPlan.objects.create(
            name="Growth",
            ai_message_limit=1000,
        )
        price = PlanPrice.objects.create(
            plan=plan,
            provider=PaymentProvider.STRIPE,
            billing_interval=BillingInterval.MONTHLY,
            amount=Decimal("19.00"),
        )
        self.subscription = ChatbotSubscription.objects.create(
            chatbot=chatbot,
            plan_price=price,
            selected_by=self.user,
            provider=PaymentProvider.STRIPE,
            renewal_mode=RenewalMode.PROVIDER_MANAGED,
            status=SubscriptionStatus.INCOMPLETE,
        )

    def event(self, event_type, data):
        return {
            "id": f"evt_{uuid4().hex}",
            "type": event_type,
            "api_version": "2026-07-29.dahlia",
            "livemode": False,
            "data": {"object": data},
        }

    def process(self, event):
        return StripeWebhookProcessor(
            stripe_service=FakeStripeService(event)
        ).process(payload=b"payload", signature="signature")

    def test_checkout_completion_activates_local_subscription_idempotently(self):
        event = self.event(
            "checkout.session.completed",
            {
                "id": "cs_test_123",
                "status": "complete",
                "payment_status": "paid",
                "customer": "cus_123",
                "subscription": "sub_123",
                "metadata": {
                    "argon_subscription_id": str(self.subscription.id),
                },
            },
        )

        webhook_event, duplicate = self.process(event)
        _, second_duplicate = self.process(event)

        self.subscription.refresh_from_db()
        self.assertFalse(duplicate)
        self.assertTrue(second_duplicate)
        self.assertEqual(self.subscription.status, SubscriptionStatus.ACTIVE)
        self.assertEqual(self.subscription.provider_subscription_id, "sub_123")
        self.assertIsNotNone(self.subscription.current_period_start)
        self.assertIsNotNone(self.subscription.current_period_end)
        self.assertEqual(
            self.subscription.next_billing_at,
            self.subscription.current_period_end,
        )
        self.assertTrue(
            Payment.objects.filter(provider_reference="in_checkout_123").exists()
        )
        self.assertEqual(
            webhook_event.processing_status,
            WebhookProcessingStatus.PROCESSED,
        )

    def test_expiration_for_replaced_checkout_does_not_cancel_subscription(self):
        self.subscription.provider_metadata = {
            "checkout_session_id": "cs_test_replacement",
            "checkout_status": "open",
        }
        self.subscription.save(update_fields=["provider_metadata", "updated_at"])

        self.process(
            self.event(
                "checkout.session.expired",
                {
                    "id": "cs_test_old",
                    "metadata": {
                        "argon_subscription_id": str(self.subscription.id),
                    },
                },
            )
        )

        self.subscription.refresh_from_db()
        self.assertEqual(self.subscription.status, SubscriptionStatus.INCOMPLETE)
        self.assertEqual(
            self.subscription.provider_metadata["checkout_session_id"],
            "cs_test_replacement",
        )

    def test_async_checkout_failure_marks_the_checkout_as_retryable(self):
        self.process(
            self.event(
                "checkout.session.async_payment_failed",
                {
                    "id": "cs_test_failed",
                    "status": "complete",
                    "payment_status": "unpaid",
                    "customer": "cus_123",
                    "subscription": "sub_failed",
                    "metadata": {
                        "argon_subscription_id": str(self.subscription.id),
                    },
                },
            )
        )

        self.subscription.refresh_from_db()
        self.assertEqual(self.subscription.status, SubscriptionStatus.INCOMPLETE)
        self.assertTrue(
            self.subscription.provider_metadata[
                "checkout_async_payment_failed"
            ]
        )
        self.assertEqual(
            self.subscription.provider_subscription_id,
            "sub_failed",
        )

    def test_paid_invoice_creates_payment_record(self):
        self.subscription.provider_subscription_id = "sub_123"
        self.subscription.provider_customer_id = "cus_123"
        self.subscription.save(
            update_fields=[
                "provider_subscription_id",
                "provider_customer_id",
                "updated_at",
            ]
        )
        event = self.event(
            "invoice.paid",
            {
                "id": "in_123",
                "customer": "cus_123",
                "currency": "usd",
                "amount_paid": 1900,
                "number": "INV-001",
                "status_transitions": {"paid_at": 1_800_000_000},
                "parent": {
                    "subscription_details": {
                        "subscription": "sub_123",
                        "metadata": {
                            "argon_subscription_id": str(self.subscription.id),
                        },
                    }
                },
            },
        )

        webhook_event, _ = self.process(event)

        payment = Payment.objects.get(provider_reference="in_123")
        self.assertEqual(payment.amount, Decimal("19.00"))
        self.assertEqual(payment.currency, "USD")
        self.assertEqual(payment.subscription, self.subscription)
        self.assertEqual(webhook_event.payment, payment)

    def test_dynamic_stripe_price_keeps_the_local_contract(self):
        self.subscription.provider_subscription_id = "sub_123"
        self.subscription.save(
            update_fields=["provider_subscription_id", "updated_at"]
        )
        event = self.event(
            "customer.subscription.updated",
            {
                "id": "sub_123",
                "customer": "cus_123",
                "status": "active",
                "start_date": 1_800_000_000,
                "cancel_at_period_end": False,
                "metadata": {
                    "argon_subscription_id": str(self.subscription.id),
                },
                "items": {
                    "data": [
                        {
                            "price": {"id": "price_created_inline"},
                            "current_period_start": 1_800_000_000,
                            "current_period_end": 1_802_592_000,
                        }
                    ]
                },
            },
        )

        self.process(event)

        self.subscription.refresh_from_db()
        self.assertEqual(ChatbotSubscription.objects.count(), 1)
        self.assertEqual(self.subscription.status, SubscriptionStatus.ACTIVE)
        self.assertEqual(self.subscription.get_plan_name(), "Growth")
        self.assertEqual(
            self.subscription.next_billing_at,
            self.subscription.current_period_end,
        )

    def test_setup_intent_stores_display_safe_card_metadata(self):
        event = self.event(
            "setup_intent.succeeded",
            {
                "id": "seti_123",
                "customer": "cus_123",
                "payment_method": "pm_123",
                "metadata": {
                    "argon_subscription_id": str(self.subscription.id),
                },
            },
        )
        payment_method = {
            "id": "pm_123",
            "type": "card",
            "customer": "cus_123",
            "card": {
                "brand": "visa",
                "last4": "4242",
                "exp_month": 12,
                "exp_year": 2030,
            },
        }

        StripeWebhookProcessor(
            stripe_service=FakeStripeService(event, payment_method)
        ).process(payload=b"payload", signature="signature")

        stored = BillingPaymentMethod.objects.get(
            provider_payment_method_id="pm_123"
        )
        self.assertEqual(stored.card_last4, "4242")
        self.assertEqual(stored.card_brand, "visa")
