from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer

from base.socket.events import SESSION_TRANSITIONS, SESSION_UPDATED
from base.socket.services.groups import chat_session_group, chatbot_dashboard_group
from app.utils.logger import logger


def broadcast(groups, payload):
    """Best-effort delivery; durable state continues to belong to domain services."""
    channel_layer = get_channel_layer()
    if channel_layer is None:
        return
    for group in set(groups):
        try:
            async_to_sync(channel_layer.group_send)(group, payload)
        except Exception:
            logger.exception("Failed to publish %s to %s", payload["type"], group)


def publish_dashboard_event(group, event):
    broadcast([group], {"type": "dashboard.event", "group": group, "event": event})


def publish_session_event(session_id, chatbot_id, event_type, data):
    event = {"type": event_type, "session_id": str(session_id), "data": data}
    broadcast(
        [chat_session_group(session_id), chatbot_dashboard_group(chatbot_id)],
        {"type": "chat_session.event", "event": event},
    )
    if event_type in SESSION_TRANSITIONS:
        # A dashboard-only partial update; keep the widget contract unchanged.
        publish_dashboard_event(chatbot_dashboard_group(chatbot_id), {
            "type": SESSION_UPDATED,
            "session_id": str(session_id),
            "data": {**data, "change": event_type},
        })
