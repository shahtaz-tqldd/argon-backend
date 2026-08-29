from decimal import Decimal

from django.db import migrations


FREE_PLAN_DEFAULTS = {
    "name": "Free",
    "plan_type": "standard",
    "ai_message_limit": 100,
    "file_size_limit_mb": 10,
    "knowledge_chunk_limit": 30,
    "ai_message_overage_enabled": False,
    "features": ["human_handoff", "knowledge_base"],
    "details_html": "",
    "is_free": True,
    "is_public": True,
    "requires_sales_contact": False,
    "is_active": True,
    "sort_order": 0,
}


def ensure_default_free_plan(apps, schema_editor):
    SubscriptionPlan = apps.get_model("subscription", "SubscriptionPlan")
    PlanPrice = apps.get_model("subscription", "PlanPrice")
    database = schema_editor.connection.alias

    plan, created = SubscriptionPlan.objects.using(database).get_or_create(
        slug="free",
        defaults=FREE_PLAN_DEFAULTS,
    )
    if not created:
        updates = {}
        if not plan.is_free:
            updates["is_free"] = True
        if not plan.is_active:
            updates["is_active"] = True
        if updates:
            SubscriptionPlan.objects.using(database).filter(pk=plan.pk).update(
                **updates
            )

    price, _ = PlanPrice.objects.using(database).get_or_create(
        plan=plan,
        provider="manual",
        billing_interval="monthly",
        currency="USD",
        defaults={
            "amount": Decimal("0.00"),
            "is_active": True,
        },
    )
    price_updates = {}
    if price.amount != Decimal("0.00"):
        price_updates["amount"] = Decimal("0.00")
    if not price.is_active:
        price_updates["is_active"] = True
    if price_updates:
        PlanPrice.objects.using(database).filter(pk=price.pk).update(
            **price_updates
        )


class Migration(migrations.Migration):
    dependencies = [
        ("subscription", "0003_allow_stripe_contract_history"),
    ]

    operations = [
        migrations.RunPython(
            ensure_default_free_plan,
            reverse_code=migrations.RunPython.noop,
        ),
    ]
