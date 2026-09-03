from django.urls import path

from chat_session.consumers import ChatSessionConsumer, VisitorChatSessionConsumer


websocket_urlpatterns = [
    path(
        "ws/widget/chatbots/<str:public_key>/"
        "conversations/<uuid:session_id>/",
        VisitorChatSessionConsumer.as_asgi(),
    ),
    path(
        "ws/chat-sessions/<uuid:session_id>/",
        ChatSessionConsumer.as_asgi(),
    ),
]
