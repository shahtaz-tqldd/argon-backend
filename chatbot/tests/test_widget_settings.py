from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase

from chatbot.models import ChatbotWidgetSettings
from chatbot.services import create_chatbot
from chatbot.utils.choices import (
    ChatbotWidgetLauncherPositionTypes,
    ChatbotWidgetThemeTypes,
)
from workspace.services import ensure_personal_workspace

User = get_user_model()


class ChatbotWidgetSettingsTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(
            email="widget-owner@example.com",
            password="StrongPass123!",
        )
        self.workspace = ensure_personal_workspace(self.owner)
        self.chatbot = create_chatbot(
            workspace=self.workspace,
            name="Widget Bot",
            created_by=self.owner,
        )

    def test_chatbot_creation_creates_dedicated_widget_settings(self):
        widget_settings = self.chatbot.widget_settings

        self.assertIsInstance(widget_settings, ChatbotWidgetSettings)
        self.assertTrue(widget_settings.is_enabled)
        self.assertTrue(widget_settings.public_key)
        self.assertEqual(widget_settings.primary_color, "#3a86ff")
        self.assertEqual(
            widget_settings.launcher_position,
            ChatbotWidgetLauncherPositionTypes.BOTTOM_RIGHT,
        )
        self.assertEqual(widget_settings.theme, ChatbotWidgetThemeTypes.LIGHT)

    def test_variable_widget_settings_must_be_a_json_object(self):
        widget_settings = self.chatbot.widget_settings
        widget_settings.other_settings = ["not", "an", "object"]

        with self.assertRaises(ValidationError) as error:
            widget_settings.full_clean()

        self.assertIn("other_settings", error.exception.message_dict)

    def test_widget_colors_positions_and_themes_are_validated(self):
        widget_settings = self.chatbot.widget_settings
        widget_settings.primary_color = "blue"
        widget_settings.launcher_position = "center"
        widget_settings.theme = "neon"

        with self.assertRaises(ValidationError) as error:
            widget_settings.full_clean()

        self.assertIn("primary_color", error.exception.message_dict)
        self.assertIn("launcher_position", error.exception.message_dict)
        self.assertIn("theme", error.exception.message_dict)
