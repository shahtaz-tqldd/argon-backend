from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("subscription", "0002_remove_provider_price_ids"),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name="chatbotsubscription",
            name="sub_provider_subscription_unique",
        ),
        migrations.AddConstraint(
            model_name="chatbotsubscription",
            constraint=models.UniqueConstraint(
                condition=models.Q(
                    ("provider_subscription_id__isnull", False),
                    (
                        "status__in",
                        ["incomplete", "active", "past_due", "paused", "unpaid"],
                    ),
                    ~models.Q(("provider_subscription_id", "")),
                ),
                fields=("provider", "provider_subscription_id"),
                name="sub_provider_subscription_unique",
            ),
        ),
    ]
