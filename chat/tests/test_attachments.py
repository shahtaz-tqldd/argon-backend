from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from app.services.r2 import R2Storage
from chat.models import (
    ChatMessage,
    ChatMessageAttachment,
    ChatSession,
    ChatSessionTakeover,
)
from chat.services.attachments import (
    classify_attachment_type,
    delete_message_attachment_uploads,
    upload_message_attachments,
)
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

FAKE_PNG_BYTES = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"
    b"\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00"
    b"\x1f\x15\xc4\x89\x00\x00\x00\rIDAT\x08\xd7c\xf8\xcf\xc0\xf0\x1f\x00\x05\x00\x01\xff"
    b"\x89\x99=\x1d\x00\x00\x00\x00IEND\xaeB`\x82"
)


SIMPLE_PDF = ("report.pdf", b"%PDF-1.4 fake body", "application/pdf")
SIMPLE_PNG = ("photo.png", FAKE_PNG_BYTES, "image/png")


def simple_uploaded_file(spec):
    name, data, content_type = spec
    return SimpleUploadedFile(name, data, content_type=content_type)


def fake_uploads(*object_keys, attachment_type=ChatMessageAttachmentType.DOCUMENT):
    return [
        {
            "attachment_type": attachment_type,
            "file_url": f"https://assets.example.com/{object_key}",
            "file_name": "report.pdf",
            "mime_type": "application/pdf",
            "file_size": 128,
            "sort_order": sort_order,
            "object_key": object_key,
        }
        for sort_order, object_key in enumerate(object_keys)
    ]


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


@override_settings(
    R2_BUCKET_NAME="argon-chatbot",
    R2_PUBLIC_URL="https://assets.example.com",
    R2_IMAGES_PREFIX="images",
    R2_FILES_PREFIX="files",
)
class UploadMessageAttachmentsTests(SimpleTestCase):
    def setUp(self):
        self.storage = R2Storage(client=Mock())

    def test_images_are_optimized_into_the_images_chat_folder(self):
        image = SimpleUploadedFile(
            "photo.png",
            FAKE_PNG_BYTES,
            content_type="image/png",
        )

        uploads = upload_message_attachments(
            [image],
            chatbot_id="bot-1",
            storage=self.storage,
        )

        self.assertEqual(len(uploads), 1)
        upload = uploads[0]
        self.assertEqual(
            upload["attachment_type"],
            ChatMessageAttachmentType.IMAGE,
        )
        self.assertTrue(
            upload["object_key"].startswith("images/chat/bot-1/"),
        )
        self.assertTrue(upload["object_key"].endswith(".webp"))
        self.assertEqual(upload["mime_type"], "image/webp")
        self.assertEqual(upload["file_name"], "photo.png")
        self.assertEqual(upload["file_size"], len(FAKE_PNG_BYTES))
        self.assertEqual(
            upload["file_url"],
            f"https://assets.example.com/{upload['object_key']}",
        )

    def test_documents_are_stored_unchanged_in_the_files_chat_folder(self):
        document = SimpleUploadedFile(
            "report.pdf",
            b"%PDF-1.4 fake body",
            content_type="application/pdf",
        )

        uploads = upload_message_attachments(
            [document],
            chatbot_id="bot-1",
            storage=self.storage,
        )

        upload = uploads[0]
        self.assertEqual(
            upload["attachment_type"],
            ChatMessageAttachmentType.DOCUMENT,
        )
        self.assertTrue(
            upload["object_key"].startswith("files/chat/bot-1/"),
        )
        self.assertTrue(upload["object_key"].endswith(".pdf"))
        self.assertEqual(upload["mime_type"], "application/pdf")
        self.assertEqual(upload["file_size"], len(b"%PDF-1.4 fake body"))

    def test_files_are_indexed_by_sort_order(self):
        uploads = upload_message_attachments(
            [
                SimpleUploadedFile("a.pdf", b"a", content_type="application/pdf"),
                SimpleUploadedFile("b.pdf", b"b", content_type="application/pdf"),
            ],
            chatbot_id="bot-1",
            storage=self.storage,
        )

        self.assertEqual(
            [upload["sort_order"] for upload in uploads],
            [0, 1],
        )

    def test_delete_message_attachment_uploads_removes_each_object(self):
        uploads = fake_uploads("files/chat/bot-1/one.pdf", "files/chat/bot-1/two.pdf")

        delete_message_attachment_uploads(uploads, storage=self.storage)

        deleted_keys = [
            call.kwargs["Key"]
            for call in self.storage.client.delete_object.call_args_list
        ]
        self.assertEqual(
            deleted_keys,
            ["files/chat/bot-1/one.pdf", "files/chat/bot-1/two.pdf"],
        )


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

    @patch("chat.services.messages.delete_message_attachment_uploads")
    @patch("chat.services.messages.upload_message_attachments")
    def test_agent_message_with_attachments_creates_rows(
        self,
        upload_message_attachments,
        delete_message_attachment_uploads,
    ):
        upload_message_attachments.return_value = fake_uploads(
            "files/chat/bot/report.pdf"
        )
        attachment_file = SimpleUploadedFile(
            "report.pdf",
            b"%PDF-1.4 fake body",
            content_type="application/pdf",
        )

        message = send_agent_message(
            self.session,
            self.agent,
            content="Here is the report.",
            attachments=[attachment_file],
        )

        upload_message_attachments.assert_called_once_with(
            [attachment_file],
            chatbot_id=self.chatbot.id,
        )
        delete_message_attachment_uploads.assert_not_called()
        attachment = message.attachments.get()
        self.assertEqual(
            attachment.attachment_type,
            ChatMessageAttachmentType.DOCUMENT,
        )
        self.assertEqual(
            attachment.file_url,
            "https://assets.example.com/files/chat/bot/report.pdf",
        )
        self.assertEqual(attachment.sort_order, 0)

    @patch("chat.services.messages.delete_message_attachment_uploads")
    @patch("chat.services.messages.upload_message_attachments")
    def test_rejected_agent_message_uploads_are_deleted(
        self,
        upload_message_attachments,
        delete_message_attachment_uploads,
    ):
        upload_message_attachments.return_value = fake_uploads(
            "files/chat/bot/report.pdf"
        )
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
                attachments=[simple_uploaded_file(SIMPLE_PDF)],
            )

        delete_message_attachment_uploads.assert_called_once_with(
            upload_message_attachments.return_value
        )
        self.assertFalse(
            ChatMessageAttachment.objects.exists(),
        )

    @patch("chat.services.messages.upload_message_attachments")
    def test_attachment_only_agent_message_is_valid(
        self,
        upload_message_attachments,
    ):
        upload_message_attachments.return_value = fake_uploads(
            "files/chat/bot/report.pdf"
        )

        message = send_agent_message(
            self.session,
            self.agent,
            content="",
            attachments=[simple_uploaded_file(SIMPLE_PDF)],
        )

        self.assertEqual(message.content, "")
        self.assertEqual(
            message.sender_type,
            ChatMessageSenderType.AGENT,
        )
        self.assertEqual(message.attachments.count(), 1)

    @patch("chat.services.chat_public.delete_message_attachment_uploads")
    @patch("chat.services.chat_public.upload_message_attachments")
    def test_duplicate_visitor_message_keeps_original_and_deletes_uploads(
        self,
        upload_message_attachments,
        delete_message_attachment_uploads,
    ):
        original, created = send_visitor_message(
            self.session,
            content="First copy",
            external_id="client-1",
        )
        self.assertTrue(created)
        upload_message_attachments.return_value = fake_uploads(
            "images/chat/bot/photo.webp",
            attachment_type=ChatMessageAttachmentType.IMAGE,
        )

        duplicate, was_created = send_visitor_message(
            self.session,
            content="Second copy",
            external_id="client-1",
            attachments=[simple_uploaded_file(SIMPLE_PNG)],
        )

        self.assertFalse(was_created)
        self.assertEqual(duplicate.id, original.id)
        delete_message_attachment_uploads.assert_called_once_with(
            upload_message_attachments.return_value
        )
        self.assertFalse(
            ChatMessageAttachment.objects.filter(chat_message=duplicate).exists(),
        )

    @patch("chat.services.chat_public.upload_message_attachments")
    def test_visitor_message_with_attachments_creates_rows(
        self,
        upload_message_attachments,
    ):
        upload_message_attachments.return_value = fake_uploads(
            "images/chat/bot/photo.webp",
            attachment_type=ChatMessageAttachmentType.IMAGE,
        )

        message, created = send_visitor_message(
            self.session,
            content="",
            attachments=[simple_uploaded_file(SIMPLE_PNG)],
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

    @patch("chat.services.messages.upload_message_attachments")
    def test_agent_can_send_message_with_attachment(
        self,
        upload_message_attachments,
    ):
        upload_message_attachments.return_value = fake_uploads(
            "files/chat/bot/report.pdf"
        )

        response = self.client.post(
            reverse("chat-message-send"),
            {
                "content": "Here is the report.",
                "attachments": SimpleUploadedFile(
                    "report.pdf",
                    b"%PDF-1.4 fake body",
                    content_type="application/pdf",
                ),
            },
            format="multipart",
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
        upload_message_attachments.assert_called_once()

    @patch("chat.services.messages.upload_message_attachments")
    def test_agent_attachment_only_message_is_accepted(
        self,
        upload_message_attachments,
    ):
        upload_message_attachments.return_value = fake_uploads(
            "files/chat/bot/report.pdf"
        )

        response = self.client.post(
            reverse("chat-message-send"),
            {"attachments": SimpleUploadedFile(
                "report.pdf",
                b"%PDF-1.4 fake body",
                content_type="application/pdf",
            )},
            format="multipart",
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
            format="multipart",
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
                "attachments": SimpleUploadedFile(
                    "big.pdf",
                    b"x" * 1024,
                    content_type="application/pdf",
                ),
            },
            format="multipart",
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

    @patch("chat.services.chat_public.upload_message_attachments")
    def test_visitor_can_send_message_with_attachment(
        self,
        upload_message_attachments,
    ):
        upload_message_attachments.return_value = fake_uploads(
            "images/chat/bot/photo.webp",
            attachment_type=ChatMessageAttachmentType.IMAGE,
        )

        response = self.client.post(
            reverse(
                "public-visitor-message-create",
                kwargs={"public_key": self.public_key},
            ),
            {
                "content": "Look at this.",
                "client_message_id": "visitor-client-1",
                "attachments": SimpleUploadedFile(
                    "photo.png",
                    FAKE_PNG_BYTES,
                    content_type="image/png",
                ),
            },
            format="multipart",
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

    @patch("chat.services.chat_public.upload_message_attachments")
    def test_visitor_attachment_only_message_is_accepted(
        self,
        upload_message_attachments,
    ):
        upload_message_attachments.return_value = fake_uploads(
            "images/chat/bot/photo.webp",
            attachment_type=ChatMessageAttachmentType.IMAGE,
        )

        response = self.client.post(
            reverse(
                "public-visitor-message-create",
                kwargs={"public_key": self.public_key},
            ),
            {"attachments": SimpleUploadedFile(
                "photo.png",
                FAKE_PNG_BYTES,
                content_type="image/png",
            )},
            format="multipart",
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
            format="multipart",
            query_params={
                "visitor_id": self.visitor_id,
                "session_id": self.session.id,
            },
            HTTP_AUTHORIZATION=f"Bearer {self.token}",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(ChatMessage.objects.exists())
