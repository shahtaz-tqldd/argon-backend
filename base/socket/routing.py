from django.urls import path

from base.socket.consumers.dashboard import DashboardConsumer
from base.socket.consumers.widget import VisitorChatSessionConsumer

websocket_urlpatterns = [
    path("ws/dashboard/", DashboardConsumer.as_asgi()),
    path(
        "ws/widget/chatbots/<str:public_key>/conversations/<uuid:session_id>/",
        VisitorChatSessionConsumer.as_asgi(),
    ),
]
