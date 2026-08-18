import logging

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.core.exceptions import ValidationError

from notification.models import Notification, NotificationType
from notification.api.v1.client.serializers import NotificationSerializer

logger = logging.getLogger(__name__)


def user_dashboard_group(user_id):
    return f"notifications.user.{user_id}"


def global_dashboard_group():
    return "notifications.global"


def create_notification(
    *,
    notification_type,
    title,
    message="",
    recipient=None,
    trip=None,
    metadata=None,
    created_by=None,
):
    notification = Notification(
        notification_type=notification_type,
        title=title,
        message=message,
        recipient=recipient,
        trip=trip,
        metadata=metadata or {},
        created_by=created_by,
    )
    notification.full_clean()
    notification.save()
    emit_notification(notification)
    return notification


def create_general_notification(*, recipient, title, message="", metadata=None, created_by=None):
    return create_notification(
        notification_type=NotificationType.GENERAL,
        recipient=recipient,
        title=title,
        message=message,
        metadata=metadata,
        created_by=created_by,
    )


def create_trip_notification(*, recipient, trip, title, message="", metadata=None, created_by=None):
    if trip.user_id != recipient.id:
        raise ValidationError("Trip notification recipient must own the trip.")
    return create_notification(
        notification_type=NotificationType.TRIP,
        recipient=recipient,
        trip=trip,
        title=title,
        message=message,
        metadata=metadata,
        created_by=created_by,
    )


def create_global_notification(*, title, message="", metadata=None, created_by=None):
    return create_notification(
        notification_type=NotificationType.GLOBAL,
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

    try:
        if notification.notification_type == NotificationType.GLOBAL:
            async_to_sync(channel_layer.group_send)(global_dashboard_group(), payload)
            return

        async_to_sync(channel_layer.group_send)(
            user_dashboard_group(notification.recipient_id),
            payload,
        )
    except Exception:
        logger.exception("Failed to emit notification %s", notification.id)
