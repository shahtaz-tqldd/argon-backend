from contextlib import nullcontext
from unittest.mock import patch
from uuid import uuid4

from django.core.exceptions import ValidationError
from django.test import SimpleTestCase

from chatbot.models import Chatbot, ChatbotConfig
from chatbot.services.chatbot_config import (
    get_chatbot_capacity,
    update_chatbot_capacity,
)


class ChatbotCapacityServiceTests(SimpleTestCase):
    def setUp(self):
        self.chatbot = Chatbot(
            id=uuid4(),
            chatbot_name="Capacity Bot",
            slug="capacity-bot",
            is_deleted=False,
        )
        self.capacity = ChatbotConfig(
            chatbot=self.chatbot,
            current_ai_message_count=10,
            current_file_size_bytes=1024,
            current_knowledge_chunk_count=25,
        )

    def test_get_returns_the_chatbot_capacity(self):
        with (
            patch(
                "chatbot.services.capacity.resolve_chatbot_reference",
                return_value=self.chatbot,
            ),
            patch.object(
                ChatbotConfig.objects,
                "get",
                return_value=self.capacity,
            ) as get_capacity,
        ):
            result = get_chatbot_capacity(chatbot_slug=self.chatbot.slug)

        self.assertIs(result, self.capacity)
        get_capacity.assert_called_once_with(chatbot_id=self.chatbot.id)

    def test_update_applies_atomic_usage_deltas(self):
        with (
            patch(
                "chatbot.services.capacity.resolve_chatbot_reference",
                return_value=self.chatbot,
            ),
            patch(
                "chatbot.services.capacity.transaction.atomic",
                return_value=nullcontext(),
            ),
            patch.object(
                ChatbotConfig.objects,
                "get_or_create",
                return_value=(self.capacity, True),
            ),
            patch.object(self.capacity, "full_clean") as full_clean,
            patch.object(self.capacity, "save") as save,
        ):
            result = update_chatbot_capacity(
                self.chatbot,
                ai_message_delta=1,
                file_size_delta_bytes=2048,
                knowledge_chunk_delta=-5,
            )

        self.assertIs(result, self.capacity)
        self.assertEqual(self.capacity.current_ai_message_count, 11)
        self.assertEqual(self.capacity.current_file_size_bytes, 3072)
        self.assertEqual(self.capacity.current_knowledge_chunk_count, 20)
        full_clean.assert_called_once_with()
        save.assert_called_once_with()

    def test_update_rejects_usage_below_zero(self):
        with (
            patch(
                "chatbot.services.capacity.resolve_chatbot_reference",
                return_value=self.chatbot,
            ),
            patch(
                "chatbot.services.capacity.transaction.atomic",
                return_value=nullcontext(),
            ),
            patch.object(
                ChatbotConfig.objects,
                "get_or_create",
                return_value=(self.capacity, True),
            ),
            self.assertRaises(ValidationError),
        ):
            update_chatbot_capacity(
                self.chatbot,
                ai_message_delta=-11,
            )
