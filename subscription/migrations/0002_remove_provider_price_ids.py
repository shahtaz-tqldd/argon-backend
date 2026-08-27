from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("subscription", "0001_initial"),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name="planprice",
            name="sub_planprice_provider_id_unique",
        ),
        migrations.RemoveField(
            model_name="planprice",
            name="provider_overage_price_id",
        ),
        migrations.RemoveField(
            model_name="planprice",
            name="provider_price_id",
        ),
    ]
