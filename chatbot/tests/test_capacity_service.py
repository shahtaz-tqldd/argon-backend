from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from django.core.exceptions import ValidationError
from django.test import SimpleTestCase

from chatbot.models import Chatbot, ChatbotCapacity
from chatbot.services.capacity import (
    BYTES_PER_MEGABYTE,
    get_chatbot_capacity,
    sync_chatbot_capacity_from_subscription,
    update_chatbot_capacity,
)
from subscription.choices import PlanFeature


class ChatbotCapacityServiceTests(SimpleTestCase):
    def setUp(self):
        self.chatbot = Chatbot(
            id=uuid4(),
            chatbot_name="Capacity Bot",
            slug="capacity-bot",
            is_deleted=False,
        )
        self.capacity = ChatbotCapacity(
            chatbot=self.chatbot,
            ai_message_limit=100,
            current_ai_message_count=10,
            file_size_limit_bytes=20 * BYTES_PER_MEGABYTE,
            current_file_size_bytes=1024,
            knowledge_chunk_limit=500,
            current_knowledge_chunk_count=25,
            active_features=[PlanFeature.KNOWLEDGE_BASE],
        )

    def test_get_returns_the_chatbot_capacity(self):
        with (
            patch(
                "chatbot.services.capacity.resolve_chatbot_reference",
                return_value=self.chatbot,
            ),
            patch.object(
                ChatbotCapacity.objects,
                "get",
                return_value=self.capacity,
            ) as get_capacity,
        ):
            result = get_chatbot_capacity(chatbot_slug=self.chatbot.slug)

        self.assertIs(result, self.capacity)
        get_capacity.assert_called_once_with(chatbot_id=self.chatbot.id)

    def test_update_applies_atomic_usage_deltas_and_limits(self):
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
                ChatbotCapacity.objects,
                "get_or_create",
                return_value=(self.capacity, True),
            ),
            patch.object(self.capacity, "full_clean") as full_clean,
            patch.object(self.capacity, "save") as save,
        ):
            result = update_chatbot_capacity(
                self.chatbot,
                ai_message_limit=200,
                active_features=[
                    PlanFeature.KNOWLEDGE_BASE,
                    PlanFeature.LEAD_CAPTURE,
                ],
                ai_message_delta=1,
                file_size_delta_bytes=2048,
                knowledge_chunk_delta=-5,
            )

        self.assertIs(result, self.capacity)
        self.assertEqual(self.capacity.ai_message_limit, 200)
        self.assertEqual(self.capacity.current_ai_message_count, 11)
        self.assertEqual(self.capacity.current_file_size_bytes, 3072)
        self.assertEqual(self.capacity.current_knowledge_chunk_count, 20)
        self.assertEqual(
            self.capacity.active_features,
            [PlanFeature.KNOWLEDGE_BASE, PlanFeature.LEAD_CAPTURE],
        )
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
                ChatbotCapacity.objects,
                "get_or_create",
                return_value=(self.capacity, True),
            ),
            self.assertRaises(ValidationError),
        ):
            update_chatbot_capacity(
                self.chatbot,
                ai_message_delta=-11,
            )

    def test_sync_copies_subscription_limits_without_usage_values(self):
        entitlements = SimpleNamespace(
            ai_message_limit=1000,
            file_size_limit_mb=50,
            knowledge_chunk_limit=5000,
            features=(
                PlanFeature.KNOWLEDGE_BASE,
                PlanFeature.LEAD_CAPTURE,
            ),
        )
        with (
            patch(
                "chatbot.services.capacity.resolve_chatbot_reference",
                return_value=self.chatbot,
            ),
            patch(
                "chatbot.services.capacity.get_chatbot_subscription_entitlements",
                return_value=entitlements,
            ),
            patch(
                "chatbot.services.capacity.update_chatbot_capacity",
                return_value=self.capacity,
            ) as update,
        ):
            result = sync_chatbot_capacity_from_subscription(self.chatbot)

        self.assertIs(result, self.capacity)
        update.assert_called_once_with(
            self.chatbot,
            ai_message_limit=1000,
            file_size_limit_bytes=50 * BYTES_PER_MEGABYTE,
            knowledge_chunk_limit=5000,
            active_features=entitlements.features,
        )
