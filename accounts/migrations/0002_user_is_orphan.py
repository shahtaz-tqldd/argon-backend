from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="user",
            name="is_orphan",
            field=models.BooleanField(
                db_index=True,
                default=False,
                verbose_name="Orphaned chatbot user",
            ),
        ),
    ]
