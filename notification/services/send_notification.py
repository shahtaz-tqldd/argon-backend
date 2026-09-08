from django.db import transaction

from notification.api.v1.client.serializers import NotificationSerializer
from notification.models import (
    Notification,
    NotificationRecipientType,
    NotificationType,
)

from base.socket.services.groups import (
    notification_group, global_dashboard_group, user_dashboard_group,
    workspace_dashboard_group, chatbot_dashboard_group, chat_session_dashboard_group,
)
from base.socket.services.broadcaster import broadcast


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
    notification_data = dict(NotificationSerializer(notification).data)
    notification_data["event"] = notification.notification_type
    payload = {
        "type": "notification.created",
        "notification": notification_data,
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

    payload["group"] = group_name
    broadcast([group_name], payload)
