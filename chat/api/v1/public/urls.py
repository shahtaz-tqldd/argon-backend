from django.urls import path, include

from chat.api.v1.public import views

chat_session = [
    path("list/", views.PublicVisitorSessionListAPIView.as_view(), name="public-visitor-session-list"),
    path("create/", views.PublicVisitorSessionCreateAPIView.as_view(), name="public-visitor-session-create"),
]

chat_message = [
    path("list/", views.PublicVisitorMessageListAPIView.as_view(), name="public-visitor-message-list"),
    path("create/", views.PublicVisitorMessageCreateAPIView.as_view(), name="public-visitor-message-create"),
]

urlpatterns = [
    path("sessions/", include(chat_session)),
    path("messages/", include(chat_message)),
]
