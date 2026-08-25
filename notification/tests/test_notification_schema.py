import uuid
from unittest.mock import patch

from asgiref.sync import async_to_sync
from channels.layers import InMemoryChannelLayer
from django.core.exceptions import ValidationError
from django.test import SimpleTestCase

from notification.api.v1.client.serializers import NotificationSerializer
from notification.models import (
    Notification,
    NotificationRecipientType,
    NotificationType,
)
from notification.services import (
    chatbot_dashboard_group,
    chat_session_dashboard_group,
    emit_notification,
    global_dashboard_group,
    user_dashboard_group,
    workspace_dashboard_group,
)


class NotificationSchemaTests(SimpleTestCase):
    def test_serializer_exposes_audience_event_and_target(self):
        self.assertEqual(
            tuple(NotificationSerializer().fields),
            (
                "id",
                "recipient_type",
                "notification_type",
                "title",
                "message",
                "metadata",
                "workspace_id",
                "chatbot_id",
                "target_id",
                "is_read",
                "read_at",
                "created_at",
            ),
        )

    def test_global_notification_cannot_have_a_target(self):
        notification = Notification(
            recipient_type=NotificationRecipientType.GLOBAL,
            target_id=uuid.uuid4(),
            title="Maintenance",
        )

        with self.assertRaises(ValidationError):
            notification.clean()

    def test_user_notification_requires_a_user(self):
        notification = Notification(
            recipient_type=NotificationRecipientType.USER,
            title="Welcome",
        )

        with self.assertRaisesMessage(
            ValidationError,
            "User notifications require a recipient.",
        ):
            notification.clean()

    def test_future_domain_notification_requires_target_id(self):
        notification = Notification(
            recipient_type=NotificationRecipientType.CHAT_SESSION,
            title="New message",
        )

        with self.assertRaisesMessage(ValidationError, "require target_id"):
            notification.clean()

    def test_training_complete_is_a_supported_event_type(self):
        self.assertIn("training_complete", NotificationType.values)


class NotificationGroupTests(SimpleTestCase):
    def test_groups_are_stable_for_every_recipient_type(self):
        target_id = uuid.uuid4()

        self.assertEqual(global_dashboard_group(), "notifications.global")
        self.assertEqual(
            user_dashboard_group(target_id),
            f"notifications.user.{target_id}",
        )
        self.assertEqual(
            workspace_dashboard_group(target_id),
            f"notifications.workspace.{target_id}",
        )
        self.assertEqual(
            chatbot_dashboard_group(target_id),
            f"notifications.chatbot.{target_id}",
        )
        self.assertEqual(
            chat_session_dashboard_group(target_id),
            f"notifications.chat_session.{target_id}",
        )

    def test_training_complete_is_emitted_to_the_chatbot_group(self):
        chatbot_id = uuid.uuid4()
        channel_layer = InMemoryChannelLayer()
        channel_name = async_to_sync(channel_layer.new_channel)()
        async_to_sync(channel_layer.group_add)(
            chatbot_dashboard_group(chatbot_id),
            channel_name,
        )
        notification = Notification(
            id=uuid.uuid4(),
            recipient_type=NotificationRecipientType.CHATBOT,
            chatbot_id=chatbot_id,
            notification_type=NotificationType.TRAINING_COMPLETE,
            title="Knowledge training complete",
            metadata={"knowledge_base_id": str(uuid.uuid4())},
        )

        with patch(
            "notification.services.send_notification.get_channel_layer",
            return_value=channel_layer,
        ):
            emit_notification(notification)

        event = async_to_sync(channel_layer.receive)(channel_name)
        self.assertEqual(event["type"], "notification.created")
        self.assertEqual(
            event["notification"]["notification_type"],
            "training_complete",
        )
        self.assertEqual(
            event["notification"]["event"],
            "training_complete",
        )
