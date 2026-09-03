from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from chatbot.models import Chatbot, ChatbotUser
from chatbot.utils.choices import ChatbotRoleTypes
from chat_session.models import ChatMessage, ChatSession
from chat_session.utils.choices import (
    ChatMessageSenderType,
    ChatMessageStatus,
)
from lead_capture.models import Lead
from workspace.models import Workspace, WorkspaceRole, WorkspaceUser


User = get_user_model()


class ChatSessionClientAPITests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email="chat-session-api@example.com",
            password="StrongPass123!",
        )
        self.workspace = Workspace.objects.create(
            name="Chat Session API Workspace",
            slug="chat-session-api-workspace",
            owner=self.user,
        )
        WorkspaceUser.objects.create(
            workspace=self.workspace,
            user=self.user,
            role=WorkspaceRole.ADMIN,
        )
        self.chatbot = Chatbot.objects.create(
            workspace=self.workspace,
            chatbot_name="Support Bot",
            slug="chat-session-api-bot",
            created_by=self.user,
        )
        ChatbotUser.objects.create(
            chatbot=self.chatbot,
            user=self.user,
            role=ChatbotRoleTypes.ADMIN,
        )
        self.client.force_authenticate(self.user)

    def test_session_list_returns_minimal_structured_data(self):
        lead = Lead.objects.create(
            chatbot=self.chatbot,
            collected_fields={"name": "Lead Name", "Ref": "lead-ref"},
            detected_country_code="BD",
        )
        session = ChatSession.objects.create(
            chatbot=self.chatbot,
            lead=lead,
            ai_enabled=False,
            user_metadata={"name": "Metadata Name"},
            metadata={"Ref": "metadata-ref"},
        )
        ChatMessage.objects.create(
            chat_session=session,
            sender_type=ChatMessageSenderType.VISITOR,
            content="Unread question",
        )
        ChatMessage.objects.create(
            chat_session=session,
            sender_type=ChatMessageSenderType.VISITOR,
            content="Read question",
            status=ChatMessageStatus.READ,
        )
        ChatMessage.objects.create(
            chat_session=session,
            sender_type=ChatMessageSenderType.AI,
            content="Latest answer",
        )

        response = self.client.get(
            f'{reverse("chat-session-list")}?chatbot_slug={self.chatbot.slug}'
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["meta"]["count"], 1)
        item = response.data["data"][0]
        self.assertEqual(
            set(item),
            {
                "id",
                "channel",
                "user_data",
                "unread_message_count",
                "last_message",
                "ai_enabled",
                "status",
                "ended_at",
                "last_activity_at",
            },
        )
        self.assertEqual(
            item["user_data"],
            {
                "name": "Lead Name",
                "detected_country": "BD",
                "Ref": "lead-ref",
            },
        )
        self.assertEqual(item["unread_message_count"], 1)
        self.assertEqual(
            item["last_message"],
            {"sender": "ai", "content": "Latest answer"},
        )

