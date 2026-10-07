from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from chat.api.v1.client.serializers import AgentMessageCreateSerializer
from chat.models import (
    ChatMessage,
    ChatMessageAttachment,
    ChatSession,
    ChatSessionTakeover,
)
from chat.services.attachments import classify_attachment_type
from chat.services.chat_public import send_visitor_message
from chat.services.messages import send_agent_message
from chat.services.visitor_tokens import issue_conversation_token
from chat.utils.choices import (
    ChatMessageAttachmentType,
    ChatMessageSenderType,
)
from chatbot.models import (
    Chatbot,
    ChatbotUser,
    ChatbotVisitor,
    ChatbotWidgetSettings,
)
from chatbot.utils.choices import ChatbotRoleTypes
from workspace.models import Workspace


User = get_user_model()


def attachment_payload(
    file_url,
    *,
    file_name="report.pdf",
    mime_type="application/pdf",
    file_size=128,
    **extra,
):
    return {
        "file_url": file_url,
        "file_name": file_name,
        "mime_type": mime_type,
        "file_size": file_size,
        **extra,
    }


PDF_ATTACHMENT = attachment_payload(
    "https://assets.example.com/files/chat/bot/report.pdf"
)
IMAGE_ATTACHMENT = attachment_payload(
    "https://assets.example.com/images/chat/bot/photo.webp",
    file_name="photo.webp",
    mime_type="image/webp",
)


class AttachmentClassificationTests(SimpleTestCase):
    def test_mime_types_map_to_attachment_types(self):
        cases = {
            "image/png": ChatMessageAttachmentType.IMAGE,
            "image/webp": ChatMessageAttachmentType.IMAGE,
            "video/mp4": ChatMessageAttachmentType.VIDEO,
            "audio/mpeg": ChatMessageAttachmentType.AUDIO,
            "application/pdf": ChatMessageAttachmentType.DOCUMENT,
            "text/plain": ChatMessageAttachmentType.DOCUMENT,
            "application/vnd.ms-excel": ChatMessageAttachmentType.DOCUMENT,
            "application/octet-stream": ChatMessageAttachmentType.OTHER,
            "": ChatMessageAttachmentType.OTHER,
        }
        for mime_type, expected in cases.items():
            self.assertEqual(
                classify_attachment_type(mime_type),
                expected,
                msg=mime_type,
            )


class MessageAttachmentSerializerTests(SimpleTestCase):
    def test_attachment_metadata_is_derived_from_url(self):
        serializer = AgentMessageCreateSerializer(
            data={
                "content": "Here is the report.",
                "attachments": [
                    {
                        "file_url": (
                            "https://assets.example.com/files/chat/bot/photo.png"
                        )
                    }
                ],
            }
        )

        self.assertTrue(serializer.is_valid())
        attachment = serializer.validated_data["attachments"][0]
        self.assertEqual(
            attachment["attachment_type"],
            ChatMessageAttachmentType.IMAGE,
        )
        self.assertEqual(attachment["mime_type"], "image/png")
        self.assertEqual(attachment["file_name"], "photo.png")
        self.assertEqual(attachment["sort_order"], 0)

    def test_attachments_are_indexed_by_sort_order(self):
        serializer = AgentMessageCreateSerializer(
            data={
                "content": "Two files.",
                "attachments": [
                    attachment_payload(
                        "https://assets.example.com/files/chat/bot/a.pdf"
                    ),
                    attachment_payload(
                        "https://assets.example.com/files/chat/bot/b.pdf"
                    ),
                ],
            }
        )

        self.assertTrue(serializer.is_valid())
        self.assertEqual(
            [
                attachment["sort_order"]
                for attachment in serializer.validated_data["attachments"]
            ],
            [0, 1],
        )

    def test_attachment_requires_a_valid_url(self):
        serializer = AgentMessageCreateSerializer(
            data={
                "content": "Not a URL.",
                "attachments": [{"file_url": "not-a-url"}],
            }
        )

        self.assertFalse(serializer.is_valid())
        self.assertIn("attachments", serializer.errors)

    @override_settings(CHAT_ATTACHMENT_MAX_FILE_SIZE_MB=0)
    def test_oversize_attachment_is_rejected(self):
        serializer = AgentMessageCreateSerializer(
            data={
                "content": "Too big.",
                "attachments": [
                    attachment_payload(
                        "https://assets.example.com/files/chat/bot/big.pdf",
                        file_size=1024,
                    )
                ],
            }
        )

        self.assertFalse(serializer.is_valid())
        self.assertIn("attachments", serializer.errors)


class MessageAttachmentServiceTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email="attachment-service@example.com",
            password="StrongPass123!",
        )
        self.workspace = Workspace.objects.create(
            name="Attachment Service Workspace",
            slug="attachment-service-workspace",
            owner=self.user,
        )
        self.chatbot = Chatbot.objects.create(
            workspace=self.workspace,
            chatbot_name="Attachment Bot",
            slug="attachment-bot",
            created_by=self.user,
        )
        self.agent = ChatbotUser.objects.create(
            chatbot=self.chatbot,
            user=self.user,
            role=ChatbotRoleTypes.ADMIN,
        )
        self.session = ChatSession.objects.create(
            chatbot=self.chatbot,
            visitor=ChatbotVisitor.objects.create(
                chatbot=self.chatbot,
                visitor_id="attachment-visitor",
            ),
            assigned_to=self.agent,
            ai_enabled=False,
        )
        ChatSessionTakeover.objects.create(
            chat_session=self.session,
            agent=self.agent,
        )

    def test_agent_message_with_attachments_creates_rows(self):
        message = send_agent_message(
            self.session,
            self.agent,
            content="Here is the report.",
            attachments=[dict(PDF_ATTACHMENT)],
        )

        attachment = message.attachments.get()
        self.assertEqual(
            attachment.attachment_type,
            ChatMessageAttachmentType.DOCUMENT,
        )
        self.assertEqual(
            attachment.file_url,
            "https://assets.example.com/files/chat/bot/report.pdf",
        )
        self.assertEqual(attachment.file_name, "report.pdf")
        self.assertEqual(attachment.mime_type, "application/pdf")
        self.assertEqual(attachment.file_size, 128)
        self.assertEqual(attachment.sort_order, 0)

    def test_rejected_agent_message_creates_no_attachment_rows(self):
        session_without_takeover = ChatSession.objects.create(
            chatbot=self.chatbot,
            visitor=ChatbotVisitor.objects.create(
                chatbot=self.chatbot,
                visitor_id="no-takeover-visitor",
            ),
            ai_enabled=False,
        )

        with self.assertRaises(ValidationError):
            send_agent_message(
                session_without_takeover,
                self.agent,
                content="",
                attachments=[dict(PDF_ATTACHMENT)],
            )

        self.assertFalse(
            ChatMessageAttachment.objects.exists(),
        )

    def test_attachment_only_agent_message_is_valid(self):
        message = send_agent_message(
            self.session,
            self.agent,
            content="",
            attachments=[dict(PDF_ATTACHMENT)],
        )

        self.assertEqual(message.content, "")
        self.assertEqual(
            message.sender_type,
            ChatMessageSenderType.AGENT,
        )
        self.assertEqual(message.attachments.count(), 1)

    def test_duplicate_visitor_message_keeps_original_attachments(self):
        original, created = send_visitor_message(
            self.session,
            content="First copy",
            external_id="client-1",
        )
        self.assertTrue(created)

        duplicate, was_created = send_visitor_message(
            self.session,
            content="Second copy",
            external_id="client-1",
            attachments=[dict(IMAGE_ATTACHMENT)],
        )

        self.assertFalse(was_created)
        self.assertEqual(duplicate.id, original.id)
        self.assertFalse(
            ChatMessageAttachment.objects.filter(chat_message=duplicate).exists(),
        )

    def test_visitor_message_with_attachments_creates_rows(self):
        message, created = send_visitor_message(
            self.session,
            content="",
            attachments=[dict(IMAGE_ATTACHMENT)],
        )

        self.assertTrue(created)
        self.assertEqual(message.attachments.count(), 1)
        self.assertEqual(
            message.attachments.get().attachment_type,
            ChatMessageAttachmentType.IMAGE,
        )


class AgentMessageAttachmentAPITests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email="agent-attachment-api@example.com",
            password="StrongPass123!",
        )
        self.workspace = Workspace.objects.create(
            name="Agent Attachment API Workspace",
            slug="agent-attachment-api-workspace",
            owner=self.user,
        )
        self.chatbot = Chatbot.objects.create(
            workspace=self.workspace,
            chatbot_name="Agent Attachment Bot",
            slug="agent-attachment-bot",
            created_by=self.user,
        )
        self.agent = ChatbotUser.objects.create(
            chatbot=self.chatbot,
            user=self.user,
            role=ChatbotRoleTypes.ADMIN,
        )
        self.session = ChatSession.objects.create(
            chatbot=self.chatbot,
            visitor=ChatbotVisitor.objects.create(
                chatbot=self.chatbot,
                visitor_id="agent-api-visitor",
            ),
            assigned_to=self.agent,
            ai_enabled=False,
        )
        ChatSessionTakeover.objects.create(
            chat_session=self.session,
            agent=self.agent,
        )
        self.client.force_authenticate(self.user)

    def test_agent_can_send_message_with_attachment(self):
        response = self.client.post(
            reverse("chat-message-send"),
            {
                "content": "Here is the report.",
                "attachments": [PDF_ATTACHMENT],
            },
            format="json",
            query_params={
                "chatbot_slug": self.chatbot.slug,
                "session_id": self.session.id,
            },
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        message = ChatMessage.objects.get(
            chat_session=self.session,
            sender_type=ChatMessageSenderType.AGENT,
        )
        self.assertEqual(message.content, "Here is the report.")
        attachment = message.attachments.get()
        self.assertEqual(
            attachment.file_url,
            "https://assets.example.com/files/chat/bot/report.pdf",
        )
        self.assertEqual(
            response.data["data"]["attachments"][0]["file_url"],
            attachment.file_url,
        )

    def test_agent_attachment_only_message_is_accepted(self):
        response = self.client.post(
            reverse("chat-message-send"),
            {"attachments": [PDF_ATTACHMENT]},
            format="json",
            query_params={
                "chatbot_slug": self.chatbot.slug,
                "session_id": self.session.id,
            },
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        message = ChatMessage.objects.get(
            chat_session=self.session,
            sender_type=ChatMessageSenderType.AGENT,
        )
        self.assertEqual(message.content, "")
        self.assertEqual(message.attachments.count(), 1)

    def test_blank_message_without_attachments_is_rejected(self):
        response = self.client.post(
            reverse("chat-message-send"),
            {"content": ""},
            format="json",
            query_params={
                "chatbot_slug": self.chatbot.slug,
                "session_id": self.session.id,
            },
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    @override_settings(CHAT_ATTACHMENT_MAX_FILE_SIZE_MB=0)
    def test_oversize_attachment_is_rejected(self):
        response = self.client.post(
            reverse("chat-message-send"),
            {
                "content": "Too big.",
                "attachments": [
                    attachment_payload(
                        "https://assets.example.com/files/chat/bot/big.pdf",
                        file_size=1024,
                    )
                ],
            },
            format="json",
            query_params={
                "chatbot_slug": self.chatbot.slug,
                "session_id": self.session.id,
            },
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(ChatMessage.objects.exists())


class VisitorMessageAttachmentAPITests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email="visitor-attachment-api@example.com",
            password="StrongPass123!",
        )
        self.workspace = Workspace.objects.create(
            name="Visitor Attachment API Workspace",
            slug="visitor-attachment-api-workspace",
            owner=self.user,
        )
        self.chatbot = Chatbot.objects.create(
            workspace=self.workspace,
            chatbot_name="Visitor Attachment Bot",
            slug="visitor-attachment-bot",
            created_by=self.user,
        )
        ChatbotWidgetSettings.objects.create(chatbot=self.chatbot)
        self.visitor_id = "visitor-api-visitor"
        self.session = ChatSession.objects.create(
            chatbot=self.chatbot,
            visitor=ChatbotVisitor.objects.create(
                chatbot=self.chatbot,
                visitor_id=self.visitor_id,
            ),
            ai_enabled=False,
        )
        self.public_key = self.chatbot.widget_settings.public_key
        self.token = issue_conversation_token(self.session)

    def test_visitor_can_send_message_with_attachment(self):
        response = self.client.post(
            reverse(
                "public-visitor-message-create",
                kwargs={"public_key": self.public_key},
            ),
            {
                "content": "Look at this.",
                "client_message_id": "visitor-client-1",
                "attachments": [IMAGE_ATTACHMENT],
            },
            format="json",
            query_params={
                "visitor_id": self.visitor_id,
                "session_id": self.session.id,
            },
            HTTP_AUTHORIZATION=f"Bearer {self.token}",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertFalse(response.data["data"]["duplicate"])
        message = ChatMessage.objects.get(
            chat_session=self.session,
            sender_type=ChatMessageSenderType.VISITOR,
        )
        attachment = message.attachments.get()
        self.assertEqual(
            attachment.attachment_type,
            ChatMessageAttachmentType.IMAGE,
        )
        self.assertEqual(
            attachment.file_url,
            "https://assets.example.com/images/chat/bot/photo.webp",
        )
        self.assertEqual(
            response.data["data"]["message"]["attachments"][0]["file_url"],
            attachment.file_url,
        )

    def test_visitor_attachment_only_message_is_accepted(self):
        response = self.client.post(
            reverse(
                "public-visitor-message-create",
                kwargs={"public_key": self.public_key},
            ),
            {"attachments": [IMAGE_ATTACHMENT]},
            format="json",
            query_params={
                "visitor_id": self.visitor_id,
                "session_id": self.session.id,
            },
            HTTP_AUTHORIZATION=f"Bearer {self.token}",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        message = ChatMessage.objects.get(
            chat_session=self.session,
            sender_type=ChatMessageSenderType.VISITOR,
        )
        self.assertEqual(message.content, "")
        self.assertEqual(message.attachments.count(), 1)

    def test_visitor_blank_message_without_attachments_is_rejected(self):
        response = self.client.post(
            reverse(
                "public-visitor-message-create",
                kwargs={"public_key": self.public_key},
            ),
            {"content": ""},
            format="json",
            query_params={
                "visitor_id": self.visitor_id,
                "session_id": self.session.id,
            },
            HTTP_AUTHORIZATION=f"Bearer {self.token}",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(ChatMessage.objects.exists())
