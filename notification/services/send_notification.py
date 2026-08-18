import logging

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.core.exceptions import ValidationError
from django.db import transaction

from notification.api.v1.client.serializers import NotificationSerializer
from notification.models import (
    Notification,
    NotificationRecipientType,
    NotificationType,
)

logger = logging.getLogger(__name__)


def notification_group(recipient_type, target_id=None):
    """
    Return the Channels group for a notification audience.

    Workspace and chatbot subscriptions are membership-aware. Chat-session
    subscriptions will use the same convention once that domain exists.
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


def create_notification(
    *,
    recipient_type,
    title,
    notification_type=NotificationType.GENERAL,
    message="",
    recipient=None,
    workspace=None,
    chatbot=None,
    target_id=None,
    metadata=None,
    created_by=None,
):
    notification = Notification(
        recipient_type=recipient_type,
        notification_type=notification_type,
        title=title,
        message=message,
        recipient=recipient,
        workspace=workspace,
        chatbot=chatbot,
        target_id=target_id,
        metadata={} if metadata is None else metadata,
        created_by=created_by,
    )
    notification.full_clean()
    notification.save()
    transaction.on_commit(lambda: emit_notification(notification))
    return notification


def create_user_notification(
    *,
    recipient,
    title,
    notification_type=NotificationType.GENERAL,
    message="",
    metadata=None,
    created_by=None,
):
    return create_notification(
        recipient_type=NotificationRecipientType.USER,
        notification_type=notification_type,
        recipient=recipient,
        title=title,
        message=message,
        metadata=metadata,
        created_by=created_by,
    )


def create_global_notification(
    *,
    title,
    notification_type=NotificationType.NOTIFY,
    message="",
    metadata=None,
    created_by=None,
):
    return create_notification(
        recipient_type=NotificationRecipientType.GLOBAL,
        notification_type=notification_type,
        title=title,
        message=message,
        metadata=metadata,
        created_by=created_by,
    )


def create_workspace_notification(
    *,
    workspace,
    title,
    notification_type=NotificationType.GENERAL,
    message="",
    metadata=None,
    created_by=None,
):
    return create_notification(
        recipient_type=NotificationRecipientType.WORKSPACE,
        notification_type=notification_type,
        workspace=workspace,
        title=title,
        message=message,
        metadata=metadata,
        created_by=created_by,
    )


def create_chatbot_notification(
    *,
    chatbot,
    title,
    notification_type=NotificationType.GENERAL,
    message="",
    metadata=None,
    created_by=None,
):
    return create_notification(
        recipient_type=NotificationRecipientType.CHATBOT,
        notification_type=notification_type,
        chatbot=chatbot,
        title=title,
        message=message,
        metadata=metadata,
        created_by=created_by,
    )


def create_chat_session_notification(
    *,
    chat_session_id,
    title,
    notification_type,
    message="",
    metadata=None,
    created_by=None,
):
    return create_notification(
        recipient_type=NotificationRecipientType.CHAT_SESSION,
        notification_type=notification_type,
        target_id=chat_session_id,
        title=title,
        message=message,
        metadata=metadata,
        created_by=created_by,
    )


def emit_notification(notification):
    channel_layer = get_channel_layer()
    if channel_layer is None:
        return

    payload = {
        "type": "notification.created",
        "notification": NotificationSerializer(notification).data,
    }

    if notification.recipient_type == NotificationRecipientType.GLOBAL:
        group_name = global_dashboard_group()
    elif notification.recipient_type == NotificationRecipientType.USER:
        group_name = user_dashboard_group(notification.recipient_id)
    elif notification.recipient_type == NotificationRecipientType.WORKSPACE:
        group_name = workspace_dashboard_group(notification.workspace_id)
    elif notification.recipient_type == NotificationRecipientType.CHATBOT:
        group_name = chatbot_dashboard_group(notification.chatbot_id)
    else:
        group_name = notification_group(
            notification.recipient_type,
            notification.target_id,
        )

    try:
        async_to_sync(channel_layer.group_send)(group_name, payload)
    except Exception:
        logger.exception("Failed to emit notification %s", notification.id)
