from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("chat_session", "0003_chatsession_attention_reason_text"),
    ]

    operations = [
        migrations.AddField(
            model_name="chatsessiontakeover",
            name="is_forced",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="chatsessiontakeover",
            name="takeover_reason",
            field=models.CharField(blank=True, default="", max_length=256),
        ),
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
                ],
                default="",
                max_length=20,
            ),
        ),
        migrations.AddConstraint(
            model_name="chatsessiontakeover",
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(is_forced=False)
                    | models.Q(takeover_reason__gt="")
                ),
                name="takeover_force_requires_reason",
            ),
        ),
    ]
