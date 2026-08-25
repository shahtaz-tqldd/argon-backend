from django.urls import include, path

from chatbot.api.v1.client import views

# chatbot
chatbot = [
    path("create/", views.ChatbotCreateView.as_view(), name="chatbot-create"),
    path("list/", views.ChatbotListView.as_view(), name="chatbot-list"),
    path("details/", views.ChatbotDetailView.as_view(), name="chatbot-detail"),
    path(
        "short-details/",
        views.ChatbotShortDetailView.as_view(),
        name="chatbot-short-detail",
    ),
    path("update/", views.ChatbotUpdateView.as_view(), name="chatbot-update"),
    path("delete/", views.ChatbotDeleteView.as_view(), name="chatbot-delete"),
]

# chatbot team
chatbot_team = [
    path("list/", views.ChatbotMemberListView.as_view(), name="chatbot-members"),
    path(
        "details/",
        views.ChatbotMemberDetailView.as_view(),
        name="chatbot-member-details",
    ),
    path(
        "invite/",
        views.InviteChatbotMemberView.as_view(),
        name="invite-chatbot-member",
    ),
    path(
        "permissions/",
        views.ChatbotMemberPermissionView.as_view(),
        name="chatbot-member-permissions",
    ),
    path(
        "accept-invite/",
        views.AcceptChatbotInvitationView.as_view(),
        name="accept-chatbot-invitation",
    ),
    path(
        "remove-member/",
        views.RemoveChatbotMemberView.as_view(),
        name="remove-chatbot-member",
    ),
]

urlpatterns = [
    path("team/", include(chatbot_team)),
    path("knowledge/", include("knowledge.api.v1.client.urls")),
    path("", include(chatbot)),
]
