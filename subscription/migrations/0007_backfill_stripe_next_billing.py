from django.db import migrations, models


def backfill_stripe_next_billing(apps, schema_editor):
    ChatbotSubscription = apps.get_model(
        "subscription", "ChatbotSubscription"
    )
    ChatbotSubscription.objects.using(schema_editor.connection.alias).filter(
        provider="stripe",
        status="active",
        cancel_at_period_end=False,
        next_billing_at__isnull=True,
        current_period_end__isnull=False,
    ).update(next_billing_at=models.F("current_period_end"))


class Migration(migrations.Migration):
    dependencies = [
        ("subscription", "0006_billingpaymentmethod"),
    ]

    operations = [
        migrations.RunPython(
            backfill_stripe_next_billing,
            reverse_code=migrations.RunPython.noop,
        ),
    ]
