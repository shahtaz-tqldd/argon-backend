from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("chat_session", "0004_forced_takeover_audit"),
    ]

    operations = [
        migrations.AlterField(
            model_name="chatsessiontakeover",
            name="release_reason",
            field=models.CharField(
                blank=True,
                choices=[
                    ("transferred", "Transferred"),
                    ("resolved", "Resolved"),
                    ("closed", "Closed"),
                    ("released", "Released"),
                    ("forced_takeover", "Forced takeover"),
                    ("forced_release", "Forced return to AI"),
                ],
                default="",
                max_length=20,
            ),
        ),
    ]
