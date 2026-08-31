from django.urls import path

from chat_session.consumers import ChatSessionConsumer


websocket_urlpatterns = [
    path(
        "ws/chat-sessions/<uuid:session_id>/",
        ChatSessionConsumer.as_asgi(),
    ),
]
