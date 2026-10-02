from uuid import uuid4

from django.core.exceptions import ValidationError
from django.test import SimpleTestCase

from chat.models import ChatMessage
from chat.utils.choices import ChatMessageSenderType


class ChatMessageValidationTests(SimpleTestCase):
    def test_metadata_only_visitor_event_is_valid(self):
        message = ChatMessage(
            chat_session_id=uuid4(),
            sender_type=ChatMessageSenderType.VISITOR,
            content="",
            metadata={
                "event_type": "appointment.submitted",
                "appointment_id": str(uuid4()),
            },
        )

        message.clean()

    def test_empty_message_without_event_type_remains_invalid(self):
        message = ChatMessage(
            chat_session_id=uuid4(),
            sender_type=ChatMessageSenderType.VISITOR,
            content="",
            metadata={"appointment_id": str(uuid4())},
        )

        with self.assertRaisesRegex(ValidationError, "event metadata"):
            message.clean()
