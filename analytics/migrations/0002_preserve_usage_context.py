import django.db.models.deletion

from django.db import migrations, models


def snapshot_existing_relations(apps, schema_editor):
    AIUsage = apps.get_model("analytics", "AIUsage")
    for usage in AIUsage.objects.all().iterator():
        AIUsage.objects.filter(pk=usage.pk).update(
            chatbot_id_snapshot=usage.chatbot_id,
            chat_session_id_snapshot=usage.chat_session_id,
            chat_message_id_snapshot=usage.chat_message_id,
        )


class Migration(migrations.Migration):
    dependencies = [
        ("analytics", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="aiusage",
            name="chatbot_id_snapshot",
            field=models.UUIDField(
                blank=True,
                db_index=True,
                editable=False,
                help_text=(
                    "Original chatbot ID retained after the chatbot is deleted."
                ),
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="aiusage",
            name="chat_session_id_snapshot",
            field=models.UUIDField(
                blank=True,
                db_index=True,
                editable=False,
                help_text=(
                    "Original session ID retained after the session is deleted."
                ),
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="aiusage",
            name="chat_message_id_snapshot",
            field=models.UUIDField(
                blank=True,
                editable=False,
                help_text=(
                    "Original generated-message ID retained after deletion."
                ),
                null=True,
            ),
        ),
        migrations.RunPython(
            snapshot_existing_relations,
            migrations.RunPython.noop,
        ),
        migrations.AlterField(
            model_name="aiusage",
            name="chatbot",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="ai_usages",
                to="chatbot.chatbot",
            ),
        ),
        migrations.AlterField(
            model_name="aiusage",
            name="chat_session",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="ai_usages",
                to="chat_session.chatsession",
            ),
        ),
        migrations.AlterField(
            model_name="aiusage",
            name="usage_type",
            field=models.CharField(
                choices=[
                    ("chat", "Chat"),
                    ("content_generation", "Content generation"),
                ],
                db_index=True,
                max_length=30,
            ),
        ),
    ]
