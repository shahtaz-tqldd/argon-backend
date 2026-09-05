from django.urls import include, path

from chat_session.api.v1.client import views


session_patterns = [
    path("list/", views.ChatSessionListView.as_view(), name="chat-session-list"),
    path(
        "details/",
        views.ChatSessionDetailView.as_view(),
        name="chat-session-detail",
    ),
    path(
        "mark-read/",
        views.ChatSessionMarkReadView.as_view(),
        name="chat-session-mark-read",
    ),
]

message_patterns = [
    path("list/", views.ChatMessageListView.as_view(), name="chat-message-list"),
    path("send/", views.AgentMessageCreateView.as_view(), name="chat-message-send"),
]

takeover_patterns = [
    path(
        "take-over/",
        views.TakeOverSessionView.as_view(),
        name="session-take-over",
    ),
    path("release/", views.ReleaseSessionView.as_view(), name="session-release"),
    path("resolve/", views.ResolveSessionView.as_view(), name="session-resolve"),
    path("reopen/", views.ReopenSessionView.as_view(), name="session-reopen"),
]

transfer_patterns = [
    path("request/", views.TransferSessionView.as_view(), name="transfer-request"),
    path(
        "incoming/",
        views.IncomingTransferListView.as_view(),
        name="transfer-incoming-list",
    ),
    path("accept/", views.AcceptTransferView.as_view(), name="transfer-accept"),
    path("decline/", views.DeclineTransferView.as_view(), name="transfer-decline"),
    path("cancel/", views.CancelTransferView.as_view(), name="transfer-cancel"),
]

urlpatterns = [
    path("sessions/", include(session_patterns)),
    path("messages/", include(message_patterns)),
    path("takeovers/", include(takeover_patterns)),
    path("transfers/", include(transfer_patterns)),
]
