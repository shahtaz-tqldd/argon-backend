from django.urls import path
from base.api.v1.client import views


urlpatterns = [
    path("", views.ArgonChatbotConfigAPIView.as_view(), name="argon-config"),
]
