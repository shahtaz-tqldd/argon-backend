from django.urls import path
from base.api.v1.admin import views

urlpatterns = [
    path("update/", views.ArgonChatbotConfigUpdateAPIView.as_view(), name="argon-config-update"),
]
