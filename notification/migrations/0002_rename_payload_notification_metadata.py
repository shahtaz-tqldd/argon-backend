from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("notification", "0001_initial"),
    ]

    operations = [
        migrations.RenameField(
            model_name="notification",
            old_name="payload",
            new_name="metadata",
        ),
    ]
