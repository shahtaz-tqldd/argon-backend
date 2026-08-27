from decimal import Decimal
from io import StringIO

from django.core.management import call_command
from django.test import TestCase

from subscription.choices import PaymentProvider, PlanFeature, PlanType
from subscription.models import SubscriptionPlan


class CreateSubscriptionPlanCommandTests(TestCase):
    expected_plans = {
        "free": {
            "ai_message_limit": 100,
            "file_size_limit_mb": 10,
            "knowledge_chunk_limit": 30,
            "amount": Decimal("0.00"),
            "provider": PaymentProvider.MANUAL,
        },
        "starter": {
            "ai_message_limit": 650,
            "file_size_limit_mb": 20,
            "knowledge_chunk_limit": 250,
            "amount": Decimal("35.00"),
            "provider": PaymentProvider.STRIPE,
        },
        "growth": {
            "ai_message_limit": 1500,
            "file_size_limit_mb": 50,
            "knowledge_chunk_limit": 600,
            "amount": Decimal("49.00"),
            "provider": PaymentProvider.STRIPE,
        },
        "premium": {
            "ai_message_limit": 3500,
            "file_size_limit_mb": 75,
            "knowledge_chunk_limit": 1500,
            "amount": Decimal("79.00"),
            "provider": PaymentProvider.STRIPE,
        },
    }

    def test_command_creates_all_subscription_plans(self):
        call_command("create_subscription_plan", stdout=StringIO())

        self.assertEqual(SubscriptionPlan.objects.count(), 5)
        for slug, expected in self.expected_plans.items():
            with self.subTest(plan=slug):
                plan = SubscriptionPlan.objects.get(slug=slug)
                self.assertEqual(plan.plan_type, PlanType.STANDARD)
                self.assertEqual(plan.ai_message_limit, expected["ai_message_limit"])
                self.assertEqual(
                    plan.file_size_limit_mb,
                    expected["file_size_limit_mb"],
                )
                self.assertEqual(
                    plan.knowledge_chunk_limit,
                    expected["knowledge_chunk_limit"],
                )
                self.assertEqual(
                    plan.features,
                    [PlanFeature.HUMAN_HANDOFF, PlanFeature.KNOWLEDGE_BASE],
                )
                price = plan.prices.get()
                self.assertEqual(price.provider, expected["provider"])
                self.assertEqual(price.amount, expected["amount"])
                self.assertTrue(price.is_active)

        enterprise = SubscriptionPlan.objects.get(slug="enterprise")
        self.assertEqual(enterprise.plan_type, PlanType.ENTERPRISE)
        self.assertIsNone(enterprise.ai_message_limit)
        self.assertIsNone(enterprise.file_size_limit_mb)
        self.assertIsNone(enterprise.knowledge_chunk_limit)
        self.assertTrue(enterprise.requires_sales_contact)
        self.assertFalse(enterprise.prices.filter(is_active=True).exists())

    def test_command_is_idempotent_and_restores_plan_configuration(self):
        call_command("create_subscription_plan", stdout=StringIO())
        starter = SubscriptionPlan.objects.get(slug="starter")
        starter_price = starter.prices.get()
        starter.ai_message_limit = 1
        starter.is_active = False
        starter.save()
        starter_price.amount = Decimal("99.00")
        starter_price.is_active = False
        starter_price.save()

        call_command("create_subscription_plan", stdout=StringIO())

        starter.refresh_from_db()
        starter_price.refresh_from_db()
        self.assertEqual(SubscriptionPlan.objects.count(), 5)
        self.assertEqual(starter.ai_message_limit, 650)
        self.assertTrue(starter.is_active)
        self.assertEqual(starter_price.amount, Decimal("35.00"))
        self.assertTrue(starter_price.is_active)
