from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from analytics.models import AIUsage
from chatbot.models import Chatbot, ChatbotCapacity, ChatbotUser
from chatbot.utils.choices import ChatbotRoleTypes
from chat.models import ChatMessage, ChatSession
from chat.utils.choices import ChatMessageSenderType
from notification.models import Notification
from workspace.models import Workspace, WorkspaceRole, WorkspaceUser


User = get_user_model()


class TestChatSessionAPITests(APITestCase):
    def setUp(self):
        self.owner = User.objects.create_user(
            email="test-chat-owner@example.com",
            password="StrongPass123!",
        )
        self.workspace = Workspace.objects.create(
            name="Test Chat Workspace",
            slug="test-chat-workspace",
            owner=self.owner,
        )
        WorkspaceUser.objects.create(
            workspace=self.workspace,
            user=self.owner,
            role=WorkspaceRole.ADMIN,
        )
        self.chatbot = Chatbot.objects.create(
            workspace=self.workspace,
            chatbot_name="Testable Bot",
            slug="testable-bot",
            created_by=self.owner,
        )
        self.admin = ChatbotUser.objects.create(
            chatbot=self.chatbot,
            user=self.owner,
            role=ChatbotRoleTypes.ADMIN,
        )
        self.capacity = ChatbotCapacity.objects.create(
            chatbot=self.chatbot,
            ai_message_limit=10,
        )
        self.client.force_authenticate(self.owner)

    @property
    def chatbot_query(self):
        return {"chatbot_slug": self.chatbot.slug}

    def create_test_session(self):
        response = self.client.post(
            reverse("test-chat-session-create"),
            query_params=self.chatbot_query,
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        return ChatSession.objects.get(pk=response.data["data"]["id"])

    @staticmethod
    def ai_response(content="The chatbot is working."):
        return {
            "result": {
                "content": content,
                "source_ids": ["source-1"],
                "appointment": None,
                "lead_score": None,
                "escalation": None,
            },
            "token": {"total_tokens": 12},
            "cost": 0.0001,
        }

    def test_admin_can_create_and_list_multiple_test_sessions(self):
        first = self.create_test_session()
        second = self.create_test_session()
        live_session = ChatSession.objects.create(chatbot=self.chatbot)

        test_response = self.client.get(
            reverse("test-chat-session-list"),
            query_params={**self.chatbot_query, "page_size": 10},
        )
        live_response = self.client.get(
            reverse("chat-session-list"),
            query_params={**self.chatbot_query, "page_size": 10},
        )

        self.assertEqual(test_response.status_code, status.HTTP_200_OK)
        self.assertEqual(test_response.data["meta"]["count"], 2)
        self.assertEqual(
            {item["id"] for item in test_response.data["data"]},
            {str(first.id), str(second.id)},
        )
        self.assertTrue(
            all(item["is_test"] for item in test_response.data["data"])
        )
        self.assertEqual(live_response.data["meta"]["count"], 1)
        self.assertEqual(
            live_response.data["data"][0]["id"],
            str(live_session.id),
        )

    @patch("chat.services.events.publish_session_event")
    @patch("chat.services.test_sessions.AgentClient")
    def test_send_returns_synchronous_ai_reply_without_live_side_effects(
        self,
        agent_client,
        publish_session_event,
    ):
        session = self.create_test_session()
        agent_client.return_value.chat_sync.return_value = self.ai_response()

        response = self.client.post(
            reverse("test-chat-message-send"),
            {"content": "Are you working?"},
            format="json",
            query_params={
                **self.chatbot_query,
                "session_id": session.id,
            },
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["data"]["usage_source"], "test_allowance")
        self.assertEqual(
            response.data["data"]["ai_message"]["content"],
            "The chatbot is working.",
        )
        self.assertEqual(
            response.data["data"]["capacity"][
                "current_test_ai_message_count"
            ],
            1,
        )
        self.assertEqual(
            response.data["data"]["capacity"]["current_ai_message_count"],
            0,
        )
        agent_client.return_value.chat_sync.assert_called_once_with(
            message="Are you working?",
            user_id=f"test:{session.id}",
        )
        self.assertEqual(session.messages.count(), 2)
        self.assertFalse(AIUsage.objects.exists())
        self.assertFalse(Notification.objects.exists())
        publish_session_event.assert_not_called()

        messages_response = self.client.get(
            reverse("test-chat-message-list"),
            query_params={
                **self.chatbot_query,
                "session_id": session.id,
                "page_size": 10,
            },
        )
        self.assertEqual(messages_response.status_code, status.HTTP_200_OK)
        self.assertEqual(messages_response.data["meta"]["count"], 2)

    @patch("chat.services.test_sessions.AgentClient")
    def test_test_allowance_falls_back_to_subscription_capacity(self, agent_client):
        session = self.create_test_session()
        self.capacity.current_test_ai_message_count = 100
        self.capacity.current_ai_message_count = 3
        self.capacity.save(
            update_fields=[
                "current_test_ai_message_count",
                "current_ai_message_count",
                "updated_at",
            ]
        )
        agent_client.return_value.chat_sync.return_value = self.ai_response()

        response = self.client.post(
            reverse("test-chat-message-send"),
            {"content": "Use subscription capacity."},
            format="json",
            query_params={
                **self.chatbot_query,
                "session_id": session.id,
            },
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(
            response.data["data"]["usage_source"],
            "subscription_allowance",
        )
        self.capacity.refresh_from_db()
        self.assertEqual(self.capacity.current_test_ai_message_count, 100)
        self.assertEqual(self.capacity.current_ai_message_count, 4)

    @patch("chat.services.test_sessions.AgentClient")
    def test_send_rejects_when_both_allowances_are_exhausted(self, agent_client):
        session = self.create_test_session()
        self.capacity.current_test_ai_message_count = 100
        self.capacity.current_ai_message_count = 10
        self.capacity.save(
            update_fields=[
                "current_test_ai_message_count",
                "current_ai_message_count",
                "updated_at",
            ]
        )

        response = self.client.post(
            reverse("test-chat-message-send"),
            {"content": "This should be rejected."},
            format="json",
            query_params={
                **self.chatbot_query,
                "session_id": session.id,
            },
        )

        self.assertEqual(response.status_code, status.HTTP_402_PAYMENT_REQUIRED)
        self.assertFalse(session.messages.exists())
        agent_client.assert_not_called()

    @patch("chat.services.test_sessions.AgentClient")
    def test_generation_failure_releases_reserved_test_capacity(self, agent_client):
        session = self.create_test_session()
        agent_client.return_value.chat_sync.side_effect = RuntimeError(
            "AI unavailable"
        )

        response = self.client.post(
            reverse("test-chat-message-send"),
            {"content": "Trigger a failure."},
            format="json",
            query_params={
                **self.chatbot_query,
                "session_id": session.id,
            },
        )

        self.assertEqual(response.status_code, status.HTTP_503_SERVICE_UNAVAILABLE)
        self.capacity.refresh_from_db()
        self.assertEqual(self.capacity.current_test_ai_message_count, 0)
        self.assertEqual(self.capacity.current_ai_message_count, 0)
        self.assertEqual(
            session.messages.filter(
                sender_type=ChatMessageSenderType.VISITOR,
            ).count(),
            1,
        )

    def test_non_admin_cannot_use_test_chat(self):
        member_user = User.objects.create_user(
            email="test-chat-member@example.com",
            password="StrongPass123!",
        )
        WorkspaceUser.objects.create(
            workspace=self.workspace,
            user=member_user,
            role=WorkspaceRole.MEMBER,
        )
        ChatbotUser.objects.create(
            chatbot=self.chatbot,
            user=member_user,
            role=ChatbotRoleTypes.MEMBER,
        )
        self.client.force_authenticate(member_user)

        response = self.client.post(
            reverse("test-chat-session-create"),
            query_params=self.chatbot_query,
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_live_session_detail_and_analytics_exclude_test_sessions(self):
        test_session = self.create_test_session()
        live_session = ChatSession.objects.create(chatbot=self.chatbot)
        ChatMessage.objects.create(
            chat_session=test_session,
            sender_type=ChatMessageSenderType.VISITOR,
            content="Test message",
        )
        ChatMessage.objects.create(
            chat_session=live_session,
            sender_type=ChatMessageSenderType.VISITOR,
            content="Live message",
        )

        detail_response = self.client.get(
            reverse("chat-session-detail"),
            query_params={
                **self.chatbot_query,
                "session_id": test_session.id,
            },
        )
        stats_response = self.client.get(
            reverse("session-stats"),
            query_params=self.chatbot_query,
        )
        overview_response = self.client.get(
            reverse("session-overview"),
            query_params=self.chatbot_query,
        )

        self.assertEqual(detail_response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(stats_response.data["data"]["total_sessions"], 1)
        self.assertEqual(stats_response.data["data"]["total_messages"], 1)
        self.assertEqual(
            sum(
                point["session_count"]
                for point in overview_response.data["data"]["points"]
            ),
            1,
        )
