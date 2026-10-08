from decimal import Decimal
from io import StringIO

from django.core.management import call_command
from django.test import TestCase

from subscription.choices import (
    BillingInterval,
    PaymentProvider,
    PlanFeature,
    PlanType,
)
from subscription.models import SubscriptionPlan


class CreateSubscriptionPlanCommandTests(TestCase):
    expected_plans = {
        "free": {
            "ai_message_limit": 100,
            "file_size_limit_mb": 10,
            "knowledge_chunk_limit": 25,
            "team_members_limit": 1,
            "monthly_amount": Decimal("0.00"),
            "annual_amount": None,
            "provider": PaymentProvider.MANUAL,
            "overage_enabled": False,
            "overage_unit_price": None,
            "features": [
                PlanFeature.HUMAN_HANDOFF,
                PlanFeature.KNOWLEDGE_BASE,
            ],
        },
        "starter": {
            "ai_message_limit": 1000,
            "file_size_limit_mb": 25,
            "knowledge_chunk_limit": 625,
            "team_members_limit": 5,
            "monthly_amount": Decimal("59.00"),
            "annual_amount": Decimal("590.00"),
            "provider": PaymentProvider.STRIPE,
            "overage_enabled": True,
            "overage_unit_price": Decimal("0.030"),
            "features": [
                PlanFeature.HUMAN_HANDOFF,
                PlanFeature.KNOWLEDGE_BASE,
                PlanFeature.LEAD_CAPTURE,
            ],
        },
        "growth": {
            "ai_message_limit": 2500,
            "file_size_limit_mb": 50,
            "knowledge_chunk_limit": 1250,
            "team_members_limit": 10,
            "monthly_amount": Decimal("119.00"),
            "annual_amount": Decimal("1190.00"),
            "provider": PaymentProvider.STRIPE,
            "overage_enabled": True,
            "overage_unit_price": Decimal("0.025"),
            "features": [
                PlanFeature.HUMAN_HANDOFF,
                PlanFeature.KNOWLEDGE_BASE,
                PlanFeature.LEAD_CAPTURE,
                PlanFeature.LEAD_INSIGHTS,
                PlanFeature.APPOINTMENT_BOOKING,
            ],
        },
        "premium": {
            "ai_message_limit": 5000,
            "file_size_limit_mb": 100,
            "knowledge_chunk_limit": 2500,
            "team_members_limit": 15,
            "monthly_amount": Decimal("229.00"),
            "annual_amount": Decimal("2290.00"),
            "provider": PaymentProvider.STRIPE,
            "overage_enabled": True,
            "overage_unit_price": Decimal("0.020"),
            "features": [
                PlanFeature.HUMAN_HANDOFF,
                PlanFeature.KNOWLEDGE_BASE,
                PlanFeature.LEAD_CAPTURE,
                PlanFeature.LEAD_INSIGHTS,
                PlanFeature.APPOINTMENT_BOOKING,
                PlanFeature.AI_RECOMMENDATIONS,
            ],
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
                    plan.team_members_limit,
                    expected["team_members_limit"],
                )
                self.assertEqual(plan.features, expected["features"])
                self.assertEqual(
                    plan.ai_message_overage_enabled,
                    expected["overage_enabled"],
                )
                monthly_price = plan.prices.get(
                    billing_interval=BillingInterval.MONTHLY,
                )
                self.assertEqual(monthly_price.provider, expected["provider"])
                self.assertEqual(monthly_price.amount, expected["monthly_amount"])
                self.assertEqual(
                    monthly_price.ai_message_overage_unit_price,
                    expected["overage_unit_price"],
                )
                self.assertTrue(monthly_price.is_active)

                if expected["annual_amount"] is None:
                    self.assertFalse(
                        plan.prices.filter(
                            billing_interval=BillingInterval.ANNUAL,
                        ).exists(),
                    )
                else:
                    annual_price = plan.prices.get(
                        billing_interval=BillingInterval.ANNUAL,
                    )
                    self.assertEqual(annual_price.provider, expected["provider"])
                    self.assertEqual(annual_price.amount, expected["annual_amount"])
                    self.assertEqual(
                        annual_price.ai_message_overage_unit_price,
                        expected["overage_unit_price"],
                    )
                    self.assertTrue(annual_price.is_active)

        enterprise = SubscriptionPlan.objects.get(slug="enterprise")
        self.assertEqual(enterprise.plan_type, PlanType.ENTERPRISE)
        self.assertIsNone(enterprise.ai_message_limit)
        self.assertIsNone(enterprise.file_size_limit_mb)
        self.assertIsNone(enterprise.knowledge_chunk_limit)
        self.assertIsNone(enterprise.team_members_limit)
        self.assertTrue(enterprise.requires_sales_contact)
        self.assertFalse(enterprise.ai_message_overage_enabled)
        self.assertEqual(
            enterprise.features,
            [
                PlanFeature.HUMAN_HANDOFF,
                PlanFeature.KNOWLEDGE_BASE,
                PlanFeature.LEAD_CAPTURE,
                PlanFeature.LEAD_INSIGHTS,
                PlanFeature.APPOINTMENT_BOOKING,
                PlanFeature.AI_RECOMMENDATIONS,
            ],
        )
        self.assertFalse(enterprise.prices.filter(is_active=True).exists())

    def test_command_is_idempotent_and_restores_plan_configuration(self):
        call_command("create_subscription_plan", stdout=StringIO())
        starter = SubscriptionPlan.objects.get(slug="starter")
        starter_prices = starter.prices.all()
        starter.ai_message_limit = 1
        starter.is_active = False
        starter.ai_message_overage_enabled = False
        starter.save()
        for starter_price in starter_prices:
            starter_price.amount = Decimal("99.00")
            starter_price.is_active = False
            starter_price.save()

        call_command("create_subscription_plan", stdout=StringIO())

        starter.refresh_from_db()
        self.assertEqual(SubscriptionPlan.objects.count(), 5)
        self.assertEqual(starter.ai_message_limit, 1000)
        self.assertTrue(starter.is_active)
        self.assertTrue(starter.ai_message_overage_enabled)
        self.assertEqual(starter.prices.count(), 2)
        self.assertEqual(
            starter.prices.get(
                billing_interval=BillingInterval.MONTHLY,
            ).amount,
            Decimal("59.00"),
        )
        self.assertEqual(
            starter.prices.get(
                billing_interval=BillingInterval.ANNUAL,
            ).amount,
            Decimal("590.00"),
        )
        self.assertTrue(
            all(
                price.ai_message_overage_unit_price == Decimal("0.030")
                for price in starter.prices.all()
            ),
        )
        self.assertTrue(
            all(price.is_active for price in starter.prices.all()),
        )

    def test_command_restores_the_canonical_slug_when_matched_by_name(self):
        SubscriptionPlan.objects.create(
            name="Starter",
            slug="legacy-starter",
            ai_message_limit=1,
        )

        call_command("create_subscription_plan", stdout=StringIO())

        self.assertFalse(
            SubscriptionPlan.objects.filter(slug="legacy-starter").exists()
        )
        starter = SubscriptionPlan.objects.get(slug="starter")
        self.assertEqual(starter.ai_message_limit, 1000)
        self.assertEqual(
            starter.prices.get(
                billing_interval=BillingInterval.MONTHLY,
            ).amount,
            Decimal("59.00"),
        )
