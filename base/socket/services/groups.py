"""Stable Channels group names shared by all realtime publishers."""
from django.core.exceptions import ValidationError
from notification.models import NotificationRecipientType


def notification_group(recipient_type, target_id=None):
    """
    Return the Channels group for a notification audience.

    Workspace, chatbot, and chat-session subscriptions are membership-aware.
    """
    try:
        recipient_type = NotificationRecipientType(recipient_type)
    except ValueError as exc:
        raise ValidationError(
            f"Unknown notification recipient type: {recipient_type}"
        ) from exc
    if recipient_type == NotificationRecipientType.GLOBAL:
        if target_id is not None:
            raise ValidationError("A global notification group has no target_id.")
        return "notifications.global"
    if not target_id:
        raise ValidationError(
            f"{recipient_type.label} notification group requires an id."
        )
    return f"notifications.{recipient_type.value}.{target_id}"


def global_dashboard_group():
    return notification_group(NotificationRecipientType.GLOBAL)


def user_dashboard_group(user_id):
    return notification_group(NotificationRecipientType.USER, user_id)


def workspace_dashboard_group(workspace_id):
    return notification_group(NotificationRecipientType.WORKSPACE, workspace_id)


def chatbot_dashboard_group(chatbot_id):
    return notification_group(NotificationRecipientType.CHATBOT, chatbot_id)


def chat_session_dashboard_group(chat_session_id):
    return notification_group(
        NotificationRecipientType.CHAT_SESSION,
        chat_session_id,
    )


def chat_session_group(session_id):
    return f"chat_session_{session_id}"
