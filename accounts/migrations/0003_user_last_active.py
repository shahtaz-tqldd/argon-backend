from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0002_user_is_orphan"),
    ]

    operations = [
        migrations.AddField(
            model_name="user",
            name="last_active",
            field=models.DateTimeField(
                blank=True,
                db_index=True,
                null=True,
                verbose_name="Last active",
            ),
        ),
    ]
