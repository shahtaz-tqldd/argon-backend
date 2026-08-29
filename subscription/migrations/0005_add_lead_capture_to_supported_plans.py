from django.db import migrations


LEAD_CAPTURE_PLAN_SLUGS = ("growth", "premium", "enterprise")


def add_lead_capture_feature(apps, schema_editor):
    SubscriptionPlan = apps.get_model("subscription", "SubscriptionPlan")
    database = schema_editor.connection.alias

    plans = SubscriptionPlan.objects.using(database).filter(
        slug__in=LEAD_CAPTURE_PLAN_SLUGS
    )
    for plan in plans.iterator():
        features = list(plan.features or [])
        if "lead_capture" not in features:
            features.append("lead_capture")
            SubscriptionPlan.objects.using(database).filter(pk=plan.pk).update(
                features=features
            )


class Migration(migrations.Migration):
    dependencies = [
        ("subscription", "0004_ensure_default_free_plan"),
    ]

    operations = [
        migrations.RunPython(
            add_lead_capture_feature,
            reverse_code=migrations.RunPython.noop,
        ),
    ]
