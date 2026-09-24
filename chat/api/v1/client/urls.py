from django.urls import include, path
from chat.api.v1.client import views

session_patterns = [
    path("list/", views.ChatSessionListView.as_view(), name="chat-session-list"),
    path("details/", views.ChatSessionDetailView.as_view(), name="chat-session-detail"),
    path("mark-read/", views.ChatSessionMarkReadView.as_view(), name="chat-session-mark-read"),
    path("block-visitor/", views.BlockVisitorView.as_view(), name="chat-session-block-visitor"),
    path("transcript/", views.ChatSessionTranscriptView.as_view(), name="chat-session-transcript"),
    path("delete/", views.ChatSessionDeleteView.as_view(), name="chat-session-delete"),
]

message_patterns = [
    path("list/", views.ChatMessageListView.as_view(), name="chat-message-list"),
    path("send/", views.AgentMessageCreateView.as_view(), name="chat-message-send"),
]

takeover_patterns = [
    path("take-over/", views.TakeOverSessionView.as_view(), name="session-take-over"),
    path("release/", views.ReleaseSessionView.as_view(), name="session-release"),
    path("force-return-to-ai/",views.ForceReturnToAIView.as_view(),name="session-force-return-to-ai"),
    path("resolve/", views.ResolveSessionView.as_view(), name="session-resolve"),
]

transfer_patterns = [
    path("request/", views.TransferSessionView.as_view(), name="transfer-request"),
    path("session/",views.SessionTransferStatusView.as_view(),name="session-transfer-status"),
    path("incoming/",views.IncomingTransferListView.as_view(),name="transfer-incoming-list",),
    path("accept/", views.AcceptTransferView.as_view(), name="transfer-accept"),
    path("decline/", views.DeclineTransferView.as_view(), name="transfer-decline"),
    path("cancel/", views.CancelTransferView.as_view(), name="transfer-cancel"),
]

analytics_patterns = [
    path("stats/", views.SessionStatsAPIView.as_view(), name="session-stats"),
    path("overview/", views.SessionOverviewAPIView.as_view(), name="session-overview"),
]

urlpatterns = [
    path("sessions/", include(session_patterns)),
    path("messages/", include(message_patterns)),
    path("takeovers/", include(takeover_patterns)),
    path("transfers/", include(transfer_patterns)),
    path("analytics/", include(analytics_patterns)),
]
