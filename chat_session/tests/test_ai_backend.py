from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase, override_settings

from chat_session.tasks import (
    _generate_reply,
    dispatch_ai_reply,
    is_ai_reply_enabled,
)
from chat_session.utils.choices import ChatSessionStatus


class AIBackendTests(SimpleTestCase):
    def setUp(self):
        self.session = SimpleNamespace()
        self.visitor_message = SimpleNamespace(id=uuid4())

    @override_settings(CHATBOT_AI_BACKEND="placeholder")
    def test_placeholder_is_enabled_when_session_ai_flag_is_off(self):
        session = SimpleNamespace(
            status=ChatSessionStatus.OPEN,
            assigned_to_id=None,
            ai_enabled=False,
        )
        chatbot = SimpleNamespace(ai_enabled=False)

        self.assertTrue(is_ai_reply_enabled(session, chatbot))

    @override_settings(CHATBOT_AI_BACKEND="placeholder")
    def test_placeholder_stays_off_during_human_takeover(self):
        session = SimpleNamespace(
            status=ChatSessionStatus.OPEN,
            assigned_to_id=uuid4(),
            ai_enabled=False,
        )

        self.assertFalse(is_ai_reply_enabled(session))

    @override_settings(
        CHATBOT_AI_BACKEND="placeholder",
        CHATBOT_PLACEHOLDER_REPLY="Hello from the test chatbot!",
    )
    def test_placeholder_backend_returns_configured_reply(self):
        content, metadata = _generate_reply(
            self.session,
            self.visitor_message,
        )

        self.assertEqual(content, "Hello from the test chatbot!")
        self.assertEqual(metadata["model"], "placeholder")
        self.assertEqual(
            metadata["in_reply_to"],
            str(self.visitor_message.id),
        )

    @override_settings(CHATBOT_AI_BACKEND="gemini")
    @patch("chat_session.tasks.GeminiChatService")
    def test_gemini_backend_delegates_to_gemini_service(self, service_class):
        service_class.return_value.generate_reply.return_value = (
            "Gemini reply",
            {"model": "gemini"},
        )

        result = _generate_reply(self.session, self.visitor_message)

        self.assertEqual(result, ("Gemini reply", {"model": "gemini"}))
        service_class.return_value.generate_reply.assert_called_once_with(
            self.session,
            self.visitor_message,
        )

    @override_settings(CHATBOT_AI_BACKEND="unknown")
    def test_unknown_backend_is_rejected(self):
        with self.assertRaises(ImproperlyConfigured):
            _generate_reply(self.session, self.visitor_message)

    @override_settings(CHATBOT_AI_BACKEND="placeholder")
    @patch("chat_session.tasks.generate_ai_reply_task.apply")
    def test_placeholder_is_dispatched_synchronously(self, apply):
        apply.return_value.successful.return_value = True

        result = dispatch_ai_reply(self.visitor_message.id)

        self.assertTrue(result)
        apply.assert_called_once_with(
            args=[str(self.visitor_message.id)],
            throw=True,
        )

    @override_settings(CHATBOT_AI_BACKEND="gemini")
    @patch("chat_session.tasks.generate_ai_reply_task.delay")
    def test_gemini_is_dispatched_through_celery(self, delay):
        result = dispatch_ai_reply(self.visitor_message.id)

        self.assertTrue(result)
        delay.assert_called_once_with(str(self.visitor_message.id))
