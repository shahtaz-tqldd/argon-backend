from django.urls import path

from base.socket.consumers.chatbot_admin import ChatbotAdminConsumer
from base.socket.consumers.widget import ChatbotWidgetConsumer

websocket_urlpatterns = [
    path(
        "ws/dashboard/", 
        ChatbotAdminConsumer.as_asgi(),
    ),
    path(
        "ws/widget/chatbots/<str:public_key>/conversations/<uuid:session_id>/",
        ChatbotWidgetConsumer.as_asgi(),
    ),
]
