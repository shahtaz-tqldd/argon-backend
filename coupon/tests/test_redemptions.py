from decimal import Decimal
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.test import TestCase

from chatbot.models import Chatbot
from coupon.utils.choices import DiscountDuration
from coupon.models import Coupon, CouponRedemption, Discount
from coupon.services import pending_coupon_info
from subscription.utils.choices import (
    BillingInterval,
    PaymentProvider,
    RenewalMode,
    SubscriptionStatus,
)
from subscription.models import ChatbotSubscription, PlanPrice, SubscriptionPlan
from subscription.services.webhooks import StripeWebhookProcessor
from workspace.models import Workspace


class FakeStripeService:
    def __init__(self, event):
        self.event = event

    def construct_webhook_event(self, **kwargs):
        return self.event


class CouponRedemptionWebhookTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            email="buyer@example.com",
            password="strong-password",
        )
        workspace = Workspace.objects.create(
            name="Example Workspace",
            owner=self.user,
        )
        self.chatbot = Chatbot.objects.create(
            workspace=workspace,
            chatbot_name="Billing Bot",
        )
        self.plan = SubscriptionPlan.objects.create(
            name="Growth",
            ai_message_limit=1000,
        )
        self.price = PlanPrice.objects.create(
            plan=self.plan,
            provider=PaymentProvider.STRIPE,
            billing_interval=BillingInterval.MONTHLY,
            currency="USD",
            amount=Decimal("19.00"),
        )
        self.subscription = ChatbotSubscription.objects.create(
            chatbot=self.chatbot,
            plan_price=self.price,
            selected_by=self.user,
            provider=PaymentProvider.STRIPE,
            renewal_mode=RenewalMode.PROVIDER_MANAGED,
            status=SubscriptionStatus.ACTIVE,
            provider_subscription_id="sub_123",
            provider_customer_id="cus_123",
        )

    def create_coupon(self, *, duration=DiscountDuration.ONCE, cycles=None):
        discount = Discount.objects.create(
            name="Launch 10%",
            discount_type="percentage",
            value=Decimal("10.00"),
            duration=duration,
            duration_in_billing_cycles=cycles,
        )
        return Coupon.objects.create(code="SAVE10", discount=discount)

    def bind_pending(self, coupon):
        self.subscription.provider_metadata = {
            "pending_coupon": pending_coupon_info(coupon),
        }
        self.subscription.save(
            update_fields=["provider_metadata", "updated_at"]
        )

    def paid_invoice_event(self, invoice_id):
        return {
            "id": f"evt_{uuid4().hex}",
            "type": "invoice.paid",
            "api_version": "2026-07-29.dahlia",
            "livemode": False,
            "data": {
                "object": {
                    "id": invoice_id,
                    "customer": "cus_123",
                    "currency": "usd",
                    "amount_paid": 1710,
                    "number": "INV-001",
                    "status_transitions": {"paid_at": 1_800_000_000},
                    "parent": {
                        "subscription_details": {
                            "subscription": "sub_123",
                            "metadata": {
                                "argon_subscription_id": str(
                                    self.subscription.id
                                ),
                            },
                        }
                    },
                }
            },
        }

    def process_paid_invoice(self, invoice_id):
        StripeWebhookProcessor(
            stripe_service=FakeStripeService(
                self.paid_invoice_event(invoice_id)
            )
        ).process(payload=b"payload", signature="signature")

    def test_first_paid_invoice_records_redemption(self):
        coupon = self.create_coupon()
        self.bind_pending(coupon)

        self.process_paid_invoice("in_123")

        redemption = CouponRedemption.objects.get(coupon=coupon)
        payment = redemption.payment
        self.assertEqual(payment.provider_reference, "in_123")
        self.assertEqual(redemption.subscription, self.subscription)
        self.assertEqual(redemption.user, self.user)
        self.assertEqual(redemption.original_amount, Decimal("19.00"))
        self.assertEqual(redemption.discount_amount, Decimal("1.90"))
        self.assertEqual(redemption.final_amount, Decimal("17.10"))
        self.assertEqual(redemption.currency, "USD")
        self.assertTrue(redemption.is_active)
        self.assertEqual(redemption.billing_cycles_remaining, 1)

        self.subscription.refresh_from_db()
        self.assertNotIn(
            "pending_coupon", self.subscription.provider_metadata
        )

    def test_once_discount_deactivates_on_next_invoice(self):
        coupon = self.create_coupon(duration=DiscountDuration.ONCE)
        self.bind_pending(coupon)
        self.process_paid_invoice("in_123")
        self.process_paid_invoice("in_456")

        redemption = CouponRedemption.objects.get(coupon=coupon)
        self.assertFalse(redemption.is_active)
        self.assertEqual(redemption.billing_cycles_remaining, 0)

    def test_repeating_discount_decrements_each_cycle(self):
        coupon = self.create_coupon(
            duration=DiscountDuration.REPEATING, cycles=3
        )
        self.bind_pending(coupon)
        self.process_paid_invoice("in_123")

        redemption = CouponRedemption.objects.get(coupon=coupon)
        self.assertTrue(redemption.is_active)
        self.assertEqual(redemption.billing_cycles_remaining, 3)

        self.process_paid_invoice("in_456")
        redemption.refresh_from_db()
        self.assertTrue(redemption.is_active)
        self.assertEqual(redemption.billing_cycles_remaining, 2)

        self.process_paid_invoice("in_789")
        redemption.refresh_from_db()
        self.assertTrue(redemption.is_active)
        self.assertEqual(redemption.billing_cycles_remaining, 1)

        self.process_paid_invoice("in_012")
        redemption.refresh_from_db()
        self.assertFalse(redemption.is_active)
        self.assertEqual(redemption.billing_cycles_remaining, 0)

    def test_forever_discount_stays_active(self):
        coupon = self.create_coupon(duration=DiscountDuration.FOREVER)
        self.bind_pending(coupon)
        self.process_paid_invoice("in_123")
        self.process_paid_invoice("in_456")

        redemption = CouponRedemption.objects.get(coupon=coupon)
        self.assertTrue(redemption.is_active)
        self.assertIsNone(redemption.billing_cycles_remaining)

    def test_invoice_without_pending_coupon_creates_no_redemption(self):
        self.process_paid_invoice("in_123")

        self.assertFalse(CouponRedemption.objects.exists())
