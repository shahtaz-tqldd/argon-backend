from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import transaction

from subscription.utils.choices import (
    BillingInterval,
    PaymentProvider,
    PlanFeature,
    PlanType,
)
from subscription.models import PlanPrice, SubscriptionPlan


STANDARD_FEATURES = [
    PlanFeature.HUMAN_HANDOFF,
    PlanFeature.KNOWLEDGE_BASE,
]

ALPHA_FEATURES = [
    *STANDARD_FEATURES,
    PlanFeature.LEAD_CAPTURE
]

BETA_FEATURES = [
    *ALPHA_FEATURES,
    PlanFeature.LEAD_INSIGHTS,
    PlanFeature.APPOINTMENT_BOOKING
]

GAMMA_FEATURES = [
    *BETA_FEATURES,
    PlanFeature.AI_RECOMMENDATIONS
]

PLAN_CONFIGURATIONS = (
    {
        "name": "Free",
        "slug": "free",
        "plan_type": PlanType.STANDARD,
        "ai_message_limit": 100,
        "file_size_limit_mb": 10,
        "knowledge_chunk_limit": 25,
        "team_members_limit": 1,
        "features": STANDARD_FEATURES,
        "is_free": True,
        "requires_sales_contact": False,
        "sort_order": 0,
        "ai_message_overage_enabled": False,
        "ai_message_overage_unit_price": None,
        "prices": [
            {
                "provider": PaymentProvider.MANUAL,
                "billing_interval": BillingInterval.MONTHLY,
                "amount": Decimal("0.00"),
            },
        ],
    },
    {
        "name": "Starter",
        "slug": "starter",
        "plan_type": PlanType.STANDARD,
        "ai_message_limit": 1000,
        "file_size_limit_mb": 25,
        "knowledge_chunk_limit": 625,
        "team_members_limit": 5,
        "features": ALPHA_FEATURES,
        "is_free": False,
        "requires_sales_contact": False,
        "sort_order": 10,
        "ai_message_overage_enabled": True,
        "ai_message_overage_unit_price": Decimal("0.030"),
        "prices": [
            {
                "provider": PaymentProvider.STRIPE,
                "billing_interval": BillingInterval.MONTHLY,
                "amount": Decimal("59.00"),
            },
            {
                "provider": PaymentProvider.STRIPE,
                "billing_interval": BillingInterval.ANNUAL,
                "amount": Decimal("588.00"),
            },
        ],
    },
    {
        "name": "Growth",
        "slug": "growth",
        "plan_type": PlanType.STANDARD,
        "ai_message_limit": 2500,
        "file_size_limit_mb": 50,
        "knowledge_chunk_limit": 1250,
        "team_members_limit": 10,
        "features": BETA_FEATURES,
        "is_free": False,
        "requires_sales_contact": False,
        "sort_order": 20,
        "ai_message_overage_enabled": True,
        "ai_message_overage_unit_price": Decimal("0.025"),
        "prices": [
            {
                "provider": PaymentProvider.STRIPE,
                "billing_interval": BillingInterval.MONTHLY,
                "amount": Decimal("119.00"),
            },
            {
                "provider": PaymentProvider.STRIPE,
                "billing_interval": BillingInterval.ANNUAL,
                "amount": Decimal("1188.00"),
            },
        ],
    },
    {
        "name": "Premium",
        "slug": "premium",
        "plan_type": PlanType.STANDARD,
        "ai_message_limit": 5000,
        "file_size_limit_mb": 100,
        "knowledge_chunk_limit": 2500,
        "team_members_limit": 15,
        "features": GAMMA_FEATURES,
        "is_free": False,
        "requires_sales_contact": False,
        "sort_order": 30,
        "ai_message_overage_enabled": True,
        "ai_message_overage_unit_price": Decimal("0.020"),
        "prices": [
            {
                "provider": PaymentProvider.STRIPE,
                "billing_interval": BillingInterval.MONTHLY,
                "amount": Decimal("229.00"),
            },
            {
                "provider": PaymentProvider.STRIPE,
                "billing_interval": BillingInterval.ANNUAL,
                "amount": Decimal("2388.00"),
            },
        ],
    },
    {
        "name": "Enterprise",
        "slug": "enterprise",
        "plan_type": PlanType.ENTERPRISE,
        "ai_message_limit": None,
        "file_size_limit_mb": None,
        "knowledge_chunk_limit": None,
        "team_members_limit": None,
        "features": GAMMA_FEATURES,
        "is_free": False,
        "requires_sales_contact": True,
        "sort_order": 40,
        "ai_message_overage_enabled": False,
        "ai_message_overage_unit_price": None,
        "prices": None,
    },
)


class Command(BaseCommand):
    help = "Create or update the default subscription plans and prices."

    # python manage.py create_subscription_plan

    @transaction.atomic
    def handle(self, *args, **options):
        created_plans = 0
        updated_plans = 0
        created_prices = 0
        updated_prices = 0

        for configuration in PLAN_CONFIGURATIONS:
            plan, plan_created = self._save_plan(configuration)
            if plan_created:
                created_plans += 1
            else:
                updated_plans += 1

            price_configurations = configuration["prices"]
            if price_configurations is None:
                PlanPrice.objects.filter(plan=plan, is_active=True).update(
                    is_active=False,
                )
                continue

            for price_configuration in price_configurations:
                price_created = self._save_price(
                    plan,
                    price_configuration,
                    configuration["ai_message_overage_unit_price"],
                )
                if price_created:
                    created_prices += 1
                else:
                    updated_prices += 1

        self.stdout.write(
            self.style.SUCCESS(
                "Subscription plans synchronized: "
                f"{created_plans} created, {updated_plans} updated; "
                f"{created_prices} prices created, {updated_prices} updated."
            )
        )

    @staticmethod
    def _save_plan(configuration):
        plan = (
            SubscriptionPlan.objects.select_for_update()
            .filter(slug=configuration["slug"])
            .first()
        )
        if plan is None:
            plan = (
                SubscriptionPlan.objects.select_for_update()
                .filter(name__iexact=configuration["name"])
                .order_by("created_at")
                .first()
            )

        created = plan is None
        if created:
            plan = SubscriptionPlan(name=configuration["name"])

        for field in (
            "name",
            "slug",
            "plan_type",
            "ai_message_limit",
            "file_size_limit_mb",
            "knowledge_chunk_limit",
            "team_members_limit",
            "features",
            "is_free",
            "requires_sales_contact",
            "sort_order",
            "ai_message_overage_enabled",
        ):
            value = configuration[field]
            if field == "features":
                value = list(value)
            setattr(plan, field, value)

        plan.is_public = True
        plan.is_active = True
        plan.full_clean()
        plan.save()
        return plan, created

    @staticmethod
    def _save_price(plan, configuration, ai_message_overage_unit_price):
        price = (
            PlanPrice.objects.select_for_update()
            .filter(
                plan=plan,
                provider=configuration["provider"],
                billing_interval=configuration["billing_interval"],
                currency="USD",
            )
            .first()
        )
        created = price is None
        if created:
            price = PlanPrice(
                plan=plan,
                provider=configuration["provider"],
                billing_interval=configuration["billing_interval"],
                currency="USD",
            )

        price.amount = configuration["amount"]
        price.ai_message_overage_unit_price = ai_message_overage_unit_price
        price.is_active = True
        price.full_clean()
        price.save()
        return created
