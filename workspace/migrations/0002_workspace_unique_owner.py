from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("workspace", "0001_initial"),
    ]

    operations = [
        migrations.AddConstraint(
            model_name="workspace",
            constraint=models.UniqueConstraint(
                fields=("owner",),
                name="unique_workspace_per_owner",
            ),
        ),
    ]
