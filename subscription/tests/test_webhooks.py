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
from subscription.models import ChatbotSubscription, Payment, PlanPrice, SubscriptionPlan
from subscription.services.webhooks import StripeWebhookProcessor
from workspace.models import Workspace


class FakeStripeService:
    def __init__(self, event):
        self.event = event

    def construct_webhook_event(self, **kwargs):
        return self.event


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
            provider_price_id="price_growth",
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
        self.assertEqual(
            webhook_event.processing_status,
            WebhookProcessingStatus.PROCESSED,
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

    def test_stripe_price_change_creates_a_new_immutable_contract(self):
        replacement_plan = SubscriptionPlan.objects.create(
            name="Pro",
            ai_message_limit=5000,
        )
        replacement_price = PlanPrice.objects.create(
            plan=replacement_plan,
            provider=PaymentProvider.STRIPE,
            billing_interval=BillingInterval.MONTHLY,
            amount=Decimal("49.00"),
            provider_price_id="price_pro",
        )
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
                            "price": {"id": "price_pro"},
                            "current_period_start": 1_800_000_000,
                            "current_period_end": 1_802_592_000,
                        }
                    ]
                },
            },
        )

        self.process(event)

        self.subscription.refresh_from_db()
        replacement = ChatbotSubscription.objects.get(
            provider_subscription_id="sub_123"
        )
        self.assertEqual(self.subscription.status, SubscriptionStatus.CANCELED)
        self.assertEqual(replacement.plan_price, replacement_price)
        self.assertEqual(replacement.status, SubscriptionStatus.ACTIVE)
        self.assertEqual(replacement.get_plan_name(), "Pro")
