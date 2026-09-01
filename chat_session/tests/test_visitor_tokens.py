from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from django.test import SimpleTestCase, override_settings

from chat_session.services.visitor_tokens import (
    InvalidConversationToken,
    decode_conversation_token,
    issue_conversation_token,
)


class VisitorConversationTokenTests(SimpleTestCase):
    def setUp(self):
        self.session = SimpleNamespace(
            id=uuid4(),
            chatbot_id=uuid4(),
            visitor_id="visitor-123",
        )

    def test_issued_token_is_bound_to_session_chatbot_and_visitor(self):
        payload = decode_conversation_token(
            issue_conversation_token(self.session)
        )

        self.assertEqual(payload["session_id"], str(self.session.id))
        self.assertEqual(payload["chatbot_id"], str(self.session.chatbot_id))
        self.assertEqual(payload["visitor_id"], self.session.visitor_id)

    def test_tampered_token_is_rejected(self):
        token = issue_conversation_token(self.session)

        with self.assertRaises(InvalidConversationToken):
            decode_conversation_token(f"{token}tampered")

    @override_settings(WIDGET_CONVERSATION_TOKEN_TTL_SECONDS=1)
    def test_expired_token_is_rejected(self):
        with patch("django.core.signing.time.time", return_value=100):
            token = issue_conversation_token(self.session)

        with (
            patch("django.core.signing.time.time", return_value=102),
            self.assertRaises(InvalidConversationToken),
        ):
            decode_conversation_token(token)
