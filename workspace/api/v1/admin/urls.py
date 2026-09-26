from django.urls import path

from workspace.api.v1.admin import views

urlpatterns = [
    path(
        "list/",
        views.AdminWorkspaceListAPIView.as_view(),
        name="admin-workspace-list",
    ),
    path(
        "details/",
        views.AdminWorkspaceDetailView.as_view(),
        name="admin-workspace-detail",
    ),
    path(
        "update/",
        views.AdminWorkspaceUpdateView.as_view(),
        name="admin-workspace-update",
    ),
    path(
        "delete/",
        views.AdminWorkspaceDeleteView.as_view(),
        name="admin-workspace-delete",
    ),
]
