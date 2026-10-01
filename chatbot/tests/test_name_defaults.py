from contextlib import nullcontext
from unittest.mock import patch

from django.test import SimpleTestCase

from app.core.models import BaseModel
from chatbot.models import Chatbot, ChatbotWidgetSettings, build_default_chatbot_welcome_message


class ChatbotNameDefaultTests(SimpleTestCase):
    def save_renamed(self, *, welcome=None, changes=None, update_fields=None):
        old_welcome = welcome if welcome is not None else build_default_chatbot_welcome_message("Old Bot", "Old Business")
        chatbot = Chatbot(chatbot_name="Old Bot", business_name="Old Business", slug="old-bot", welcome_message=old_welcome)
        chatbot._state.adding = False
        for field, value in (changes or {}).items():
            setattr(chatbot, field, value)
        with (
            patch.object(Chatbot.objects, "using") as query,
            patch.object(ChatbotWidgetSettings.objects, "using") as widget,
            patch.object(BaseModel, "save") as save,
            patch("chatbot.models.transaction.atomic", return_value=nullcontext()),
        ):
            query.return_value.filter.return_value.values.return_value.first.return_value = {
                "chatbot_name": "Old Bot", "business_name": "Old Business", "welcome_message": old_welcome,
            }
            chatbot.save(update_fields=update_fields)
        return chatbot, save, widget

    def test_both_names_refresh_defaults_with_partial_save(self):
        chatbot, save, widget = self.save_renamed(
            changes={"chatbot_name": "New Bot", "business_name": "New Business"},
            update_fields=["chatbot_name", "business_name"],
        )
        self.assertEqual(chatbot.welcome_message, build_default_chatbot_welcome_message("New Bot", "New Business"))
        self.assertIn("welcome_message", save.call_args.kwargs["update_fields"])
        widget.return_value.filter.assert_called_once_with(
            chatbot_id=chatbot.id, header_title__in=["Old Bot", "{chatbot_name}"],
        )
        self.assertEqual(widget.return_value.filter.return_value.update.call_args.kwargs["header_title"], "New Bot")

    def test_business_name_only_refreshes_welcome(self):
        chatbot, save, widget = self.save_renamed(
            changes={"business_name": "New Business"}, update_fields=["business_name"],
        )
        self.assertEqual(chatbot.welcome_message, build_default_chatbot_welcome_message("Old Bot", "New Business"))
        widget.assert_not_called()

    def test_custom_welcome_is_preserved(self):
        chatbot, save, widget = self.save_renamed(
            welcome="Welcome to our store!", changes={"chatbot_name": "New Bot"}, update_fields=["chatbot_name"],
        )
        self.assertEqual(chatbot.welcome_message, "Welcome to our store!")
        self.assertNotIn("welcome_message", save.call_args.kwargs["update_fields"])

    def test_explicit_welcome_in_same_update_is_preserved(self):
        chatbot, save, widget = self.save_renamed(
            changes={"chatbot_name": "New Bot", "welcome_message": "New custom welcome"},
            update_fields=["chatbot_name", "welcome_message"],
        )
        self.assertEqual(chatbot.welcome_message, "New custom welcome")

    def test_names_excluded_from_partial_save_are_not_used(self):
        chatbot, save, widget = self.save_renamed(
            changes={"chatbot_name": "Unsaved Bot", "business_name": "New Business"},
            update_fields=["business_name"],
        )
        self.assertEqual(chatbot.welcome_message, build_default_chatbot_welcome_message("Old Bot", "New Business"))
        widget.assert_not_called()

    def test_widget_header_is_truncated_to_maximum_length(self):
        chatbot, save, widget = self.save_renamed(
            changes={"chatbot_name": "A" * 120}, update_fields=["chatbot_name"],
        )
        self.assertEqual(widget.return_value.filter.return_value.update.call_args.kwargs["header_title"], "A" * 60)
