import uuid

from django.core.exceptions import ValidationError
from django.test import SimpleTestCase

from notification.api.v1.client.serializers import NotificationSerializer
from notification.models import Notification, NotificationRecipientType
from notification.services import (
    chatbot_dashboard_group,
    chat_session_dashboard_group,
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
