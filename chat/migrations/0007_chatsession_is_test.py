from django.db import migrations, models
from django.db.models import Q


class Migration(migrations.Migration):

    dependencies = [
        ("chat_session", "0006_chatbotblockedvisitor"),
    ]

    operations = [
        migrations.AddField(
            model_name="chatsession",
            name="is_test",
            field=models.BooleanField(
                default=False,
                help_text=(
                    "Whether this is an admin-only chatbot test conversation."
                ),
            ),
        ),
        migrations.AddIndex(
            model_name="chatsession",
            index=models.Index(
                fields=["chatbot", "is_test", "-last_activity_at"],
                name="chat_session_test_idx",
            ),
        ),
        migrations.AddConstraint(
            model_name="chatsession",
            constraint=models.CheckConstraint(
                condition=(
                    Q(is_test=False)
                    | Q(
                        assigned_to__isnull=True,
                        attention_reason="",
                        attention_requested_at__isnull=True,
                        is_test=True,
                        lead__isnull=True,
                        requires_attention=False,
                        visitor_id="",
                    )
                ),
                name="test_session_has_no_live_ownership",
            ),
        ),
    ]
