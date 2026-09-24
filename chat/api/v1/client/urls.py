from django.urls import include, path
from chat.api.v1.client import views

chat_sessions = [
    path("list/", views.ChatSessionListView.as_view(), name="chat-session-list"),
    path("details/", views.ChatSessionDetailView.as_view(), name="chat-session-detail"),
    path("mark-read/", views.ChatSessionMarkReadView.as_view(), name="chat-session-mark-read"),
    path("block-visitor/", views.BlockVisitorView.as_view(), name="chat-session-block-visitor"),
    path("transcript/", views.ChatSessionTranscriptView.as_view(), name="chat-session-transcript"),
    path("delete/", views.ChatSessionDeleteView.as_view(), name="chat-session-delete"),
]

chat_messages = [
    path("list/", views.ChatMessageListView.as_view(), name="chat-message-list"),
    path("send/", views.AgentMessageCreateView.as_view(), name="chat-message-send"),
]

session_takeover = [
    path("take-over/", views.TakeOverSessionView.as_view(), name="session-take-over"),
    path("release/", views.ReleaseSessionView.as_view(), name="session-release"),
    path("force-return-to-ai/",views.ForceReturnToAIView.as_view(),name="session-force-return-to-ai"),
    path("resolve/", views.ResolveSessionView.as_view(), name="session-resolve"),
]

session_transfer = [
    path("request/", views.TransferSessionView.as_view(), name="transfer-request"),
    path("session/",views.SessionTransferStatusView.as_view(),name="session-transfer-status"),
    path("incoming/",views.IncomingTransferListView.as_view(),name="transfer-incoming-list",),
    path("accept/", views.AcceptTransferView.as_view(), name="transfer-accept"),
    path("decline/", views.DeclineTransferView.as_view(), name="transfer-decline"),
    path("cancel/", views.CancelTransferView.as_view(), name="transfer-cancel"),
]

chat_analytics = [
    path("stats/", views.SessionStatsAPIView.as_view(), name="session-stats"),
    path("overview/", views.SessionOverviewAPIView.as_view(), name="session-overview"),
]

test_chat_sessions = [
    path(
        "create/",
        views.TestChatSessionCreateView.as_view(),
        name="test-chat-session-create",
    ),
    path(
        "list/",
        views.TestChatSessionListView.as_view(),
        name="test-chat-session-list",
    ),
    path(
        "details/",
        views.TestChatSessionDetailView.as_view(),
        name="test-chat-session-detail",
    ),
]

test_chat_messages = [
    path(
        "list/",
        views.TestChatMessageListView.as_view(),
        name="test-chat-message-list",
    ),
    path(
        "send/",
        views.TestChatMessageCreateView.as_view(),
        name="test-chat-message-send",
    ),
]

urlpatterns = [
    path("sessions/", include(chat_sessions)),
    path("messages/", include(chat_messages)),
    path("takeovers/", include(session_takeover)),
    path("transfers/", include(session_transfer)),
    path("analytics/", include(chat_analytics)),
    path("test-sessions/", include(test_chat_sessions)),
    path("test-messages/", include(test_chat_messages)),
]
