from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import transaction

from subscription.choices import (
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
LEAD_CAPTURE_FEATURES = [
    *STANDARD_FEATURES,
    PlanFeature.LEAD_CAPTURE,
]

PLAN_CONFIGURATIONS = (
    {
        "name": "Free",
        "slug": "free",
        "plan_type": PlanType.STANDARD,
        "ai_message_limit": 100,
        "file_size_limit_mb": 10,
        "knowledge_chunk_limit": 30,
        "features": STANDARD_FEATURES,
        "is_free": True,
        "requires_sales_contact": False,
        "sort_order": 0,
        "price": {
            "provider": PaymentProvider.MANUAL,
            "amount": Decimal("0.00"),
        },
    },
    {
        "name": "Starter",
        "slug": "starter",
        "plan_type": PlanType.STANDARD,
        "ai_message_limit": 650,
        "file_size_limit_mb": 20,
        "knowledge_chunk_limit": 250,
        "features": STANDARD_FEATURES,
        "is_free": False,
        "requires_sales_contact": False,
        "sort_order": 10,
        "price": {
            "provider": PaymentProvider.STRIPE,
            "amount": Decimal("35.00"),
        },
    },
    {
        "name": "Growth",
        "slug": "growth",
        "plan_type": PlanType.STANDARD,
        "ai_message_limit": 1500,
        "file_size_limit_mb": 50,
        "knowledge_chunk_limit": 600,
        "features": LEAD_CAPTURE_FEATURES,
        "is_free": False,
        "requires_sales_contact": False,
        "sort_order": 20,
        "price": {
            "provider": PaymentProvider.STRIPE,
            "amount": Decimal("49.00"),
        },
    },
    {
        "name": "Premium",
        "slug": "premium",
        "plan_type": PlanType.STANDARD,
        "ai_message_limit": 3500,
        "file_size_limit_mb": 75,
        "knowledge_chunk_limit": 1500,
        "features": LEAD_CAPTURE_FEATURES,
        "is_free": False,
        "requires_sales_contact": False,
        "sort_order": 30,
        "price": {
            "provider": PaymentProvider.STRIPE,
            "amount": Decimal("79.00"),
        },
    },
    {
        "name": "Enterprise",
        "slug": "enterprise",
        "plan_type": PlanType.ENTERPRISE,
        "ai_message_limit": None,
        "file_size_limit_mb": None,
        "knowledge_chunk_limit": None,
        "features": LEAD_CAPTURE_FEATURES,
        "is_free": False,
        "requires_sales_contact": True,
        "sort_order": 40,
        "price": None,
    },
)


class Command(BaseCommand):
    help = "Create or update the default subscription plans and monthly prices."

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

            price_configuration = configuration["price"]
            if price_configuration is None:
                PlanPrice.objects.filter(plan=plan, is_active=True).update(
                    is_active=False,
                )
                continue

            price_created = self._save_price(plan, price_configuration)
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
            "plan_type",
            "ai_message_limit",
            "file_size_limit_mb",
            "knowledge_chunk_limit",
            "features",
            "is_free",
            "requires_sales_contact",
            "sort_order",
        ):
            value = configuration[field]
            if field == "features":
                value = list(value)
            setattr(plan, field, value)

        plan.ai_message_overage_enabled = False
        plan.is_public = True
        plan.is_active = True
        plan.full_clean()
        plan.save()
        return plan, created

    @staticmethod
    def _save_price(plan, configuration):
        price = (
            PlanPrice.objects.select_for_update()
            .filter(
                plan=plan,
                provider=configuration["provider"],
                billing_interval=BillingInterval.MONTHLY,
                currency="USD",
            )
            .first()
        )
        created = price is None
        if created:
            price = PlanPrice(
                plan=plan,
                provider=configuration["provider"],
                billing_interval=BillingInterval.MONTHLY,
                currency="USD",
            )

        price.amount = configuration["amount"]
        price.ai_message_overage_unit_price = None
        price.is_active = True
        price.full_clean()
        price.save()
        return created
