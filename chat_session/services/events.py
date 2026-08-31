from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer


def chat_session_group(session_id):
    return f"chat_session_{session_id}"


def publish_session_event(session_id, event_type, data):
    channel_layer = get_channel_layer()
    if channel_layer is None:
        return
    async_to_sync(channel_layer.group_send)(
        chat_session_group(session_id),
        {
            "type": "chat_session.event",
            "event": {
                "type": event_type,
                "session_id": str(session_id),
                "data": data,
            },
        },
    )
