from django.db import migrations, models


def move_instructions_forward(apps, schema_editor):
    Chatbot = apps.get_model("chatbot", "Chatbot")
    ChatbotSettings = apps.get_model("chatbot", "ChatbotSettings")
    database = schema_editor.connection.alias

    for chatbot in Chatbot.objects.using(database).iterator():
        chatbot_settings = (
            ChatbotSettings.objects.using(database)
            .filter(chatbot_id=chatbot.pk)
            .first()
        )
        if chatbot_settings is None:
            if not chatbot.instructions:
                continue
            ChatbotSettings.objects.using(database).create(
                chatbot_id=chatbot.pk,
                instructions=chatbot.instructions,
                created_by_id=chatbot.created_by_id,
                updated_by_id=chatbot.updated_by_id,
            )
        else:
            chatbot_settings.instructions = chatbot.instructions
            chatbot_settings.save(update_fields=["instructions"])


def move_instructions_backward(apps, schema_editor):
    Chatbot = apps.get_model("chatbot", "Chatbot")
    ChatbotSettings = apps.get_model("chatbot", "ChatbotSettings")
    database = schema_editor.connection.alias

    for chatbot_settings in ChatbotSettings.objects.using(database).iterator():
        Chatbot.objects.using(database).filter(
            pk=chatbot_settings.chatbot_id
        ).update(instructions=chatbot_settings.instructions)


class Migration(migrations.Migration):

    dependencies = [
        ("chatbot", "0004_chatbotinvitation_invited_at"),
    ]

    operations = [
        migrations.AddField(
            model_name="chatbotsettings",
            name="instructions",
            field=models.TextField(blank=True, default=""),
            preserve_default=False,
        ),
        migrations.RunPython(
            move_instructions_forward,
            reverse_code=move_instructions_backward,
        ),
        migrations.RemoveField(
            model_name="chatbot",
            name="instructions",
        ),
        migrations.AlterField(
            model_name="chatbotsettings",
            name="human_handoff_enabled",
            field=models.BooleanField(
                default=True,
                help_text=(
                    "Allow the chatbot to handoff to the human assistant."
                ),
            ),
        ),
    ]
