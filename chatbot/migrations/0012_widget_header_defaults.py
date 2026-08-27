from django.db import migrations, models


DEFAULT_HEADER_DESCRIPTION = "typically replies instantly"


def backfill_widget_headers(apps, schema_editor):
    Chatbot = apps.get_model("chatbot", "Chatbot")
    ChatbotWidgetSettings = apps.get_model(
        "chatbot",
        "ChatbotWidgetSettings",
    )
    database = schema_editor.connection.alias
    chatbot_names = dict(
        Chatbot.objects.using(database).values_list("id", "chatbot_name")
    )

    for widget_settings in ChatbotWidgetSettings.objects.using(
        database
    ).iterator():
        updates = {}
        if not widget_settings.header_title:
            updates["header_title"] = chatbot_names.get(
                widget_settings.chatbot_id,
                "",
            )[:60]
        if not widget_settings.header_description:
            updates["header_description"] = DEFAULT_HEADER_DESCRIPTION
        if updates:
            ChatbotWidgetSettings.objects.using(database).filter(
                pk=widget_settings.pk,
            ).update(**updates)


class Migration(migrations.Migration):

    dependencies = [
        ("chatbot", "0011_alter_chatbot_welcome_message"),
    ]

    operations = [
        migrations.AlterField(
            model_name="chatbotwidgetsettings",
            name="header_title",
            field=models.CharField(
                blank=True,
                default="{chatbot_name}",
                max_length=60,
            ),
        ),
        migrations.AlterField(
            model_name="chatbotwidgetsettings",
            name="header_description",
            field=models.CharField(
                blank=True,
                default=DEFAULT_HEADER_DESCRIPTION,
                max_length=100,
            ),
        ),
        migrations.RunPython(
            backfill_widget_headers,
            reverse_code=migrations.RunPython.noop,
        ),
    ]
