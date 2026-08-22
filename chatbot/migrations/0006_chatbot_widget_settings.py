import uuid

import chatbot.utils.validation
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


def move_widget_settings_forward(apps, schema_editor):
    ChatbotSettings = apps.get_model("chatbot", "ChatbotSettings")
    ChatbotWidgetSettings = apps.get_model(
        "chatbot",
        "ChatbotWidgetSettings",
    )
    database = schema_editor.connection.alias

    for chatbot_settings in ChatbotSettings.objects.using(database).iterator():
        widget_settings = ChatbotWidgetSettings.objects.using(database).create(
            id=chatbot_settings.pk,
            chatbot_id=chatbot_settings.chatbot_id,
            widget_public_key=chatbot_settings.widget_public_key,
            widget_enabled=chatbot_settings.widget_enabled,
            widget_settings=chatbot_settings.widget_settings,
            created_by_id=chatbot_settings.created_by_id,
            updated_by_id=chatbot_settings.updated_by_id,
        )
        ChatbotWidgetSettings.objects.using(database).filter(
            pk=widget_settings.pk
        ).update(
            created_at=chatbot_settings.created_at,
            updated_at=chatbot_settings.updated_at,
        )


def move_widget_settings_backward(apps, schema_editor):
    ChatbotSettings = apps.get_model("chatbot", "ChatbotSettings")
    ChatbotWidgetSettings = apps.get_model(
        "chatbot",
        "ChatbotWidgetSettings",
    )
    database = schema_editor.connection.alias

    for widget_settings in ChatbotWidgetSettings.objects.using(database).iterator():
        ChatbotSettings.objects.using(database).filter(
            chatbot_id=widget_settings.chatbot_id
        ).update(
            widget_public_key=widget_settings.widget_public_key,
            widget_enabled=widget_settings.widget_enabled,
            widget_settings=widget_settings.widget_settings,
        )


class Migration(migrations.Migration):

    dependencies = [
        ("chatbot", "0005_move_chatbot_instructions"),
    ]

    operations = [
        migrations.CreateModel(
            name="ChatbotWidgetSettings",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "widget_public_key",
                    models.CharField(
                        default=(
                            chatbot.utils.validation.generate_widget_public_key
                        ),
                        editable=False,
                        help_text=(
                            "Public key embedded in the generated widget script."
                        ),
                        max_length=64,
                        unique=True,
                    ),
                ),
                ("widget_enabled", models.BooleanField(default=True)),
                (
                    "widget_settings",
                    models.JSONField(
                        blank=True,
                        default=dict,
                        help_text=(
                            "Widget presentation settings such as colors, text, "
                            "position, and launcher appearance."
                        ),
                        validators=[
                            chatbot.utils.validation.validate_widget_settings
                        ],
                    ),
                ),
                (
                    "chatbot",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="widget_settings",
                        to="chatbot.chatbot",
                    ),
                ),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name=(
                            "%(app_label)s_%(class)s_created_records"
                        ),
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "updated_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name=(
                            "%(app_label)s_%(class)s_updated_records"
                        ),
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
        ),
        migrations.RunPython(
            move_widget_settings_forward,
            reverse_code=move_widget_settings_backward,
        ),
    ]
