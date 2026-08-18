from django.urls import path

from app.api.v1.views import ArgonChatbotConfigAPIView, ArgonChatbotConfigUpdateAPIView


client_urlpatterns = [
    path("", ArgonChatbotConfigAPIView.as_view(), name="argon-config"),
]

admin_urlpatterns = [
    path("update/", ArgonChatbotConfigUpdateAPIView.as_view(), name="argon-config-update"),
]
