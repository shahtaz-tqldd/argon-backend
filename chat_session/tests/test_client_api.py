from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from chatbot.models import Chatbot, ChatbotUser
from chatbot.utils.choices import ChatbotPermissionTypes, ChatbotRoleTypes
from chat_session.models import (
    ChatMessage,
    ChatSession,
    ChatSessionTakeover,
    ChatSessionTransfer,
)
from chat_session.utils.choices import (
    ChatMessageSenderType,
    ChatMessageStatus,
    ChatSessionTakeoverReleaseReason,
    ChatSessionTransferStatus,
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
        self.agent = ChatbotUser.objects.create(
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
                "assigned_to",
                "requires_attention",
                "attention_reason",
                "attention_requested_at",
                "resolved_at",
                "closed_at",
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

    def test_mark_read_marks_only_unread_visitor_messages(self):
        session = ChatSession.objects.create(chatbot=self.chatbot)
        unread_visitor_message = ChatMessage.objects.create(
            chat_session=session,
            sender_type=ChatMessageSenderType.VISITOR,
            content="Unread question",
        )
        read_visitor_message = ChatMessage.objects.create(
            chat_session=session,
            sender_type=ChatMessageSenderType.VISITOR,
            content="Read question",
            status=ChatMessageStatus.READ,
        )
        agent_message = ChatMessage.objects.create(
            chat_session=session,
            sender_type=ChatMessageSenderType.AI,
            content="Answer",
        )

        response = self.client.patch(
            reverse("chat-session-mark-read"),
            query_params={
                "chatbot_slug": self.chatbot.slug,
                "session_id": session.id,
            },
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["data"]["marked_read_count"], 1)
        unread_visitor_message.refresh_from_db()
        read_visitor_message.refresh_from_db()
        agent_message.refresh_from_db()
        self.assertEqual(unread_visitor_message.status, ChatMessageStatus.READ)
        self.assertEqual(read_visitor_message.status, ChatMessageStatus.READ)
        self.assertEqual(agent_message.status, ChatMessageStatus.SENT)

    def test_mark_read_requires_chat_session_management_permission(self):
        member = User.objects.create_user(
            email="chat-session-member@example.com",
            password="StrongPass123!",
        )
        ChatbotUser.objects.create(
            chatbot=self.chatbot,
            user=member,
            role=ChatbotRoleTypes.MEMBER,
            permissions=[],
        )
        session = ChatSession.objects.create(chatbot=self.chatbot)
        message = ChatMessage.objects.create(
            chat_session=session,
            sender_type=ChatMessageSenderType.VISITOR,
            content="Unread question",
        )
        self.client.force_authenticate(member)

        response = self.client.patch(
            reverse("chat-session-mark-read"),
            query_params={
                "chatbot_slug": self.chatbot.slug,
                "session_id": session.id,
            },
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        message.refresh_from_db()
        self.assertEqual(message.status, ChatMessageStatus.SENT)

    def test_mark_read_allows_member_with_required_permission(self):
        member = User.objects.create_user(
            email="permitted-chat-session-member@example.com",
            password="StrongPass123!",
        )
        ChatbotUser.objects.create(
            chatbot=self.chatbot,
            user=member,
            role=ChatbotRoleTypes.MEMBER,
            permissions=[ChatbotPermissionTypes.CHAT_SESSION_MANAGEMENT],
        )
        session = ChatSession.objects.create(chatbot=self.chatbot)
        message = ChatMessage.objects.create(
            chat_session=session,
            sender_type=ChatMessageSenderType.VISITOR,
            content="Unread question",
        )
        self.client.force_authenticate(member)

        response = self.client.patch(
            reverse("chat-session-mark-read"),
            query_params={
                "chatbot_slug": self.chatbot.slug,
                "session_id": session.id,
            },
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["data"]["marked_read_count"], 1)
        message.refresh_from_db()
        self.assertEqual(message.status, ChatMessageStatus.READ)

    def _create_agent(self, email):
        user = User.objects.create_user(
            email=email,
            password="StrongPass123!",
        )
        agent = ChatbotUser.objects.create(
            chatbot=self.chatbot,
            user=user,
            role=ChatbotRoleTypes.MEMBER,
            permissions=[ChatbotPermissionTypes.CHAT_SESSION_MANAGEMENT],
        )
        return user, agent

    def _owned_session(self):
        session = ChatSession.objects.create(
            chatbot=self.chatbot,
            assigned_to=self.agent,
            ai_enabled=False,
        )
        ChatSessionTakeover.objects.create(
            chat_session=session,
            agent=self.agent,
        )
        return session

    def test_owner_can_request_and_recipient_can_accept_transfer(self):
        recipient_user, recipient = self._create_agent("recipient@example.com")
        session = self._owned_session()

        response = self.client.post(
            reverse("transfer-request"),
            {"to_agent_id": recipient.id, "reason": "Shift handoff"},
            format="json",
            query_params={
                "chatbot_slug": self.chatbot.slug,
                "session_id": session.id,
            },
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        transfer = ChatSessionTransfer.objects.get()
        self.assertEqual(transfer.status, ChatSessionTransferStatus.PENDING)

        self.client.force_authenticate(recipient_user)
        response = self.client.post(
            reverse("transfer-accept"),
            query_params={
                "chatbot_slug": self.chatbot.slug,
                "transfer_id": transfer.id,
            },
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        transfer.refresh_from_db()
        session.refresh_from_db()
        old_takeover = ChatSessionTakeover.objects.get(agent=self.agent)
        self.assertEqual(transfer.status, ChatSessionTransferStatus.ACCEPTED)
        self.assertEqual(session.assigned_to, recipient)
        self.assertEqual(
            old_takeover.release_reason,
            ChatSessionTakeoverReleaseReason.TRANSFERRED,
        )
        self.assertEqual(old_takeover.released_to, recipient)
        self.assertTrue(
            ChatSessionTakeover.objects.filter(
                chat_session=session,
                agent=recipient,
                released_at__isnull=True,
            ).exists()
        )

    def test_non_recipient_cannot_accept_transfer(self):
        _, recipient = self._create_agent("recipient-2@example.com")
        session = self._owned_session()
        response = self.client.post(
            reverse("transfer-request"),
            {"to_agent_id": recipient.id},
            format="json",
            query_params={
                "chatbot_slug": self.chatbot.slug,
                "session_id": session.id,
            },
        )
        transfer_id = response.data["data"]["id"]

        response = self.client.post(
            reverse("transfer-accept"),
            query_params={
                "chatbot_slug": self.chatbot.slug,
                "transfer_id": transfer_id,
            },
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(
            ChatSessionTransfer.objects.get(pk=transfer_id).status,
            ChatSessionTransferStatus.PENDING,
        )

    def test_recipient_can_list_and_decline_incoming_transfer(self):
        recipient_user, recipient = self._create_agent("recipient-3@example.com")
        session = self._owned_session()
        response = self.client.post(
            reverse("transfer-request"),
            {"to_agent_id": recipient.id},
            format="json",
            query_params={
                "chatbot_slug": self.chatbot.slug,
                "session_id": session.id,
            },
        )
        transfer_id = response.data["data"]["id"]
        self.client.force_authenticate(recipient_user)

        response = self.client.get(
            reverse("transfer-incoming-list"),
            query_params={"chatbot_slug": self.chatbot.slug},
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["meta"]["count"], 1)

        response = self.client.post(
            reverse("transfer-decline"),
            query_params={
                "chatbot_slug": self.chatbot.slug,
                "transfer_id": transfer_id,
            },
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            ChatSessionTransfer.objects.get(pk=transfer_id).status,
            ChatSessionTransferStatus.DECLINED,
        )
        session.refresh_from_db()
        self.assertEqual(session.assigned_to, self.agent)
