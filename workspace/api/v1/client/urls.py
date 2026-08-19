from django.urls import path

from workspace.api.v1.client import views

urlpatterns = [
    path("", views.WorkspaceDetailView.as_view(), name="current-workspace"),
    path("<slug:workspace_slug>/", views.WorkspaceDetailView.as_view(), name="workspace-detail"),

    # invitations
    path("invitations/accept/", views.AcceptWorkspaceInvitationView.as_view(), name="accept-workspace-invitation"),
    path("invitations/", views.InviteWorkspaceMemberView.as_view(), name="invite-current-workspace-member"),
    path(
        "<slug:workspace_slug>/invitations/",
        views.InviteWorkspaceMemberView.as_view(),
        name="invite-workspace-member",
    ),
]
