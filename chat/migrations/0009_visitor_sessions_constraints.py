from django.db import migrations, models
import django.db.models.deletion
from django.db.models import Q


class Migration(migrations.Migration):

    dependencies = [
        ("chat_session", "0008_visitor_sessions"),
        ("chatbot", "0019_chatbotvisitor"),
    ]

    operations = [
        migrations.AlterField(
            model_name="chatbotblockedvisitor",
            name="visitor",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="block_records",
                to="chatbot.chatbotvisitor",
            ),
        ),

        migrations.RemoveConstraint(
            model_name="chatsession",
            name="test_session_has_no_live_ownership",
        ),

        migrations.RemoveIndex(
            model_name="chatsession",
            name="chat_session_visitor_idx",
        ),

        migrations.AddIndex(
            model_name="chatsession",
            index=models.Index(
                fields=["visitor", "-last_activity_at"],
                name="chat_session_visitor_idx",
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
                        visitor__isnull=True,
                    )
                ),
                name="test_session_has_no_live_ownership",
            ),
        ),

        migrations.RemoveField(
            model_name="chatsession",
            name="user_metadata",
        ),

        migrations.RemoveField(
            model_name="chatsession",
            name="legacy_visitor_id",
        ),

        migrations.RemoveConstraint(
            model_name="chatbotblockedvisitor",
            name="unique_blocked_visitor_per_chatbot",
        ),

        migrations.RemoveConstraint(
            model_name="chatbotblockedvisitor",
            name="blocked_visitor_id_not_blank",
        ),

        migrations.AddConstraint(
            model_name="chatbotblockedvisitor",
            constraint=models.UniqueConstraint(
                fields=["visitor"],
                name="unique_blocked_visitor",
            ),
        ),

        migrations.RemoveField(
            model_name="chatbotblockedvisitor",
            name="legacy_visitor_id",
        ),

        migrations.RemoveField(
            model_name="chatbotblockedvisitor",
            name="chatbot",
        ),
    ]