from django.db import migrations, models
from django.db.models import F
from django.utils import timezone


def backfill_invited_at(apps, schema_editor):
    ChatbotInvitation = apps.get_model("chatbot", "ChatbotInvitation")
    ChatbotInvitation.objects.filter(invited_at__isnull=True).update(
        invited_at=F("created_at"),
    )


class Migration(migrations.Migration):

    dependencies = [
        ("chatbot", "0003_chatbotinvitation_permissions"),
    ]

    operations = [
        migrations.AddField(
            model_name="chatbotinvitation",
            name="invited_at",
            field=models.DateTimeField(blank=True, db_index=True, null=True),
        ),
        migrations.RunPython(
            backfill_invited_at,
            reverse_code=migrations.RunPython.noop,
        ),
        migrations.AlterField(
            model_name="chatbotinvitation",
            name="invited_at",
            field=models.DateTimeField(db_index=True, default=timezone.now),
        ),
    ]
