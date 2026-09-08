from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer

from app.utils.logger import logger
from notification.services import chatbot_dashboard_group


def chat_session_group(session_id):
    return f"chat_session_{session_id}"


def publish_session_event(session_id, chatbot_id, event_type, data):
    """Publish a chat event to the conversation and its authorized dashboards."""

    channel_layer = get_channel_layer()
    if channel_layer is None:
        return

    payload = {
        "type": "chat_session.event",
        "event": {
            "type": event_type,
            "session_id": str(session_id),
            "data": data,
        },
    }
    for group_name in (
        chat_session_group(session_id),
        chatbot_dashboard_group(chatbot_id),
    ):
        try:
            async_to_sync(channel_layer.group_send)(group_name, payload)
        except Exception:
            logger.exception(
                "Failed to publish %s for chat session %s to %s",
                event_type,
                session_id,
                group_name,
            )
