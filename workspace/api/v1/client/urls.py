from django.urls import include, path

from workspace.api.v1.client import views

workspaces = [
    path("", views.WorkspaceDetailView.as_view(), name="workspace-detail"),
    path("create/", views.WorkspaceCreateView.as_view(), name="workspace-create"),
    path("update/", views.WorkspaceUpdateView.as_view(), name="workspace-update"),
    path("delete/", views.WorkspaceDeleteView.as_view(), name="workspace-delete"),
]

workspace_members = [
    path("list/", views.WorkspaceMemberListView.as_view(), name="workspace-members"),
    path(
        "details/",
        views.WorkspaceMemberDetailView.as_view(),
        name="workspace-member-details",
    ),
    path(
        "invite/",
        views.InviteWorkspaceMemberView.as_view(),
        name="invite-workspace-member",
    ),
    path(
        "role/",
        views.WorkspaceMemberRoleView.as_view(),
        name="workspace-member-role",
    ),
    path(
        "accept-invite/",
        views.AcceptWorkspaceInvitationView.as_view(),
        name="accept-workspace-invitation",
    ),
    path(
        "remove-member/",
        views.RemoveWorkspaceMemberView.as_view(),
        name="remove-workspace-member",
    ),
]

urlpatterns = [
    path("team/", include(workspace_members)),
    path("", include(workspaces)),
]
