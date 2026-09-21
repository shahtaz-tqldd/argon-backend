from datetime import datetime, time, timedelta

from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from chatbot.models import Chatbot, ChatbotUser
from chatbot.utils.choices import ChatbotPermissionTypes, ChatbotRoleTypes
from chat.models import (
    ChatMessage,
    ChatSession,
    ChatSessionTakeover,
    ChatSessionTransfer,
)
from chat.utils.choices import (
    ChatMessageSenderType,
    ChatMessageStatus,
    ChatSessionStatus,
    ChatSessionTakeoverReleaseReason,
    ChatSessionTransferStatus,
)
from lead_capture.models import Lead
from notification.models import Notification, NotificationType
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
            user_metadata={
                "name": "Metadata Name",
                "email": "visitor@example.com",
                "browser": "Firefox",
            },
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
                "transfer_requested_to",
                "requires_attention",
                "attention_reason",
                "is_recently_active",
                "last_activity_at",
            },
        )
        self.assertEqual(
            item["user_data"],
            {
                "name": "Lead Name",
                "detected_country": "BD",
            },
        )
        self.assertIsNone(item["assigned_to"])
        self.assertIsNone(item["transfer_requested_to"])
        self.assertEqual(item["unread_message_count"], 1)
        self.assertEqual(
            item["last_message"],
            {"sender": self.chatbot.chatbot_name, "content": "Latest answer"},
        )

    def test_session_list_returns_display_name_for_each_sender_type(self):
        self.user.name = "Support Agent"
        self.user.save(update_fields=["name"])

        agent_session = ChatSession.objects.create(chatbot=self.chatbot)
        named_visitor_session = ChatSession.objects.create(
            chatbot=self.chatbot,
            user_metadata={"name": "Jamie"},
        )
        anonymous_visitor_session = ChatSession.objects.create(chatbot=self.chatbot)
        system_session = ChatSession.objects.create(chatbot=self.chatbot)

        messages = (
            (agent_session, ChatMessageSenderType.AGENT, self.agent, "Agent reply"),
            (
                named_visitor_session,
                ChatMessageSenderType.VISITOR,
                None,
                "Named visitor reply",
            ),
            (
                anonymous_visitor_session,
                ChatMessageSenderType.VISITOR,
                None,
                "Anonymous visitor reply",
            ),
            (system_session, ChatMessageSenderType.SYSTEM, None, "System update"),
        )
        for session, sender_type, sender, content in messages:
            ChatMessage.objects.create(
                chat_session=session,
                sender_type=sender_type,
                sender=sender,
                content=content,
            )

        response = self.client.get(
            f'{reverse("chat-session-list")}?chatbot_slug={self.chatbot.slug}'
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        results = {item["id"]: item for item in response.data["data"]}
        self.assertEqual(
            results[str(agent_session.id)]["last_message"]["sender"],
            "Support Agent",
        )
        self.assertEqual(
            results[str(named_visitor_session.id)]["last_message"]["sender"],
            "Jamie",
        )
        self.assertEqual(
            results[str(anonymous_visitor_session.id)]["last_message"]["sender"],
            "visitor",
        )
        self.assertEqual(
            results[str(system_session.id)]["last_message"]["sender"],
            "system",
        )

    def test_session_list_returns_minimal_assignee_and_transfer_recipient(self):
        self.user.name = "Assigned Agent"
        self.user.save(update_fields=["name"])
        recipient_user = User.objects.create_user(
            email="transfer-recipient@example.com",
            password="StrongPass123!",
            name="Transfer Recipient",
        )
        recipient = ChatbotUser.objects.create(
            chatbot=self.chatbot,
            user=recipient_user,
            role=ChatbotRoleTypes.MEMBER,
        )
        ChatSession.objects.create(
            chatbot=self.chatbot,
            assigned_to=self.agent,
        )
        session = ChatSession.objects.get()
        ChatSessionTransfer.objects.create(
            chat_session=session,
            from_agent=self.agent,
            to_agent=recipient,
        )

        response = self.client.get(
            f'{reverse("chat-session-list")}?chatbot_slug={self.chatbot.slug}'
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            response.data["data"][0]["assigned_to"],
            {"name": "Assigned Agent"},
        )
        self.assertEqual(
            response.data["data"][0]["transfer_requested_to"],
            {"name": "Transfer Recipient"},
        )

    def test_session_list_orders_newest_message_activity_first(self):
        older_session = ChatSession.objects.create(chatbot=self.chatbot)
        newest_session = ChatSession.objects.create(chatbot=self.chatbot)
        ChatMessage.objects.create(
            chat_session=older_session,
            sender_type=ChatMessageSenderType.VISITOR,
            content="Older message",
        )
        ChatMessage.objects.create(
            chat_session=newest_session,
            sender_type=ChatMessageSenderType.VISITOR,
            content="Newest message",
        )

        response = self.client.get(
            f'{reverse("chat-session-list")}?chatbot_slug={self.chatbot.slug}'
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            [item["id"] for item in response.data["data"]],
            [str(newest_session.id), str(older_session.id)],
        )

    def test_session_list_filters_by_recent_visitor_activity(self):
        now = timezone.now()
        recent_session = ChatSession.objects.create(
            chatbot=self.chatbot,
            last_visitor_activity_at=now - timedelta(minutes=5),
            requires_attention=True,
            attention_reason="Visitor requested human support.",
            attention_requested_at=now - timedelta(minutes=4),
        )
        ChatSession.objects.create(
            chatbot=self.chatbot,
            last_visitor_activity_at=now - timedelta(minutes=11),
        )
        ChatSession.objects.create(chatbot=self.chatbot)

        unfiltered_response = self.client.get(
            reverse("chat-session-list"),
            query_params={"chatbot_slug": self.chatbot.slug},
        )
        false_filter_response = self.client.get(
            reverse("chat-session-list"),
            query_params={
                "chatbot_slug": self.chatbot.slug,
                "is_recently_active": False,
            },
        )
        response = self.client.get(
            reverse("chat-session-list"),
            query_params={
                "chatbot_slug": self.chatbot.slug,
                "is_recently_active": True,
            },
        )

        self.assertEqual(unfiltered_response.status_code, status.HTTP_200_OK)
        self.assertEqual(unfiltered_response.data["meta"]["count"], 3)
        self.assertEqual(false_filter_response.status_code, status.HTTP_200_OK)
        self.assertEqual(false_filter_response.data["meta"]["count"], 3)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["meta"]["count"], 1)
        self.assertEqual(response.data["data"][0]["id"], str(recent_session.id))

    def test_session_list_only_filters_attention_when_true(self):
        now = timezone.now()
        attention_session = ChatSession.objects.create(
            chatbot=self.chatbot,
            requires_attention=True,
            attention_reason="Visitor requested human support.",
            attention_requested_at=now,
        )
        regular_session = ChatSession.objects.create(chatbot=self.chatbot)

        for requires_attention in (None, False):
            query_params = {"chatbot_slug": self.chatbot.slug}
            if requires_attention is not None:
                query_params["requires_attention"] = requires_attention
            response = self.client.get(
                reverse("chat-session-list"),
                query_params=query_params,
            )

            self.assertEqual(response.status_code, status.HTTP_200_OK)
            self.assertEqual(
                {item["id"] for item in response.data["data"]},
                {str(attention_session.id), str(regular_session.id)},
            )

        filtered_response = self.client.get(
            reverse("chat-session-list"),
            query_params={
                "chatbot_slug": self.chatbot.slug,
                "requires_attention": True,
            },
        )

        self.assertEqual(filtered_response.status_code, status.HTTP_200_OK)
        self.assertEqual(filtered_response.data["meta"]["count"], 1)
        self.assertEqual(
            filtered_response.data["data"][0]["id"],
            str(attention_session.id),
        )

    def test_session_detail_returns_nested_chatbot(self):
        self.chatbot.logo = "https://example.com/support-bot.png"
        self.chatbot.save(update_fields=("logo", "updated_at"))
        session = ChatSession.objects.create(chatbot=self.chatbot)

        response = self.client.get(
            reverse("chat-session-detail"),
            query_params={
                "chatbot_slug": self.chatbot.slug,
                "session_id": session.id,
            },
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertNotIn("chatbot_id", response.data["data"])
        self.assertEqual(
            response.data["data"]["chatbot"],
            {
                "slug": self.chatbot.slug,
                "chatbot_name": self.chatbot.chatbot_name,
                "logo": self.chatbot.logo,
            },
        )

    def test_session_detail_merges_lead_contact_fields_into_user_metadata(self):
        lead = Lead.objects.create(
            chatbot=self.chatbot,
            collected_fields={
                "name": "Lead Name",
                "email": "lead@example.com",
                "phone": "+8801700000000",
            },
        )
        session = ChatSession.objects.create(
            chatbot=self.chatbot,
            lead=lead,
            user_metadata={
                "name": "Metadata Name",
                "email": "metadata@example.com",
                "phone": "unknown",
                "browser": "Firefox",
            },
        )

        response = self.client.get(
            reverse("chat-session-detail"),
            query_params={
                "chatbot_slug": self.chatbot.slug,
                "session_id": session.id,
            },
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            response.data["data"]["user_metadata"],
            {
                "name": "Lead Name",
                "email": "lead@example.com",
                "phone": "+8801700000000",
                "browser": "Firefox",
            },
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
        request_notification = Notification.objects.get(recipient=recipient.user)
        self.assertEqual(
            request_notification.notification_type,
            NotificationType.NOTIFY,
        )
        self.assertEqual(request_notification.metadata["status"], "pending")
        self.assertEqual(
            request_notification.metadata["transfer_id"],
            str(transfer.id),
        )
        session.refresh_from_db()
        current_takeover = ChatSessionTakeover.objects.get(
            chat_session=session,
            released_at__isnull=True,
        )
        self.assertEqual(session.assigned_to, self.agent)
        self.assertEqual(current_takeover.agent, self.agent)

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
        accepted_notification = Notification.objects.get(
            recipient=self.user,
            metadata__status="accepted",
        )
        self.assertEqual(
            accepted_notification.metadata["transfer_id"],
            str(transfer.id),
        )

    def test_agent_can_request_and_recipient_can_accept_unowned_transfer(self):
        recipient_user, recipient = self._create_agent(
            "unowned-transfer-recipient@example.com"
        )
        session = ChatSession.objects.create(chatbot=self.chatbot)

        response = self.client.post(
            reverse("transfer-request"),
            {"to_agent_id": recipient.id},
            format="json",
            query_params={
                "chatbot_slug": self.chatbot.slug,
                "session_id": session.id,
            },
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        transfer = ChatSessionTransfer.objects.get(pk=response.data["data"]["id"])

        self.client.force_authenticate(recipient_user)
        response = self.client.post(
            reverse("transfer-accept"),
            query_params={
                "chatbot_slug": self.chatbot.slug,
                "transfer_id": transfer.id,
            },
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        session.refresh_from_db()
        self.assertEqual(session.assigned_to, recipient)
        self.assertFalse(session.ai_enabled)
        self.assertTrue(
            ChatSessionTakeover.objects.filter(
                chat_session=session,
                agent=recipient,
                released_at__isnull=True,
            ).exists()
        )

    def test_unowned_transfer_can_be_requested_in_every_session_phase(self):
        _, recipient = self._create_agent("all-phases-recipient@example.com")

        for session_status in ChatSessionStatus.values:
            with self.subTest(session_status=session_status):
                session = ChatSession.objects.create(
                    chatbot=self.chatbot,
                    status=session_status,
                )

                response = self.client.post(
                    reverse("transfer-request"),
                    {"to_agent_id": recipient.id},
                    format="json",
                    query_params={
                        "chatbot_slug": self.chatbot.slug,
                        "session_id": session.id,
                    },
                )

                self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    def test_pending_transfer_blocks_takeover_until_recipient_declines(self):
        recipient_user, recipient = self._create_agent(
            "pending-transfer-recipient@example.com"
        )
        other_user, other_agent = self._create_agent(
            "pending-transfer-other-agent@example.com"
        )
        session = ChatSession.objects.create(chatbot=self.chatbot)
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

        self.client.force_authenticate(other_user)
        response = self.client.post(
            reverse("session-take-over"),
            query_params={
                "chatbot_slug": self.chatbot.slug,
                "session_id": session.id,
            },
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(
            response.data["message"],
            "Session cannot be taken over while a transfer is pending.",
        )
        self.assertFalse(
            ChatSessionTakeover.objects.filter(chat_session=session).exists()
        )

        self.client.force_authenticate(recipient_user)
        response = self.client.post(
            reverse("transfer-decline"),
            query_params={
                "chatbot_slug": self.chatbot.slug,
                "transfer_id": transfer_id,
            },
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        self.client.force_authenticate(other_user)
        response = self.client.post(
            reverse("session-take-over"),
            query_params={
                "chatbot_slug": self.chatbot.slug,
                "session_id": session.id,
            },
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        session.refresh_from_db()
        self.assertEqual(session.assigned_to, other_agent)

    def test_forced_takeover_replaces_owner_and_cancels_pending_transfer(self):
        _, recipient = self._create_agent(
            "forced-takeover-recipient@example.com"
        )
        forced_user, forced_agent = self._create_agent(
            "forced-takeover-agent@example.com"
        )
        session = self._owned_session()
        original_takeover = ChatSessionTakeover.objects.get(
            chat_session=session,
            released_at__isnull=True,
        )
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

        self.client.force_authenticate(forced_user)
        response = self.client.post(
            reverse("session-take-over"),
            {
                "is_forced": True,
                "reason": "The requested agent is unavailable.",
            },
            format="json",
            query_params={
                "chatbot_slug": self.chatbot.slug,
                "session_id": session.id,
            },
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        session.refresh_from_db()
        original_takeover.refresh_from_db()
        transfer = ChatSessionTransfer.objects.get(pk=transfer_id)
        forced_takeover = ChatSessionTakeover.objects.get(
            chat_session=session,
            released_at__isnull=True,
        )
        self.assertEqual(transfer.status, ChatSessionTransferStatus.CANCELLED)
        self.assertEqual(session.assigned_to, forced_agent)
        self.assertEqual(
            original_takeover.release_reason,
            ChatSessionTakeoverReleaseReason.FORCED_TAKEOVER,
        )
        self.assertEqual(forced_takeover.agent, forced_agent)
        self.assertTrue(forced_takeover.is_forced)
        self.assertEqual(
            forced_takeover.takeover_reason,
            "The requested agent is unavailable.",
        )
        self.assertTrue(response.data["data"]["is_forced"])
        self.assertEqual(
            response.data["data"]["takeover_reason"],
            "The requested agent is unavailable.",
        )

    def test_forced_takeover_requires_a_reason_of_at_most_256_characters(self):
        session = ChatSession.objects.create(chatbot=self.chatbot)

        for reason in ("", "x" * 257):
            with self.subTest(reason_length=len(reason)):
                response = self.client.post(
                    reverse("session-take-over"),
                    {"is_forced": True, "reason": reason},
                    format="json",
                    query_params={
                        "chatbot_slug": self.chatbot.slug,
                        "session_id": session.id,
                    },
                )

                self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
                self.assertFalse(
                    ChatSessionTakeover.objects.filter(
                        chat_session=session
                    ).exists()
                )

    def test_any_agent_can_view_a_sessions_pending_transfer_status(self):
        recipient_user, recipient = self._create_agent(
            "session-transfer-status-recipient@example.com"
        )
        observer_user, _ = self._create_agent(
            "session-transfer-status-observer@example.com"
        )
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

        self.client.force_authenticate(observer_user)
        response = self.client.get(
            reverse("session-transfer-status"),
            query_params={
                "chatbot_slug": self.chatbot.slug,
                "session_id": session.id,
            },
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.data["data"]
        self.assertTrue(data["has_pending_transfer"])
        self.assertEqual(data["chat_session_id"], str(session.id))
        self.assertEqual(data["assigned_to"]["id"], str(self.agent.id))
        self.assertEqual(data["transfer"]["id"], str(transfer_id))
        self.assertEqual(
            data["transfer"]["from_agent"]["id"],
            str(self.agent.id),
        )
        self.assertEqual(data["transfer"]["to_agent"]["id"], str(recipient.id))

        self.client.force_authenticate(recipient_user)
        response = self.client.post(
            reverse("transfer-decline"),
            query_params={
                "chatbot_slug": self.chatbot.slug,
                "transfer_id": transfer_id,
            },
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        self.client.force_authenticate(observer_user)
        response = self.client.get(
            reverse("session-transfer-status"),
            query_params={
                "chatbot_slug": self.chatbot.slug,
                "session_id": session.id,
            },
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertFalse(response.data["data"]["has_pending_transfer"])
        self.assertIsNone(response.data["data"]["transfer"])

    def test_non_owner_cannot_transfer_session_with_active_takeover(self):
        requester_user, _ = self._create_agent("non-owner-requester@example.com")
        _, recipient = self._create_agent("non-owner-recipient@example.com")
        session = self._owned_session()
        self.client.force_authenticate(requester_user)

        response = self.client.post(
            reverse("transfer-request"),
            {"to_agent_id": recipient.id},
            format="json",
            query_params={
                "chatbot_slug": self.chatbot.slug,
                "session_id": session.id,
            },
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(
            response.data["message"],
            "Only the current owner can perform this action.",
        )
        self.assertFalse(ChatSessionTransfer.objects.exists())

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
        declined_notification = Notification.objects.get(
            recipient=self.user,
            metadata__status="declined",
        )
        self.assertEqual(
            declined_notification.metadata["transfer_id"],
            str(transfer_id),
        )
        session.refresh_from_db()
        self.assertEqual(session.assigned_to, self.agent)

    def test_requester_can_cancel_transfer_and_recipient_is_notified(self):
        recipient_user, recipient = self._create_agent(
            "cancelled-transfer-recipient@example.com"
        )
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
            reverse("transfer-cancel"),
            query_params={
                "chatbot_slug": self.chatbot.slug,
                "transfer_id": transfer_id,
            },
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        cancelled_notification = Notification.objects.get(
            recipient=recipient_user,
            metadata__status="cancelled",
        )
        self.assertEqual(
            cancelled_notification.metadata["transfer_id"],
            str(transfer_id),
        )

    def test_only_requester_can_cancel_transfer(self):
        recipient_user, recipient = self._create_agent(
            "cancel-permission-recipient@example.com"
        )
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
        response = self.client.post(
            reverse("transfer-cancel"),
            query_params={
                "chatbot_slug": self.chatbot.slug,
                "transfer_id": transfer_id,
            },
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(
            response.data["message"],
            "Only the requesting agent can cancel this transfer.",
        )
        self.assertEqual(
            ChatSessionTransfer.objects.get(pk=transfer_id).status,
            ChatSessionTransferStatus.PENDING,
        )

    @staticmethod
    def _set_created_at(instance, value):
        type(instance).objects.filter(pk=instance.pk).update(created_at=value)

    def test_session_stats_returns_chatbot_totals_and_30_day_changes(self):
        now = timezone.now()

        old_session = ChatSession.objects.create(chatbot=self.chatbot)
        previous_sessions = [
            ChatSession.objects.create(chatbot=self.chatbot)
            for _ in range(2)
        ]
        current_session = ChatSession.objects.create(chatbot=self.chatbot)
        self._set_created_at(old_session, now - timedelta(days=70))
        for session in previous_sessions:
            self._set_created_at(session, now - timedelta(days=45))
        self._set_created_at(current_session, now - timedelta(days=10))

        old_message = ChatMessage.objects.create(
            chat_session=old_session,
            sender_type=ChatMessageSenderType.VISITOR,
            content="Old",
        )
        previous_messages = [
            ChatMessage.objects.create(
                chat_session=previous_sessions[0],
                sender_type=ChatMessageSenderType.VISITOR,
                content="Previous",
            )
            for _ in range(2)
        ]
        current_messages = [
            ChatMessage.objects.create(
                chat_session=current_session,
                sender_type=ChatMessageSenderType.VISITOR,
                content="Current",
            )
            for _ in range(3)
        ]
        self._set_created_at(old_message, now - timedelta(days=70))
        for message in previous_messages:
            self._set_created_at(message, now - timedelta(days=45))
        for message in current_messages:
            self._set_created_at(message, now - timedelta(days=10))

        other_chatbot = Chatbot.objects.create(
            workspace=self.workspace,
            chatbot_name="Other Bot",
            created_by=self.user,
        )
        ChatSession.objects.create(chatbot=other_chatbot)

        response = self.client.get(
            reverse("session-stats"),
            query_params={"chatbot_slug": self.chatbot.slug},
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            response.data["data"],
            {
                "total_sessions": 4,
                "total_messages": 6,
                "sessions_last_30_days": 1,
                "messages_last_30_days": 3,
                "session_percentage_change": -50.0,
                "message_percentage_change": 50.0,
            },
        )

    def test_session_stats_uses_defined_zero_baseline_percentage(self):
        session = ChatSession.objects.create(chatbot=self.chatbot)
        ChatMessage.objects.create(
            chat_session=session,
            sender_type=ChatMessageSenderType.VISITOR,
            content="Current",
        )

        response = self.client.get(
            reverse("session-stats"),
            query_params={"chatbot_slug": self.chatbot.slug},
        )

        self.assertEqual(
            response.data["data"]["session_percentage_change"],
            100.0,
        )
        self.assertEqual(
            response.data["data"]["message_percentage_change"],
            100.0,
        )

    def test_session_overview_defaults_to_14_days_and_fills_empty_days(self):
        today = timezone.localdate()
        two_days_ago = today - timedelta(days=2)
        timestamp = timezone.make_aware(
            datetime.combine(two_days_ago, time(hour=12)),
            timezone.get_current_timezone(),
        )
        for _ in range(2):
            session = ChatSession.objects.create(chatbot=self.chatbot)
            self._set_created_at(session, timestamp)

        response = self.client.get(
            reverse("session-overview"),
            query_params={"chatbot_slug": self.chatbot.slug},
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.data["data"]
        self.assertEqual(
            data["start_date"],
            (today - timedelta(days=13)).isoformat(),
        )
        self.assertEqual(data["end_date"], today.isoformat())
        self.assertEqual(len(data["points"]), 14)
        counts = {
            point["date"]: point["session_count"]
            for point in data["points"]
        }
        self.assertEqual(counts[two_days_ago.isoformat()], 2)
        self.assertEqual(counts[(today - timedelta(days=1)).isoformat()], 0)

    def test_session_overview_accepts_an_inclusive_custom_date_range(self):
        today = timezone.localdate()
        start_date = today - timedelta(days=4)
        end_date = today - timedelta(days=2)

        response = self.client.get(
            reverse("session-overview"),
            query_params={
                "chatbot_slug": self.chatbot.slug,
                "start_date": start_date.isoformat(),
                "end_date": end_date.isoformat(),
            },
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            [point["date"] for point in response.data["data"]["points"]],
            [
                (start_date + timedelta(days=offset)).isoformat()
                for offset in range(3)
            ],
        )

    def test_session_overview_rejects_an_inverted_date_range(self):
        today = timezone.localdate()

        response = self.client.get(
            reverse("session-overview"),
            query_params={
                "chatbot_slug": self.chatbot.slug,
                "start_date": today.isoformat(),
                "end_date": (today - timedelta(days=1)).isoformat(),
            },
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("end_date", response.data)
