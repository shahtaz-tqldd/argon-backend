import json
from unittest.mock import Mock, patch
from uuid import uuid4

from django.test import SimpleTestCase

from chatbot.models import Chatbot
from chatbot.services.subscription import (
    ActiveChatbotSubscriptionNotFound,
    get_chatbot_subscription_entitlements,
)
from subscription.choices import PlanFeature, SubscriptionStatus
from subscription.models import ChatbotSubscription


class ChatbotSubscriptionEntitlementServiceTests(SimpleTestCase):
    def setUp(self):
        self.chatbot = Chatbot(
            id=uuid4(),
            chatbot_name="Entitlement Bot",
            slug="entitlement-bot",
            is_deleted=False,
        )
        self.subscription = Mock(
            id=uuid4(),
            chatbot_id=self.chatbot.id,
        )
        self.subscription.get_ai_message_limit.return_value = 1000
        self.subscription.get_file_size_limit_mb.return_value = 25
        self.subscription.get_knowledge_chunk_limit.return_value = 5000
        self.subscription.get_features.return_value = [
            PlanFeature.KNOWLEDGE_BASE,
            PlanFeature.LEAD_CAPTURE,
        ]

    def subscription_query(self):
        query = Mock()
        query.get.return_value = self.subscription
        return patch.object(
            ChatbotSubscription.objects,
            "only",
            return_value=query,
        ), query

    def test_returns_snapshotted_limits_and_feature_enums(self):
        subscription_patch, query = self.subscription_query()

        with subscription_patch:
            entitlements = get_chatbot_subscription_entitlements(self.chatbot)

        self.assertEqual(entitlements.ai_message_limit, 1000)
        self.assertEqual(entitlements.file_size_limit_mb, 25)
        self.assertEqual(entitlements.knowledge_chunk_limit, 5000)
        self.assertEqual(
            entitlements.features,
            (
                PlanFeature.KNOWLEDGE_BASE,
                PlanFeature.LEAD_CAPTURE,
            ),
        )
        self.assertTrue(entitlements.has_feature(PlanFeature.LEAD_CAPTURE))
        self.assertFalse(entitlements.has_feature("unknown"))
        self.assertEqual(
            json.loads(json.dumps(entitlements)),
            {
                "subscription_id": str(self.subscription.id),
                "chatbot_id": str(self.chatbot.id),
                "ai_message_limit": 1000,
                "file_size_limit_mb": 25,
                "knowledge_chunk_limit": 5000,
                "features": ["knowledge_base", "lead_capture"],
            },
        )
        query.get.assert_called_once_with(
            chatbot_id=self.chatbot.id,
            status=SubscriptionStatus.ACTIVE,
        )

    def test_resolves_chatbot_by_slug(self):
        chatbot_query = Mock()
        chatbot_query.get.return_value = self.chatbot
        subscription_patch, _ = self.subscription_query()

        with (
            patch.object(
                Chatbot.objects,
                "only",
                return_value=chatbot_query,
            ),
            subscription_patch,
        ):
            get_chatbot_subscription_entitlements(
                chatbot_slug=self.chatbot.slug,
            )

        chatbot_query.get.assert_called_once_with(
            slug=self.chatbot.slug,
            is_deleted=False,
        )

    def test_resolves_chatbot_by_id(self):
        chatbot_query = Mock()
        chatbot_query.get.return_value = self.chatbot
        subscription_patch, _ = self.subscription_query()

        with (
            patch.object(
                Chatbot.objects,
                "only",
                return_value=chatbot_query,
            ),
            subscription_patch,
        ):
            get_chatbot_subscription_entitlements(
                chatbot_id=self.chatbot.id,
            )

        chatbot_query.get.assert_called_once_with(
            id=self.chatbot.id,
            is_deleted=False,
        )

    def test_requires_exactly_one_chatbot_reference(self):
        with self.assertRaises(ValueError):
            get_chatbot_subscription_entitlements()

        with self.assertRaises(ValueError):
            get_chatbot_subscription_entitlements(
                self.chatbot,
                chatbot_id=self.chatbot.id,
            )

    def test_raises_when_chatbot_has_no_active_subscription(self):
        query = Mock()
        query.get.side_effect = ChatbotSubscription.DoesNotExist

        with (
            patch.object(
                ChatbotSubscription.objects,
                "only",
                return_value=query,
            ),
            self.assertRaises(ActiveChatbotSubscriptionNotFound),
        ):
            get_chatbot_subscription_entitlements(self.chatbot)
