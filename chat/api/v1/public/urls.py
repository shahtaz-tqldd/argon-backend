from django.urls import path

from chat.api.v1.public import views

urlpatterns = [
    path(
        "visitors/<str:visitor_id>/sessions/",
        views.PublicVisitorSessionListView.as_view(),
        name="public-visitor-sessions",
    ),
    path(
        "conversations/",
        views.VisitorConversationView.as_view(),
        name="visitor-conversation",
    ),
    path(
        "conversations/<uuid:session_id>/messages/",
        views.VisitorMessageCreateView.as_view(),
        name="visitor-message-create",
    ),
]
