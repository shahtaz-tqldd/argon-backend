from django.urls import include, path

from chat_session.api.v1.client import views


session_patterns = [
    path("list/", views.ChatSessionListView.as_view(), name="chat-session-list"),
    path(
        "details/",
        views.ChatSessionDetailView.as_view(),
        name="chat-session-detail",
    ),
]

message_patterns = [
    path("list/", views.ChatMessageListView.as_view(), name="chat-message-list"),
    path("send/", views.AgentMessageCreateView.as_view(), name="chat-message-send"),
]

takeover_patterns = [
    path("take-over/", views.TakeOverSessionView.as_view(), name="session-take-over"),
    path("reassign/", views.ReassignSessionView.as_view(), name="session-reassign"),
    path("release/", views.ReleaseSessionView.as_view(), name="session-release"),
    path("resolve/", views.ResolveSessionView.as_view(), name="session-resolve"),
    path("reopen/", views.ReopenSessionView.as_view(), name="session-reopen"),
]

urlpatterns = [
    path("sessions/", include(session_patterns)),
    path("messages/", include(message_patterns)),
    path("takeovers/", include(takeover_patterns)),
]
