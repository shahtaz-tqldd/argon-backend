import re

import app.utils.validators
import chatbot.utils.validation
from django.db import migrations, models


DEFERRED_FEATURE_SETTINGS_KEY = "_deferred_feature_settings"
DEFERRED_FEATURE_FIELDS = (
    "appointment_booking_enabled",
    "lead_capture_enabled",
    "order_taking_enabled",
    "quotation_generation_enabled",
)
HEX_COLOR_PATTERN = re.compile(r"^#[0-9a-fA-F]{6}(?:[0-9a-fA-F]{2})?$")


def _pop_string(payload, key, *, max_length, default):
    value = payload.get(key)
    if isinstance(value, str) and len(value) <= max_length:
        payload.pop(key)
        return value
    return default


def _pop_choice(payload, key, *, choices, default):
    value = payload.get(key)
    if value in choices:
        payload.pop(key)
        return value
    return default


def _pop_color(payload, key, *, default):
    value = payload.get(key)
    if isinstance(value, str) and HEX_COLOR_PATTERN.fullmatch(value):
        payload.pop(key)
        return value
    return default


def _pop_boolean(payload, key, *, default):
    value = payload.get(key)
    if isinstance(value, bool):
        payload.pop(key)
        return value
    return default


def align_models_forward(apps, schema_editor):
    Chatbot = apps.get_model("chatbot", "Chatbot")
    ChatbotSettings = apps.get_model("chatbot", "ChatbotSettings")
    ChatbotWidgetSettings = apps.get_model(
        "chatbot",
        "ChatbotWidgetSettings",
    )
    database = schema_editor.connection.alias

    for legacy_settings in ChatbotSettings.objects.using(database).iterator():
        general_other_settings = legacy_settings.other_settings
        if not isinstance(general_other_settings, dict):
            general_other_settings = {}
        else:
            general_other_settings = dict(general_other_settings)

        deferred_feature_settings = {
            field: getattr(legacy_settings, field)
            for field in DEFERRED_FEATURE_FIELDS
        }
        if any(deferred_feature_settings.values()):
            general_other_settings[DEFERRED_FEATURE_SETTINGS_KEY] = (
                deferred_feature_settings
            )

        Chatbot.objects.using(database).filter(
            pk=legacy_settings.chatbot_id
        ).update(
            welcome_message=legacy_settings.welcome_message,
            fallback_message=legacy_settings.fallback_message,
            instructions=legacy_settings.instructions,
            language=legacy_settings.language,
            timezone=legacy_settings.timezone,
            ai_enabled=legacy_settings.ai_enabled,
            knowledge_base_enabled=legacy_settings.knowledge_base_enabled,
            human_handoff_enabled=legacy_settings.human_handoff_enabled,
            other_settings=general_other_settings,
        )

    for widget_settings in ChatbotWidgetSettings.objects.using(database).iterator():
        payload = widget_settings.other_settings
        if not isinstance(payload, dict):
            payload = {}
        else:
            payload = dict(payload)

        ChatbotWidgetSettings.objects.using(database).filter(
            pk=widget_settings.pk
        ).update(
            primary_color=_pop_color(
                payload,
                "primary_color",
                default="#3a86ff",
            ),
            secondary_color=_pop_color(
                payload,
                "secondary_color",
                default="#ff683a",
            ),
            launcher_position=_pop_choice(
                payload,
                "launcher_position",
                choices={"bottom_left", "bottom_right"},
                default="bottom_right",
            ),
            launcher_text=_pop_string(
                payload,
                "launcher_text",
                max_length=100,
                default="",
            ),
            header_title=_pop_string(
                payload,
                "header_title",
                max_length=60,
                default="",
            ),
            header_description=_pop_string(
                payload,
                "header_description",
                max_length=100,
                default="",
            ),
            show_branding=_pop_boolean(
                payload,
                "show_branding",
                default=True,
            ),
            theme=_pop_choice(
                payload,
                "theme",
                choices={"light", "dark", "system"},
                default="light",
            ),
            other_settings=payload,
        )

    widget_chatbot_ids = ChatbotWidgetSettings.objects.using(database).values(
        "chatbot_id"
    )
    for chatbot in (
        Chatbot.objects.using(database)
        .exclude(pk__in=widget_chatbot_ids)
        .iterator()
    ):
        ChatbotWidgetSettings.objects.using(database).create(
            chatbot_id=chatbot.pk,
            created_by_id=chatbot.created_by_id,
            updated_by_id=chatbot.updated_by_id,
        )


def align_models_backward(apps, schema_editor):
    Chatbot = apps.get_model("chatbot", "Chatbot")
    ChatbotSettings = apps.get_model("chatbot", "ChatbotSettings")
    ChatbotWidgetSettings = apps.get_model(
        "chatbot",
        "ChatbotWidgetSettings",
    )
    database = schema_editor.connection.alias

    widgets_by_chatbot = {
        widget.chatbot_id: widget
        for widget in ChatbotWidgetSettings.objects.using(database).iterator()
    }

    for chatbot in Chatbot.objects.using(database).iterator():
        general_other_settings = chatbot.other_settings
        if not isinstance(general_other_settings, dict):
            general_other_settings = {}
        else:
            general_other_settings = dict(general_other_settings)
        deferred_feature_settings = general_other_settings.pop(
            DEFERRED_FEATURE_SETTINGS_KEY,
            {},
        )
        if not isinstance(deferred_feature_settings, dict):
            deferred_feature_settings = {}

        widget_settings = widgets_by_chatbot.get(chatbot.pk)
        legacy_settings_values = {
            "chatbot_id": chatbot.pk,
            "welcome_message": chatbot.welcome_message,
            "fallback_message": chatbot.fallback_message,
            "instructions": chatbot.instructions,
            "language": chatbot.language,
            "timezone": chatbot.timezone,
            "ai_enabled": chatbot.ai_enabled,
            "knowledge_base_enabled": chatbot.knowledge_base_enabled,
            "human_handoff_enabled": chatbot.human_handoff_enabled,
            "other_settings": general_other_settings,
            "created_by_id": chatbot.created_by_id,
            "updated_by_id": chatbot.updated_by_id,
            **{
                field: bool(deferred_feature_settings.get(field, False))
                for field in DEFERRED_FEATURE_FIELDS
            },
        }
        if widget_settings is not None:
            legacy_settings_values["id"] = widget_settings.pk
            legacy_widget_payload = widget_settings.other_settings
            if not isinstance(legacy_widget_payload, dict):
                legacy_widget_payload = {}
            else:
                legacy_widget_payload = dict(legacy_widget_payload)
            legacy_widget_payload.update(
                {
                    "primary_color": widget_settings.primary_color,
                    "secondary_color": widget_settings.secondary_color,
                    "launcher_position": widget_settings.launcher_position,
                    "launcher_text": widget_settings.launcher_text,
                    "header_title": widget_settings.header_title,
                    "header_description": widget_settings.header_description,
                    "show_branding": widget_settings.show_branding,
                    "theme": widget_settings.theme,
                }
            )
            legacy_settings_values.update(
                {
                    "widget_public_key": widget_settings.public_key,
                    "widget_enabled": widget_settings.is_enabled,
                    "widget_settings": legacy_widget_payload,
                }
            )

        legacy_settings = ChatbotSettings.objects.using(database).create(
            **legacy_settings_values
        )
        ChatbotSettings.objects.using(database).filter(
            pk=legacy_settings.pk
        ).update(
            created_at=chatbot.created_at,
            updated_at=chatbot.updated_at,
        )

    for widget_settings in ChatbotWidgetSettings.objects.using(database).iterator():
        payload = widget_settings.other_settings
        if not isinstance(payload, dict):
            payload = {}
        else:
            payload = dict(payload)
        payload.update(
            {
                "primary_color": widget_settings.primary_color,
                "secondary_color": widget_settings.secondary_color,
                "launcher_position": widget_settings.launcher_position,
                "launcher_text": widget_settings.launcher_text,
                "header_title": widget_settings.header_title,
                "header_description": widget_settings.header_description,
                "show_branding": widget_settings.show_branding,
                "theme": widget_settings.theme,
            }
        )
        ChatbotWidgetSettings.objects.using(database).filter(
            pk=widget_settings.pk
        ).update(other_settings=payload)


class Migration(migrations.Migration):

    dependencies = [
        ("chatbot", "0006_chatbot_widget_settings"),
    ]

    operations = [
        migrations.AddField(
            model_name="chatbot",
            name="ai_enabled",
            field=models.BooleanField(default=True),
        ),
        migrations.AddField(
            model_name="chatbot",
            name="fallback_message",
            field=models.TextField(blank=True, default=""),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="chatbot",
            name="human_handoff_enabled",
            field=models.BooleanField(
                default=True,
                help_text="Allow the chatbot to handoff to a human assistant.",
            ),
        ),
        migrations.AddField(
            model_name="chatbot",
            name="instructions",
            field=models.TextField(blank=True, default=""),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="chatbot",
            name="knowledge_base_enabled",
            field=models.BooleanField(
                default=True,
                help_text=(
                    "Allow answers grounded in the chatbot's knowledge bases."
                ),
            ),
        ),
        migrations.AddField(
            model_name="chatbot",
            name="language",
            field=models.CharField(default="en", max_length=20),
        ),
        migrations.AddField(
            model_name="chatbot",
            name="other_settings",
            field=models.JSONField(
                blank=True,
                default=dict,
                help_text="Miscellaneous settings to operate chatbot",
                validators=[chatbot.utils.validation.validate_other_settings],
            ),
        ),
        migrations.AddField(
            model_name="chatbot",
            name="timezone",
            field=models.CharField(
                default="UTC",
                help_text="IANA timezone, for example Asia/Dhaka.",
                max_length=64,
                validators=[app.utils.validators.validate_timezone_name],
            ),
        ),
        migrations.AddField(
            model_name="chatbot",
            name="welcome_message",
            field=models.TextField(blank=True, default=""),
            preserve_default=False,
        ),
        migrations.RenameField(
            model_name="chatbotwidgetsettings",
            old_name="widget_enabled",
            new_name="is_enabled",
        ),
        migrations.RenameField(
            model_name="chatbotwidgetsettings",
            old_name="widget_public_key",
            new_name="public_key",
        ),
        migrations.RenameField(
            model_name="chatbotwidgetsettings",
            old_name="widget_settings",
            new_name="other_settings",
        ),
        migrations.AlterField(
            model_name="chatbotwidgetsettings",
            name="public_key",
            field=models.CharField(
                default=chatbot.utils.validation.generate_widget_public_key,
                editable=False,
                max_length=64,
                unique=True,
            ),
        ),
        migrations.AlterField(
            model_name="chatbotwidgetsettings",
            name="other_settings",
            field=models.JSONField(
                blank=True,
                default=dict,
                validators=[chatbot.utils.validation.validate_widget_settings],
            ),
        ),
        migrations.AddField(
            model_name="chatbotwidgetsettings",
            name="header_description",
            field=models.CharField(blank=True, default="", max_length=100),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="chatbotwidgetsettings",
            name="header_title",
            field=models.CharField(blank=True, default="", max_length=60),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="chatbotwidgetsettings",
            name="launcher_position",
            field=models.CharField(
                choices=[
                    ("bottom_left", "Bottom left"),
                    ("bottom_right", "Bottom right"),
                ],
                default="bottom_right",
                max_length=30,
            ),
        ),
        migrations.AddField(
            model_name="chatbotwidgetsettings",
            name="launcher_text",
            field=models.CharField(blank=True, default="", max_length=100),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="chatbotwidgetsettings",
            name="primary_color",
            field=models.CharField(
                default="#3a86ff",
                max_length=9,
                validators=[chatbot.utils.validation.validate_hex_color],
            ),
        ),
        migrations.AddField(
            model_name="chatbotwidgetsettings",
            name="secondary_color",
            field=models.CharField(
                default="#ff683a",
                max_length=9,
                validators=[chatbot.utils.validation.validate_hex_color],
            ),
        ),
        migrations.AddField(
            model_name="chatbotwidgetsettings",
            name="show_branding",
            field=models.BooleanField(default=True),
        ),
        migrations.AddField(
            model_name="chatbotwidgetsettings",
            name="theme",
            field=models.CharField(
                choices=[
                    ("light", "Light"),
                    ("dark", "Dark"),
                    ("system", "System"),
                ],
                default="light",
                max_length=20,
            ),
        ),
        migrations.RunPython(
            align_models_forward,
            reverse_code=align_models_backward,
        ),
        migrations.DeleteModel(
            name="ChatbotSettings",
        ),
        migrations.AlterField(
            model_name="chatbot",
            name="status",
            field=models.CharField(
                choices=[
                    ("draft", "Draft"),
                    ("active", "Active"),
                    ("disabled", "Disabled"),
                    ("disabled_by_admin", "Disabled by Admin"),
                ],
                db_index=True,
                default="draft",
                max_length=30,
            ),
        ),
    ]
