from django.urls import path

from chatbot.api.v1.client import views


urlpatterns = [
    path("", views.ChatbotListCreateView.as_view(), name="chatbot-list-create"),
    path(
        "invitations/accept/",
        views.AcceptChatbotInvitationView.as_view(),
        name="accept-chatbot-invitation",
    ),
    path(
        "<slug:chatbot_slug>/",
        views.ChatbotDetailView.as_view(),
        name="chatbot-detail",
    ),
    path(
        "<slug:chatbot_slug>/members/",
        views.ChatbotMemberListView.as_view(),
        name="chatbot-members",
    ),
    path(
        "<slug:chatbot_slug>/invitations/",
        views.InviteChatbotMemberView.as_view(),
        name="invite-chatbot-member",
    ),
]
